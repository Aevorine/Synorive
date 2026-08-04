"""
HTTP 接口（G4）—— 桌面端、安卓端、CLI、MCP 共用同一套
====================================================================
字段名和 packages/shared-types/src/index.ts 严格一致。
两边不一致的症状是"某个字段界面上永远是空的"，而且不报错。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import uuid

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
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
    #: D10 `type:pdf` 的扩展名过滤。以前只有查询串这一条入口，
    #: 界面上点选的筛选传不进来 —— 补齐，两条入口共用同一套语义
    extensions: list[str] | None = None
    #: L3-plus `section:方法`。**子串匹配**，不是精确相等
    #: （真实标题是 `3.2 Experimental Method`，精确匹配一条都命中不了）
    sections: list[str] | None = None


class RankingWeights(BaseModel):
    semantic: float = 1.0
    keyword: float = 1.0
    recency: float = 0.3
    sourceTrust: float = 0.2
    popularity: float = 0.2
    titleBoost: float = 0.5
    #: D1 结果多样性：同一个目录/域名下的第 2、3 条依次降权。0 = 允许一个目录霸屏
    diversity: float = 0.5
    #: D1 长度惩罚：很短的片段（目录行、页眉）降权
    lengthPenalty: float = 0.3


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
    #: A3：横跨多条结果摘一份带出处的答案。和 answer（单条秒答卡）互不影响
    ask: bool = False
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
        ask=req.ask,
    )


@router.post("/ask")
async def ask(req: SearchRequest, request: Request) -> dict[str, Any]:
    """
    A3 Ask 模式 —— 问一句话，回一段**带出处**的答案。

    它就是一次 `stage=semantic` 的检索 + `ask=True`，单开一个路由的理由有两个：
      ① 语义上是两件事。调用方（界面、MCP、CLI）写 `/ask` 比写
         `/search {"ask":true,"stage":"semantic"}` 清楚得多，也不会写错组合 ——
         `ask=True` 配 `stage=keyword` 是个静默无效的组合，引擎会照常返回
         但 `ask` 字段永远不出现，调用方只会觉得"这功能坏了"。
      ② 默认值不同。Ask 只需要少量高质量候选（摘录用不上第 40 条），
         limit 默认压到 12，省掉一大半排序和高亮的开销。

    答不上的时候**照样返回 200 和一个完整对象**（`enough:false` + `why` +
    `suggest`），不返回 4xx —— "库里没有这个答案"是正常业务结果，不是错误。
    用 4xx 表达它会让调用方的错误处理分支里混进一堆正常情况。
    """
    rt = _rt(request)
    if rt.search is None:
        raise HTTPException(503, "检索引擎还没就绪")

    result = await asyncio.to_thread(
        rt.search.search,
        req.query,
        filters=req.filters.model_dump(exclude_none=True) if req.filters else None,
        weights=req.weights.model_dump() if req.weights else None,
        preset=req.preset,
        # Ask 只摘录，用不上长尾结果；12 条足够覆盖 MAX_SOURCES=4 个来源
        limit=min(req.limit, 12),
        offset=0,
        explain=False,
        # 🔴 写死 semantic。Ask 走 keyword 层会拿没排好序的候选去摘句子，
        #    摘出来的东西一轮之后就被推翻 —— 那不是"快"，那是给错答案
        stage="semantic",
        rerank=req.rerank,
        answer=False,
        ask=True,
    )
    return {
        "ask": result.get("ask") or {"question": req.query, "passages": [], "enough": False},
        # 结果列表一并带回：答案下面就是"这几条是我读过的"，
        # 用户想自己核对时不用再发一次请求
        "hits": result.get("hits", []),
        "elapsedMs": result.get("elapsedMs", 0),
        "weakMatch": result.get("weakMatch", False),
        "recovery": result.get("recovery"),
    }


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


@router.get("/ingest/{job_id}")
async def ingest_job(job_id: str, request: Request) -> dict[str, Any]:
    """
    F2 驾驶舱轮询这条。

    🔴 **任务表只在内存里**，引擎重启就没了 —— 所以这里 404 是**正常情况**
    而不是异常，界面收到 404 要说"引擎重启过，这个任务的进度查不到了"，
    不能显示成一个卡在 0% 的任务让人一直等。
    """
    d = _rt(request).job_detail(job_id)
    if d is None:
        raise HTTPException(404, "没有这个任务（引擎重启后任务表会清空）")
    return d


# ── E17 端到端加密同步 ｜ 6.5 离线队列 ────────────────────


def _sync_queue(request: Request) -> Any:
    """
    懒建同步队列。**独立的 sqlite 文件，不塞进主库** ——
    同步状态可以整个丢掉重来，主库不能。
    """
    rt = _rt(request)
    q = getattr(rt, "_sync_queue", None)
    if q is None:
        from ..sync.queue import SyncQueue

        q = SyncQueue(rt.config.data_dir / "sync" / "queue.db")
        rt._sync_queue = q  # type: ignore[attr-defined]
    return q


def _sync_key(request: Request) -> bytes:
    """
    取这次会话的同步密钥。**没配对就明确拒绝**，不去猜一个默认口令。

    🔴 引擎重启后密钥就没了（只存内存）——这时候返回 409 而不是 500，
    并且把"去重新配对"这句话写出来。含糊的 500 会让用户以为是引擎坏了。
    """
    key = getattr(_rt(request), "_sync_key", None)
    if not key:
        raise HTTPException(
            409,
            "还没配对（或者引擎重启过 —— 密钥只存内存，不落盘）。"
            "先在两台设备上用同一个口令调一次 /api/sync/pair",
        )
    return bytes(key)


class SyncPairRequest(BaseModel):
    passphrase: str
    #: 第一次配对不传，由这一端生成并返回；对端拿到之后原样带回来
    salt: str | None = None


class SyncPushRequest(BaseModel):
    """
    对端推来的一批操作。**整批是一个加密信封**，不是明文数组。

    🔴 **不带口令。** 口令在 `/sync/pair` 时派生成密钥留在内存里，
    之后每次收发都用那把内存里的钥匙。每个请求都带口令的话，
    口令会在网络上传来传去 —— 端到端加密最不该做的就是这个。
    """

    envelope: dict[str, Any]


class SyncPullRequest(BaseModel):
    """拉取本机待推操作。同样不带口令，用内存里那把钥匙。"""

    limit: int = Field(default=200, ge=1, le=500)


class SyncAckRequest(BaseModel):
    ids: list[str]


class SyncEnqueueRequest(BaseModel):
    entity: str = "item"
    entityId: str
    kind: str = "upsert"
    payload: dict[str, Any] | None = None


@router.get("/sync/status")
async def sync_status(request: Request) -> dict[str, Any]:
    """同步状态：本机设备号、Lamport 时钟、队列里还有多少没推出去。"""
    from ..sync.crypto import crypto_available

    q = _sync_queue(request)
    st = q.stats()
    st["cryptoAvailable"] = crypto_available()
    if not st["cryptoAvailable"]:
        # 🔴 说清楚是**整个不可用**而不是"降级运行"。含糊其辞的话，
        # 用户会以为同步在跑只是没加密 —— 而实际上一条都推不出去
        st["note"] = (
            "没装 cryptography，**同步整个不可用**（不是降级、不是明文同步）。"
            "装一下：pip install \"synorive[sync]\""
        )
    else:
        st["note"] = "冲突按 Lamport 时钟判，不看系统时间；删除留墓碑 30 天"
    return st


@router.post("/sync/pair")
async def sync_pair(req: SyncPairRequest, request: Request) -> dict[str, Any]:
    """
    E17 配对：口令 + 盐 → 密钥指纹。

    🔴 **口令和密钥都不返回、不落盘、不写日志。** 只返回**指纹**
    （密钥的哈希），两台设备各自算一遍比对 —— 指纹一样就说明钥匙一样。
    把密钥本身发出去的话，这套端到端加密就只是个装饰。
    """
    from ..sync.crypto import CryptoUnavailable, derive_key, key_fingerprint, make_challenge, new_salt

    try:
        salt = req.salt or new_salt()
        key = await asyncio.to_thread(derive_key, req.passphrase, salt)
        # 🔴 密钥**只存内存，跟着进程走**。落盘的话，攻破这台机器的人
        # 不需要口令就能解开所有同步数据 —— 那这套加密就只是个装饰。
        # 引擎重启后要重新配对，这是刻意的代价（和云端 Key 同一条约定）
        rt = _rt(request)
        rt._sync_key = key  # type: ignore[attr-defined]
        rt._sync_salt = salt  # type: ignore[attr-defined]
        return {
            "salt": salt,
            "fingerprint": key_fingerprint(key),
            "challenge": make_challenge(key),
            "deviceId": _sync_queue(request).device_id,
            "note": "把 salt 和指纹给另一台设备，让它用**同一个口令**算一遍。"
            "指纹对得上才算配对成功 —— 口令本身两边都不要传",
        }
    except CryptoUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/sync/enqueue")
async def sync_enqueue(req: SyncEnqueueRequest, request: Request) -> dict[str, Any]:
    """6.5 —— 把一条本地改动压进离线队列，等联网了再推。"""
    op = _sync_queue(request).enqueue(req.entity, req.entityId, req.kind, req.payload)
    return {"ok": True, "op": op.to_dict()}


@router.post("/sync/pull")
async def sync_pull(req: SyncPullRequest, request: Request) -> dict[str, Any]:
    """
    把本机待推的操作封成一个加密信封交出去。

    🔴 **不在这里标记 `sent`。** 只有对端 `/sync/push` 成功之后
    才由调用方回来调 `/sync/ack` —— 发出去就标的话，网络在半路断了
    这批操作永远不会重发，数据静默丢失而两端看起来都正常。
    """
    from ..sync.crypto import CryptoUnavailable, seal

    q = _sync_queue(request)
    key = _sync_key(request)
    ops = q.pending(req.limit)
    # 🔴 守卫要包住**真正会抛的那一句**。`pending()` 只读 sqlite，
    # 从不抛 CryptoUnavailable —— 包着它等于没包，异常会原样变成 500，
    # 用户看到的是"内部错误"而不是"去装 cryptography"
    try:
        # aad 里放发送方设备号：它不加密但参与认证，被改了解密就会失败。
        # ⚠️ 说明白它的边界：aad 本身也随信封传输，所以这**不是**
        # "绑定到一个外部已知值"，只是保证 aad 没被悄悄改过
        env = seal(key, [o.to_dict() for o in ops], aad=q.device_id.encode("utf-8"))
    except CryptoUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return {
        "envelope": env,
        "opIds": [o.id for o in ops],
        "count": len(ops),
        "deviceId": q.device_id,
        "note": "这批还**没有**标记成已发送。对端确认收到后调 /sync/ack 才算数",
    }


@router.post("/sync/ack")
async def sync_ack(req: SyncAckRequest, request: Request) -> dict[str, Any]:
    """对端确认收到之后才标记已发送。"""
    return {"marked": _sync_queue(request).mark_sent(req.ids)}


@router.post("/sync/push")
async def sync_push(req: SyncPushRequest, request: Request) -> dict[str, Any]:
    """
    收对端推来的一批操作，解密后合并。

    🔴 **解密失败一律拒绝，不去"试试能不能解出点什么"。**
    认证失败意味着数据被改过或者口令不对 —— 两种都必须让用户知道，
    而不是安静地跳过这一批然后报"同步完成"。
    """
    from ..sync.crypto import CryptoUnavailable, open_envelope
    from ..sync.queue import Op

    q = _sync_queue(request)
    key = _sync_key(request)
    try:
        raw = open_envelope(key, req.envelope)
    except CryptoUnavailable as e:
        # 🔴 **必须排在 `except Exception` 前面。** `CryptoUnavailable` 是
        # `RuntimeError` 的子类，会被下面那个宽 catch 吞掉，然后报成
        # "口令不对" —— 而真实原因是根本没装库。用户会一直去改口令，
        # 改到天亮也不会好。诊断错的报错比不报错更浪费时间
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, f"信封坏了或版本对不上：{e}") from e
    except Exception as e:  # noqa: BLE001 — cryptography 的 InvalidTag 不是 ValueError
        raise HTTPException(
            403,
            "解密失败：口令不对，或者这批数据在路上被改过。"
            "**没有应用任何一条** —— 检查两台设备的配对口令是不是同一个",
        ) from e

    if not isinstance(raw, list):
        raise HTTPException(400, "信封里应该是一个操作数组")
    return q.merge([Op.from_dict(d) for d in raw if isinstance(d, dict)])


@router.post("/sync/purge")
async def sync_purge(request: Request) -> dict[str, Any]:
    """清理已确认的历史和过期墓碑。"""
    q = _sync_queue(request)
    return {"purgedSent": q.purge_sent(), "purgedTombstones": q.purge_tombstones()}


class ArchiveShotRequest(BaseModel):
    """C12 整页截图归档 ｜ C13 登录态抓取。"""

    url: str
    #: C13：这个站点的 cookie。**不给就是匿名抓取**
    cookies: list[dict[str, str]] | None = None
    #: 顺便把正文也抓一份入库（截图是版面证据，正文才能被搜索到）
    ingest: bool = True
    tags: list[str] | None = None


@router.post("/web/archive-shot")
async def archive_shot(req: ArchiveShotRequest, request: Request) -> dict[str, Any]:
    """
    C12 —— 把一个网页**整页截图**存进归档目录；C13 —— 可带 cookie 抓登录后的页面。

    🔴 **两个开关都受 `allow_network` 管**：截图要真的去访问那个网址。
    隐私围栏关了联网就一律拒绝，不给"截图不算联网"这种解释空间。

    🔴 **cookie 只在这一次请求里存在。** 引擎不落盘、不写日志、不回显；
    桌面端那边用的是内存分区的 session，每次抓取前先清空。
    **绝不能为了"下次不用再传"把它存起来** —— 那是把用户的登录凭证
    变成一个躺在磁盘上的长期资产，而用户完全不知道。

    🔴 **截图和正文是两回事，都要给。** 只有截图的话搜不到（图里的字要 OCR）；
    只有正文的话版面证据就没了（"这个页面当时长这样"）。
    """
    rt = _rt(request)
    if not rt.config.allow_network:
        raise HTTPException(403, "联网功能被隐私设置关闭了")
    if not req.url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "只支持 http/https 网址")

    broker = getattr(rt, "render_broker", None)
    if broker is None or not broker.available:
        raise HTTPException(
            503,
            "整页截图要借桌面端的浏览器，现在借不到"
            "（桌面端没连上引擎，或者引擎是在纯命令行模式下跑的）",
        )

    shot = await broker.capture(req.url, cookies=req.cookies)
    # 🔴 把**真实原因**透出去，不要自己编一句"可能是A可能是B"的猜测清单 ——
    # 用户拿着猜测清单什么也做不了，拿着"等了 23 秒超时"就知道该换个页面试
    if shot is None or shot.get("error"):
        raise HTTPException(502, f"截图没成功：{(shot or {}).get('error') or '渲染端没有回应'}")

    import base64 as _b64
    import hashlib as _hl

    raw = _b64.b64decode(shot["png"])
    # 🔴 再查一次字节数。base64 串非空**不代表**解出来非空
    if not raw:
        raise HTTPException(502, "截图解码出来是 0 字节")

    shots_dir = rt.config.archive_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{_hl.sha1(req.url.encode()).hexdigest()[:10]}.png"
    (shots_dir / name).write_bytes(raw)

    out: dict[str, Any] = {
        "ok": True,
        "shot": name,
        "path": str(shots_dir / name),
        "bytes": len(raw),
        "width": shot.get("width"),
        "height": shot.get("height"),
        "truncated": bool(shot.get("truncated")),
        "usedCookies": bool(req.cookies),
    }
    # 🔴 cookie 有没有设失败必须报上来。少了关键的那个 session cookie，
    # 抓回来的是登录页 —— 而截图、字节数、HTTP 200 全都正常
    if shot.get("cookieFailures"):
        out["cookieFailures"] = shot["cookieFailures"]
        out["warning"] = (
            "有 cookie 没设成功，截到的可能是**登录页而不是内容页** —— 打开图确认一下"
        )
    if out["truncated"]:
        out["warning"] = (
            (out.get("warning", "") + "；") if out.get("warning") else ""
        ) + f"页面太长，只截到前 {shot.get('height')} 像素"

    if req.ingest and rt.pipeline is not None:
        try:
            r = await asyncio.to_thread(
                rt.pipeline.ingest_url, req.url, tags=(req.tags or ["整页归档"])
            )
            out["ingest"] = r
        except Exception as e:  # noqa: BLE001
            # 入库失败不该让截图也白截 —— 图已经存下来了，如实说一声
            out["ingest"] = f"正文入库失败：{type(e).__name__}: {e}"
    return out


@router.get("/web/archive-shot/{name}")
async def get_archive_shot(name: str, request: Request) -> Any:
    """
    把归档的整页截图发出去（渲染层读不了任意本地文件，这是唯一的出口）。

    🔴 越界判据用「解析后的真实路径是否仍在 shots 目录内」，
    不用黑名单过滤 `..` —— 黑名单永远漏（`%2e%2e`、`....//`、软链接）。
    这和 `/media/thumb/{name}` 是同一条判据，别用两套。
    """
    from fastapi.responses import FileResponse

    rt = _rt(request)
    base = (rt.config.archive_dir / "shots").resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(404, "没有这张截图")
    return FileResponse(target, media_type="image/png")


class ModelReloadRequest(BaseModel):
    """E15。`preferGpu` 不传就只重建会话、不改偏好。"""

    preferGpu: bool | None = None


@router.get("/models/status")
async def models_status(request: Request) -> dict[str, Any]:
    """E15 —— 各模型装没装、加载没加载、跑在哪个执行器上。"""
    return _rt(request).model_status()


@router.post("/models/reload")
async def models_reload(req: ModelReloadRequest, request: Request) -> dict[str, Any]:
    """
    E15 —— **不重启引擎**换执行器 / 重建推理会话。

    🔴 **不能用它换「另一个」文本向量模型。** 库里的向量都是当前模型算的，
    换模型之后新查询的向量和旧向量不在同一个空间里 ——
    **搜索不会报错，只会开始返回不相干的东西**。要换必须整库重建索引。
    所以这条接口只重建会话、只换执行器（CPU ↔ 核显），不碰模型身份。
    """
    return await asyncio.to_thread(_rt(request).reload_models, prefer_gpu=req.preferGpu)


class PreviewRequest(BaseModel):
    path: str


@router.post("/preview/media")
async def preview_media_route(req: PreviewRequest) -> dict[str, Any]:
    """
    A2 —— 视频/音频「先看后搜」秒开预览：等距缩略带 + 语音波形。

    🔴 **这条不入库、不写任何记录。** 它回答的是"这里面是什么"，
    不是"把它分析进库"。混起来会让一次随手预览在库里留下半成品。

    🔴 在**工作线程**里跑：ffmpeg 抽帧和解音轨都是阻塞调用，
    直接在事件循环里跑会把 `/health` 和 WebSocket 一起卡住 ——
    症状是"预览一个视频，整个界面显示引擎掉线了"。
    """
    from ..analyze.preview import preview_media

    p = Path(req.path)
    if not p.exists():
        raise HTTPException(400, f"文件不在：{req.path}")
    return await asyncio.to_thread(preview_media, p)


@router.post("/ingest/{job_id}/{action}")
async def ingest_job_control(job_id: str, action: str, request: Request) -> dict[str, Any]:
    """暂停 / 继续 / 取消。重试不走这里 —— 重试就是拿失败清单再 POST 一次 `/ingest`。"""
    if action not in ("pause", "resume", "cancel"):
        raise HTTPException(400, f"只支持 pause/resume/cancel，收到的是 {action}")
    return _rt(request).control_job(job_id, action)


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


@router.post("/maintenance/verify-sources")
async def verify_sources_route(
    request: Request,
    limit: int = Query(default=5000, ge=1, le=100000),
) -> dict[str, Any]:
    """
    4.22b H2 —— 查库里的记录和磁盘上的文件还对不对得上。

    重算 `file_fingerprint`（头 1MB + 尾 1MB + 大小）和入库时存的比。
    三种结论分开报：`changed`（文件被改过，搜出来的内容是旧的）／
    `missing`（文件没了，搜到也打不开）／`ok`。

    🔴 **只报告，绝不自动删或自动重建。** 外接硬盘没插、网络盘没连上时
    整库都会报 missing —— 那种情况下自动清理等于把库删掉。

    走线程：几千个文件各读 2MB 是实打实的阻塞 IO，直接在事件循环里跑
    会把引擎的其他请求全卡住（包括界面的状态轮询，表现是"整个应用假死"）。
    """
    from ..ingest.pipeline import verify_sources

    rt = _rt(request)
    return await asyncio.to_thread(verify_sources, rt.repo, limit=limit)


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


@router.get("/media/thumb/{name}")
async def media_thumb(name: str, request: Request) -> Any:
    """
    缩略图与视频关键帧的静态出口 —— N3 场景缩略条靠它。

    **在这之前，关键帧一直躺在磁盘上而界面根本没办法显示它们**：
    `scenes_of()` 返回的 `keyframePath` 只是个文件名，渲染层没有任何途径
    把它变成一张图（渲染进程读不了任意本地文件，那是对的）。
    所以"视频场景缩略条"这个功能的数据早就有了，缺的就是这一条路由。

    🔴 **路径穿越必须挡死**。这条接口拿用户可控的字符串去拼路径，
    不挡的话 `../../../settings.json` 就能把任意文件读走 ——
    而引擎对本机是完全信任的（只听 127.0.0.1），这里是少数几个
    "外部输入直接变成文件路径"的地方之一。
    判据用**解析后的真实路径是否仍在 thumbs 目录内**，
    而不是黑名单过滤 `..` —— 黑名单永远漏（`%2e%2e`、`....//`、软链接）。
    """
    rt = _rt(request)
    base = rt.config.thumb_dir.resolve()
    try:
        target = (base / name).resolve()
    except (OSError, ValueError):
        raise HTTPException(400, "文件名不合法") from None

    # 解析后必须还在 base 里面。`is_relative_to` 是 3.9+ 的标准做法，
    # 比自己拼字符串比较可靠（它处理了大小写、符号链接、盘符等一堆边角）
    if not target.is_relative_to(base):
        raise HTTPException(403, "越界访问")
    if not target.is_file():
        raise HTTPException(404, "没有这个文件")

    from fastapi.responses import FileResponse

    return FileResponse(
        target,
        # 缩略图内容按文件名唯一，永不变 —— 让浏览器长期缓存，
        # 一个 20 场景的视频滚动时不会反复回源
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/duplicates/sweep")
async def duplicates_sweep(request: Request, limit: int = 200) -> dict[str, Any]:
    """
    E9 —— **全库**近重复扫描，按组返回。

    原来只有 `/items/{id}/duplicates`（"和这一张像的还有哪些"）。
    那条回答不了用户真正的问题：**"我库里到底有多少重复，能不能一次清掉"**。
    要靠它清库，得先知道该点开哪一张 —— 而那正是他不知道的事。

    🔴 **只扫、不删。** 删除是另一条显式接口，而且一次只删被点名的那几个 id。
    "扫描顺便清理"是这类功能最容易出事的地方：判据错一点就静默删掉真东西，
    而删掉的东西**没有回收站**（`delete_item` 是直接删索引和记录的）。

    🔴 **每组里"留哪一张"由用户定，这里不替他选。** 常见做法是留最大/最早的那张，
    但真实情况是他可能要留有 EXIF 的那张、或者路径在正式目录里的那张。
    这里只按"分辨率×字节数"给一个**建议保留**标记，最终点哪个是他的事。
    """
    import json as _json

    rt = _rt(request)
    conn = rt.db.connect()
    rows = conn.execute(
        "SELECT id, title, locator, size_bytes, created_at, meta_json FROM items "
        "WHERE modality = 'image' AND meta_json IS NOT NULL"
    ).fetchall()

    # phash → 成员。**只按完整 phash 精确分桶**，不在这里做汉明距离两两比对：
    # 一万张图两两比是五千万次，会把这个请求变成一次几十秒的阻塞
    by_hash: dict[str, list[dict[str, Any]]] = {}
    scanned = 0
    for r in rows:
        try:
            meta = _json.loads(str(r["meta_json"] or "{}"))
        except _json.JSONDecodeError:
            continue
        ph = meta.get("phash")
        if not ph or len(str(ph)) < 16:
            continue
        scanned += 1
        w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
        by_hash.setdefault(str(ph), []).append({
            "id": str(r["id"]),
            "title": str(r["title"] or ""),
            "locator": str(r["locator"] or ""),
            "sizeBytes": int(r["size_bytes"] or 0),
            "createdAt": str(r["created_at"] or ""),
            "width": w,
            "height": h,
            "score": w * h * max(1, int(r["size_bytes"] or 0)),
        })

    groups: list[dict[str, Any]] = []
    for ph, members in by_hash.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: -int(m["score"]))
        for i, m in enumerate(members):
            m["suggestKeep"] = i == 0
            m.pop("score", None)
        groups.append({
            "phash": ph,
            "count": len(members),
            "wastedBytes": sum(int(m["sizeBytes"]) for m in members[1:]),
            "members": members,
        })
    groups.sort(key=lambda g: -int(g["wastedBytes"]))
    shown = groups[:limit]

    return {
        "groups": shown,
        "groupCount": len(groups),
        "truncated": len(groups) > limit,
        "scannedImages": scanned,
        "wastedBytes": sum(int(g["wastedBytes"]) for g in groups),
        "note": "只找**指纹完全相同**的（同一张图的多份拷贝）。"
        "改过尺寸、压过一道、加过水印的算不同指纹，这里找不出来 —— "
        "那种要用「和这张像的」逐张看。**建议保留**只按分辨率×体积排，最终留哪张你自己定",
    }


class DeleteItemsRequest(BaseModel):
    ids: list[str]
    #: 二次确认。**没传 true 就只干跑**，返回将要删什么但一个都不动
    confirm: bool = False


@router.post("/items/delete")
async def delete_items(req: DeleteItemsRequest, request: Request) -> dict[str, Any]:
    """
    E9 清理 —— 按 id 删内容。

    🔴 **`confirm` 默认 false = 干跑。** 删除**没有回收站**
    （`delete_item` 直接清索引和记录，撤不回来），所以默认行为必须是
    "告诉你将要删什么"而不是"删给你看"。界面拿干跑结果做二次确认。

    🔴 **只删库里的记录，不碰硬盘上的原文件。** 这是刻意的：
    用户点的是"从我的检索库里去掉"，不是"把我的照片删了"。
    两者混为一谈的后果不可逆，而且他八成到很久以后才发现。
    """
    rt = _rt(request)
    ids = [i for i in dict.fromkeys(req.ids) if i]
    if not ids:
        raise HTTPException(400, "没给要删的 id")
    if len(ids) > 500:
        raise HTTPException(400, f"一次最多删 500 条，收到 {len(ids)} 条")

    rows = rt.repo.get_items(ids)
    found = [i for i in ids if i in rows]
    missing = [i for i in ids if i not in rows]

    if not req.confirm:
        return {
            "dryRun": True,
            "wouldDelete": len(found),
            "missing": missing,
            "titles": [str(rows[i]["title"] or rows[i]["locator"]) for i in found[:20]],
            "note": "这是干跑，**一条都没删**。确认无误后带 confirm:true 再发一次。"
            "删的只是库里的记录，硬盘上的原文件不动",
        }

    deleted, failed = 0, []
    for i in found:
        try:
            rt.repo.delete_item(i)
            deleted += 1
        except Exception as e:  # noqa: BLE001
            # 逐条报因：一条删不掉不该让另外 99 条也白删一遍
            failed.append({"id": i, "error": f"{type(e).__name__}: {e}"})

    return {
        "dryRun": False,
        "deleted": deleted,
        "missing": missing,
        "failed": failed,
        "note": f"删掉了 {deleted} 条库记录。**硬盘上的原文件没动** —— "
        "要连文件一起删，请自己去文件管理器里删",
    }


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
    #: B5 意图分流：判「找定义/教程/论文/新闻/代码/产品」自动换阵容。
    #: 默认**开** —— 它是纯本地正则，1ms 内出结果，不花任何网络代价。
    #: 🔴 用户显式传了 `engines` 时它一律不生效（见 `intent.apply_intent`）
    intent: bool = True
    #: D1/D7 内容农场指纹 + 利益相关标注。纯本地判据，默认开
    farm: bool = True
    #: B7 站点独立性统计。纯本地，默认开
    independence: bool = True


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
    #: X3 全局时间预算（秒）。不传就用 deepdive.TOTAL_BUDGET_S（8.0，正好是 X3 的目标）。
    #: 传 0 表示**不设死线**，回到"能挖多深挖多深"的老行为。
    #: 超预算时是降级（少一轮追问 / 核查降到不出网）而不是截断，
    #: 降了什么在响应的 `budget.degraded` 里列着。
    budgetS: float | None = Field(
        default=None, ge=0, le=120,
        description="全局时间预算（秒）。0 = 不限时。默认 8s",
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


@router.post("/web/engines/reset-health")
async def web_engines_reset_health(request: Request) -> dict[str, Any]:
    """
    清掉引擎记分与熔断，从零重新学（S1）。`?engine=baidu` 只清一家。

    有这条是因为记分是**滑动窗口**：一家引擎修好之后，界面上仍会被
    几十次旧失败压着显示成"用不了"，而其中相当一部分本来就是误记的
    （没派上场被当成搜了没结果、查询串写错被当成引擎坏了）。
    详见 `EngineScheduler.reset` 的注释。
    """
    eid = (request.query_params.get("engine") or "").strip() or None
    return _web(request).reset_health(eid)


@router.post("/web/search")
async def web_search(req: WebSearchRequest, request: Request) -> dict[str, Any]:
    """W1：多引擎并发元搜索 + R1~R6 可信度标注 + R11 已排除抽屉。"""
    from ..websearch.expand import expand_query, route_variants
    from ..websearch.presets import apply_preset
    from ..websearch.trust import TrustProfile, rank_with_trust, summarize_trust

    rt = _rt(request)
    web = _web(request)

    # B5 意图分流放在 apply_preset **之前** —— 它可能自己带一个 preset
    # （比如判成「找代码」就锁 github），而用户显式传的 preset 优先级更高
    eff_engines, eff_limit = req.engines, req.limit
    eff_preset, eff_range = req.preset, req.timeRange
    intent_out: dict[str, Any] | None = None
    if req.intent:
        from ..websearch.intent import apply_intent, detect

        it = detect(req.query)
        eff_engines, eff_limit, eff_preset, eff_range = apply_intent(
            it, engines=req.engines, limit=req.limit,
            preset=req.preset, time_range=req.timeRange,
        )
        intent_out = it.to_dict()

    q, preset = apply_preset(req.query, eff_preset)

    res = await web.search(
        q, engines=eff_engines, limit=eff_limit, lang=req.lang,
        region=req.region, time_range=eff_range, use_cache=req.useCache,
    )
    out = res.to_dict()
    out["query"] = req.query
    if intent_out and intent_out.get("kind") != "general":
        # 换过阵容就必须说出来，理由和 preset 那条一样：
        # 用户搜一句话却看到一屏 arXiv，不告诉他是谁干的，他只会以为搜索坏了
        out["intent"] = intent_out
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

    # D1/D7、B7 都是**纯本地判据**（不发请求、微秒级），所以直接跟着
    # 每次搜索一起算完返回，不另开一次往返。真正要花时间的那几条
    # （D2 事件时间 / D3 文风 / D5 数字）要正文，留在各自的接口里
    if req.farm:
        from ..websearch.farm import annotate as _farm_annotate
        from ..websearch.farm import summarize as _farm_summary

        flat = [(c.get("best") or c) for c in out.get("results") or []]
        _farm_annotate(flat)
        out["farmSummary"] = _farm_summary(flat)

    if req.independence:
        from ..websearch.meta import site_independence

        out["independence"] = site_independence(out.get("results") or [])
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
    from ..websearch.deepdive import TOTAL_BUDGET_S, deep_research
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
        # X3：不传 → 用默认 8s 死线；传 0 → 关掉死线（老行为）。
        # `req.budgetS or DEFAULT` 这种写法在这里是**错的** —— 0 会被当成"没传"，
        # 用户明确要求的"不限时"会被悄悄改回 8s
        total_budget_s=(
            TOTAL_BUDGET_S if req.budgetS is None
            else (req.budgetS if req.budgetS > 0 else None)
        ),
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
    #: B2 多家并发。默认只跑 bing —— 另外两家要借桌面端浏览器渲染结果页，
    #: 而且更容易被验证码挡，不该在用户没要求时默默去撞
    providers: list[str] | None = None


@router.post("/web/reverse-image/multi")
async def reverse_image_multi(req: ReverseImageRequest, request: Request) -> dict[str, Any]:
    """
    B2 —— 三家反查**同时跑**：Bing / Yandex / Google Lens。

    为什么值得多跑两家：三家的索引重合度出乎意料地低。Bing 擅长
    英文站和商品图，Yandex 对人像和小语种明显更好，Lens 偶尔能命中
    另外两家都没有的社交媒体内容。一家没结果**不等于**网上没有。

    🔴 **一家失败绝不带倒另外两家**，每家单独回自己的 `error`。
    Yandex 和 Lens 都很容易被人机验证挡住，而那时候 Bing 往往是好的。

    🔴 **「解析不出条目」和「网上没有这张图」是两件事**，各家的 `error`
    文案里都写死了这句话。把它折叠掉，用户就会拿一次失败的查询
    当成"这张图是原创的"证据 —— 那是这个功能能造成的最大误导。
    """
    rt = _rt(request)
    if not rt.config.allow_network:
        raise HTTPException(403, "联网功能被隐私设置关闭了")
    if not req.itemId and not req.path:
        raise HTTPException(400, "itemId 和 path 至少给一个")

    target = Path(req.path) if req.path else None
    if req.itemId and target is None:
        row = rt.repo.get_item(req.itemId)
        if row is None:
            raise HTTPException(404, "没有这条内容")
        target = Path(str(row["locator"]))
    assert target is not None
    if not target.exists():
        raise HTTPException(404, f"文件不存在：{target}")

    from ..websearch.reverse_image import BingReverseImage
    from ..websearch.reverse_image_alt import LensReverseImage, YandexReverseImage

    broker = getattr(rt, "render_broker", None)
    catalog: dict[str, Any] = {
        "bing": BingReverseImage(),
        "yandex": YandexReverseImage(broker),
        "lens": LensReverseImage(broker),
    }
    wanted = [p for p in (req.providers or ["bing"]) if p in catalog]
    if not wanted:
        raise HTTPException(400, f"不认识的反查来源。可选：{', '.join(catalog)}")

    results = await asyncio.gather(
        *(catalog[p].search_file(target, limit=req.limit) for p in wanted),
        return_exceptions=True,
    )
    out: dict[str, Any] = {}
    for name, r in zip(wanted, results, strict=True):
        if isinstance(r, BaseException):
            out[name] = {"error": f"{type(r).__name__}: {r}"}
        else:
            out[name] = r.to_dict()

    total = sum(len(v.get("pagesIncluding") or []) for v in out.values())
    return {
        "providers": out,
        "totalPages": total,
        "note": "三家索引重合度很低，一家没结果不代表网上没有这张图。"
        "Yandex 和 Google Lens 容易被人机验证挡下 —— 那是「这次没查成」，不是「查过了没有」",
    }


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


class ImageLanesRequest(BaseModel):
    """A3 一张图四路并发。itemId 和 path 二选一。"""

    itemId: str | None = None
    path: str | None = None
    limit: int = Field(default=12, ge=1, le=40)
    #: 关掉就只跑本地三路。默认跟随隐私设置，不在这里硬开
    web: bool = True


@router.post("/image/lanes")
async def image_lanes(req: ImageLanesRequest, request: Request) -> dict[str, Any]:
    """
    A3 —— 一张图，**四路同时跑，一屏出完**。

    四路各自回答一个不同的问题：
      ① 像不像我库里已有的东西？（以图搜图 / 搜镜头）
      ② 图里写了什么字？（OCR → 再拿这些字去搜一遍）
      ③ 这张图网上还出现在哪？（W5 反查，找出处和更高清版）
      ④ 它像不像被改过？（D4 四条判据初筛）

    🔴 **一路失败绝不能带倒另外三路。** 反查那一路最容易挂
    （要联网、可能被限流、可能被隐私开关关掉），而它挂掉时
    OCR 和本地相似图仍然完全有效。所以每一路都单独 try，
    失败的那一路回 `{"error": ...}` 而不是让整个请求 500 ——
    否则表现是"网络一抖，连本地以图搜图都用不了了"。

    🔴 **串行做这四件事要十几秒**（反查一次就好几秒），并发之后
    总耗时 = 最慢那一路。这是 A3 唯一的技术要点，别把它做成顺序调用。
    """
    rt = _rt(request)
    if not req.itemId and not req.path:
        raise HTTPException(400, "itemId 和 path 至少给一个")

    target = Path(req.path) if req.path else None
    if req.itemId and target is None:
        row = rt.repo.get_item(req.itemId)
        if row is None:
            raise HTTPException(404, "没有这条内容")
        target = Path(str(row["locator"]))
    assert target is not None
    if not target.exists():
        raise HTTPException(404, f"文件不存在：{target}")

    want_web = req.web and rt.config.allow_network

    async def lane_similar() -> dict[str, Any]:
        if rt.search is None:
            return {"error": "检索器还没就绪"}
        # 🔴 `search_by_image` 收的是**向量**不是路径。先过一道
        # `image_vector_for`；拿不到向量（图像模型没装）时给出
        # 能照着做的话，而不是一句"失败了"
        vec = await asyncio.to_thread(rt.image_vector_for, req.itemId, str(target))
        if vec is None:
            return {
                "error": "拿不到这张图的向量。多半是图像模型还没装 —— "
                "去「分析中心 → 能力与依赖」装 embed-image"
            }
        return await asyncio.to_thread(
            rt.search.search_by_image,
            vec,
            limit=req.limit,
            include_scenes=True,
            exclude_item=req.itemId or "",
        )

    async def lane_ocr() -> dict[str, Any]:
        from ..analyze.image import OcrEngine, analyze_image

        # 只要 OCR，不要向量 —— 向量这一路已经由 lane_similar 走过了，
        # 再算一遍纯属重复劳动（而且是这四路里最慢的一步）。
        # `OcrEngine()` 是懒加载的：没装 OCR 依赖时它自己降级返回空行，
        # 不抛异常，所以这里不需要额外的 try
        engine = OcrEngine()
        # 🔴 「没装 OCR」和「图里没字」结果长得一模一样（都是空字符串），
        # 但对用户是两件完全不同的事：前者要去装东西，后者什么也不用做。
        # 不分开说的话，一个没装 OCR 的人会一直以为自己的图里没字
        if not engine.available:
            return {"text": "", "charCount": 0, "note": "OCR 引擎没装，这一路没跑（不是图里没字）"}
        res = await asyncio.to_thread(analyze_image, target, ocr=engine, embedder=None)
        text = (getattr(res, "ocr_text", "") or "").strip()
        out: dict[str, Any] = {"text": text, "charCount": len(text)}
        if not text:
            out["note"] = "OCR 跑完了，但一个字都没认出来 —— 图里可能本来就没字，也可能字太小或太花"
            return out
        if rt.search is not None:
            hit = await asyncio.to_thread(
                rt.search.search, text[:200], limit=req.limit, stage="keyword"
            )
            out["hits"] = hit.get("hits", [])
        return out

    async def lane_reverse() -> dict[str, Any]:
        if not want_web:
            return {"note": "联网反查没跑：隐私设置里关掉了联网，或这次请求要求只跑本地"}
        from ..websearch.reverse_image import BingReverseImage

        r = await BingReverseImage().search_file(target, limit=req.limit)
        return r.to_dict()

    async def lane_tamper() -> dict[str, Any]:
        from ..analyze.tamper import screen

        rep = await asyncio.to_thread(screen, target)
        return rep.to_dict() if hasattr(rep, "to_dict") else dict(rep.__dict__)

    names = ["similar", "ocr", "reverse", "tamper"]
    results = await asyncio.gather(
        lane_similar(), lane_ocr(), lane_reverse(), lane_tamper(), return_exceptions=True
    )
    lanes: dict[str, Any] = {}
    for name, r in zip(names, results, strict=True):
        if isinstance(r, BaseException):
            # 🔴 把真实异常文本给出去。写成"分析失败"这类模板话，
            # 用户和我都没法知道到底是没装 OCR、还是被限流、还是文件读不了
            lanes[name] = {"error": f"{type(r).__name__}: {r}"}
        else:
            lanes[name] = r

    return {
        "path": str(target),
        "lanes": lanes,
        "note": "四路是并发跑的，总耗时等于最慢那一路。某一路显示错误不影响其他三路的结果",
    }


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
    format: str = Field(
        default="markdown",
        description="markdown / html / json / docx / single-html（E6 离线单文件）",
    )
    title: str | None = None
    includeExcluded: bool = False
    #: E1 简报模板：points 要点式 / timeline 时间线 / compare 对比表 / qa 问答。
    #: **换模板不改任何一句摘录**，只换组织方式
    template: str = Field(default="points", description="points / timeline / compare / qa")


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
            payload, fmt=req.format, title=title,
            include_excluded=req.includeExcluded, template=req.template,
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


# ════════════════════════════════════════════════════════════════
# 第四轮增强：B / C / D / E / A 五组的接口
# ════════════════════════════════════════════════════════════════
# 这一段里所有接口共享两条约定：
#   ① **需要正文才准的判据（D1 排版 / D3 文风 / D5 数字 / D2 事件时间）
#      都接受调用方传进来的 `texts: {url: 正文}`**，自己不去抓 ——
#      抓正文是 `/web/read` 的事，混在一起会让一次「判个真假」的调用
#      悄悄发出十几个网络请求，用户完全预料不到。
#   ② 拿不到正文时一律降级并**在返回里说清楚降级了**，不静默跳过。
# ════════════════════════════════════════════════════════════════


# ── B1 首字节竞速（SSE 流式）────────────────────────────────
@router.post("/web/search/stream")
async def web_search_stream(req: WebSearchRequest, request: Request) -> Any:
    """
    B1 —— 哪家引擎先回哪家先画。返回 `text/event-stream`。

    每个事件是一行 `data: {...}\\n\\n`，`kind` 取值 engines / partial / final。

    🔴 **用 SSE 不用 WebSocket**：`/events` 那条 WebSocket 是**全局广播**，
    多个窗口开着时每个都会收到别人的搜索结果。搜索是一次请求对应一条流，
    SSE 天然就是这个形状，而且断线重连的语义比 WebSocket 简单得多。
    """
    from fastapi.responses import StreamingResponse

    from ..websearch.presets import apply_preset

    web = _web(request)
    q, preset = apply_preset(req.query, req.preset)

    async def gen() -> Any:
        import json as _json
        try:
            async for ev in web.search_stream(
                q, engines=req.engines, limit=req.limit, lang=req.lang,
                region=req.region, time_range=req.timeRange, use_cache=req.useCache,
            ):
                if preset and ev.get("kind") == "engines":
                    ev["appliedPreset"] = preset.to_dict()
                    ev["effectiveQuery"] = q
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            # 流里出错必须**发一个事件出去**再结束。直接断流的话前端只会看到
            # 连接关闭，分不清是搜完了还是崩了 —— 那正是静默失败的形状
            log.exception("流式搜索出错")
            err = {"kind": "error", "error": f"{type(exc).__name__}: {exc}"}
            yield f"data: {_json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",      # 防止反代把流缓冲成一次性响应
    })


# ── B4 缓存 ────────────────────────────────────────────────
class PrewarmRequest(BaseModel):
    queries: list[str] = Field(default_factory=list, max_length=8)
    limit: int = Field(default=20, ge=1, le=50)


@router.get("/web/cache")
async def web_cache_stats(request: Request) -> dict[str, Any]:
    return _web(request).cache_stats()


@router.post("/web/prewarm")
async def web_prewarm(req: PrewarmRequest, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    if not getattr(rt.config, "allow_network", True):
        return {"warmed": 0, "errors": [], "note": "联网已关闭，没有预热"}
    return await _web(request).prewarm(req.queries, limit=req.limit)


# ── B5 意图分流 ────────────────────────────────────────────
class IntentRequest(BaseModel):
    query: str


@router.post("/web/intent")
async def web_intent(req: IntentRequest) -> dict[str, Any]:
    """判一句查询的意图，并给出它会怎么改阵容。**纯本地，不发请求。**"""
    from ..websearch.intent import detect

    return detect(req.query).to_dict()


@router.get("/web/intent/describe")
async def web_intent_describe() -> dict[str, Any]:
    from ..websearch.intent import describe

    return {"intents": describe()}


# ── B7 站点独立性 ──────────────────────────────────────────
class ClustersRequest(BaseModel):
    """好几个接口都只要一批已经搜到的结果，共用这一个模型。"""

    results: list[dict[str, Any]] = Field(default_factory=list)
    texts: dict[str, str] = Field(default_factory=dict)


@router.post("/web/independence")
async def web_independence(req: ClustersRequest) -> dict[str, Any]:
    from ..websearch.meta import site_independence

    return site_independence(req.results)


# ── D1 / D7 内容农场与利益相关 ──────────────────────────────
@router.post("/web/farm")
async def web_farm(req: ClustersRequest) -> dict[str, Any]:
    from ..websearch.farm import annotate, summarize

    flat = [
        {**(c.get("best") or c), "published": (c.get("best") or c).get("published")}
        for c in req.results
    ]
    annotate(flat)
    return {"items": flat, "summary": summarize(flat)}


# ── D2 时间线冲突 ──────────────────────────────────────────
@router.post("/web/timeline-conflicts")
async def web_timeline_conflicts(req: ClustersRequest) -> dict[str, Any]:
    from ..websearch.timeline import detect_conflicts

    flat = [dict(c.get("best") or c) for c in req.results]
    return detect_conflicts(flat, req.texts)


# ── D3 AI 文风标注 ─────────────────────────────────────────
@router.post("/web/ai-style")
async def web_ai_style(req: ClustersRequest) -> dict[str, Any]:
    from ..websearch.aidetect import annotate

    flat = [dict(c.get("best") or c) for c in req.results]
    annotate(flat, req.texts)
    scored = [e for e in flat if e.get("aiStyle")]
    return {
        "items": flat,
        "analyzed": len(scored),
        "skipped": len(flat) - len(scored),
        "note": "只对真的抓到正文的条目跑。摘要太短，在上面算这些统计量全是噪声",
    }


# ── D5 数字回原文校对 ──────────────────────────────────────
class NumberCheckRequest(BaseModel):
    briefing: dict[str, Any] = Field(default_factory=dict)
    texts: dict[str, str] = Field(default_factory=dict)
    maxNumbers: int = Field(default=30, ge=1, le=120)


@router.post("/web/numbers")
async def web_numbers(req: NumberCheckRequest) -> dict[str, Any]:
    from ..websearch.numbers import verify_briefing

    return verify_briefing(req.briefing, req.texts, max_numbers=req.maxNumbers)


# ── D6 争议度 ──────────────────────────────────────────────
class ControversyRequest(BaseModel):
    verification: dict[str, Any] = Field(default_factory=dict)


@router.post("/web/controversy")
async def web_controversy(req: ControversyRequest) -> dict[str, Any]:
    from ..websearch.timeline import annotate_controversy

    return annotate_controversy(dict(req.verification))


# ── B8 把搜到的网页存进本地库 ───────────────────────────────
class IngestResultsRequest(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] | None = None


@router.post("/web/ingest-results")
async def web_ingest_results(req: IngestResultsRequest, request: Request) -> dict[str, Any]:
    """
    B8 —— 把这次搜到的网页抓正文入库，以后**离线也能搜到**。

    🔴 **上限 30 条且逐条报结果**。一键把两百条网页全存进来听起来很爽，
    实际是往库里灌一堆再也不会看的东西，还把语义检索的信噪比拉低。
    """
    rt = _rt(request)
    done: list[dict[str, Any]] = []
    for url in req.urls[:30]:
        try:
            item_id = await asyncio.to_thread(
                rt.pipeline.ingest_url, url, tags=(req.tags or ["网页存档"])
            )
            done.append({"url": url, "itemId": item_id, "status": "ok"})
        except Exception as exc:  # noqa: BLE001
            done.append({"url": url, "status": "failed", "error": str(exc)})
    ok = sum(1 for d in done if d["status"] == "ok")
    return {
        "items": done, "ok": ok, "failed": len(done) - ok,
        "note": f"入库 {ok} 条，失败 {len(done) - ok} 条。失败的都写了原因，没有静默跳过",
    }


# ── C8 结果聚类 ────────────────────────────────────────────
class ClusterRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)
    maxClusters: int = Field(default=8, ge=2, le=20)


@router.post("/scholar/cluster")
async def scholar_cluster(req: ClusterRequest) -> dict[str, Any]:
    from ..websearch.cluster import cluster_entries

    return cluster_entries(req.entries, max_clusters=req.maxClusters)


# ── C3 引用网络 ────────────────────────────────────────────
class CitationGraphRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)
    direction: str = Field(default="both", description="back / forward / both")
    maxSeeds: int = Field(default=20, ge=1, le=40)
    topN: int = Field(default=15, ge=3, le=40)


@router.post("/scholar/citations")
async def scholar_citations(req: CitationGraphRequest, request: Request) -> dict[str, Any]:
    from ..websearch.citations import build_graph

    if not getattr(_rt(request).config, "allow_network", True):
        raise HTTPException(status_code=409, detail="联网已关闭，引用图谱要访问 OpenAlex")
    return await build_graph(
        req.entries, direction=req.direction,
        max_seeds=req.maxSeeds, top_n=req.topN,
    )


# ── C4 综述 ｜ C5 对齐抽表 ─────────────────────────────────
class ReviewRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)
    topic: str = ""
    maxSections: int = Field(default=6, ge=2, le=12)


@router.post("/scholar/review")
async def scholar_review(req: ReviewRequest) -> dict[str, Any]:
    from ..websearch.review import build_review

    return build_review(req.entries, topic=req.topic, max_sections=req.maxSections)


class AlignTableRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[str] | None = None
    extra: dict[str, str] | None = None
    format: str = Field(default="json", description="json / csv")


@router.post("/scholar/table")
async def scholar_table(req: AlignTableRequest) -> Any:
    from fastapi.responses import Response

    from ..websearch.review import align_table, table_to_csv

    table = align_table(req.entries, metrics=req.metrics, extra=req.extra)
    if req.format == "csv":
        # BOM 是给 Excel 的：不带的话中文列名在 Excel 里全是乱码，
        # 而用户拿到 csv 十有八九就是要用 Excel 打开
        body = "﻿" + table_to_csv(table)
        return Response(
            content=body.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="synorive-table.csv"'},
        )
    return table


# ── C9 引用格式导出 ────────────────────────────────────────
class CitationExportRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)
    format: str = Field(default="bibtex", description="bibtex / gbt7714")


@router.post("/scholar/citations/export")
async def scholar_citation_export(req: CitationExportRequest) -> Any:
    from fastapi.responses import Response

    from ..websearch.scholar import to_bibtex, to_gbt7714

    if req.format == "gbt7714":
        body, name, mime = to_gbt7714(req.entries), "references.txt", "text/plain"
    else:
        body, name, mime = to_bibtex(req.entries), "references.bib", "application/x-bibtex"
    return Response(
        content=("﻿" + body).encode("utf-8"),
        media_type=f"{mime}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ── C6 预印本合并 ──────────────────────────────────────────
@router.post("/scholar/merge-preprints")
async def scholar_merge_preprints(req: ClusterRequest) -> dict[str, Any]:
    from ..websearch.scholar import merge_preprints

    before = len(req.entries)
    out = merge_preprints(list(req.entries))
    return {
        "entries": out, "before": before, "after": len(out),
        "merged": before - len(out),
        "note": f"{before} 篇合并掉 {before - len(out)} 篇重复的预印本。"
                "**只有标题归一化后完全相同才合并** —— 用相似度阈值会误合并，"
                "而误合并用户根本看不出来",
    }


# ── C2 批量 PDF ────────────────────────────────────────────
class HarvestRequest(BaseModel):
    entries: list[dict[str, Any]] = Field(default_factory=list)
    apply: bool = Field(default=False, description="false = 干跑，只告诉你打算下什么")
    ingest: bool = True
    tags: list[str] | None = None
    limit: int = Field(default=50, ge=1, le=50)


@router.post("/scholar/harvest")
async def scholar_harvest(req: HarvestRequest, request: Request) -> dict[str, Any]:
    """
    C2 —— 批量下载开放获取 PDF 并入库。

    **默认干跑**（`apply=false`）：先告诉你打算下几篇、大概多大，
    点头了才真下。和 `setup-searxng.mjs` 同一个规矩。
    """
    from ..websearch.harvest import harvest, plan

    rt = _rt(request)
    if not req.apply:
        return {**plan(req.entries, limit=req.limit), "dryRun": True}
    if not getattr(rt.config, "allow_network", True):
        raise HTTPException(status_code=409, detail="联网已关闭，没法下载 PDF")

    out_dir = Path(rt.config.data_dir) / "papers"

    def _progress(done: int, total: int, item: dict[str, Any]) -> None:
        rt.events.publish("harvest.progress", {
            "done": done, "total": total, "item": item,
        })

    return await harvest(
        req.entries, out_dir=out_dir,
        pipeline=(rt.pipeline if req.ingest else None),
        tags=req.tags, limit=req.limit, on_progress=_progress,
    )


# ── C7 主题订阅 ────────────────────────────────────────────
def _watches(request: Request) -> Any:
    """
    订阅仓库。**挂在 runtime 上缓存一个实例** —— 它持有内存里的
    `seen` 集合，每次现建会把去重状态丢掉，结果是每次跑订阅
    所有结果都被当成"新的"，这个功能就彻底失效了（而且不报错）。
    """
    rt = _rt(request)
    inst = getattr(rt, "_watch_store", None)
    if inst is None:
        from ..websearch.harvest import WatchStore

        inst = WatchStore(Path(rt.config.data_dir) / "watches.json")
        rt._watch_store = inst           # noqa: SLF001
    return inst


class WatchCreateRequest(BaseModel):
    query: str
    label: str = ""
    engines: list[str] = Field(default_factory=list)
    preset: str | None = None
    intervalHours: int = Field(default=24, ge=1, le=720)
    autoIngest: bool = False


@router.get("/watches")
async def list_watches(request: Request) -> dict[str, Any]:
    ws = _watches(request)
    return {"watches": [w.to_dict() for w in ws.watches.values()],
            "due": [w.id for w in ws.due()]}


@router.post("/watches")
async def create_watch(req: WatchCreateRequest, request: Request) -> dict[str, Any]:
    ws = _watches(request)
    w = ws.add(
        query=req.query, label=req.label, engines=req.engines,
        preset=req.preset, interval_hours=req.intervalHours,
        auto_ingest=req.autoIngest,
    )
    return w.to_dict()


@router.delete("/watches/{wid}")
async def delete_watch(wid: str, request: Request) -> dict[str, Any]:
    return {"deleted": _watches(request).remove(wid)}


@router.post("/watches/{wid}/run")
async def run_watch(wid: str, request: Request) -> dict[str, Any]:
    rt = _rt(request)
    ws = _watches(request)
    w = ws.watches.get(wid)
    if w is None:
        raise HTTPException(status_code=404, detail="没有这条订阅")
    if not getattr(rt.config, "allow_network", True):
        raise HTTPException(status_code=409, detail="联网已关闭")
    return await ws.run_one(w, _web(request), pipeline=rt.pipeline)


@router.post("/watches/run-due")
async def run_due_watches(request: Request) -> dict[str, Any]:
    """把所有到点的订阅跑一遍。**串行跑** —— 后台白工不跟前台抢带宽。"""
    rt = _rt(request)
    if not getattr(rt.config, "allow_network", True):
        return {"ran": 0, "note": "联网已关闭，没有跑"}
    ws = _watches(request)
    out = []
    for w in ws.due():
        try:
            out.append(await ws.run_one(w, _web(request), pipeline=rt.pipeline))
        except Exception as exc:  # noqa: BLE001
            out.append({"watchId": w.id, "error": str(exc)})
    return {"ran": len(out), "results": out}


# ── E2 长期记忆 ｜ E4 差异复读 ─────────────────────────────
def _memory(request: Request) -> Any:
    from ..websearch.memory import MemoryStore

    return MemoryStore(_rt(request).db)


class RememberRequest(BaseModel):
    topic: str
    briefing: dict[str, Any] = Field(default_factory=dict)
    clusters: list[dict[str, Any]] = Field(default_factory=list)
    controversy: int | None = None


@router.post("/memory/remember")
async def memory_remember(req: RememberRequest, request: Request) -> dict[str, Any]:
    return _memory(request).remember(
        req.topic, req.briefing, clusters=req.clusters, controversy=req.controversy
    )


@router.get("/memory/recall")
async def memory_recall(request: Request, topic: str, limit: int = 30) -> dict[str, Any]:
    return _memory(request).recall(topic, limit=max(1, min(200, limit)))


@router.get("/memory/site")
async def memory_site(request: Request, site: str) -> dict[str, Any]:
    return _memory(request).site_history(site)


@router.get("/memory/stats")
async def memory_stats(request: Request) -> dict[str, Any]:
    return _memory(request).stats()


@router.delete("/memory/topic")
async def memory_forget(request: Request, topic: str) -> dict[str, Any]:
    return {"deleted": _memory(request).forget(topic)}


class DiffRunsRequest(BaseModel):
    old: dict[str, Any] = Field(default_factory=dict)
    new: dict[str, Any] = Field(default_factory=dict)


@router.post("/memory/diff")
async def memory_diff(req: DiffRunsRequest) -> dict[str, Any]:
    from ..websearch.memory import diff_runs

    return diff_runs(req.old, req.new)


# ── A5 文件比对 ────────────────────────────────────────────
class CompareRequest(BaseModel):
    a: str
    b: str


@router.post("/compare/files")
async def compare_files_route(req: CompareRequest) -> dict[str, Any]:
    from ..analyze.compare import compare_files

    # 比对是纯 CPU 活（大文件 diff 能跑几秒），扔线程池 ——
    # 占住事件循环会让 WebSocket 心跳断掉，界面以为引擎挂了
    return await asyncio.to_thread(compare_files, req.a, req.b)


class CompareItemsRequest(BaseModel):
    aItemId: str
    bItemId: str


@router.post("/compare/videos")
async def compare_videos_route(req: CompareItemsRequest, request: Request) -> dict[str, Any]:
    from ..analyze.compare import compare_videos

    rt = _rt(request)
    a = rt.repo.scenes_of(req.aItemId)
    b = rt.repo.scenes_of(req.bItemId)
    return await asyncio.to_thread(compare_videos, a, b)


# ── A6 章节化 ──────────────────────────────────────────────
@router.get("/items/{item_id}/chapters")
async def item_chapters(item_id: str, request: Request, maxChapters: int = 30) -> dict[str, Any]:
    """
    A6 —— 给一个视频/音频出章节目录。

    **每次现算不缓存**：算一遍是毫秒级（数据全在库里），
    而缓存就要处理"转写补跑完了章节要不要重算"这个问题 ——
    不重算的话用户会看到一份基于空转写生成的等分目录，永远不更新。
    """
    from ..analyze.chapters import build_chapters

    rt = _rt(request)
    row = rt.repo.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有这个条目")
    scenes = rt.repo.scenes_of(item_id)

    # meta 在库里是一段 JSON 文本。**取不出来就当空的继续**，
    # 而不是 404 —— 没有时长只会让章节退回等分，功能仍然可用
    meta: dict[str, Any] = {}
    try:
        import json as _json
        meta = _json.loads(row["meta_json"] or "{}") or {}
    except (ValueError, TypeError, KeyError, IndexError):
        meta = {}

    transcript = [
        {"start_sec": s.get("startSec"), "end_sec": s.get("endSec"),
         "text": s.get("transcript") or ""}
        for s in scenes if (s.get("transcript") or "").strip()
    ]
    return build_chapters(
        scenes, transcript,
        duration_sec=float(meta.get("durationSec") or 0),
        max_chapters=max(2, min(60, maxChapters)),
    )


# ── D4 图片篡改初筛 ────────────────────────────────────────
class TamperRequest(BaseModel):
    paths: list[str] = Field(default_factory=list, max_length=200)
    earliestSeen: str = ""


@router.post("/images/tamper")
async def images_tamper(req: TamperRequest) -> dict[str, Any]:
    from ..analyze.tamper import screen, screen_batch

    if len(req.paths) == 1:
        return await asyncio.to_thread(
            lambda: screen(req.paths[0], earliest_seen=req.earliestSeen).to_dict()
        )
    return await asyncio.to_thread(screen_batch, list(req.paths))


# ── E3 研究成果一键入本地库 ─────────────────────────────────
class SaveToLibraryRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None
    template: str = "points"
    tags: list[str] | None = None


@router.post("/research/save-to-library")
async def research_save_to_library(
    req: SaveToLibraryRequest, request: Request
) -> dict[str, Any]:
    """
    E3 —— 把一份研究简报存成本地条目，以后**本地搜索也能搜到它**。

    存的是 **Markdown 全文**而不是 JSON：JSON 存进去以后语义检索
    会把一堆字段名当成内容去向量化，搜「我上次研究的那个结论」
    永远搜不到。Markdown 是人读的形状，也是检索该看到的形状。
    """
    from ..websearch.export import export_research, safe_filename

    rt = _rt(request)
    title = req.title or str(req.payload.get("query") or "研究简报")
    content, ext, _mime = export_research(
        req.payload, fmt="markdown", title=title, template=req.template
    )
    out_dir = Path(rt.config.data_dir) / "briefings"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(title, ext)
    path.write_text(
        content if isinstance(content, str) else content.decode("utf-8"),
        encoding="utf-8",
    )
    item_id = await asyncio.to_thread(
        rt.pipeline.ingest_file, path, source="research",
        tags=(req.tags or ["研究简报"]),
    )
    return {
        "itemId": item_id, "path": str(path), "title": title,
        "note": "已存进本地库，以后用本地搜索也能搜到这份简报的内容",
    }


# ── G 组指标：目标值与当前实测并排 ──────────────────────────
@router.get("/metrics/budgets")
async def metrics_budgets(request: Request) -> dict[str, Any]:
    """
    G1~G9 —— 把**目标值**和**这次运行观察到的值**并排给出来。

    🔴 **观察值不等于基准测试结果**。这里报的是引擎跑起来之后
    自然积累的采样（缓存命中率、引擎耗时中位数…），采样量小的时候
    抖动很大。真正的达标判定要跑 `engine/tests/bench_*.py`，
    那是另一件事，**不要拿这个页面上的数字当验收证据**。
    """
    from ..metrics import BUDGETS, INGEST_BUDGETS, observe

    return {
        # `budgets` 保持只有 G 组 —— 界面和已有调用方按这个键取数，
        # 往里塞 A 组会让"九条指标"凭空变成十三条
        "budgets": [b.to_dict() for b in BUDGETS],
        # A 组（吞吐/时延）单独一个键。其中 A6/A7 的 target 现在是
        # 「⚠️ 待重定」而不是一个数字 —— 那是**故意的**：
        # 一个明知达不到的数字挂在那里，比承认"还没定"更糟
        "ingestBudgets": [b.to_dict() for b in INGEST_BUDGETS],
        "observed": observe(_rt(request)),
        "note": "目标值来自 G 组 / A 组验收标准；观察值是运行期采样，"
                "**样本少时抖动很大，不能当基准测试结果用**",
    }
