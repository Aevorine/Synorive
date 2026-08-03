"""
浏览器渲染代理 —— 8.5
====================================================================
Google/Yandex 这类需要跑 JavaScript 才能拿到结果的引擎，纯 Python 进程
没有能力执行。往引擎里塞一个 Playwright 要多背几百 MB 依赖，
而桌面端本来就带着一个完整的 Chromium（Electron）——所以走这条路：
**引擎向桌面端要一次渲染，不是自己长出一个浏览器**。

协议很朴素（细节见 `apps/desktop/electron/main/render.ts`）：
桌面端定期把它本地渲染服务的端口注册过来，引擎需要渲染时直接 POST 过去。

**命令行/MCP 单独跑引擎、桌面端没开** 时，从来没人调用 `register()`，
`available` 恒为 False，`render()` 立刻返回 None —— 不重试、不假装能用，
Google/Yandex 那两家会如实报"需要浏览器渲染，未连接到桌面端"。
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger("synorive.render")

#: 注册有效期。桌面端每 20 秒续一次（见 render.ts 的 HEARTBEAT_MS），
#: 45 秒给了两次心跳的容错空间，不会因为一次网络抖动就被判定"不可用"
REGISTRATION_TTL_S = 45.0


class RenderBroker:
    def __init__(self, *, ttl_s: float = REGISTRATION_TTL_S) -> None:
        self._port: int | None = None
        self._registered_at: float = 0.0
        self._ttl_s = ttl_s

    def register(self, port: int) -> None:
        self._port = port
        self._registered_at = time.monotonic()

    def unregister(self) -> None:
        self._port = None

    @property
    def available(self) -> bool:
        return self._port is not None and (time.monotonic() - self._registered_at) < self._ttl_s

    async def render(self, url: str, *, timeout_s: float = 12.0) -> str | None:
        """请桌面端渲染一个网址，返回渲染后的完整 HTML；拿不到就是 None。"""
        if not self.available:
            return None
        port = self._port

        async def _post() -> httpx.Response:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s + 1.0, connect=min(3.0, timeout_s))
            ) as client:
                return await client.post(
                    f"http://127.0.0.1:{port}/render",
                    json={"url": url, "timeoutMs": int(timeout_s * 1000)},
                )

        # 🔴 实测踩过两层：① 原来只传 `timeout_s + 3.0` 给 httpx，
        # 对调用方传的 `timeout_s=1.0` 来说实际预算变成了 4 秒，
        # 超时形同虚设。② 缩小 httpx 自己的超时值后，实测这台机器上
        # httpx/httpcore 的 read timeout **仍然不准**——配了 2.0s，
        # 真实耗时量到 3.4~3.9s（用一个卡住 3 秒的假服务器实测出来的）。
        # httpx 内部超时精度信不过，就不能只靠它，改用 `asyncio.wait_for`
        # 套一层硬截止时间——这是事件循环自己的定时器，不依赖 httpx 的实现细节。
        try:
            resp = await asyncio.wait_for(_post(), timeout=timeout_s + 1.5)
        except (httpx.HTTPError, TimeoutError):
            return None

        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        # 桌面端渲染失败时返回的是 {"error": "..."} 不是 {"html": "..."}，
        # 两者都要按"没拿到"处理，不能把 error 字段误当 html 用
        html = data.get("html")
        return html if isinstance(html, str) and html else None
