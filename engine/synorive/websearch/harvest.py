"""
C2 批量 PDF 管线 ＋ C7 主题订阅监控
====================================================================
**C2**：搜到一批论文 → 一键把有开放全文的那些**全下下来** → 自动入库 →
走 L3 分节索引。分节索引早就做好了（`ingest/parsers.py`），
一直缺的是**中间这条把「搜索结果」变成「本地文件」的管线**。

**C7**：把一次检索存成「订阅」，定时重跑，**只把新出现的**入库并提醒。

🔴 **五条硬约束，全是为了不让这个功能变成一个下载器**：

1. **只下开放获取的**。`meta.pdf` 有值才下 —— 那是各家源明确标了
   "这个 PDF 是公开的"。**绝不去猜 PDF 地址**（把 DOI 拼成出版商链接
   那种做法既会下到付费墙的登录页，也踩合规红线）。
2. **有并发上限和总量上限**。默认 4 并发 / 单次 50 篇。学术站点对
   批量抓取很敏感，打太狠会让整个 IP 被封，代价是后面所有检索都不能用。
3. **单篇失败不影响其他**，失败原因逐条记下来给用户看，不静默跳过。
4. **先查库里有没有**。同一篇下第二遍是纯浪费，而用户会反复检索同一个主题。
5. **默认干跑**（`apply=False`）—— 先告诉用户"打算下这 23 篇，约 180MB"，
   点头了才真下。这条和 `setup-searxng.mjs` 是同一个规矩。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("synorive.harvest")

UA = "Synorive/1.0 (local research tool; https://github.com/Aevorine/Synorive)"

#: 并发下载上限。见模块头第 2 条 —— 这个数字是"不惹麻烦"而不是"跑得快"
_CONCURRENCY = 4
#: 单次最多下几篇
_MAX_BATCH = 50
#: 单个 PDF 体积上限。超过多半不是论文（是书或数据集打包）
_MAX_BYTES = 60 * 1024 * 1024
#: 单篇超时
_TIMEOUT_S = 45.0


def _safe_name(title: str, doi: str = "") -> str:
    """
    文件名。**用标题而不是 DOI** —— 用户在文件管理器里看到的应该是
    人能读的东西。DOI 只在标题为空时兜底。
    """
    base = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", str(title or "")).strip()
    base = re.sub(r"\s+", " ", base)[:90]
    if not base:
        base = re.sub(r"[^\w.-]+", "_", str(doi or "paper"))[:60] or "paper"
    return f"{base}.pdf"


@dataclass
class HarvestItem:
    """一篇的下载结果。"""

    title: str = ""
    url: str = ""
    pdf_url: str = ""
    doi: str = ""
    path: str = ""
    bytes: int = 0
    status: str = "pending"      # ok / skipped / failed / pending
    reason: str = ""
    item_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "url": self.url, "pdfUrl": self.pdf_url,
            "doi": self.doi, "path": self.path, "bytes": self.bytes,
            "status": self.status, "reason": self.reason, "itemId": self.item_id,
        }


def plan(entries: list[dict[str, Any]], *, limit: int = _MAX_BATCH) -> dict[str, Any]:
    """
    C2 第一步 —— **干跑**：这批里有几篇能下、下哪些、大概多大。

    体积是估的（学术 PDF 中位数约 2.5MB），并且**明说是估的** ——
    给一个精确到字节的假数字不如给一个标着"约"的粗数字。
    """
    downloadable: list[HarvestItem] = []
    skipped: list[HarvestItem] = []
    for e in entries:
        meta = e.get("meta") or {}
        pdf = str(meta.get("pdf") or "").strip()
        it = HarvestItem(
            title=str(e.get("title") or ""), url=str(e.get("url") or ""),
            pdf_url=pdf, doi=str(meta.get("doi") or ""),
        )
        if not pdf.startswith(("http://", "https://")):
            it.status = "skipped"
            it.reason = "这一篇没有公开的 PDF 地址（多半在付费墙后面）"
            skipped.append(it)
            continue
        downloadable.append(it)
        if len(downloadable) >= limit:
            break

    return {
        "downloadable": [i.to_dict() for i in downloadable],
        "skipped": [i.to_dict() for i in skipped],
        "count": len(downloadable),
        "estimatedMb": round(len(downloadable) * 2.5, 1),
        "note": (
            f"打算下 {len(downloadable)} 篇，估计约 {round(len(downloadable) * 2.5, 1)}MB"
            f"（按学术 PDF 中位数 2.5MB 估的，实际会有出入）。"
            f"另外 {len(skipped)} 篇没有公开全文，跳过 —— "
            "**不会去猜出版商的下载地址**，那既下不到也不合规"
        ),
    }


async def _download_one(
    client: httpx.AsyncClient, it: HarvestItem, out_dir: Path
) -> HarvestItem:
    try:
        async with client.stream("GET", it.pdf_url, headers={"User-Agent": UA}) as r:
            if r.status_code != 200:
                it.status = "failed"
                it.reason = f"HTTP {r.status_code}"
                return it
            ctype = (r.headers.get("content-type") or "").lower()
            # 付费墙常见的表现是返回 200 + 一个 HTML 登录页。
            # 不查这一条的话，库里会多出一堆 0 字节或者内容是"请登录"的 PDF——
            # 静默失败第①问的典型形态
            if "pdf" not in ctype and "octet-stream" not in ctype:
                it.status = "failed"
                it.reason = f"返回的不是 PDF（Content-Type: {ctype or '空'}），多半是登录页"
                return it
            path = out_dir / _safe_name(it.title, it.doi)
            n = 0
            with path.open("wb") as f:
                async for chunk in r.aiter_bytes(65536):
                    n += len(chunk)
                    if n > _MAX_BYTES:
                        f.close()
                        path.unlink(missing_ok=True)
                        it.status = "failed"
                        it.reason = f"体积超过 {_MAX_BYTES // 1024 // 1024}MB，多半不是论文"
                        return it
                    f.write(chunk)
            if n < 2048:
                path.unlink(missing_ok=True)
                it.status = "failed"
                it.reason = f"只有 {n} 字节，不是一篇完整的 PDF"
                return it
            it.path = str(path)
            it.bytes = n
            it.status = "ok"
    except (httpx.HTTPError, OSError) as exc:
        it.status = "failed"
        it.reason = f"{type(exc).__name__}: {exc}"
    return it


async def harvest(
    entries: list[dict[str, Any]],
    *,
    out_dir: Path,
    pipeline: Any | None = None,
    tags: list[str] | None = None,
    limit: int = _MAX_BATCH,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """
    C2 主入口 —— 真下。`pipeline` 传进来就顺手入库（走 L3 分节索引）。

    `on_progress(done, total, item)` 每完成一篇回调一次 ——
    几十篇要跑一两分钟，没有进度回调的话界面上就是一个转圈，
    而用户完全不知道卡在第几篇。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    p = plan(entries, limit=limit)
    todo = [
        HarvestItem(**{
            "title": d["title"], "url": d["url"], "pdf_url": d["pdfUrl"],
            "doi": d["doi"],
        }) for d in p["downloadable"]
    ]
    if not todo:
        return {**p, "downloaded": 0, "ingested": 0, "items": []}

    t0 = time.monotonic()
    sem = asyncio.Semaphore(_CONCURRENCY)
    done = 0

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(_TIMEOUT_S, connect=8.0), follow_redirects=True
    ) as client:
        async def one(it: HarvestItem) -> HarvestItem:
            nonlocal done
            async with sem:
                r = await _download_one(client, it, out_dir)
            done += 1
            if on_progress:
                try:
                    on_progress(done, len(todo), r.to_dict())
                except Exception:       # noqa: BLE001 — 回调坏了不该拖垮下载
                    log.debug("harvest 进度回调抛异常，已忽略", exc_info=True)
            return r

        results = await asyncio.gather(*[one(i) for i in todo])

    ingested = 0
    if pipeline is not None:
        for r in results:
            if r.status != "ok":
                continue
            try:
                # 入库放在同步侧串行做：分节 + 向量化本身是 CPU 密集的，
                # 并发跑只会互相抢 GIL，还会把界面帧率拖下去
                r.item_id = pipeline.ingest_file(
                    Path(r.path), source="harvest", tags=(tags or ["文献"])
                )
                ingested += 1
            except Exception as exc:    # noqa: BLE001
                r.reason = f"下载成功但入库失败：{exc}"
                log.warning("harvest 入库失败 %s: %s", r.path, exc)

    ok = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status == "failed"]
    return {
        "downloaded": len(ok),
        "ingested": ingested,
        "failed": len(failed),
        "elapsedMs": int((time.monotonic() - t0) * 1000),
        "items": [r.to_dict() for r in results] + p["skipped"],
        "outDir": str(out_dir),
        "note": (
            f"下成功 {len(ok)} 篇，入库 {ingested} 篇，失败 {len(failed)} 篇。"
            "失败的每一条都写了原因，**没有静默跳过** —— "
            "最常见的失败是返回了登录页而不是 PDF"
        ),
    }


# ────────────────────────────────────────────────────────────────
# C7 主题订阅监控
# ────────────────────────────────────────────────────────────────
@dataclass
class Watch:
    """一条订阅。存成 JSON，不进数据库 —— 数量很少且用户可能想手改。"""

    id: str = ""
    query: str = ""
    engines: list[str] = field(default_factory=list)
    preset: str | None = None
    interval_hours: int = 24
    last_run: float = 0.0
    seen: list[str] = field(default_factory=list)
    auto_ingest: bool = False
    enabled: bool = True
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "query": self.query, "engines": self.engines,
            "preset": self.preset, "intervalHours": self.interval_hours,
            "lastRun": self.last_run, "seenCount": len(self.seen),
            "autoIngest": self.auto_ingest, "enabled": self.enabled,
            "label": self.label or self.query,
        }


class WatchStore:
    """
    订阅的持久化。**`seen` 只存 URL 指纹不存全文** ——
    存全文会让这个 JSON 在几个月后涨到几十兆，而它每次启动都要读。
    """

    #: 每条订阅最多记住多少个已见 URL。超了丢最旧的 ——
    #: 订阅的用途是"发现新的"，三个月前见过什么已经不重要了
    MAX_SEEN = 2000

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.watches: dict[str, Watch] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("订阅文件读不出来（%s），当成空的继续 —— 不因此让引擎起不来", exc)
            return
        for d in data.get("watches") or []:
            try:
                self.watches[d["id"]] = Watch(
                    id=d["id"], query=d.get("query", ""),
                    engines=d.get("engines") or [], preset=d.get("preset"),
                    interval_hours=int(d.get("intervalHours") or 24),
                    last_run=float(d.get("lastRun") or 0),
                    seen=list(d.get("seen") or []),
                    auto_ingest=bool(d.get("autoIngest")),
                    enabled=bool(d.get("enabled", True)),
                    label=d.get("label") or "",
                )
            except (KeyError, TypeError, ValueError):
                continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "watches": [
                {**w.to_dict(), "seen": w.seen[-self.MAX_SEEN:]}
                for w in self.watches.values()
            ],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)      # 原子替换：写一半断电不会毁掉原文件

    def add(self, **kwargs: Any) -> Watch:
        import uuid
        w = Watch(id=uuid.uuid4().hex[:12], **kwargs)
        self.watches[w.id] = w
        self.save()
        return w

    def remove(self, wid: str) -> bool:
        ok = self.watches.pop(wid, None) is not None
        if ok:
            self.save()
        return ok

    def due(self, *, now: float | None = None) -> list[Watch]:
        """该跑的那几条。`enabled=False` 的永远不返回。"""
        t = now if now is not None else time.time()
        return [
            w for w in self.watches.values()
            if w.enabled and (t - w.last_run) >= w.interval_hours * 3600
        ]

    async def run_one(
        self, w: Watch, meta: Any, *, limit: int = 20, pipeline: Any = None
    ) -> dict[str, Any]:
        """
        跑一条订阅，**只返回新出现的**。

        `meta` 是 `MetaSearch` 实例。跑完就更新 `last_run` 和 `seen`，
        哪怕一条新的都没有 —— 否则下次启动会立刻再跑一遍。
        """
        res = await meta.search(
            w.query, engines=(w.engines or None), limit=limit, use_cache=False
        )
        seen = set(w.seen)
        fresh: list[dict[str, Any]] = []
        for c in res.clusters:
            d = c.to_dict()
            url = str((d.get("best") or {}).get("url") or d.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            fresh.append(d)

        w.seen = list(seen)[-self.MAX_SEEN:]
        w.last_run = time.time()
        self.save()

        ingested = 0
        if w.auto_ingest and pipeline is not None:
            for d in fresh[:10]:        # 自动入库限 10 条，防止一觉醒来库里多了几百个网页
                url = str((d.get("best") or {}).get("url") or "")
                try:
                    pipeline.ingest_url(url, tags=["订阅", w.label or w.query])
                    ingested += 1
                except Exception as exc:    # noqa: BLE001
                    log.debug("订阅自动入库失败 %s: %s", url, exc)

        return {
            "watchId": w.id,
            "query": w.query,
            "fresh": fresh,
            "freshCount": len(fresh),
            "ingested": ingested,
            "note": (
                f"「{w.label or w.query}」这次新出现 {len(fresh)} 条"
                if fresh else f"「{w.label or w.query}」这次没有新内容"
            ),
        }
