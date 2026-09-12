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
import re
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

#: 引擎源码完整性自检结果。**模块加载时算一次**，不是每次 /status 都算 ——
#: 77 个文件的 SHA-256 大约十几毫秒，放进请求路径上是白白的浪费。
from .integrity import check as _integrity_check  # noqa: E402

_INTEGRITY = _integrity_check()

#: A16 安卓配对闸放行的路径——不带令牌也能探测到"这是不是 Synorive"。
#: 🔴 **只放行 `/pairing/status` 这一个最小端点，`/health`/`/status` 不再免鉴权。**
#: 后两者会报 CPU/内存/DB 大小/索引条数/已装模型这些信息，局域网里随便一台没配对
#: 过的设备扫到端口就能读，超出了"配对前确认这是不是 Synorive"本身需要的范围。
_UNGUARDED_PATHS = {"/pairing/status"}

#: S5：这次请求是不是来自**非本机**。`_PairingGuardMiddleware` 写进 ASGI scope，
#: 路由层用 `routes._is_remote()` 读。
#:
#: 🔴 **判定只在这一处做。** 路由层自己去看 `request.client.host` 是行不通的：
#:    反代/测试客户端下那个字段千奇百怪（TestClient 是 "testclient"），
#:    判错的方向是"把本机当成远程"，那会直接砍掉桌面端的主功能。
#:    scope 里没有这个键 = 没经过这道闸 = 按本机处理，和改动前完全一样。
REMOTE_SCOPE_KEY = "synorive_remote"


def dev_mode() -> bool:
    """
    M1/M18：现在跑的是不是**开发模式**。

    打包版和开发版在两件事上必须不一样：
      ① CORS：开发时渲染层跑在 `http://localhost:5173`（Vite），打包后是 `file://`。
      ② `/docs` `/openapi.json`：那是 150 条接口的完整签名表。本机零鉴权，
         把它常开等于给"本机任意网页/任意进程"发了一份攻击说明书。

    判据两条，任意一条成立就算开发模式：
      · `SYNORIVE_DEV=1`（显式，命令行调试用）
      · 环境里有 `ELECTRON_RENDERER_URL`（electron-vite 开发模式会设它，
        引擎是 Electron 主进程 spawn 的、继承整个 env，所以拿得到）
    第二条是为了**不用改 apps/ 就能让现有的开发流程照常工作** ——
    只加显式变量的话，谁都不会记得设，开发模式第二天就坏了。
    """
    if os.environ.get("SYNORIVE_DEV", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return bool(os.environ.get("ELECTRON_RENDERER_URL", "").strip())


#: M1 打包版放行的浏览器来源。**只有 file://** —— 渲染层是 `loadFile` 起来的。
#: 原来这里连本机任意端口都放行（`http://127.0.0.1:任意端口`），意味着
#: **这台机器上跑着的任何一个网页**（Vite dev server、Jupyter、某个有 XSS 的
#: 本地服务）都能跨源把整个资料库读走 —— 而引擎对本机是完全信任的。
_ORIGIN_RE_PROD = r"^file://$"
#: 开发模式额外放行本机端口（Vite 是 5173，但端口会变，所以按段放行）。
_ORIGIN_RE_DEV = r"^(http://(127\.0\.0\.1|localhost)(:\d+)?|file://)$"


class _OriginGuardMiddleware:
    """
    M2 跨站请求闸 —— 挡"恶意网页用表单 POST 打本机接口"。

    CORS 只管**读不读得到响应**，不管**请求发不发得出去**。所以一个
    `<form action="http://127.0.0.1:8731/api/security/db/encrypt" method="post">`
    照样会真的执行 —— 攻击者读不到返回值，但副作用已经发生了。
    `/api/security/db/encrypt` 那条尤其严重：它只校验口令 ≥8 位、
    不校验库当前是什么状态，等于**用攻击者的口令把整库锁死**。

    两条规则，都刻意做成"宁可放行也不误伤"：

    1. 非 GET 请求**带**了 Origin，而且既不是 `null` 也不在白名单里 → 403。
       **不带 Origin 的照常放行** —— CLI、MCP、安卓客户端、curl 都不带，
       按"没有 Origin 就拒绝"写的话，等于把非浏览器调用方全部弄坏。

    2. 非 GET 且 `Sec-Fetch-Mode: navigate` → 403。这正是"表单直接提交"
       的特征；浏览器的 fetch/XHR 永远是 `cors`/`same-origin`/`no-cors`，
       非浏览器客户端根本不发这个头。

    ⚠️ **说清楚剩下的口子**：`Origin: null`（sandbox iframe、data: 页面）里
       发出的 **fetch** 仍然能过第 1 条。放行 `null` 不是疏忽，是因为
       file:// 页面在部分 Chromium 版本里发出的就是 `Origin: null` ——
       拒掉它有把整个桌面端打死的实际风险。第 2 条能挡住 sandbox 里的
       **表单**提交，挡不住 sandbox 里的 fetch。这是已知残留，不是"应该没问题"。
    """

    def __init__(self, app: Any, origin_re: str) -> None:
        self.app = app
        self._re = re.compile(origin_re)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method", "GET") in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        origin = headers.get(b"origin", b"").decode("latin-1").strip()
        mode = headers.get(b"sec-fetch-mode", b"").decode("latin-1").strip().lower()

        bad_origin = bool(origin) and origin != "null" and not self._re.match(origin)
        form_navigation = mode == "navigate"
        if bad_origin or form_navigation:
            why = (
                f"跨站来源 {origin} 不在白名单里"
                if bad_origin
                else "这是一次页面级表单提交（Sec-Fetch-Mode: navigate），不是程序调用"
            )
            resp = JSONResponse(
                {"detail": f"拒绝：{why}。Synorive 的接口只给本机的桌面端/CLI/MCP 用。"},
                status_code=403,
            )
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)


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
            scope[REMOTE_SCOPE_KEY] = False
            await self.app(scope, receive, send)
            return

        # S5：从这里往下都是"非本机"。放行与否下面再判，但**是不是远程这件事
        # 现在就要写进 scope** —— 路由层要靠它决定"这个字符串能不能当本机路径用"
        scope[REMOTE_SCOPE_KEY] = True

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
        if runtime.watcher is not None:
            runtime.watcher.stop()
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

    dev = dev_mode()

    # M18：`/docs` 和 `/openapi.json` **打包版关掉**。
    # 它们把 150 条接口的完整签名（路径、方法、请求体字段）端出来，而本机
    # 调用是零鉴权的 —— 等于把"本机任意网页/任意进程能干什么"的探索成本降到零。
    # 关掉不影响任何正常功能：桌面端/安卓端/CLI/MCP 走的都是写死的路径，
    # 没有一个是靠读 OpenAPI 才知道该调什么的。
    # 开发和调试要用就设 `SYNORIVE_DEV=1`（`engine/tests/test_web_api.py`
    # 里那段"接口有没有真的挂上路由"的自查需要它）。
    app = FastAPI(
        title="Synorive Engine",
        version=__version__,
        description="多模态并发分析与极速内容检索引擎",
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    origin_re = _ORIGIN_RE_DEV if dev else _ORIGIN_RE_PROD
    if dev:
        log.warning(
            "开发模式：CORS 放行本机任意端口，/docs 和 /openapi.json 是开着的。"
            "打包版两样都会收紧 —— 这行日志出现在正式版里就是配置错了"
        )

    # CORS 只放行本机的浏览器场景（打包版 = 只有 file://；开发版 = 再加本机端口）。
    # 这道闸对安卓端不起作用——CORS 是浏览器自己遵守的规矩，原生 App
    # 发请求根本不看这层，真正挡安卓端的是下面注册的 `_PairingGuardMiddleware`。
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=origin_re,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # M2 跨站写操作闸。**加在 CORS 之前 = 跑在 CORS 外面**（Starlette 里后加的
    # 在外层），所以连预检 OPTIONS 都过它一道，恶意来源连预检都拿不到。
    app.add_middleware(_OriginGuardMiddleware, origin_re=origin_re)

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
        out["integrity"] = _INTEGRITY.to_dict()
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
            "queueDepth": sum(1 for j in runtime._jobs.values() if j.get("status") == "queued"),
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
        out["integrity"] = _INTEGRITY.to_dict()
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
    # S6-B：默认只允许把简报发给白名单里的大模型厂商。自建/中转端点要显式开。
    # 也认环境变量 SYNORIVE_ALLOW_CUSTOM_CLOUD_ENDPOINT=1（设置页那个开关走它）
    p.add_argument("--allow-custom-cloud-endpoint", action="store_true",
                   help="S6-B：允许把云端通道的 baseUrl 指向白名单之外的自建/中转地址。"
                        "默认关 —— 开着的话一个被篡改的 baseUrl 就能把整份研究简报"
                        "发到攻击者的服务器上，而界面上一切正常")
    p.add_argument("--enable-image-description", action="store_true",
                    help="C4：允许调云端视觉模型给图片生成描述并入索引（还要 --allow-cloud 且配置好视觉模型）")
    p.add_argument("--enable-face-clustering", action="store_true",
                    help="C5：本地人脸检测与聚类，默认关（隐私敏感）")
    # 跟 --no-network 一样：默认开的隐私/安全类开关，关掉必须显式传参数，
    # 不能靠"不传就是关"——那样以后哪次忘了传，这道闸就静默消失了
    p.add_argument("--disable-sensitive-guard", action="store_true",
                    help="关掉投喂目录时的敏感文件（.env/私钥/凭据）自动跳过。默认这道闸是开着的")
    # B6：跟上面几个"默认开、关掉要显式传参"的开关同一套纪律——批量摄取
    # 让路给前台搜索这件事应该是默认姿态，不该需要用户自己找到开关去开
    p.add_argument("--disable-background-priority", action="store_true",
                    help="B6：关掉批量摄取/分析线程的 Windows 后台优先级调低"
                         "（SetThreadPriority THREAD_MODE_BACKGROUND_BEGIN）。"
                         "默认开着——系统繁忙时批量摄取自动让路给正在处理的搜索请求。"
                         "非 Windows 平台本来就是空操作。只在怀疑优先级调整"
                         "导致某些机器上摄取异常慢时才关掉排查")
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

    # ── 密钥改走环境变量（argv 全机可见）────────────────────────
    #
    # 🔴 `--pairing-token xxx` 和 `--web-key brave=xxx` 会**明文出现在进程命令行里**。
    #    Windows 任务管理器加一列"命令行"、或者一句 `Get-CimInstance Win32_Process`
    #    就能看到 —— 同一台机器上任何一个普通权限的进程都读得到。
    #    `SYNORIVE_DB_KEY` 早就因为这个理由走了环境变量，这两个只是漏了。
    #
    # **环境变量优先于 argv**：桌面端在兼容期里两条都传，argv 那份是回退。
    # 等它把 `ENGINE_ARGV_SECRET_COMPAT` 关掉之后，命令行泄露才算真的没了。
    # argv 参数**保留不删** —— 现在就删会让局域网配对和付费搜索静默失效。
    env_token = os.environ.pop("SYNORIVE_PAIRING_TOKEN", "").strip()
    pairing_token = env_token or a.pairing_token

    raw_web_keys = os.environ.pop("SYNORIVE_WEB_KEYS", "").strip()
    if raw_web_keys:
        # 🔴 解析失败**不许静默吞掉**。这里悄悄吞掉的后果是"付费搜索 Key 没生效"，
        #    而表现是搜索结果变少 —— 用户永远不会把它和一个 JSON 语法错误联系起来。
        #    所以：说清是哪个变量、错在哪、这次改用什么，然后退回 argv。
        try:
            got = json.loads(raw_web_keys)
            if not isinstance(got, dict):
                raise TypeError(f"顶层必须是 JSON 对象，收到的是 {type(got).__name__}")
            parsed = {
                str(k).strip(): str(v).strip()
                for k, v in got.items()
                if str(k).strip() and str(v).strip()
            }
            if parsed:
                web_keys = parsed
            else:
                log.warning(
                    "环境变量 SYNORIVE_WEB_KEYS 解析出来是空的（键或值全是空串），"
                    "本次改用命令行 --web-key 传进来的 %d 个 Key", len(web_keys),
                )
        except (TypeError, ValueError) as e:
            log.error(
                "环境变量 SYNORIVE_WEB_KEYS 解析失败：%s: %s —— "
                "它应该是一个 JSON 对象，例如 {\"brave\":\"xxx\"}。"
                "**本次退回命令行 --web-key 里的 %d 个 Key**；"
                "如果那边也没有，付费搜索引擎这次就是没配（不是坏了）。"
                "（不打印原文，里面是密钥）",
                type(e).__name__, e, len(web_keys),
            )

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
        # 🔴 口令走环境变量，**不接受命令行参数** —— argv 全机可见。
        #    桌面端从 safeStorage 取出来后用 env 传给引擎子进程。
        db_key=os.environ.get("SYNORIVE_DB_KEY", ""),
        host=a.host,
        port=a.port,
        data_dir=data_dir,
        model_dir=(a.model_dir.resolve() if a.model_dir else data_dir / "models"),
        concurrency=max(1, min(16, a.concurrency)),
        allow_cloud=a.allow_cloud,
        allow_custom_cloud_endpoint=(
            a.allow_custom_cloud_endpoint
            or os.environ.get("SYNORIVE_ALLOW_CUSTOM_CLOUD_ENDPOINT", "").strip().lower()
            in ("1", "true", "yes", "on")
        ),
        allow_network=not a.no_network,
        enable_image_description=a.enable_image_description,
        enable_face_clustering=a.enable_face_clustering,
        pairing_token=pairing_token,
        lan_tls=bool(a.lan_tls),
        web_engines=[s.strip() for s in a.web_engines.split(",") if s.strip()] or None,
        web_keys=web_keys or None,
        web_lineup_size=max(0, a.web_lineup),
        verify_level=a.verify_level,
        trust_profile=trust_profile,
        prefer_gpu=a.prefer_gpu,
        sensitive_guard_enabled=not a.disable_sensitive_guard,
        background_priority=not a.disable_background_priority,
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
