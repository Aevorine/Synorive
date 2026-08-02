"""
HTTP 接口（G4）—— 桌面端、安卓端、CLI、MCP 共用同一套
====================================================================
字段名和 packages/shared-types/src/index.ts 严格一致。
两边不一致的症状是"某个字段界面上永远是空的"，而且不报错。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger("synorive.api")

router = APIRouter()


# ── 请求模型 ────────────────────────────────────────────────


class SearchFilters(BaseModel):
    modalities: list[str] | None = None
    sources: list[str] | None = None
    tags: list[str] | None = None
    timeFrom: str | None = None
    timeTo: str | None = None
    sizeMinBytes: int | None = None
    sizeMaxBytes: int | None = None
    scopes: list[str] | None = None
    excludeScopes: list[str] | None = None


class RankingWeights(BaseModel):
    semantic: float = 1.0
    keyword: float = 1.0
    recency: float = 0.3
    sourceTrust: float = 0.2
    popularity: float = 0.2
    titleBoost: float = 0.5


class SearchRequest(BaseModel):
    query: str = ""
    byContentId: str | None = None
    filters: SearchFilters | None = None
    weights: RankingWeights | None = None
    preset: str | None = None
    limit: int = Field(default=30, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    rerank: bool = False
    explain: bool = False
    answer: bool = False
    #: keyword = 只跑快的那两路（首屏）；semantic = 全跑
    stage: str = "semantic"


class IngestRequest(BaseModel):
    targets: list[str]
    source: str = "file"
    recursive: bool = True
    priority: str = "normal"
    tags: list[str] | None = None
    allowCloud: bool = False


# ── 路由 ────────────────────────────────────────────────────


def _rt(request: Request) -> Any:
    return request.app.state.runtime


@router.post("/search")
async def search(req: SearchRequest, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    if rt.search is None:
        raise HTTPException(503, "检索引擎还没就绪")

    # 检索是 CPU 密集的同步代码，丢到线程池里跑，
    # 不能占住事件循环 —— 占住的话 WebSocket 心跳会断，界面以为引擎挂了
    return await asyncio.to_thread(
        rt.search.search,
        req.query,
        filters=req.filters.model_dump(exclude_none=True) if req.filters else None,
        weights=req.weights.model_dump() if req.weights else None,
        preset=req.preset,
        limit=req.limit,
        offset=req.offset,
        explain=req.explain,
        stage=req.stage,
    )


@router.post("/ingest")
async def ingest(req: IngestRequest, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    if rt.pipeline is None:
        raise HTTPException(503, "摄取流水线还没就绪")

    paths = [Path(t) for t in req.targets]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise HTTPException(400, f"这些路径不存在：{missing[:5]}")

    job_id = rt.start_ingest(paths, recursive=req.recursive, source=req.source, tags=req.tags)
    return {"jobId": job_id, "status": "running", "totalItems": 0}


@router.get("/items/{item_id}")
async def get_item(item_id: str, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    row = rt.repo.get_item(item_id)
    if row is None:
        raise HTTPException(404, "没有这条内容")
    from ..search.engine import _row_to_item

    return _row_to_item(row, rt.repo.item_tags(item_id))


@router.get("/items/{item_id}/content")
async def get_content(item_id: str, request: Request, maxChars: int = 20000) -> dict[str, Any]:
    """取正文全文，给 MCP 的 get_content 和界面的预览用。"""
    rt = _rt(request)
    row = rt.repo.get_item(item_id)
    if row is None:
        raise HTTPException(404, "没有这条内容")

    conn = rt.db.connect()
    rows = conn.execute(
        "SELECT text FROM chunks WHERE item_id = ? ORDER BY chunk_index LIMIT 2000", (item_id,)
    ).fetchall()
    text = "\n\n".join(str(r["text"]) for r in rows)[:maxChars]

    from ..search.engine import _row_to_item

    return {"text": text, "item": _row_to_item(row, rt.repo.item_tags(item_id))}


@router.post("/items/{item_id}/open")
async def record_open(item_id: str, request: Request) -> dict[str, Any]:
    """E11 热度学习：记一次打开。"""
    _rt(request).repo.record_open(item_id)
    return {"ok": True}


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, request: Request) -> dict[str, Any]:
    _rt(request).repo.delete_item(item_id)
    return {"ok": True}


@router.get("/stats")
async def stats(request: Request) -> dict[str, Any]:
    return _rt(request).repo.stats()


@router.get("/similar/{item_id}")
async def similar(item_id: str, request: Request, limit: int = 20) -> list[dict[str, Any]]:
    """E1 相似内容 / MCP 的 synorive_similar：用这条内容的正文去搜别的。"""
    rt = _rt(request)
    row = rt.repo.get_item(item_id)
    if row is None:
        raise HTTPException(404, "没有这条内容")

    conn = rt.db.connect()
    chunks = conn.execute(
        "SELECT text FROM chunks WHERE item_id = ? ORDER BY chunk_index LIMIT 3", (item_id,)
    ).fetchall()
    seed = " ".join(str(c["text"]) for c in chunks)[:1200] or str(row["title"] or "")
    if not seed.strip():
        return []

    r = await asyncio.to_thread(rt.search.search, seed, limit=limit + 1, stage="semantic")
    # 把自己剔掉 —— 用自己的正文去搜，自己必然排第一
    return [h for h in r["hits"] if h["item"]["id"] != item_id][:limit]


class ByImageRequest(BaseModel):
    """D3 以图搜图 / 以图搜镜头。二选一：给库里已有的 itemId，或给一个本机文件路径。"""

    itemId: str | None = None
    path: str | None = None
    limit: int = Field(default=30, ge=1, le=100)
    includeScenes: bool = True


@router.post("/search/by-image")
async def search_by_image(req: ByImageRequest, request: Request) -> dict[str, Any]:
    """
    用一张图搜：既搜库里的图片，也搜**视频里的镜头**。

    后者是这个功能最有意思的地方 —— 丢一张截图进来，
    它能告诉你"这个画面出现在某个视频的第 3 分 24 秒"。
    """
    rt = _rt(request)
    vec = await asyncio.to_thread(rt.image_vector_for, req.itemId, req.path)
    if vec is None:
        raise HTTPException(
            400,
            "拿不到这张图的向量。可能是：图像模型还没装（依赖医生里装 embed-image）、"
            "这条内容不是图片、或者路径不存在",
        )
    return await asyncio.to_thread(
        rt.search.search_by_image,
        vec,
        limit=req.limit,
        include_scenes=req.includeScenes,
        exclude_item=req.itemId or "",
    )


@router.get("/items/{item_id}/scenes")
async def item_scenes(item_id: str, request: Request) -> list[dict[str, Any]]:
    """视频的场景列表（含关键帧和台词）—— 界面上的时间轴条靠它。"""
    return _rt(request).repo.scenes_of(item_id)


@router.get("/items/{item_id}/duplicates")
async def item_duplicates(item_id: str, request: Request) -> list[dict[str, Any]]:
    """E9 近重复：找出和这张图几乎一样的其它图。"""
    import json as _json

    rt = _rt(request)
    row = rt.repo.get_item(item_id)
    if row is None:
        raise HTTPException(404, "没有这条内容")
    try:
        ph = _json.loads(str(row["meta_json"] or "{}")).get("phash")
    except _json.JSONDecodeError:
        ph = None
    if not ph:
        return []
    ids = rt.repo.find_near_duplicates(ph, exclude_item=item_id)
    from ..search.engine import _row_to_item

    rows = rt.repo.get_items(ids)
    return [
        _row_to_item(rows[i], rt.repo.item_tags(i)) for i in ids if i in rows
    ]


@router.get("/graph")
async def graph(
    request: Request, entityId: str | None = None, kind: str | None = None, limit: int = 60
) -> dict[str, Any]:
    """E6 知识图谱：实体节点 + 共现边。"""
    return _rt(request).repo.graph_slice(entity_id=entityId, kind=kind, limit=limit)


@router.get("/timeline")
async def timeline(request: Request, bucket: str = "day", limit: int = 400) -> list[dict[str, Any]]:
    """E5 语义时间轴：按时间桶统计。"""
    if bucket not in ("hour", "day", "week", "month", "year"):
        raise HTTPException(400, f"bucket 只能是 hour/day/week/month/year，收到 {bucket}")
    return _rt(request).repo.timeline(bucket=bucket, limit=limit)


# ── 依赖医生 ────────────────────────────────────────────────


@router.get("/doctor")
async def doctor_status(request: Request) -> list[dict[str, Any]]:
    return _rt(request).doctor.check_all()


@router.post("/doctor/{dep_id}/install")
async def doctor_install(dep_id: str, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    # 安装可能要几分钟，不能让 HTTP 请求挂着等 ——
    # 后台跑，进度通过 WebSocket 推
    asyncio.create_task(rt.install_dependency(dep_id))
    return {"ok": True, "started": dep_id}
