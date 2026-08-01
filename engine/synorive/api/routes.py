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
