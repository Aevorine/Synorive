#!/usr/bin/env python
"""
局域网配对闸 —— /health /status 收紧鉴权 的回归
====================================================================
之前 `_UNGUARDED_PATHS` 里放着 `/health` 和 `/status`，意味着局域网里
任何没配对过的设备，不带令牌也能读到 CPU/内存/DB 大小/索引条数这些信息。
现在只放行 `/pairing/status`（配对页"测试连接"要用，字段收窄成最小集），
`/health`/`/status` 改成跟其它接口一样要令牌。

这里直接单元测 `_PairingGuardMiddleware`，不起真引擎——因为要测的是
"非本机来源"这个分支，而测试进程发出的请求 client host 永远是
127.0.0.1，起真引擎测不出"局域网设备"这个场景，必须直接构造 ASGI scope。

用法：python -m tests.test_pairing_guard
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.main import _PairingGuardMiddleware, _UNGUARDED_PATHS  # noqa: E402

problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'PASS' if cond else 'FAIL'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


class _FakeConfig:
    def __init__(self, token: str) -> None:
        self.pairing_token = token


class _FakeRuntime:
    def __init__(self, token: str) -> None:
        self.config = _FakeConfig(token)


async def _call_guard(
    middleware: _PairingGuardMiddleware,
    *,
    client_host: str,
    path: str,
    token_header: str | None,
) -> tuple[bool, int | None]:
    """跑一次中间件，返回 (下游 app 有没有被放行, 如果被拒绝的话状态码是多少)。"""
    downstream_called = False

    async def fake_app(scope: dict, receive: Any, send: Any) -> None:
        nonlocal downstream_called
        downstream_called = True

    headers = []
    if token_header is not None:
        headers.append((b"x-synorive-token", token_header.encode("latin-1")))

    scope = {
        "type": "http",
        "path": path,
        "client": (client_host, 12345),
        "headers": headers,
    }

    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    async def send(msg: dict) -> None:
        sent.append(msg)

    middleware.app = fake_app
    await middleware(scope, receive, send)

    status = None
    for msg in sent:
        if msg.get("type") == "http.response.start":
            status = msg.get("status")
    return downstream_called, status


def test_unguarded_paths_only_pairing_status() -> None:
    check(
        _UNGUARDED_PATHS == {"/pairing/status"},
        "免鉴权白名单现在只有 /pairing/status",
        f"免鉴权白名单不对：{_UNGUARDED_PATHS}（/health //status 不该再免鉴权）",
    )


def test_localhost_always_passes() -> None:
    rt = _FakeRuntime(token="secret-token")
    mw = _PairingGuardMiddleware(app=None, runtime=rt)
    for path in ["/health", "/status", "/pairing/status", "/api/search"]:
        passed, _ = asyncio.run(_call_guard(mw, client_host="127.0.0.1", path=path, token_header=None))
        check(passed, f"本机访问 {path} 不带令牌也放行", f"本机访问 {path} 被挡了，不该挡")


def test_lan_pairing_status_no_token_needed() -> None:
    rt = _FakeRuntime(token="secret-token")
    mw = _PairingGuardMiddleware(app=None, runtime=rt)
    passed, status = asyncio.run(
        _call_guard(mw, client_host="192.168.1.50", path="/pairing/status", token_header=None)
    )
    check(passed, "局域网设备不带令牌探测 /pairing/status 放行", f"/pairing/status 被挡了（status={status}），配对页会用不了")


def test_lan_health_requires_token() -> None:
    rt = _FakeRuntime(token="secret-token")
    mw = _PairingGuardMiddleware(app=None, runtime=rt)

    passed, status = asyncio.run(_call_guard(mw, client_host="192.168.1.50", path="/health", token_header=None))
    check(not passed, "局域网设备不带令牌访问 /health 被挡（401）", f"没带令牌居然放行了 /health，status={status}")
    check(status == 401, f"状态码是 401（实际 {status}）", f"状态码不对：{status}")

    passed2, _ = asyncio.run(_call_guard(mw, client_host="192.168.1.50", path="/status", token_header=None))
    check(not passed2, "局域网设备不带令牌访问 /status 也被挡", "/status 没带令牌居然放行了")

    passed3, _ = asyncio.run(
        _call_guard(mw, client_host="192.168.1.50", path="/health", token_header="wrong-token")
    )
    check(not passed3, "局域网设备带错误令牌访问 /health 依然被挡", "错误令牌居然放行了 /health")

    passed4, _ = asyncio.run(
        _call_guard(mw, client_host="192.168.1.50", path="/health", token_header="secret-token")
    )
    check(passed4, "局域网设备带正确令牌访问 /health 放行", "带对了令牌还是被挡了")


def _run_all() -> None:
    test_unguarded_paths_only_pairing_status()
    test_localhost_always_passes()
    test_lan_pairing_status_no_token_needed()
    test_lan_health_requires_token()
    if problems:
        print(f"\n{len(problems)} 个问题")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    _run_all()
