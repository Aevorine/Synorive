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
from ..store.repository import ChunkRow, Repository
from .chunker import chunk_segments
from .parsers import ParseError, can_parse, iter_supported, parse

log = logging.getLogger("synorive.ingest")

#: 算指纹时最多读多少字节。大文件全读一遍太慢，
#: 取「头 1MB + 尾 1MB + 文件大小」已经足够区分。
FINGERPRINT_SAMPLE = 1 << 20


@dataclass
class IngestStats:
    total: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    chunks: int = 0
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
                self._vec_ready = True
        return emb

    # ── 单个文件 ────────────────────────────────────────────

    def ingest_file(self, path: Path, *, source: str = "file", tags: list[str] | None = None) -> str:
        """
        处理一个文件。返回 'done' / 'skipped' / 'failed'。
        异常在这里就地消化 —— 一个坏文件不该拖垮整批。
        """
        try:
            if not path.is_file():
                return "skipped"
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

            snippet = doc.full_text[:400].replace("\n", " ").strip()
            self.repo.update_item_fields(
                item_id,
                title=doc.title or path.stem,
                snippet=snippet,
                meta_json=_meta_json(doc),
            )

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

    # ── 批量 ────────────────────────────────────────────────

    def ingest_paths(
        self,
        targets: list[Path],
        *,
        recursive: bool = True,
        source: str = "file",
        tags: list[str] | None = None,
    ) -> IngestStats:
        files: list[Path] = []
        for t in targets:
            if t.is_dir():
                files.extend(iter_supported(t, recursive))
            elif t.is_file() and can_parse(t):
                files.append(t)

        stats = IngestStats(total=len(files))
        if not files:
            return stats

        log.info("开始摄取 %d 个文件，并发 %d", len(files), self.concurrency)
        lock = threading.Lock()
        last_report = [0.0]

        def work(p: Path) -> None:
            r = self.ingest_file(p, source=source, tags=tags)
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


def _meta_json(doc: Any) -> str:
    import json

    return json.dumps(
        {
            "kind": "document",
            "pageCount": doc.page_count,
            "chunkCount": len(doc.segments),
            "author": doc.author,
            "warnings": doc.warnings,
        },
        ensure_ascii=False,
    )
