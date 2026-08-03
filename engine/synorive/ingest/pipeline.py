"""
摄取流水线 —— 从文件到可检索
====================================================================
阶段：probe → extract → chunk → embed → index

每个阶段单独记状态（item_stages 表），所以：
  · 强杀进程重启后，已完成的阶段不重做（验收标准 A13）
  · 某个阶段失败不影响其它阶段（比如 OCR 挂了，正文照样能搜）
  · 换嵌入模型时只需重跑 embed 阶段（E15 模型热插拔）

并发模型：进程内 N 个线程，每个线程一个 threads=1 的推理会话。
实测本机 7 线程 → 317 段/秒，比单会话多线程（247）更快。
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..analyze.embedder import TextEmbedder
from ..analyze.enrich import enrich
from ..analyze.image import ImageEmbedder, OcrEngine, analyze_image, is_image
from ..analyze.transcribe import Transcriber
from ..analyze.video import analyze_video, is_audio, is_video
from ..store.repository import ChunkRow, Repository
from .chunker import chunk_segments
from .parsers import CODE_EXT, ParseError, TextSegment, can_parse, iter_supported, parse
from .web import fetch, is_url, url_fingerprint

log = logging.getLogger("synorive.ingest")

#: 算指纹时最多读多少字节。大文件全读一遍太慢，
#: 取「头 1MB + 尾 1MB + 文件大小」已经足够区分。
FINGERPRINT_SAMPLE = 1 << 20


class JobControl:
    """
    F2 —— 一个摄取任务的暂停 / 取消开关。

    🔴 **暂停必须真的能暂停，取消必须真的能取消。**
    一个点了没反应的按钮比没有按钮更糟：用户会以为自己点错了，
    反复点，然后放弃，最后连着不信任别的按钮。

    实现上只做一件事：在**每个文件开工前**看一眼开关。
    所以粒度是"当前这个文件做完就停"，不是"立刻断在半路" ——
    半路断掉会留下写了一半的索引记录，那是拿一致性换响应速度，不划算。
    单个文件最长几十秒（长视频转写），点了暂停最坏等这么久。

    `_gate` 是"没暂停"的信号：set = 放行。反过来写（set = 暂停）的话，
    新建对象的默认状态就是暂停，每次都得记着先 set 一下，迟早忘。
    """

    __slots__ = ("_gate", "_cancelled")

    def __init__(self) -> None:
        self._gate = threading.Event()
        self._gate.set()
        self._cancelled = False

    def pause(self) -> None:
        self._gate.clear()

    def resume(self) -> None:
        self._gate.set()

    def cancel(self) -> None:
        self._cancelled = True
        # 🔴 取消时**必须一并放行**：否则正卡在 wait() 上的工作线程
        # 永远醒不过来，任务表上显示"已取消"而线程池还挂着，
        # 进程退出时会卡在等线程 —— 不报错、只是关不掉
        self._gate.set()

    @property
    def paused(self) -> bool:
        return not self._gate.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def wait_if_paused(self, poll_s: float = 0.25) -> None:
        """
        真的等到被放行（或被取消）为止。

        🔴 **这里曾经写成 `self._gate.wait(timeout)` 一次就返回 —— 那是个
        「不报错、不崩溃、但功能完全无效」的 bug。** `Event.wait(t)` 到点就返回，
        **不管开关是不是还关着**。结果是：点了暂停，每个文件只慢 1 秒，
        任务照跑到底。界面上按钮状态、`paused` 字段全是对的，
        进度条也在动 —— 唯一错的是它根本没停。

        改成循环轮询：醒来先看一眼开关，还关着就接着睡。
        用轮询而不是无参 `wait()` 是为了让**取消**这条路多一层保险 ——
        万一哪天 `cancel()` 忘了 `set()`，最坏是每 0.25 秒醒一次发现该退出，
        而不是永远挂在那里把线程池和进程退出一起拖死。
        """
        while not self._gate.is_set():
            if self._cancelled:
                return
            self._gate.wait(poll_s)


@dataclass
class IngestStats:
    total: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    chunks: int = 0
    cancelled: bool = False
    started_at: float = field(default_factory=time.perf_counter)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started_at

    @property
    def items_per_sec(self) -> float:
        return self.done / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def chunks_per_sec(self) -> float:
        return self.chunks / self.elapsed if self.elapsed > 0 else 0.0


ProgressCb = Callable[[dict[str, Any]], None]


def file_fingerprint(path: Path) -> str:
    """
    内容指纹。同一份内容换个路径进来也认得出，重复投喂直接跳过。

    不整文件哈希是因为几百 MB 的视频算一遍要好几秒，
    而「头 1MB + 尾 1MB + 大小」在实际使用里碰撞概率可以忽略。
    """
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(FINGERPRINT_SAMPLE))
        if size > FINGERPRINT_SAMPLE * 2:
            f.seek(-FINGERPRINT_SAMPLE, 2)
            h.update(f.read(FINGERPRINT_SAMPLE))
    return h.hexdigest()[:32]


def verify_sources(
    repo: Repository,
    *,
    limit: int = 5000,
    on_progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """
    4.22b H2 —— 源文件完整性校验：**库里的记录和磁盘上的文件还对得上吗。**

    不用改表、不用额外存哈希：`items.fingerprint` 本来就是内容派生的
    （头 1MB + 尾 1MB + 大小，见 `file_fingerprint`），重算一遍对比就知道了。
    读 2MB 一个文件，几千条也就几秒。

    三种结论，**必须分开报**，因为用户要做的事完全不同：
      · `changed` 文件被改过 → 库里的正文/向量是旧的，**搜出来的内容和文件对不上**，
        该重新投喂
      · `missing` 文件没了 → 记录成了孤儿，搜到了也打不开，该清理
      · `ok` 一致

    🔴 **只报告，不自动删也不自动重建。** 外接硬盘没插、网络盘没连上时，
    整库都会报 missing —— 那种情况下自动清理等于把整个库删掉。
    删什么由用户看着报告决定。
    """
    rows = repo.file_backed_items(limit=limit)
    changed: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    ok = 0

    for n, r in enumerate(rows, 1):
        locator = str(r["locator"] or "")
        p = Path(locator)
        info = {"itemId": r["id"], "title": r["title"] or p.name, "path": locator}
        try:
            if not p.exists() or not p.is_file():
                missing.append(info)
                continue
            now_fp = file_fingerprint(p)
            if now_fp != str(r["fingerprint"] or ""):
                info["was"] = str(r["fingerprint"] or "")[:12]
                info["now"] = now_fp[:12]
                changed.append(info)
            else:
                ok += 1
        except OSError as e:
            # 权限不足 / 文件被独占 / 路径太长 —— 这些**不是"文件被改了"**，
            # 混进 changed 里会让用户去重新投喂一堆其实没问题的文件
            info["error"] = str(e)
            missing.append(info)

        if on_progress and n % 50 == 0:
            on_progress({"stage": "verify", "done": n, "total": len(rows)})

    return {
        "checked": len(rows),
        "ok": ok,
        "changed": changed,
        "missing": missing,
        # 🔴 报出来的是"检查了几条"，不是"库里有几条" —— 超过 limit 时
        #    不说清楚的话，用户会把"抽查了 5000 条全对"当成"全库都对"
        "truncated": len(rows) >= limit,
        "limit": limit,
    }


class IngestPipeline:
    def __init__(
        self,
        repo: Repository,
        model_dir: Path,
        *,
        concurrency: int = 7,
        on_progress: ProgressCb | None = None,
    ) -> None:
        self.repo = repo
        self.model_dir = model_dir
        self.concurrency = max(1, min(16, concurrency))
        self.on_progress = on_progress
        self._local = threading.local()
        self._lock = threading.Lock()
        self._vec_ready = False
        self._img_vec_ready = False

        # ── 线程配置：实测出来的，不是拍脑袋的 ──────────────
        # 本机 i5-1155G7（4 物理核 / 8 逻辑核），真实分块平均 293 字：
        #   单会话 threads=1            19.6 块/秒
        #   单会话 threads=4            44.5
        #   7 会话 × 1 线程             42.7
        #   **4 会话 × 2 线程           47.2  ← 最优**
        # 总线程数超过物理核数 ×2 之后就纯粹是互相抢核了，怎么排都是 47 封顶。
        #
        # 所以并行度不能直接用 concurrency：那是「同时处理几个文件」，
        # 而推理会话数要按物理核算。两者混为一谈的话，
        # concurrency 调到 16 反而更慢。
        phys = _physical_cores()
        self.embed_workers = max(1, min(self.concurrency, phys))
        self.threads_per_session = max(1, round(phys * 2 / self.embed_workers))
        log.info(
            "推理配置：%d 个会话 × 每会话 %d 线程（物理核 %d，文件并发 %d）",
            self.embed_workers, self.threads_per_session, phys, self.concurrency,
        )

    # ── 每线程一个推理会话 ──────────────────────────────────

    def _embedder(self) -> TextEmbedder | None:
        """本线程的向量化器。线程数见 __init__ 里那张实测表。"""
        emb: TextEmbedder | None = getattr(self._local, "embedder", None)
        if emb is not None:
            return emb

        d = self.model_dir / "bge-small-zh-v1.5"
        if not (d / "model.onnx").exists():
            return None
        emb = TextEmbedder(d, threads=self.threads_per_session)
        try:
            emb.load()
        except Exception as e:  # noqa: BLE001
            log.warning("向量模型加载失败，本次只建关键词索引：%s", e)
            return None
        self._local.embedder = emb

        # 向量表要按实际维度建，第一个到这儿的线程负责建
        with self._lock:
            if not self._vec_ready:
                self.repo.db.ensure_vector_tables(emb.dim, emb.model_id)
                self._setup_ann_index(emb.dim, emb.model_id)
                self._vec_ready = True
        return emb

    def _setup_ann_index(self, dim: int, model_id: str) -> None:
        """
        A17：维度和模型确定的这一刻，顺带把 ANN 索引也接上——
        和 `ensure_vector_tables` 同一个时机、同一把锁，因为道理是一样的：
        两者都要等实际嵌入维度确定了才能建。

        usearch 没装（可选依赖，见 `doctor/registry.py` 的 `pkg-ann`）
        就安静跳过，`self.repo.ann_index` 保持 None——写入路径本来就对
        `ann_index is None` 做了判断，行为等价于这个功能不存在。
        """
        try:
            from ..search.ann_index import AnnIndex
        except ImportError:
            log.info("usearch 没装，大规模检索提速功能不可用（不影响其它任何功能）")
            return

        index_path = self.repo.db.path.parent / "ann_index.usearch"
        model_tag = f"{model_id}:{dim}"
        ann = AnnIndex(dim=dim, model_tag=model_tag, index_path=index_path)
        try:
            ann.load()
        except Exception as e:  # noqa: BLE001
            log.warning("ANN 索引加载失败，等库大到需要时再重建：%s", e)
        self.repo.ann_index = ann

    # ── 单个文件 ────────────────────────────────────────────

    def ingest_file(self, path: Path, *, source: str = "file", tags: list[str] | None = None) -> str:
        """
        处理一个文件。返回 'done' / 'skipped' / 'failed'。
        异常在这里就地消化 —— 一个坏文件不该拖垮整批。
        """
        try:
            if not path.is_file():
                return "skipped"
            if is_image(path):
                return self.ingest_image(path, source=source, tags=tags)
            if is_video(path) or is_audio(path):
                return self.ingest_media(path, source=source, tags=tags)
            if not can_parse(path):
                return "skipped"

            stat = path.stat()
            fp = file_fingerprint(path)

            existing = self.repo.find_by_fingerprint(fp)
            if existing is not None:
                settled = self.repo.get_settled_stages(str(existing["id"]))
                # done 或 skipped 都算"这个阶段有结论了"，不用再跑。
                # 只认 done 的话，空文件那类永远重做（它的 chunk 阶段是 skipped）。
                if {"extract", "chunk", "index"} <= settled:
                    return "skipped"

            item_id, _created = self.repo.upsert_item(
                fingerprint=fp,
                modality="text",
                source=source,
                title=path.stem,
                locator=str(path),
                mime=mimetypes.guess_type(path.name)[0],
                size_bytes=stat.st_size,
                content_time=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(
                    timespec="seconds"
                ),
                status="analyzing",
                tags=tags,
            )

            # ── extract ──────────────────────────────────
            self.repo.set_stage(item_id, "extract", "running")
            try:
                doc = parse(path)
            except ParseError as e:
                self.repo.set_stage(item_id, "extract", "failed", error=str(e))
                self.repo.set_item_status(item_id, "failed", str(e))
                return "failed"

            if not doc.segments or doc.char_count == 0:
                warn = "；".join(doc.warnings or []) or "文件里没有可提取的文字"
                self.repo.set_stage(item_id, "extract", "done")
                # 后面两个阶段标成 skipped 并算作"已处理"，否则
                # 每次重跑都会把这些空文件再走一遍全流程 ——
                # 空的 __init__.py 这类文件在代码库里成百上千，白烧时间。
                self.repo.set_stage(item_id, "chunk", "skipped", error="无内容")
                self.repo.set_stage(item_id, "index", "skipped", error="无内容")
                self.repo.set_item_status(item_id, "partial", warn)
                self.repo.update_item_fields(item_id, title=doc.title or path.stem)
                self.repo.index_item_text(item_id)
                return "done"

            self.repo.set_stage(item_id, "extract", "done")

            # ── enrich（C9 摘要关键词 + C10 实体）─────────
            # 放在 chunk 之前：摘要要基于全文算，而且它会变成 items.snippet，
            # 决定结果列表里那一行显示什么。
            full = doc.full_text
            enrichment = None
            self.repo.set_stage(item_id, "enrich", "running")
            try:
                # 源代码走另一条路：散文那套在代码上产出的是
                # 「关键词 = return/str/len」「摘要 = return []」这种垃圾
                enrichment = enrich(
                    full[:120_000], is_code=path.suffix.lower() in CODE_EXT
                )
                self.repo.set_stage(item_id, "enrich", "done")
            except Exception as e:  # noqa: BLE001
                # 增强失败不算整体失败：没有摘要和实体，检索照样能用
                log.debug("%s 内容增强失败：%s", path.name, e)
                self.repo.set_stage(item_id, "enrich", "failed", error=str(e))

            # 摘要优先于"截前 400 字"——截断出来的往往是版权头、import 语句这类噪声
            snippet = (enrichment.summary if enrichment and enrichment.summary else "") or full[
                :400
            ].replace("\n", " ").strip()

            self.repo.update_item_fields(
                item_id,
                title=doc.title or path.stem,
                snippet=snippet,
                meta_json=_meta_json(doc, enrichment),
            )
            if enrichment:
                if enrichment.keywords:
                    self.repo._attach_tags(item_id, enrichment.keywords[:8])
                if enrichment.entities:
                    try:
                        self.repo.write_entities(
                            item_id, [(e.kind, e.name, e.count) for e in enrichment.entities]
                        )
                    except Exception as e:  # noqa: BLE001
                        log.debug("%s 实体入库失败：%s", path.name, e)

            # ── chunk ────────────────────────────────────
            self.repo.set_stage(item_id, "chunk", "running")
            chunks = chunk_segments(doc.segments)
            if not chunks:
                self.repo.set_stage(item_id, "chunk", "skipped")
                self.repo.set_item_status(item_id, "partial", "切不出有效分块")
                self.repo.index_item_text(item_id)
                return "done"
            self.repo.set_stage(item_id, "chunk", "done")

            # ── embed ────────────────────────────────────
            emb = self._embedder()
            vectors = None
            model_id = ""
            if emb is not None:
                self.repo.set_stage(item_id, "embed", "running")
                try:
                    vectors = emb.encode([c.text for c in chunks])
                    model_id = emb.model_id
                    self.repo.set_stage(item_id, "embed", "done", model_id=model_id)
                except Exception as e:  # noqa: BLE001
                    # 向量化失败不算整体失败：关键词索引照建，
                    # 用户至少能用关键词搜到，之后补跑 embed 阶段即可
                    log.warning("%s 向量化失败：%s", path.name, e)
                    self.repo.set_stage(item_id, "embed", "failed", error=str(e))
                    vectors = None
            else:
                self.repo.set_stage(item_id, "embed", "skipped", error="向量模型不可用")

            # ── index ────────────────────────────────────
            self.repo.set_stage(item_id, "index", "running")
            rows = [
                ChunkRow(
                    text=c.text,
                    channel=c.channel,
                    index=c.index,
                    page=c.page,
                    section=c.section,
                    token_count=c.token_estimate,
                )
                for c in chunks
            ]
            n = self.repo.write_chunks(item_id, rows, vectors, model_id=model_id)
            self.repo.index_item_text(item_id)
            self.repo.set_stage(item_id, "index", "done")

            status = "ready" if vectors is not None else "partial"
            note = None if vectors is not None else "只建了关键词索引，语义检索需要向量模型"
            self.repo.set_item_status(item_id, status, note)

            return "done"

        except Exception as e:  # noqa: BLE001
            log.exception("处理 %s 时出了意料之外的错", path)
            return "failed"

    # ── 图片 ────────────────────────────────────────────────

    def _image_embedder(self) -> Any:
        emb = getattr(self._local, "img_embedder", None)
        if emb is not None:
            return emb
        d = self.model_dir / "clip-vit-b32"
        e = ImageEmbedder(d, threads=self.threads_per_session)
        if not e.available():
            return None
        try:
            e.load()
        except Exception as ex:  # noqa: BLE001
            log.warning("图像向量模型加载失败：%s", ex)
            return None
        self._local.img_embedder = e
        return e

    def ingest_image(
        self, path: Path, *, source: str = "file", tags: list[str] | None = None
    ) -> str:
        """
        图片的快速通道：EXIF + 感知哈希 + 截图判定 + 图像向量。

        **OCR 不在这里跑。** 实测 OCR 单张 1.5~2 秒，而且因为 Python GIL
        几乎不并行（4 线程只比 1 线程快 1.38 倍）。塞进主流水线的话，
        索引一个照片库的速度会从 19 张/秒掉到 1.2 张/秒。
        所以 OCR 走 run_deferred_ocr() 做低优先级后台补跑 ——
        图片先能按时间、地点、以图搜图、文件名找到，图里的文字随后补上。
        """
        try:
            stat = path.stat()
            fp = file_fingerprint(path)

            existing = self.repo.find_by_fingerprint(fp)
            if existing is not None:
                settled = self.repo.get_settled_stages(str(existing["id"]))
                if {"extract", "embed", "index"} <= settled:
                    return "skipped"

            item_id, _ = self.repo.upsert_item(
                fingerprint=fp,
                modality="image",
                source=source,
                title=path.stem,
                locator=str(path),
                mime=mimetypes.guess_type(path.name)[0],
                size_bytes=stat.st_size,
                content_time=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(
                    timespec="seconds"
                ),
                status="analyzing",
                tags=tags,
            )

            self.repo.set_stage(item_id, "extract", "running")
            info = analyze_image(path, ocr=None, embedder=self._image_embedder())
            if info.width == 0:
                err = "；".join(info.warnings) or "打不开这张图"
                self.repo.set_stage(item_id, "extract", "failed", error=err)
                self.repo.set_item_status(item_id, "failed", err)
                return "failed"
            self.repo.set_stage(item_id, "extract", "done")

            # EXIF 的拍摄时间比文件修改时间准得多 —— 文件复制一次修改时间就变了
            if info.exif_time:
                self.repo.update_item_fields(item_id, content_time=info.exif_time)

            meta = {
                "kind": "image",
                "width": info.width,
                "height": info.height,
                "phash": info.phash,
                "exifTime": info.exif_time,
                "cameraModel": info.camera,
                "gps": {"lat": info.gps[0], "lon": info.gps[1]} if info.gps else None,
                "isScreenshot": info.is_screenshot,
                "dominantColors": info.dominant_colors,
            }
            bits = [f"{info.width}×{info.height}"]
            if info.is_screenshot:
                bits.append("截图")
            if info.camera:
                bits.append(info.camera)
            if info.gps:
                bits.append(f"{info.gps[0]:.4f},{info.gps[1]:.4f}")
            self.repo.update_item_fields(
                item_id,
                snippet=" · ".join(bits),
                meta_json=json.dumps(meta, ensure_ascii=False),
            )

            if info.phash:
                self.repo.write_phash(item_id, info.phash)

            if info.embedding is not None:
                emb = self._image_embedder()
                if emb is not None:
                    with self._lock:
                        if not self._img_vec_ready:
                            self.repo.db.ensure_image_vector_table(emb.dim, emb.model_id)
                            self._img_vec_ready = True
                    self.repo.write_item_vector(item_id, info.embedding)
                self.repo.set_stage(item_id, "embed", "done", model_id="clip-vit-b32")
            else:
                self.repo.set_stage(item_id, "embed", "skipped", error="图像模型不可用")

            self.repo.index_item_text(item_id)
            self.repo.set_stage(item_id, "index", "done")
            # OCR 阶段先标 pending，等后台补跑
            self.repo.set_stage(item_id, "ocr", "pending")
            self.repo.set_item_status(item_id, "ready")
            return "done"

        except Exception:  # noqa: BLE001
            log.exception("处理图片 %s 时出错", path)
            return "failed"

    def run_deferred_ocr(self, limit: int = 200, on_progress: ProgressCb | None = None) -> int:
        """
        后台补跑 OCR。返回处理了几张。

        单线程跑：实测多线程只快 1.38 倍（Python GIL 卡着），
        多开线程只会把 CPU 抢光让界面和检索变卡，得不偿失。
        宁可慢慢跑 —— 反正是后台，图片先按别的维度能搜到了。
        """
        pending = self.repo.pending_ocr_items(limit)
        if not pending:
            return 0

        ocr = OcrEngine()
        if not ocr.available:
            for item_id, _ in pending:
                self.repo.set_stage(item_id, "ocr", "skipped", error="OCR 引擎不可用")
            return 0

        done = 0
        for item_id, locator in pending:
            p = Path(locator)
            if not p.exists():
                self.repo.set_stage(item_id, "ocr", "skipped", error="文件已不存在")
                continue
            try:
                self.repo.set_stage(item_id, "ocr", "running")
                info = analyze_image(p, ocr=ocr, embedder=None)
                text = info.ocr_text.strip()
                if text:
                    rows = [
                        ChunkRow(
                            text=l.text,
                            channel="ocr",
                            index=i,
                            bbox_json=json.dumps(l.bbox),
                            token_count=len(l.text),
                        )
                        for i, l in enumerate(info.ocr_lines)
                    ]
                    self.repo.write_chunks(item_id, rows, None)
                    # OCR 出来的文字并进摘要，列表里就能看到图里写了什么
                    cur = self.repo.get_item(item_id)
                    base = str(cur["snippet"] or "") if cur else ""
                    self.repo.update_item_fields(
                        item_id, snippet=(base + " · " + text[:160].replace("\n", " ")).strip(" ·")
                    )
                    self.repo.index_item_text(item_id)
                self.repo.set_stage(item_id, "ocr", "done")
                done += 1
                if on_progress and done % 5 == 0:
                    on_progress({"stage": "ocr", "done": done, "total": len(pending)})
            except Exception as e:  # noqa: BLE001
                self.repo.set_stage(item_id, "ocr", "failed", error=str(e))

        log.info("后台 OCR 补跑完成 %d 张", done)
        return done

    def run_deferred_description(
        self, limit: int, provider: str, api_key: str, base_url: str, model: str,
    ) -> int:
        """
        C4：后台补跑图片详细描述。调用方（`runtime.py` 的
        `deferred_jobs_loop`）已经检查过 `enable_image_description` 和
        `cloud.vision_configured` 都为真才会调这个方法，这里不重复判断，
        但 `model` 传空字符串这种"配置本身不完整"的情况还是要挡一道，
        不能假设调用方永远传对——这是个 public 方法，直接调用它的人
        不一定经过 runtime.py 那层检查。

        用 `asyncio.run()` 桥接：`cloud/adapters.py` 是给 FastAPI 路由写的
        纯 async 接口，而这一整个流水线模块是同步的（后台线程池跑），
        这个方法本身又是在 `asyncio.to_thread` 里被调用（见 runtime.py），
        所以在这个独立线程里开一个新的事件循环跑几次 HTTP 请求是安全的——
        不会和引擎主线程那个事件循环打架。
        """
        import asyncio

        if not model or provider == "none" or not api_key:
            return 0

        pending = self.repo.pending_description_items(limit)
        if not pending:
            return 0

        from ..cloud.adapters import CloudAdapterError, build_adapter
        from ..cloud.describe import describe_image

        adapter = build_adapter(provider, api_key=api_key, base_url=base_url or None)

        async def one(path: Path) -> str:
            return await describe_image(path, adapter=adapter, model=model)

        done = 0
        for item_id, locator in pending:
            p = Path(locator)
            if not p.exists():
                self.repo.set_stage(item_id, "description", "skipped", error="文件已不存在")
                continue
            try:
                self.repo.set_stage(item_id, "description", "running")
                text = asyncio.run(one(p))
                self.repo.write_chunks(
                    item_id,
                    [ChunkRow(text=text, channel="description", index=0, token_count=len(text))],
                    None,
                )
                cur = self.repo.get_item(item_id)
                base = str(cur["snippet"] or "") if cur else ""
                # 描述并进摘要，和 OCR 文字一个待遇——列表里直接能看到
                self.repo.update_item_fields(item_id, snippet=f"{base} · {text}".strip(" ·"))
                self.repo.index_item_text(item_id)
                self.repo.set_stage(item_id, "description", "done")
                done += 1
            except CloudAdapterError as e:
                # 标 failed（不是 skipped）只是为了在界面上如实区分"没做"和"做了但没成"，
                # 不代表会被自动重试——`pending_description_items` 的判据是
                # "从没跑过"，failed 之后就不会再被这条队列捡起来，
                # 和现有 OCR 补跑（`pending_ocr_items`）的重试行为是一致的，
                # 不是这里刻意放松的
                self.repo.set_stage(item_id, "description", "failed", error=str(e))
            except Exception as e:  # noqa: BLE001
                self.repo.set_stage(item_id, "description", "failed", error=str(e))

        if done:
            log.info("后台图片描述补跑完成 %d 张", done)
        return done

    def run_deferred_faces(self, limit: int = 100, on_progress: ProgressCb | None = None) -> int:
        """C5：后台补跑人脸检测与聚类。单线程跑——和 OCR 同理，检测本身是 CPU 密集的
        本地推理，多开线程只会跟主摄取流水线抢核，得不偿失。"""
        pending = self.repo.pending_face_items(limit)
        if not pending:
            return 0

        from ..analyze.face import FaceAnalyzer, bgr_from_pil

        analyzer = FaceAnalyzer(self.model_dir)
        if not analyzer.available():
            for item_id, _ in pending:
                self.repo.set_stage(item_id, "faces", "skipped", error="人脸模型未安装")
            return 0

        done = 0
        for item_id, locator in pending:
            p = Path(locator)
            if not p.exists():
                self.repo.set_stage(item_id, "faces", "skipped", error="文件已不存在")
                continue
            try:
                self.repo.set_stage(item_id, "faces", "running")
                from PIL import Image

                with Image.open(p) as img:
                    img_bgr = bgr_from_pil(img)
                detected = analyzer.analyze(img_bgr)
                if detected:
                    self.repo.write_faces(
                        item_id,
                        [(f.bbox, f.det_score, f.embedding) for f in detected],
                    )
                self.repo.set_stage(item_id, "faces", "done")
                done += 1
                if on_progress and done % 10 == 0:
                    on_progress({"stage": "faces", "done": done, "total": len(pending)})
            except Exception as e:  # noqa: BLE001
                self.repo.set_stage(item_id, "faces", "failed", error=str(e))

        if done:
            log.info("后台人脸聚类补跑完成 %d 张", done)
        return done

    # ── 网页 C11 ────────────────────────────────────────────

    def ingest_url(self, url: str, *, tags: list[str] | None = None) -> str:
        """
        抓一个网页、存档、索引。

        存档是关键：原网页删了、改了、要登录了，你这儿还有当时那一份。
        技术博客一年后 404 是常态 —— 收藏夹变成一堆死链是最让人恼火的情况之一。
        """
        try:
            fp = url_fingerprint(url)
            existing = self.repo.find_by_fingerprint(fp)
            if existing is not None:
                settled = self.repo.get_settled_stages(str(existing["id"]))
                if {"extract", "index"} <= settled:
                    return "skipped"

            archive_dir = self.repo.db.path.parent / "archive"
            page = fetch(url, archive_dir=archive_dir)

            if not page.text.strip():
                why = "；".join(page.warnings) or "这个页面没有可提取的正文"
                item_id, _ = self.repo.upsert_item(
                    fingerprint=fp, modality="link", source="link",
                    title=page.title or url, locator=url, status="failed", tags=tags,
                )
                self.repo.set_stage(item_id, "extract", "failed", error=why)
                self.repo.set_item_status(item_id, "failed", why)
                return "failed"

            item_id, _ = self.repo.upsert_item(
                fingerprint=fp,
                modality="link",
                source="link",
                title=page.title or page.domain or url,
                locator=page.final_url or url,
                mime="text/html",
                size_bytes=len(page.text),
                content_time=page.published,
                status="analyzing",
                tags=tags,
            )
            self.repo.set_stage(item_id, "extract", "done")

            enrichment = None
            self.repo.set_stage(item_id, "enrich", "running")
            try:
                enrichment = enrich(page.text[:120_000])
                self.repo.set_stage(item_id, "enrich", "done")
            except Exception as e:  # noqa: BLE001
                self.repo.set_stage(item_id, "enrich", "failed", error=str(e))

            meta = {
                "kind": "link",
                "url": page.final_url or url,
                "domain": page.domain,
                "site": page.site,
                "author": page.author,
                "fetchedAt": datetime.now(UTC).isoformat(timespec="seconds"),
                "archivePath": page.archive_path,
                "httpStatus": page.status,
                "isDead": page.status >= 400,
                "warnings": page.warnings or None,
            }
            self.repo.update_item_fields(
                item_id,
                snippet=(enrichment.summary if enrichment and enrichment.summary
                         else page.text[:300].replace("\n", " ").strip()),
                meta_json=json.dumps(meta, ensure_ascii=False),
            )
            if enrichment:
                if enrichment.keywords:
                    self.repo._attach_tags(item_id, enrichment.keywords[:8])
                if enrichment.entities:
                    try:
                        self.repo.write_entities(
                            item_id, [(e.kind, e.name, e.count) for e in enrichment.entities]
                        )
                    except Exception:  # noqa: BLE001
                        pass

            segs = [TextSegment(text=page.title, channel="title")] if page.title else []
            segs.append(TextSegment(text=page.text))
            chunks = chunk_segments(segs)

            vectors = None
            model_id = ""
            emb = self._embedder()
            if emb is not None and chunks:
                self.repo.set_stage(item_id, "embed", "running")
                try:
                    vectors = emb.encode([c.text for c in chunks])
                    model_id = emb.model_id
                    self.repo.set_stage(item_id, "embed", "done", model_id=model_id)
                except Exception as e:  # noqa: BLE001
                    self.repo.set_stage(item_id, "embed", "failed", error=str(e))

            rows = [
                ChunkRow(text=c.text, channel=c.channel, index=c.index,
                         token_count=c.token_estimate)
                for c in chunks
            ]
            self.repo.write_chunks(item_id, rows, vectors, model_id=model_id)
            self.repo.index_item_text(item_id)
            self.repo.set_stage(item_id, "index", "done")
            self.repo.set_item_status(
                item_id,
                "ready" if vectors is not None else "partial",
                None if vectors is not None else "只建了关键词索引",
            )
            return "done"

        except Exception:  # noqa: BLE001
            log.exception("抓取 %s 时出错", url)
            return "failed"

    # ── 视频 / 音频 ─────────────────────────────────────────

    def ingest_media(
        self, path: Path, *, source: str = "file", tags: list[str] | None = None
    ) -> str:
        """
        视频/音频的快速通道：场景切分 + 关键帧 + 关键帧向量。

        **语音转写不在这里跑**，理由和 OCR 一样：
        实测完整分析（含转写）是 5.97 倍速，而只做场景+关键帧是 **88.6 倍速**。
        一个小时的视频，快速通道 40 秒就能让你按画面搜到，
        转写让它在后台慢慢补（约 6.7 倍速），台词随后可搜。
        """
        try:
            stat = path.stat()
            fp = file_fingerprint(path)
            existing = self.repo.find_by_fingerprint(fp)
            if existing is not None:
                settled = self.repo.get_settled_stages(str(existing["id"]))
                if {"extract", "index"} <= settled:
                    return "skipped"

            modality = "video" if is_video(path) else "audio"
            item_id, _ = self.repo.upsert_item(
                fingerprint=fp,
                modality=modality,
                source=source,
                title=path.stem,
                locator=str(path),
                mime=mimetypes.guess_type(path.name)[0],
                size_bytes=stat.st_size,
                content_time=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(
                    timespec="seconds"
                ),
                status="analyzing",
                tags=tags,
            )

            self.repo.set_stage(item_id, "extract", "running")
            thumb_dir = self.repo.db.path.parent / "thumbs" / "video"
            info = analyze_video(
                path,
                thumb_dir=thumb_dir,
                item_id=item_id,
                transcriber=None,          # 转写延后
                want_scenes=(modality == "video"),
            )
            if info.duration_sec <= 0:
                err = "；".join(info.warnings) or "读不出这个媒体文件"
                self.repo.set_stage(item_id, "extract", "failed", error=err)
                self.repo.set_item_status(item_id, "failed", err)
                return "failed"
            self.repo.set_stage(item_id, "extract", "done")

            # A7：音频和视频**必须挂不同的 kind**。以前这里写死 "video"，
            # 结果一首 mp3 在界面上会被当成视频渲染 —— 去要它的场景缩略图
            # （音频根本没有），拿到空数组后画出一条空白的缩略条。
            # 不报错、不崩溃，就是一块永远空着的区域，正是静默失败的形状
            meta = {
                "kind": modality,
                "durationSec": round(info.duration_sec, 2),
                "width": info.width,
                "height": info.height,
                "fps": info.fps,
                "codec": info.codec,
                "hasAudio": info.has_audio,
                "hasTranscript": False,
                "sceneCount": len(info.scenes),
            }
            mins, secs = divmod(int(info.duration_sec), 60)
            bits = [f"{mins}:{secs:02d}"]
            if info.width and modality == "video":
                bits.append(f"{info.width}×{info.height}")
            if info.scenes:
                bits.append(f"{len(info.scenes)} 个片段")
            elif modality == "audio":
                # 音频没有场景，给一句它自己的描述，否则摘要只剩一个时长
                bits.append("音频，台词转写后可按句搜")
            self.repo.update_item_fields(
                item_id,
                snippet=" · ".join(bits),
                meta_json=json.dumps(meta, ensure_ascii=False),
            )

            if info.scenes:
                self.repo.write_scenes(
                    item_id,
                    [
                        (s.index, s.start_sec, s.end_sec, s.keyframe_path, s.transcript)
                        for s in info.scenes
                    ],
                )
                self._embed_keyframes(item_id, info.scenes, thumb_dir)

            self.repo.index_item_text(item_id)
            self.repo.set_stage(item_id, "index", "done")
            if info.has_audio:
                self.repo.set_stage(item_id, "transcribe", "pending")
            else:
                self.repo.set_stage(item_id, "transcribe", "skipped", error="没有音轨")
            self.repo.set_item_status(item_id, "ready")
            return "done"

        except Exception:  # noqa: BLE001
            log.exception("处理媒体 %s 时出错", path)
            return "failed"

    def _embed_keyframes(self, item_id: str, scenes: list[Any], thumb_dir: Path) -> None:
        """
        给每个关键帧算 CLIP 向量 —— 「用一张图找视频里的相似镜头」靠它。
        向量存在场景级，所以命中之后能直接给出"第几分几秒"。
        """
        emb = self._image_embedder()
        if emb is None:
            return
        from PIL import Image as _Image

        pairs: list[tuple[int, Any]] = []
        for s in scenes:
            if not s.keyframe_path:
                continue
            p = thumb_dir / s.keyframe_path
            if not p.exists():
                continue
            try:
                pairs.append((s.index, _Image.open(p).convert("RGB")))
            except Exception:  # noqa: BLE001
                continue
        if not pairs:
            return

        try:
            with self._lock:
                if not self._img_vec_ready:
                    self.repo.db.ensure_image_vector_table(emb.dim, emb.model_id)
                    self._img_vec_ready = True
            vecs = emb.encode([im for _, im in pairs])
            self.repo.write_scene_vectors(item_id, [(i, v) for (i, _), v in zip(pairs, vecs)])
        except Exception as e:  # noqa: BLE001
            log.debug("关键帧向量化失败：%s", e)

    def run_deferred_transcribe(
        self, limit: int = 20, on_progress: ProgressCb | None = None
    ) -> int:
        """
        后台补跑语音转写。返回处理了几个。

        limit 默认只有 20：一个小时的视频要转 9 分钟，
        一次拉太多会让这个后台任务几个小时不结束，进度也没法反馈。
        """
        pending = self.repo.pending_transcribe_items(limit)
        if not pending:
            return 0

        tr = Transcriber(self.model_dir / "sense-voice", self.model_dir / "vad")
        if not tr.available():
            for item_id, _ in pending:
                self.repo.set_stage(item_id, "transcribe", "skipped", error="语音模型未安装")
            return 0

        done = 0
        for item_id, locator in pending:
            p = Path(locator)
            if not p.exists():
                self.repo.set_stage(item_id, "transcribe", "skipped", error="文件已不存在")
                continue
            try:
                self.repo.set_stage(item_id, "transcribe", "running")
                thumb_dir = self.repo.db.path.parent / "thumbs" / "video"
                info = analyze_video(
                    p, thumb_dir=thumb_dir, item_id=item_id,
                    transcriber=tr, want_scenes=False,
                )
                if info.transcript:
                    rows = [
                        ChunkRow(
                            text=u.text,
                            channel="transcript",
                            index=i,
                            start_sec=u.start_sec,
                            end_sec=u.end_sec,
                            token_count=len(u.text),
                        )
                        for i, u in enumerate(info.transcript)
                    ]
                    self.repo.write_chunks(item_id, rows, None)
                    self.repo.attach_transcript_to_scenes(
                        item_id, [(u.start_sec, u.end_sec, u.text) for u in info.transcript]
                    )
                    head = " ".join(u.text for u in info.transcript)[:200]
                    cur = self.repo.get_item(item_id)
                    base = str(cur["snippet"] or "") if cur else ""
                    self.repo.update_item_fields(item_id, snippet=f"{base} · {head}".strip(" ·"))
                    self.repo.index_item_text(item_id)
                self.repo.set_stage(item_id, "transcribe", "done")
                done += 1
                if on_progress:
                    on_progress({"stage": "transcribe", "done": done, "total": len(pending)})
            except Exception as e:  # noqa: BLE001
                self.repo.set_stage(item_id, "transcribe", "failed", error=str(e))

        log.info("后台转写完成 %d 个", done)
        return done

    # ── 批量 ────────────────────────────────────────────────

    def ingest_paths(
        self,
        targets: list[Path | str],
        *,
        recursive: bool = True,
        source: str = "file",
        tags: list[str] | None = None,
        control: JobControl | None = None,
        on_item: Callable[[str, str, str], None] | None = None,
        on_total: Callable[[int], None] | None = None,
    ) -> IngestStats:
        """
        混合投喂：路径和 URL 都能进来。

        `control` 给 F2 驾驶舱用：暂停 / 取消。不传就是原来的行为。
        `on_item(path, status, detail)` 每处理完一个就回调一次 ——
        🔴 **失败清单是驾驶舱存在的主要理由**：一万个文件失败 37 个，
        不逐条报出来的话进度条走到 100% 看起来就像全成功了。

        `on_total(n)` 在**展开目录之后立刻**回调一次。
        🔴 少了它，调用方要等这个函数**返回**才知道总共几个文件 ——
        而那已经是任务结束的时候了。中间几分钟进度条一直是 `0 / 0`。

        ⚠️ URL **必须以 str 传，不能包成 Path**。
           `Path("https://example.com/a")` 在 Windows 上会变成
           `WindowsPath('https:/example.com/a')` —— 双斜杠被折叠成一个，
           再转回字符串就已经不是合法 URL 了。所以这里的类型是 `Path | str`。
        """
        urls: list[str] = []
        files: list[Path] = []
        for t in targets:
            if isinstance(t, str) and is_url(t):
                urls.append(t)
                continue
            p = t if isinstance(t, Path) else Path(t)
            if p.is_dir():
                files.extend(iter_supported(p, recursive))
            elif p.is_file() and (can_parse(p) or p.suffix.lower() in _MEDIA_EXT):
                files.append(p)

        stats = IngestStats(total=len(files) + len(urls))
        # 展开完目录立刻报总数。**放在 `if not files` 之前** ——
        # 一个文件都没有时也要报 0，否则调用方那边会一直挂着上一次的总数
        if on_total is not None:
            on_total(stats.total)
        if not files and not urls:
            return stats

        # 网页串行抓：并发抓会对同一个站点形成一小波请求，
        # 容易被限流甚至封 IP。抓取是 IO 等待为主，串行也不慢。
        for u in urls:
            if control is not None:
                control.wait_if_paused()
                if control.cancelled:
                    stats.cancelled = True
                    return stats
            r = self.ingest_url(u, tags=tags)
            if r == "done":
                stats.done += 1
            elif r == "skipped":
                stats.skipped += 1
            else:
                stats.failed += 1
                stats.errors.append((u, r))
            if on_item is not None:
                on_item(u, r if r in ("done", "skipped") else "failed", "" if r in ("done", "skipped") else r)
        if not files:
            return stats

        log.info("开始摄取 %d 个文件，并发 %d", len(files), self.concurrency)
        lock = threading.Lock()
        last_report = [0.0]

        def work(p: Path) -> None:
            # 🔴 检查放在**开工之前**，不是做完之后。放后面的话点了暂停，
            # 线程池里在跑的那 N 个还会各自再抓一个新文件下来做完才停 ——
            # 表现是"点了暂停，进度条又往前跳了一截"，用户会以为按钮坏了
            if control is not None:
                control.wait_if_paused()
                if control.cancelled:
                    with lock:
                        stats.cancelled = True
                    return
            r = self.ingest_file(p, source=source, tags=tags)
            if on_item is not None:
                on_item(
                    str(p),
                    r if r in ("done", "skipped") else "failed",
                    "" if r in ("done", "skipped") else r,
                )
            with lock:
                if r == "done":
                    stats.done += 1
                elif r == "skipped":
                    stats.skipped += 1
                else:
                    stats.failed += 1
                    stats.errors.append((str(p), r))

                now = time.perf_counter()
                if self.on_progress and now - last_report[0] > 0.5:
                    last_report[0] = now
                    self.on_progress(
                        {
                            "total": stats.total,
                            "done": stats.done,
                            "failed": stats.failed,
                            "skipped": stats.skipped,
                            "itemsPerSec": round(stats.items_per_sec, 2),
                            "elapsedSec": round(stats.elapsed, 1),
                        }
                    )

        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            list(ex.map(work, files))

        st = self.repo.stats()
        stats.chunks = st["chunks"]
        log.info(
            "摄取完成：成功 %d / 跳过 %d / 失败 %d，耗时 %.1fs（%.1f 个/秒）",
            stats.done, stats.skipped, stats.failed, stats.elapsed, stats.items_per_sec,
        )
        return stats


def _media_ext() -> set[str]:
    from ..analyze.image import SUPPORTED_IMAGE_EXT
    from ..analyze.video import SUPPORTED_AUDIO_EXT, SUPPORTED_VIDEO_EXT

    return SUPPORTED_IMAGE_EXT | SUPPORTED_VIDEO_EXT | SUPPORTED_AUDIO_EXT


_MEDIA_EXT = _media_ext()


def _physical_cores() -> int:
    """物理核数。超线程的逻辑核对矩阵运算没有帮助，反而互相抢 SIMD 单元。"""
    try:
        import psutil

        n = psutil.cpu_count(logical=False)
        if n:
            return int(n)
    except Exception:  # noqa: BLE001
        pass
    import os

    return max(1, (os.cpu_count() or 4) // 2)


def _meta_json(doc: Any, enrichment: Any = None) -> str:
    import json

    meta: dict[str, Any] = {
        "kind": "document",
        "pageCount": doc.page_count,
        "chunkCount": len(doc.segments),
        "author": doc.author,
        "warnings": doc.warnings,
    }
    if enrichment is not None:
        meta["keywords"] = enrichment.keywords[:12]
        meta["language"] = enrichment.language
        meta["entityCount"] = len(enrichment.entities)
    return json.dumps(meta, ensure_ascii=False)
