"""
Synorive 引擎入口
====================================================================
跑法：
    python -m synorive.main --port 8731 --data-dir D:\\...\\data

桌面端会自己挑一个空闲端口把它拉起来，并轮询 /health 等就绪。
命令行也能单独跑，方便调试和给 CLI/MCP 用。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api.routes import router
from .runtime import EngineConfig, Runtime

log = logging.getLogger("synorive")

#: A16 安卓配对闸放行的路径——不带令牌也能探测到"这是不是 Synorive"。
#: 🔴 **只放行 `/pairing/status` 这一个最小端点，`/health`/`/status` 不再免鉴权。**
#: 后两者会报 CPU/内存/DB 大小/索引条数/已装模型这些信息，局域网里随便一台没配对
#: 过的设备扫到端口就能读，超出了"配对前确认这是不是 Synorive"本身需要的范围。
_UNGUARDED_PATHS = {"/pairing/status"}


class _PairingGuardMiddleware:
    """
    A16 局域网配对闸。

    用裸 ASGI 中间件而不是 `@app.middleware("http")`——后者只包住 http scope，
    WebSocket 握手会直接绕过去，而 `/events` 推的内容（摄取进度、搜索分级结果）
    一样是要保护的数据，不能只挡 REST 这一半。

    本机（127.0.0.1，桌面端自己/MCP/CLI 全走这条）永远放行；局域网配对没开时
    引擎压根不监听 0.0.0.0，外部连接根本进不来，这道闸碰不到；配对开着时，
    非本机来源必须带匹配的令牌，没有的话直接拒绝——不然局域网里随便一台机器
    扫到端口就能读写整个资料库。
    """

    def __init__(self, app: Any, runtime: Runtime) -> None:
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = client[0] if client else ""
        if client_host in ("127.0.0.1", "::1", "localhost"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        token = self.runtime.config.pairing_token
        headers = dict(scope.get("headers") or [])
        given = headers.get(b"x-synorive-token", b"").decode("latin-1")

        # 🔴 **必须用 compare_digest，不能用 ==**（4.22b H3）。
        #    `==` 一遇到不同的字节就返回，比对耗时随"猜对了几位"变长——
        #    局域网里能反复重试的攻击者可以据此一位一位地把令牌试出来。
        #    这不是理论问题：令牌是 32 位十六进制，逐位爆破是 16×32 次，
        #    而盲爆破是 16^32 次。**代价差了 30 个数量级。**
        #    compare_digest 恒定时间返回，这条路直接没了。
        #    （`token` 为空时下面整个条件为假 → 401，是**失败关闭**，没问题。）
        ok_token = bool(token) and hmac.compare_digest(given, token)
        if path in _UNGUARDED_PATHS or ok_token:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
        else:
            resp = JSONResponse({"detail": "未配对：缺少或错误的 X-Synorive-Token"}, status_code=401)
            await resp(scope, receive, send)


def build_app(runtime: Runtime) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        runtime.attach_loop(asyncio.get_running_loop())
        log.info("引擎就绪 · 版本 %s · 数据目录 %s", __version__, runtime.config.data_dir)
        log.info("SQLite 能力：%s", runtime.db.capabilities)
        if runtime.db.capabilities.get("degraded"):
            log.warning("降级运行：%s", runtime.db.capabilities["degraded"])

        st = runtime.repo.stats()
        log.info("库里已有 %d 条内容 / %d 个分块", st["items"], st["chunks"])
        missing = [
            d["name"] for d in runtime.doctor.check_all(deep=False)
            if d["state"] != "ok" and not d["optional"]
        ]
        if missing:
            log.warning("必需依赖还缺：%s —— 界面上会提示一键安装", missing)

        # 写端口文件，MCP 服务器和 CLI 靠它找到这个引擎
        runtime.write_endpoint()

        # 模型后台预热，不挡启动（A1 冷启动 ≤2s）
        runtime.warmup_async()
        status_task = asyncio.create_task(runtime.status_loop())
        deferred_task = asyncio.create_task(runtime.deferred_jobs_loop())

        yield

        status_task.cancel()
        deferred_task.cancel()
        runtime.clear_endpoint()
        # A17：干净关闭时把 ANN 索引落盘——这样重启就能直接从磁盘加载，
        # 不用触发那条"发现落差就后台重建"的兜底路径（见 runtime.py
        # 的 _load_ann_index）。那条兜底是为异常退出准备的安全网，
        # 不是常态该走的路，正常关闭这里顺手存一次就不用每次都靠它
        if runtime.repo.ann_index is not None:
            try:
                runtime.repo.ann_index.save()
            except Exception as e:  # noqa: BLE001
                log.warning("ANN 索引落盘失败（不影响数据本身，下次会自动重建）：%s", e)
        log.info("引擎关闭，累计运行 %.1fs", runtime.uptime_sec)
        runtime.db.close()

    app = FastAPI(
        title="Synorive Engine",
        version=__version__,
        description="多模态并发分析与极速内容检索引擎",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    # CORS 只放行本机的浏览器场景（file:// 打包页面 / 本机调试）。
    # 这道闸对安卓端不起作用——CORS 是浏览器自己遵守的规矩，原生 App
    # 发请求根本不看这层，真正挡安卓端的是下面注册的 `_PairingGuardMiddleware`。
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(http://(127\.0\.0\.1|localhost)(:\d+)?|file://)$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(_PairingGuardMiddleware, runtime=runtime)

    app.include_router(router, prefix="/api")

    # ── 配对前探测：免鉴权，只报"这是不是 Synorive + 证书指纹" ──────
    @app.get("/pairing/status")
    async def pairing_status() -> dict[str, Any]:
        """
        A16 安卓配对页"测试连接"用这个，不是 `/health`。

        配对前手机还没有令牌，但需要两样东西：①确认这台机器真的是 Synorive
        ②TLS 开着的话，拿到证书指纹做手动核对（4.22b H1）。除此之外的内容
        （CPU、内存、DB 大小、装了哪些模型……）不属于"配对前需要确认"的范畴，
        那些字段留在 `/health`/`/status` 里，两者现在都要求配对令牌。
        """
        out: dict[str, Any] = {
            "ok": True,
            "version": __version__,
            "indexedItems": runtime.db.count_items(),
            "pairingRequired": bool(runtime.config.pairing_token),
        }
        out["lanTls"] = bool(runtime.config.lan_tls)
        if runtime.config.lan_tls:
            from .lan_tls import CERT_NAME, fingerprint

            cert = runtime.config.data_dir / CERT_NAME
            out["lanCertFingerprint"] = fingerprint(cert) if cert.exists() else None
            out["lanTlsNote"] = (
                "手机端要把这个指纹填进去做固定校验。"
                "**别让手机'第一次连上就信任'** —— 那样第一次就被劫持的话，之后每次都会信任攻击者。"
            )
        return out

    # ── 健康检查：桌面端靠它判断引擎起没起来（本机永远放行；局域网需要配对令牌） ──
    @app.get("/health")
    async def health() -> dict[str, Any]:
        cpu, mem = runtime.resource_usage()
        deps = runtime.doctor.check_all(deep=False) if runtime.doctor else []
        return {
            "ok": True,
            "version": __version__,
            "uptimeSec": round(runtime.uptime_sec, 1),
            "concurrency": runtime.config.concurrency,
            "cpuPercent": round(cpu, 1),
            "memoryMb": round(mem, 1),
            "queueDepth": 0,
            "activeJobs": sum(1 for j in runtime._jobs.values() if j.get("status") == "running"),
            "indexedItems": runtime.db.count_items(),
            "dbSizeMb": round(runtime.db.size_mb(), 2),
            "executionProvider": _execution_provider(),
            "cloudReady": runtime.config.allow_cloud,
            "modelsReady": [d["id"] for d in deps if d["state"] == "ok"],
            "modelsMissing": [d["id"] for d in deps if d["state"] != "ok" and not d["optional"]],
            "capabilities": runtime.db.capabilities,
        }

    @app.get("/status")
    async def status() -> dict[str, Any]:
        out = await health()
        # 4.22b H1：证书指纹要在**免鉴权**的路径上报出来 ——
        # 手机是在配对**之前**读它的，那时候还没有令牌。
        # 🔴 指纹不是秘密（它是公钥的哈希），公开它没有任何风险；
        #    真正重要的是用户**核对**它，而不是让手机"第一次连上就信任"。
        out["lanTls"] = bool(runtime.config.lan_tls)
        if runtime.config.lan_tls:
            from .lan_tls import CERT_NAME, fingerprint

            cert = runtime.config.data_dir / CERT_NAME
            out["lanCertFingerprint"] = fingerprint(cert) if cert.exists() else None
            out["lanTlsNote"] = (
                "手机端要把这个指纹填进去做固定校验。"
                "**别让手机'第一次连上就信任'** —— 那样第一次就被劫持的话，之后每次都会信任攻击者。"
            )
        return out

    # ── 实时事件通道 ────────────────────────────────────────
    @app.websocket("/events")
    async def events(ws: WebSocket) -> None:
        await ws.accept()
        q = await runtime.events.subscribe()
        try:
            # 连上先推一次当前状态，免得界面要等到下一次变化才有东西显示
            await ws.send_json({"type": "engine.status", "payload": await health()})
            while True:
                msg = await q.get()
                await ws.send_json(msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("事件通道异常关闭：%s", e)
        finally:
            await runtime.events.unsubscribe(q)

    return app


def _execution_provider() -> str:
    """当前推理执行器。装了 DirectML 版 onnxruntime 就用核显，否则 CPU。"""
    try:
        import onnxruntime as ort

        providers = ort.get_available_providers()
        if "DmlExecutionProvider" in providers:
            return "DirectML"
        if "CUDAExecutionProvider" in providers:
            return "CUDA"
        return "CPU"
    except Exception:
        return "unknown"


def parse_args(argv: list[str] | None = None) -> EngineConfig:
    p = argparse.ArgumentParser(prog="synorive-engine", description="Synorive 引擎")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8731)
    p.add_argument("--data-dir", type=Path, default=Path.cwd() / "data")
    p.add_argument("--model-dir", type=Path, default=None)
    p.add_argument(
        "--concurrency",
        type=int,
        default=max(1, (os.cpu_count() or 8) - 1),
        help="分析并发度，1~16",
    )
    p.add_argument("--allow-cloud", action="store_true")
    p.add_argument("--enable-image-description", action="store_true",
                    help="C4：允许调云端视觉模型给图片生成描述并入索引（还要 --allow-cloud 且配置好视觉模型）")
    p.add_argument("--enable-face-clustering", action="store_true",
                    help="C5：本地人脸检测与聚类，默认关（隐私敏感）")
    p.add_argument("--pairing-token", default=None,
                    help="A16：安卓配对令牌。设了之后，非本机地址的 /api 请求"
                         "必须带匹配的 X-Synorive-Token 头才放行")
    # 🔴 **默认关，而且必须默认关**（4.22b H1）。
    # 现在的明文配对是能用的、用户已经在用的功能；一个没法在开发机上
    # 端到端验证的 TLS 改造如果默认打开，最坏结果是"更新了一下手机连不上了"，
    # 而用户完全不知道为什么。安全改进不该以弄坏能用的功能为代价。
    p.add_argument("--lan-tls", action="store_true",
                   help="4.22b H1：局域网走 HTTPS（自签证书 + 手机端指纹固定）。"
                        "默认关 —— 开了之后手机端要改成 https:// 并填证书指纹，"
                        "指纹在 /status 里报出来")
    # ── 联网搜索这一路（E12/U9 · S1 · S3 · V5）────────────────
    # 🔴 用 `--no-network` 而不是 `--allow-network`：联网是这个软件的主要
    # 用途之一，默认必须是开的（不然装完发现半个功能是灰的）。
    # 而**关掉这件事必须是显式的一个参数** —— 靠"不传就是关"的话，
    # 桌面端哪天忘了传，用户的隐私闸就被静默打开了
    p.add_argument("--no-network", action="store_true",
                   help="E12：完全关掉联网搜索。注意它和 --allow-cloud 是两回事："
                        "这个管的是把**查询词**发出去，那个管的是把**你的资料原文**发出去")
    p.add_argument("--web-lineup", type=int, default=0,
                   help="S1：每轮最多派几家引擎（按最近表现排班 + 一个探索位）。0 = 全派")
    p.add_argument("--verify-level", default="counter",
                   choices=("annotate", "counter", "claim"),
                   help="V 组核查档位：只标注 / 反向检索（默认）/ 断言级逐句核查")
    p.add_argument("--web-engines", default="",
                   help="启用哪几家引擎，逗号分隔。空 = 用各家自带的默认开关")
    p.add_argument("--web-key", action="append", default=[], metavar="ID=VALUE",
                   help="S3：引擎的 Key 或地址，如 serper=xxx、searxng=http://127.0.0.1:8888。"
                        "可以重复传多次")
    p.add_argument("--trust-profile", default="",
                   help="V5：可信度权重的 JSON 串。空 = 用默认档")
    p.add_argument("--prefer-gpu", action="store_true",
                   help="E15：优先用核显（DirectML）跑推理。装了 onnxruntime-directml 才有效；"
                        "拿不到核显会自动退回 CPU，不报错")
    p.add_argument("--log-level", default="info")
    a = p.parse_args(argv)

    # `--web-key serper=abc` → {"serper": "abc"}。
    # 用 split("=", 1) 而不是 split("=")：SearXNG 的地址里可能带查询参数，
    # 里面就有等号，切多了会把地址切断
    web_keys: dict[str, str] = {}
    for pair in a.web_key or []:
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip() and v.strip():
                web_keys[k.strip()] = v.strip()

    trust_profile: dict[str, Any] | None = None
    if a.trust_profile:
        try:
            got = json.loads(a.trust_profile)
            if isinstance(got, dict):
                trust_profile = got
        except (TypeError, ValueError):
            # 配置串坏了就用默认档 —— 让引擎因为一个可选的权重配置起不来，
            # 是把小问题放大成大问题
            log.warning("--trust-profile 不是合法 JSON，本次用默认可信度档")

    data_dir = a.data_dir.resolve()
    return EngineConfig(
        host=a.host,
        port=a.port,
        data_dir=data_dir,
        model_dir=(a.model_dir.resolve() if a.model_dir else data_dir / "models"),
        concurrency=max(1, min(16, a.concurrency)),
        allow_cloud=a.allow_cloud,
        allow_network=not a.no_network,
        enable_image_description=a.enable_image_description,
        enable_face_clustering=a.enable_face_clustering,
        pairing_token=a.pairing_token,
        lan_tls=bool(a.lan_tls),
        web_engines=[s.strip() for s in a.web_engines.split(",") if s.strip()] or None,
        web_keys=web_keys or None,
        web_lineup_size=max(0, a.web_lineup),
        verify_level=a.verify_level,
        trust_profile=trust_profile,
        prefer_gpu=a.prefer_gpu,
    )


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认按 GBK 解码，中文日志会变成乱码。
    # 桌面端拉起时会设 PYTHONIOENCODING，但直接跑命令行时没人设，
    # 所以这里自己强制一次 —— 日志看不懂等于没有日志。
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    config = parse_args(argv)
    runtime = Runtime(config)

    try:
        runtime.initialize()
    except Exception as e:  # noqa: BLE001
        # 建库失败是致命的，但要把原因说清楚 —— 用户看到的是这句话
        log.error("引擎初始化失败：%s", e)
        return 2

    app = build_app(runtime)

    # 4.22b H1 局域网 TLS。**拿不到证书就退回明文并大声说出来** ——
    # 静默地"以为开了 TLS 其实是明文"，比根本没这个功能危险得多
    ssl_kw: dict[str, Any] = {}
    if config.lan_tls:
        from .lan_tls import ensure_cert, fingerprint

        pair = ensure_cert(config.data_dir)
        if pair is None:
            log.error(
                "🔴 --lan-tls 开着但证书没弄出来，**本次是明文 HTTP**。"
                "手机端如果按 https 配的会连不上 —— 这是故意让你看见的，"
                "不是悄悄降级。"
            )
        else:
            cert, key = pair
            ssl_kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
            fp = fingerprint(cert)
            log.info("局域网 TLS 已启用。手机端要固定的证书指纹：%s", fp or "(读不出来)")
            log.info("指纹也可以从 http(s)://<本机IP>:%d/status 读到", config.port)

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="warning",  # uvicorn 自己的日志太吵，我们有自己的
        access_log=False,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        **ssl_kw,
    )
    return 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
