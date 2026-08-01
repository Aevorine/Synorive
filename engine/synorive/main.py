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
import logging
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.routes import router
from .runtime import EngineConfig, Runtime

log = logging.getLogger("synorive")


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

        # 模型后台预热，不挡启动（A1 冷启动 ≤2s）
        runtime.warmup_async()
        status_task = asyncio.create_task(runtime.status_loop())

        yield

        status_task.cancel()
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

    # 只放行本机。安卓端走的是另一条带证书校验的通道，不从这里进。
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(http://(127\.0\.0\.1|localhost)(:\d+)?|file://)$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api")

    # ── 健康检查：桌面端靠它判断引擎起没起来 ──────────────
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
        return await health()

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
    p.add_argument("--log-level", default="info")
    a = p.parse_args(argv)

    data_dir = a.data_dir.resolve()
    return EngineConfig(
        host=a.host,
        port=a.port,
        data_dir=data_dir,
        model_dir=(a.model_dir.resolve() if a.model_dir else data_dir / "models"),
        concurrency=max(1, min(16, a.concurrency)),
        allow_cloud=a.allow_cloud,
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

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="warning",  # uvicorn 自己的日志太吵，我们有自己的
        access_log=False,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
    return 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
