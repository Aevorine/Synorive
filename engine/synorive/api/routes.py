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

import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
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
        rerank=req.rerank,
        answer=req.answer,
    )


@router.post("/ingest")
async def ingest(req: IngestRequest, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    if rt.pipeline is None:
        raise HTTPException(503, "摄取流水线还没就绪")

    from ..ingest.web import is_url

    # URL 保持字符串，路径转 Path —— URL 包成 Path 在 Windows 上会被折叠双斜杠
    targets: list[Any] = []
    missing: list[str] = []
    for t in req.targets:
        if is_url(t):
            targets.append(t)
            continue
        p = Path(t)
        if p.exists():
            targets.append(p)
        else:
            missing.append(t)

    if missing:
        raise HTTPException(400, f"这些路径不存在（也不是合法链接）：{missing[:5]}")
    if not targets:
        raise HTTPException(400, "没有可处理的目标")

    job_id = rt.start_ingest(targets, recursive=req.recursive, source=req.source, tags=req.tags)
    return {"jobId": job_id, "status": "running", "totalItems": 0}


#: 单次上传上限（512MB，够放视频）——没有这道闸，一个恶意/失控的客户端
#: 能一直写到把磁盘塞满
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """
    A16 安卓端等远程客户端专用：接收上传的文件，落盘到本地再返回路径。

    `/ingest` 和 `/search/by-image` 都是按**本机路径**设计的——桌面端自己
    传路径从来没问题，但手机上的照片/视频对引擎所在的机器来说不是"本机路径"，
    根本没法直接引用。所以先把文件传上来落盘，拿到的 `path` 再喂给
    `/ingest`（`targets: [path]`）或 `/search/by-image`（`path`）。
    """
    rt = _rt(request)
    inbox = rt.config.data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "upload").suffix
    dest = inbox / f"{uuid.uuid4().hex}{suffix}"

    size = 0
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "文件太大（上限 512MB）")
                f.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    return {"path": str(dest), "sizeBytes": size, "filename": file.filename}


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


@router.get("/search/ann/status")
async def ann_status(request: Request) -> dict[str, Any]:
    """A17：ANN 索引现在是什么状态——装没装、建没建、接没接管查询。"""
    ann = _rt(request).repo.ann_index
    if ann is None:
        return {"available": False, "reason": "usearch 没装，或嵌入模型还没就绪过一次"}
    from ..search.ann_index import ANN_THRESHOLD

    return {
        "available": True, "size": ann.size, "active": ann.active,
        "threshold": ANN_THRESHOLD,
    }


@router.post("/search/ann/rebuild")
async def ann_rebuild(request: Request) -> dict[str, Any]:
    """
    手动触发全量重建。库已经很大但从没建过索引时用得上（首次用这个功能，
    或者磁盘上的索引文件丢了/损坏）——正常情况下这一步是自动的
    （见 `runtime.py` 的 `_load_ann_index`），这个接口是给用户一个
    "我知道库很大，现在就要"的手动开关，不用等后台自己发现。
    """
    rt = _rt(request)
    if rt.repo.ann_index is None:
        raise HTTPException(503, "ANN 功能不可用（usearch 没装，或嵌入模型还没就绪）")
    rt.rebuild_ann_async()
    return {"ok": True, "detail": "重建已在后台开始，规模大的话要几分钟，期间搜索仍然可用（走暴力扫描）"}


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


# ── C5 人脸聚类（默认关，隐私最敏感的一类）───────────────────


@router.get("/faces/clusters")
async def face_clusters(request: Request, limit: int = 200) -> list[dict[str, Any]]:
    """"人物"列表——按聚类里的照片数量倒序，从没被应用起过名字（label=None）。"""
    return _rt(request).repo.list_face_clusters(limit)


class LabelClusterRequest(BaseModel):
    label: str | None = Field(default=None, max_length=100)


@router.post("/faces/clusters/{cluster_id}/label")
async def label_face_cluster(cluster_id: str, req: LabelClusterRequest, request: Request) -> dict[str, Any]:
    """给一个人物命名。传 null/空字符串等于清空名字，回到"未命名"。"""
    ok = _rt(request).repo.label_face_cluster(cluster_id, req.label or None)
    if not ok:
        raise HTTPException(404, "没有这个人物聚类")
    return {"ok": True}


@router.get("/faces/clusters/{cluster_id}/items")
async def face_cluster_items(cluster_id: str, request: Request, limit: int = 200) -> list[dict[str, Any]]:
    """这个人物出现在哪些内容里。"""
    from ..search.engine import _row_to_item

    rt = _rt(request)
    rows = rt.repo.items_in_face_cluster(cluster_id, limit)
    return [_row_to_item(r, rt.repo.item_tags(str(r["id"]))) for r in rows]


@router.get("/items/{item_id}/faces")
async def item_faces(item_id: str, request: Request) -> list[dict[str, Any]]:
    """一张图里检测到的所有脸，含各自所属人物——供界面在图上画框+标名字。"""
    return _rt(request).repo.faces_for_item(item_id)


# ── 依赖医生 ────────────────────────────────────────────────


# ── 联网搜索 / 鉴伪 / 提炼（W / R / L）─────────────────────
#
# 这几个接口和上面本地检索那批的**根本区别**：它们会出网。
# 所以每一个都必须能被单独关掉（隐私围栏 E12），
# 且返回里始终带着"这条哪来的、可信度分项是什么"，不给一个光秃秃的结论。


class WebSearchRequest(BaseModel):
    query: str
    engines: list[str] | None = Field(default=None, description="不传就用设置里启用的那几家")
    limit: int = Field(default=20, ge=1, le=50)
    lang: str = "zh"
    region: str = ""
    timeRange: str | None = Field(default=None, description="day/week/month/year")
    useCache: bool = True
    #: 关掉可信度评估只在调试时有用 —— 正常使用永远该开着
    trust: bool = True
    #: S8 定向源预设 id。开了就只搜这几个站
    preset: str | None = None
    #: S4 自动扩写：去口语壳 + 中英对照，并按语言分派给合适的引擎。
    #: 快搜默认**关**——它要多花一个维基往返，而快搜要的就是快；
    #: 深挖默认开（那边本来就要等几秒）
    expand: bool = False


class ResearchRequest(BaseModel):
    query: str
    engines: list[str] | None = None
    #: 抓几篇正文来做简报。抓得越多越全，也越慢
    fetch: int = Field(default=5, ge=1, le=15)
    limit: int = Field(default=20, ge=1, le=50)
    lang: str = "zh"
    #: S5 挖几轮。1 = 旧行为（搜一次就出简报）；2 = 读完第一轮再自动追问一轮
    rounds: int = Field(default=2, ge=1, le=3)
    #: S4 是否自动扩写查询（去口语壳 / 中英对照）
    expand: bool = True
    #: S8 定向源预设 id（official-docs / academic / gov / code / cn-media / factcheck）
    preset: str | None = None
    #: V 组核查档位。不传就用设置里的默认档
    verifyLevel: str | None = Field(
        default=None, description="annotate / counter / claim"
    )


class ScholarRequest(BaseModel):
    query: str
    sources: list[str] | None = None
    limit: int = Field(default=25, ge=1, le=50)


def _web(request: Request) -> Any:
    rt = _rt(request)
    web = getattr(rt, "web", None)
    if web is None:
        raise HTTPException(503, "联网搜索没有启用（可能是隐私围栏关掉了出网）")
    return web


class RenderRegisterRequest(BaseModel):
    port: int = Field(ge=1, le=65535)


@router.post("/render/register")
async def render_register(req: RenderRegisterRequest, request: Request) -> dict[str, Any]:
    """
    桌面端专用：告诉引擎"我这儿有个浏览器渲染服务，端口是这个"（8.5）。

    没有鉴权 —— 这条接口和其余所有 `/api/*` 一样只对 127.0.0.1 开放
    （见 `main.py` 的 CORS 白名单），信任边界就是"能连上这个端口的都是本机进程"。
    """
    _rt(request).render_broker.register(req.port)
    return {"ok": True}


@router.post("/render/unregister")
async def render_unregister(request: Request) -> dict[str, Any]:
    _rt(request).render_broker.unregister()
    return {"ok": True}


@router.get("/web/engines")
async def web_engines(request: Request) -> dict[str, Any]:
    """
    每家引擎是什么、要不要 Key、要不要浏览器、当前熔断状态。

    界面靠它把「这家为什么用不了」说清楚 ——
    只显示一个灰掉的开关，用户只会以为是软件坏了。
    """
    from ..websearch.engines import describe_engines

    web = _web(request)
    return {"engines": describe_engines(), "health": web.engine_health()}


@router.post("/web/search")
async def web_search(req: WebSearchRequest, request: Request) -> dict[str, Any]:
    """W1：多引擎并发元搜索 + R1~R6 可信度标注 + R11 已排除抽屉。"""
    from ..websearch.expand import expand_query, route_variants
    from ..websearch.presets import apply_preset
    from ..websearch.trust import TrustProfile, rank_with_trust, summarize_trust

    rt = _rt(request)
    web = _web(request)
    q, preset = apply_preset(req.query, req.preset)

    res = await web.search(
        q, engines=req.engines, limit=req.limit, lang=req.lang,
        region=req.region, time_range=req.timeRange, use_cache=req.useCache,
    )
    out = res.to_dict()
    out["query"] = req.query
    if preset:
        # 改写过查询就必须说出来。用户搜「向量数据库」却看到一屏 arxiv.org，
        # 不告诉他是预设干的，他只会以为搜索坏了
        out["appliedPreset"] = preset.to_dict()
        out["effectiveQuery"] = q

    if req.expand:
        variants = await expand_query(req.query, lang=req.lang)
        extra = [v for v in variants if v.kind != "original"]
        if extra:
            routed = route_variants(extra, list(web.enabled))
            more = await asyncio.gather(
                *(
                    web.search(apply_preset(v.text, req.preset)[0], engines=eids,
                               limit=req.limit, lang=v.lang)
                    for v, eids in routed
                ),
                return_exceptions=True,
            )
            for v_and_e, r in zip(routed, more, strict=False):
                if isinstance(r, BaseException):
                    continue
                w = v_and_e[0].weight
                for c in r.clusters:
                    d = c.to_dict()
                    d["score"] = float(d.get("score") or 0.0) * w
                    d["viaQuery"] = v_and_e[0].text
                    out["results"].append(d)
            from ..websearch.deepdive import _dedupe

            out["results"] = _dedupe(out["results"])
            out["variants"] = [v.to_dict() for v in variants]

    if req.trust:
        shown, dropped = rank_with_trust(
            out["results"],
            profile=TrustProfile.from_dict(getattr(rt.config, "trust_profile", None)),
        )
        out["results"] = shown
        # 被排除的**必须一起返回**。前端可以折叠它，但不能拿不到 ——
        # 拿不到就等于这些结果被静默删除了（R11 的全部要点）
        out["excluded"] = dropped
        out["trustSummary"] = summarize_trust(shown, dropped)
    return out


@router.post("/web/research")
async def web_research(req: ResearchRequest, request: Request) -> dict[str, Any]:
    """
    深挖一条龙（S4+S5+S8+V组）：扩写查询 → 多轮递进搜 → 判可信度 →
    抓正文 → 出**摘录版**简报 → 反向核查。

    返回里的 `briefing.kind` 恒为 `extract` —— 每句话都逐字来自某篇原文。
    生成版简报是另一个接口（右栏），两边分开是为了让你随时能对照。

    `rounds=1` 就是老行为（搜一次出简报），默认 2 会在读完第一轮后
    **自己想出该追问什么再搜一轮**，每一轮问了什么、为什么问，
    都在返回的 `rounds` 字段里如实列着。
    """
    from ..websearch.deepdive import deep_research
    from ..websearch.trust import TrustProfile

    rt = _rt(request)
    web = _web(request)

    # U2：每一步实时推给界面。深挖含两轮加核查要十几到三十秒，
    # 全程只有一个转圈图标的话，用户分不清"它在干活"和"它卡死了"——
    # 而这两种情况下他该做的事完全相反（等 vs 重来）
    def emit(payload: dict[str, Any]) -> None:
        rt.events.publish("research.progress", payload)

    return await deep_research(
        web,
        req.query,
        on_progress=emit,
        engines=req.engines,
        rounds=req.rounds,
        fetch=req.fetch,
        limit=req.limit,
        lang=req.lang,
        preset=req.preset,
        expand=req.expand,
        verify_level=req.verifyLevel or getattr(rt.config, "verify_level", "counter"),
        profile=TrustProfile.from_dict(getattr(rt.config, "trust_profile", None)),
    )


@router.get("/web/presets")
async def web_presets() -> dict[str, Any]:
    """
    S8 定向源预设：一键「只看官方文档 / 只看学术 / 只看事实核查」。

    每个预设都带 `caveat` —— 开了它就搜不到别的东西，这一点必须让用户看见。
    """
    from ..websearch.presets import describe_presets

    return {"presets": describe_presets()}


class VerifyRequest(BaseModel):
    query: str
    #: 要核查的具体说法。不传就只对 query 本身做反向检索
    claims: list[str] | None = None
    engines: list[str] | None = None
    level: str = Field(default="counter", description="annotate / counter / claim")
    #: 要检查撤稿的 DOI
    dois: list[str] | None = None


@router.post("/web/verify")
async def web_verify(req: VerifyRequest, request: Request) -> dict[str, Any]:
    """
    V 组单独入口：不做完整深挖，只对一个说法做核查。

    典型用法是「我看到一句话，想知道有没有人反驳过」——
    走完整深挖要十几秒，而这条只做反向检索，两三秒就有结果。
    """
    from ..websearch.verify import (
        ClaimVerdict,
        check_retractions,
        counter_search,
        run_verification,
        verify_claims,
    )

    web = _web(request)
    if req.claims:
        verdicts: list[ClaimVerdict] = await verify_claims(
            web, [(c, "") for c in req.claims[:8]], engines=req.engines
        )
        out: dict[str, Any] = {"claims": [v.to_dict() for v in verdicts]}
        if req.dois:
            out["retracted"] = await check_retractions(req.dois[:20])
        return out

    if req.level == "annotate":
        return await run_verification(
            web, query=req.query, clusters=[], level="annotate"
        )
    counter = await counter_search(web, req.query, engines=req.engines)
    return {
        "query": req.query,
        "level": req.level,
        "counterEvidence": [s.to_dict() for s in counter],
        "note": (
            f"找到 {len(counter)} 条质疑/辟谣材料 —— 请自己看原文再判断"
            if counter else
            "没找到公开的质疑材料。**这不等于它是真的**，只说明没人公开反驳过"
        ),
    }


def out_engines(res: Any) -> list[dict[str, Any]]:
    return [
        {"id": r.engine, "outcome": r.outcome.value, "count": len(r.results),
         "elapsedMs": r.elapsed_ms, **({"error": r.error} if r.error else {})}
        for r in res.replies
    ]


class ReadUrlRequest(BaseModel):
    url: str
    maxChars: int = Field(default=6000, ge=200, le=50000)
    #: N4 顺藤摸瓜：它引用了谁（出链，按来源等级分组）+ 谁在讨论它（反链）
    trail: bool = False
    #: 反链要发两次检索，默认跟着 trail 走；单独关掉可以快一点
    backlinks: bool = True


@router.post("/web/read")
async def web_read(req: ReadUrlRequest, request: Request) -> dict[str, Any]:
    """
    W7 链接秒析：给一个网址，回标题/作者/时间/正文 + 来源分级。

    走的是和网页存档 C11 同一套抓取（含 SSRF 防护），但**不存档、不入库** ——
    「我想看看这篇讲什么」和「我要把这篇收进库」是两件事，
    合成一个动作会让用户每点一个链接都往库里塞一条。
    """
    from ..ingest.web import fetch, is_safe_url
    from ..websearch.trust import classify_domain

    ok, why = is_safe_url(req.url)
    if not ok:
        raise HTTPException(400, why)

    # N4 要原始 HTML 才能抠出链 —— 正文提取那一步恰恰把链接扔掉了
    # （那是对的：正文里混着导航链接会让语义检索废掉，见 C11 的实测）。
    # 所以只在真的要摸瓜时才留 HTML，平时照旧不留
    page = await asyncio.to_thread(fetch, req.url, save_html=False, keep_html=req.trail)
    if not page.text and page.warnings:
        # 抓失败要给出原文里的失败原因，而不是笼统一句"抓取失败"
        raise HTTPException(502, "；".join(page.warnings))

    trail: dict[str, Any] | None = None
    if req.trail:
        from ..websearch.trail import build_trail

        rt = _rt(request)
        trail = (
            await build_trail(
                getattr(rt, "web", None),
                url=page.final_url or req.url,
                html=page.html or "",
                with_backlinks=req.backlinks and getattr(rt, "web", None) is not None,
            )
        ).to_dict()

    tier = classify_domain(page.final_url or req.url)
    return {
        **({"trail": trail} if trail else {}),
        "url": req.url,
        "finalUrl": page.final_url,
        "title": page.title,
        "site": page.site or page.domain,
        "author": page.author,
        "published": page.published,
        "lang": page.lang,
        "text": page.text[: req.maxChars],
        "truncated": len(page.text) > req.maxChars,
        "chars": len(page.text),
        "warnings": page.warnings,
        "trust": {"tier": tier.value, "tierLabel": tier.label},
    }


@router.post("/web/scholar")
async def web_scholar(req: ScholarRequest, request: Request) -> dict[str, Any]:
    """L1：五家学术源并发检索，按 DOI 合并成一份带被引数和 PDF 的清单。"""
    web = _web(request)
    return await web.search_scholar(req.query, sources=req.sources, limit=req.limit)


# ── W5 图片反查 / W6 视频反查 ──────────────────────────────
#
# 这两条走的是同一个 allow_network 开关（联网搜索），不是 allow_cloud——
# 反查的是"网上有没有这张图/这段视频"，不是把内容发给大模型，
# 性质上和 W1 多引擎搜索是一类事，理应受同一道闸管。


class ReverseImageRequest(BaseModel):
    #: 库里已有的图片条目，二选一
    itemId: str | None = None
    #: 或者直接给本机文件路径（桌面端拖一张图进来，不一定已经入库）
    path: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


@router.post("/web/reverse-image")
async def reverse_image_search(req: ReverseImageRequest, request: Request) -> dict[str, Any]:
    """W5：以图搜图，找这张图在网上还出现在哪些地方（出处/更高清版/搬运源）。"""
    rt = _rt(request)
    if not rt.config.allow_network:
        raise HTTPException(403, "联网功能被隐私设置关闭了")

    if not req.itemId and not req.path:
        raise HTTPException(400, "itemId 和 path 至少给一个")

    target = Path(req.path) if req.path else None
    if req.itemId and not target:
        row = rt.repo.get_item(req.itemId)
        if row is None:
            raise HTTPException(404, "没有这条内容")
        target = Path(str(row["locator"]))
    assert target is not None

    if not target.exists():
        raise HTTPException(404, f"文件不存在：{target}")

    from ..websearch.reverse_image import BingReverseImage

    result = await BingReverseImage().search_file(target, limit=req.limit)
    return result.to_dict()


class ReverseVideoRequest(BaseModel):
    itemId: str
    maxFrames: int = Field(default=5, ge=1, le=15)


@router.post("/web/reverse-video")
async def reverse_video_search_route(req: ReverseVideoRequest, request: Request) -> dict[str, Any]:
    """W6：视频反查——均匀抽几个已有的关键帧分别以图搜图，聚合出最可能的原始来源。"""
    rt = _rt(request)
    if not rt.config.allow_network:
        raise HTTPException(403, "联网功能被隐私设置关闭了")

    scenes = rt.repo.scenes_of(req.itemId)
    if not scenes:
        raise HTTPException(404, "这条内容没有场景/关键帧数据（不是视频，或者还没分析完）")

    thumb_dir = rt.db.path.parent / "thumbs" / "video"
    keyframes = [
        thumb_dir / str(s["keyframePath"])
        for s in scenes
        if s.get("keyframePath")
    ]
    if not keyframes:
        raise HTTPException(404, "这条视频没有提取到关键帧图片")

    from ..websearch.reverse_image import reverse_video_search

    return await reverse_video_search(keyframes, max_frames=req.maxFrames)


# ── 云端简报生成（R8 右栏）─────────────────────────────────
#
# 和上面 /web/* 系列的根本区别：这里会把摘录内容发给云端模型。
# `allow_cloud` 是独立于 `allow_network` 的开关（CLAUDE.md 里定死的原则 ——
# 联网搜索泄露"在查什么"，调云端泄露"手里有什么资料"，不能合成一个开关）。


class CloudConfigureRequest(BaseModel):
    provider: str = Field(pattern="^(none|openai-compatible|anthropic)$")
    apiKey: str = ""
    baseUrl: str = ""
    chatModel: str = ""
    #: C4 图片描述专用模型，可以和 chatModel 不同（很多厂商的纯文本模型不能读图）
    visionModel: str = ""


@router.post("/cloud/configure")
async def cloud_configure(req: CloudConfigureRequest, request: Request) -> dict[str, Any]:
    """
    桌面端专用：把（已经从系统凭据存储解密出来的）Key 推给引擎。

    引擎只在内存里存一份，重启就没了——这是刻意的，key 的持久化
    只交给桌面端的 `safeStorage`，引擎这层少一个需要考虑"文件权限/加密"的地方。
    """
    rt = _rt(request)
    rt.cloud.provider = req.provider
    rt.cloud.api_key = req.apiKey
    rt.cloud.base_url = req.baseUrl
    rt.cloud.chat_model = req.chatModel
    rt.cloud.vision_model = req.visionModel
    return {"ok": True, **rt.cloud.status()}


@router.post("/cloud/clear")
async def cloud_clear(request: Request) -> dict[str, Any]:
    rt = _rt(request)
    rt.cloud = type(rt.cloud)()
    return {"ok": True}


@router.get("/cloud/status")
async def cloud_status(request: Request) -> dict[str, Any]:
    return _rt(request).cloud.status()


@router.post("/cloud/test")
async def cloud_test(request: Request) -> dict[str, Any]:
    """设置页「测试连接」按钮用：一次最小的调用，确认 Key/地址/模型名都对得上。"""
    rt = _rt(request)
    if not rt.config.allow_cloud:
        raise HTTPException(403, "云端功能被隐私设置关闭了")
    if not rt.cloud.configured:
        raise HTTPException(400, "还没配置完整（通道/Key/模型名缺一样）")

    from ..cloud.adapters import CloudAdapterError, build_adapter

    adapter = build_adapter(
        rt.cloud.provider, api_key=rt.cloud.api_key, base_url=rt.cloud.base_url or None,
    )
    try:
        result = await adapter.chat(
            system="你只需要原样回复用户发的两个字，不要加任何其他内容。",
            user="OK", model=rt.cloud.chat_model,
        )
    except CloudAdapterError as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "reply": result.text.strip()[:50], "model": result.model}


class SynthesizeRequest(BaseModel):
    query: str
    #: 直接传 `/api/web/research` 返回的 `briefing` 字段，不重新抓一遍网页 ——
    #: 慢的那一半（搜索+抓正文）已经做过了，这一步只是快速的一次 LLM 调用
    briefing: dict[str, Any]


@router.post("/cloud/synthesize")
async def cloud_synthesize(req: SynthesizeRequest, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    if not rt.config.allow_cloud:
        raise HTTPException(403, "云端功能被隐私设置关闭了 —— 去设置里打开「允许调用云端」")
    if not rt.cloud.configured:
        raise HTTPException(400, "还没配置云端通道，去设置里填 Key")

    from ..cloud.adapters import CloudAdapterError, build_adapter
    from ..cloud.synthesize import synthesize

    adapter = build_adapter(
        rt.cloud.provider, api_key=rt.cloud.api_key, base_url=rt.cloud.base_url or None,
    )
    try:
        return await synthesize(
            req.query, req.briefing, adapter=adapter, model=rt.cloud.chat_model,
        )
    except CloudAdapterError as e:
        raise HTTPException(502, str(e)) from e


@router.get("/doctor")
async def doctor_status(request: Request, deep: bool = False) -> list[dict[str, Any]]:
    """
    依赖体检。**默认走快档**（只查模块在不在，微秒级）。

    深档要真 import 一遍 fitz / trafilatura / rapidocr，每个 200~400ms，
    九个依赖加起来能到两秒 —— 界面打开分析中心要等两秒才出内容，
    中间一片空白。加 `?deep=true` 才做真导入（点"重新体检"时用）。
    """
    return await asyncio.to_thread(_rt(request).doctor.check_all, deep=deep)


@router.post("/doctor/{dep_id}/install")
async def doctor_install(dep_id: str, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    # 安装可能要几分钟，不能让 HTTP 请求挂着等 ——
    # 后台跑，进度通过 WebSocket 推
    asyncio.create_task(rt.install_dependency(dep_id))
    return {"ok": True, "started": dep_id}


@router.get("/items/{item_id}/questions")
async def item_questions(item_id: str, request: Request, limit: int = 20) -> dict[str, Any]:
    """
    N6：这篇能回答哪些问题。点一条直接跳到对应段落。

    **搜索解决不了这个问题** —— 搜索的前提是你已经知道要问什么，
    而一篇四十页的 PDF 躺在库里，难的恰恰是"我该问它什么"。

    🔴 **不用模型生成**：本地没有生成模型，而云端生成的问题会编造这篇
    没有的东西 —— 用户点进去发现那一段根本不讲这个，比没有这个功能更糟。
    所以每一个问题都**必须能指回一个具体的块**，指不回去的一律不生成。
    """
    from ..search.questions import build_questions, summarize

    rt = _rt(request)
    item = rt.repo.get_item(item_id)
    if item is None:
        raise HTTPException(404, "没有这条内容")

    rows = await asyncio.to_thread(rt.repo.item_chunks, item_id)
    chunks = [
        {
            "rowid": r["rowid"], "text": r["text"],
            "section": r["section"], "page": r["page"],
        }
        for r in rows
    ]
    qs = build_questions(chunks, limit=max(1, min(50, limit)))
    return {
        "itemId": item_id,
        "title": str(item["title"] or ""),
        "questions": [q.to_dict() for q in qs],
        "chunkCount": len(chunks),
        "note": summarize(qs, doc_title=str(item["title"] or "")),
    }


# ────────────────────────────────────────────────────────────────
# P4 研究项目持久化 ｜ P3 导出 ｜ P5 本地 × 网上并排
# ────────────────────────────────────────────────────────────────
def _store(request: Request) -> Any:
    """研究项目仓库。**每次现建一个薄壳** —— 它没有状态，只是一组 SQL 的封装，
    挂在 Runtime 上反而要多管一处生命周期。"""
    from ..websearch.projects import ResearchStore

    return ResearchStore(_rt(request).db)


class ProjectCreateRequest(BaseModel):
    query: str
    title: str | None = None
    settings: dict[str, Any] | None = None


class ProjectUpdateRequest(BaseModel):
    title: str | None = None
    status: str | None = Field(default=None, description="open / done / archived")
    notes: str | None = None
    query: str | None = None
    settings: dict[str, Any] | None = None


@router.post("/research/projects")
async def create_project(req: ProjectCreateRequest, request: Request) -> dict[str, Any]:
    """新建一个研究项目。深挖结果挂在它下面，关掉窗口再打开能接着挖。"""
    return _store(request).create_project(req.query, title=req.title, settings=req.settings)


@router.get("/research/projects")
async def list_projects(
    request: Request, status: str | None = None, limit: int = 50
) -> dict[str, Any]:
    return {"projects": _store(request).list_projects(status=status, limit=limit)}


@router.get("/research/projects/{pid}")
async def get_project(pid: str, request: Request) -> dict[str, Any]:
    p = _store(request).get_project(pid)
    if p is None:
        raise HTTPException(404, "没有这个研究项目")
    return p


@router.patch("/research/projects/{pid}")
async def update_project(
    pid: str, req: ProjectUpdateRequest, request: Request
) -> dict[str, Any]:
    p = _store(request).update_project(pid, **req.model_dump(exclude_none=True))
    if p is None:
        raise HTTPException(404, "没有这个研究项目")
    return p


@router.delete("/research/projects/{pid}")
async def delete_project(pid: str, request: Request) -> dict[str, Any]:
    if not _store(request).delete_project(pid):
        raise HTTPException(404, "没有这个研究项目")
    return {"ok": True, "deleted": pid}


@router.get("/research/projects/{pid}/resume")
async def resume_project(pid: str, request: Request) -> dict[str, Any]:
    """
    续做上下文：项目 + 上次的简报 + 钉住的来源 + **已经搜过哪些词**。

    最后一项是关键 —— 续做时最不该发生的事，就是把上次搜过的词再搜一遍。
    """
    ctx = _store(request).resume_context(pid)
    if not ctx:
        raise HTTPException(404, "没有这个研究项目")
    return ctx


@router.get("/research/projects/{pid}/runs")
async def list_runs(pid: str, request: Request, limit: int = 20) -> dict[str, Any]:
    return {"runs": _store(request).list_runs(pid, limit=limit)}


@router.get("/research/runs/{rid}")
async def get_run(rid: str, request: Request) -> dict[str, Any]:
    r = _store(request).get_run(rid)
    if r is None:
        raise HTTPException(404, "没有这条运行记录")
    return r


class RunSaveRequest(BaseModel):
    query: str
    mode: str = Field(default="deep", description="quick / deep / scholar")
    payload: dict[str, Any]


@router.post("/research/projects/{pid}/runs")
async def save_run(pid: str, req: RunSaveRequest, request: Request) -> dict[str, Any]:
    """
    把一次搜索/深挖的完整结果存进项目。

    **由界面显式调用而不是搜索时自动存**：不是每次搜索都值得留档，
    自动存会让项目里堆满随手搜的东西，真正要接着挖的那次反而找不到。
    """
    st = _store(request)
    if st.get_project(pid) is None:
        raise HTTPException(404, "没有这个研究项目")
    rid = st.add_run(pid, query=req.query, mode=req.mode, payload=req.payload)
    return {"ok": True, "runId": rid, "project": st.get_project(pid)}


class PinSourceRequest(BaseModel):
    url: str
    pinned: bool = True
    note: str | None = None


@router.get("/research/projects/{pid}/sources")
async def list_project_sources(
    pid: str, request: Request, pinnedOnly: bool = False, limit: int = 500
) -> dict[str, Any]:
    return {"sources": _store(request).list_sources(pid, pinned_only=pinnedOnly, limit=limit)}


@router.post("/research/projects/{pid}/sources/pin")
async def pin_source(pid: str, req: PinSourceRequest, request: Request) -> dict[str, Any]:
    if not _store(request).pin_source(pid, req.url, pinned=req.pinned, note=req.note):
        raise HTTPException(404, "这个项目里没有这条来源")
    return {"ok": True}


# ── P3 导出 ──────────────────────────────────────────────────
class ExportRequest(BaseModel):
    #: 三选一：直接给 payload / 给 runId / 给 projectId（取最近一次）
    payload: dict[str, Any] | None = None
    runId: str | None = None
    projectId: str | None = None
    format: str = Field(default="markdown", description="markdown / html / json / docx")
    title: str | None = None
    includeExcluded: bool = False


@router.post("/research/export")
async def export_research_route(req: ExportRequest, request: Request) -> Any:
    """
    导出一次研究。返回 `{content, filename, mime}`；docx 走 base64。

    **PDF 不在这里出**：桌面端本身就是 Chromium，它的 printToPDF 排版和
    中文字体都是现成的，比任何 Python PDF 库都好。所以这里出 HTML，
    桌面端负责打印 —— 把活交给已经擅长它的那一层。
    """
    import base64

    from ..websearch.export import export_research, safe_filename

    payload = req.payload
    if payload is None and req.runId:
        r = _store(request).get_run(req.runId)
        if r is None:
            raise HTTPException(404, "没有这条运行记录")
        payload = r["payload"]
    if payload is None and req.projectId:
        runs = _store(request).list_runs(req.projectId, limit=1, full=True)
        if not runs:
            raise HTTPException(404, "这个项目还没有任何一次搜索结果")
        payload = runs[0]["payload"]
    if payload is None:
        raise HTTPException(400, "payload / runId / projectId 至少给一个")

    title = req.title or str(payload.get("query") or "研究简报")
    try:
        content, ext, mime = export_research(
            payload, fmt=req.format, title=title, include_excluded=req.includeExcluded
        )
    except RuntimeError as e:
        # 缺 python-docx 这类**可操作**的失败要把原话给用户，
        # 而不是笼统的"导出失败"
        raise HTTPException(503, str(e)) from e

    if isinstance(content, bytes):
        return {
            "filename": safe_filename(title, ext), "mime": mime,
            "encoding": "base64", "content": base64.b64encode(content).decode("ascii"),
        }
    return {
        "filename": safe_filename(title, ext), "mime": mime,
        "encoding": "utf-8", "content": content,
    }


# ── P5 本地库 × 网上 并排 ────────────────────────────────────
class UnifiedSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=15, ge=1, le=50)
    #: 关掉其中一边就退化成普通搜索。两边都要才是这个接口的意义
    local: bool = True
    web: bool = True
    engines: list[str] | None = None
    preset: str | None = None


@router.post("/unified/search")
async def unified_search(req: UnifiedSearchRequest, request: Request) -> dict[str, Any]:
    """
    P5：**我自己有的** vs **网上说的**，一次查完并排给出，冲突高亮。

    这是锚点 9「三件事一个入口」缺的最后一块。两路是**并发**的：
    本地检索几十毫秒、联网几秒，串行等于让本地也跟着等联网。

    冲突判定沿用 R5 那套（讲同一件事 + 一方否定），不另起一套标准 ——
    两处用不同判据会出现"这里说冲突、那里说不冲突"的自相矛盾界面。
    """
    from ..websearch.presets import apply_preset
    from ..websearch.research import _NEG, _overlap, _terms
    from ..websearch.trust import TrustProfile, rank_with_trust

    rt = _rt(request)

    async def local_side() -> dict[str, Any]:
        if not req.local or rt.search is None:
            return {}
        return await asyncio.to_thread(
            rt.search.search, req.query, limit=req.limit, stage="semantic", answer=True
        )

    async def web_side() -> dict[str, Any]:
        if not req.web:
            return {}
        web = getattr(rt, "web", None)
        if web is None:
            return {"unavailable": "联网搜索被隐私设置关掉了"}
        q, preset = apply_preset(req.query, req.preset)
        res = await web.search(q, engines=req.engines, limit=req.limit)
        shown, dropped = rank_with_trust(
            [c.to_dict() for c in res.clusters],
            profile=TrustProfile.from_dict(getattr(rt.config, "trust_profile", None)),
        )
        out: dict[str, Any] = {
            "results": shown, "excluded": dropped, "elapsedMs": res.elapsed_ms,
        }
        if preset:
            out["appliedPreset"] = preset.to_dict()
        return out

    local_res, web_res = await asyncio.gather(
        local_side(), web_side(), return_exceptions=True
    )
    local_out = local_res if isinstance(local_res, dict) else {"error": "本地检索失败"}
    web_out = web_res if isinstance(web_res, dict) else {"error": "联网检索失败"}

    # 冲突高亮：本地片段 vs 网上摘要，讲同一件事但一方带否定
    conflicts: list[dict[str, Any]] = []
    local_items = (local_out.get("results") or [])[:10]
    web_items = (web_out.get("results") or [])[:10]
    for li in local_items:
        lt = str(li.get("snippet") or li.get("summary") or li.get("title") or "")
        if len(lt) < 12:
            continue
        lterms = _terms(lt)
        for wi in web_items:
            wt = f"{wi.get('title') or ''} {wi.get('snippet') or ''}"
            if len(wt) < 12 or _overlap(lterms, _terms(wt)) < 0.55:
                continue
            ln, wn = len(_NEG.findall(lt)), len(_NEG.findall(wt))
            if abs(ln - wn) >= 2 or (ln == 0) != (wn == 0):
                conflicts.append({
                    "local": {
                        "itemId": li.get("itemId") or li.get("id"),
                        "title": li.get("title"), "text": lt[:200],
                    },
                    "web": {
                        "url": wi.get("url"), "site": wi.get("site"),
                        "title": wi.get("title"), "text": wt[:200],
                    },
                })
                break
        if len(conflicts) >= 5:
            break

    return {
        "query": req.query,
        "local": local_out,
        "web": web_out,
        "conflicts": conflicts,
        "note": (
            f"本地 {len(local_items)} 条 ／ 网上 {len(web_items)} 条"
            + (f"，其中 {len(conflicts)} 处你自己的资料和网上说法对不上"
               if conflicts else "，没有发现明显冲突")
        ),
    }
