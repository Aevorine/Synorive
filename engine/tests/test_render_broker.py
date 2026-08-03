#!/usr/bin/env python
"""
浏览器渲染代理 —— 8.5 端到端
====================================================================
真正的桌面端渲染服务是 Electron 里的一个隐藏 `BrowserWindow`，
这个测试环境里没有 Electron、也没有真的能跑 JS 的浏览器内核可用。

所以这里验的是**协议契约**，不是"Google 真的渲染对了没有"：
用 Python 标准库起一个假的渲染服务，扮演桌面端那一端 ——
只要它遵守同一份 JSON 协议（POST /render {url,timeoutMs} → {html}），
引擎这边的注册、超时、熔断豁免、解析对接就必须全部工作。

**明确的能力边界**：这个测试不能证明"Google/Yandex 的选择器在真实
JS 渲染后的页面上能解析出正确结果"——那需要一个真的浏览器内核，
本机没有。它证明的是"这条通道接得通、协议没写错、失败路径不会把
好引擎拖累"。选择器本身的正确性要等接进真桌面端后用真实操作验证，
已经写进台账的待验证项。

用法：python -m tests.test_render_broker
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.render_broker import RenderBroker  # noqa: E402
from synorive.websearch import MetaSearch  # noqa: E402
from synorive.websearch.engines import Google, ParseOutcome  # noqa: E402

problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'✓' if cond else '✗'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


# ── 假扮桌面端的渲染服务 ────────────────────────────────────
#: 一段**合成的**、结构上模拟 Google 结果页的 HTML（真实结构 <a href><h3>）。
#: 这不是真实抓来的页面 —— 没有浏览器内核，拿不到真实的 JS 渲染结果。
#: 用途仅仅是验证"引擎解析器接到渲染后的 HTML 时能正常工作"这条链路。
FAKE_RENDERED_HTML = """
<html><body>
<div class="g">
  <a href="https://www.sqlite.org/wal.html"><h3>Write-Ahead Logging</h3></a>
  <div>SQLite 的 WAL 模式允许读写并发，不互相阻塞。</div>
</div>
<div class="g">
  <a href="https://en.wikipedia.org/wiki/Write-ahead_logging"><h3>Write-ahead logging - Wikipedia</h3></a>
  <div>WAL is a family of techniques for providing atomicity and durability.</div>
</div>
</body></html>
"""


class _Behavior:
    mode = "ok"  # ok / timeout / error / empty


def make_handler(behavior: _Behavior, calls: list[dict]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:  # 静音，别刷屏
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            calls.append(body)

            if self.path != "/render":
                self.send_response(404)
                self.end_headers()
                return

            if behavior.mode == "timeout":
                time.sleep(3.0)  # 比测试用的 timeout_s 长，逼出超时路径
                payload = {"html": FAKE_RENDERED_HTML}
            elif behavior.mode == "error":
                payload = {"error": "loadURL 失败：net::ERR_NAME_NOT_RESOLVED"}
            elif behavior.mode == "empty":
                payload = {"html": "<html><body>没有结果</body></html>"}
            else:
                payload = {"html": FAKE_RENDERED_HTML}

            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def start_fake_desktop(behavior: _Behavior) -> tuple[ThreadingHTTPServer, threading.Thread, list[dict]]:
    calls: list[dict] = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(behavior, calls))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t, calls


async def main() -> int:
    line = "─" * 70

    print(line)
    print("① 没有桌面端连接时 —— 必须老实说不可用，不假装能用")
    print(line)
    broker = RenderBroker()
    check(not broker.available, "初始状态 available=False", "初始状态不该是可用的")
    html = await broker.render("https://example.com")
    check(html is None, "render() 直接返回 None，不发任何请求", "没连接时不该尝试渲染")

    print()
    print(line)
    print("② 注册后可用，且真的把请求转发到假扮的桌面端 —— 协议契约")
    print(line)
    behavior = _Behavior()
    behavior.mode = "ok"
    srv, thread, calls = start_fake_desktop(behavior)
    port = srv.server_address[1]
    try:
        broker.register(port)
        check(broker.available, "register 后 available=True", "注册后没有变成可用")

        html = await broker.render("https://www.google.com/search?q=sqlite+wal", timeout_s=5.0)
        check(html is not None and "Write-Ahead Logging" in html,
              "拿到了假扮桌面端返回的 HTML", "没拿到渲染结果")
        check(len(calls) == 1 and calls[0].get("url", "").startswith("https://www.google.com"),
              f"请求体里带了正确的 url：{calls[0].get('url', '')[:50] if calls else '(无)'}",
              "请求体里的 url 不对，说明协议对不上")
        check("timeoutMs" in calls[0], "请求体里带了 timeoutMs", "没带超时预算")

        print()
        print(line)
        print("③ 渲染后的 HTML 喂给真正的 Google 解析器 —— 走完整条链路")
        print(line)
        import httpx

        req = httpx.Request("GET", "https://www.google.com/search?q=sqlite+wal")
        resp = httpx.Response(200, request=req, content=html.encode())
        outcome, results = Google().parse(resp)
        check(outcome is ParseOutcome.OK, f"解析出 {len(results)} 条", f"解析状态是 {outcome.value}")
        check(len(results) == 2 and results[0].title == "Write-Ahead Logging",
              "标题解析正确", f"标题解析不对：{[r.title for r in results]}")

        print()
        print(line)
        print("④ 桌面端返回 error 字段时 —— 不能把 error 误当 html 用")
        print(line)
        behavior.mode = "error"
        html2 = await broker.render("https://x.com")
        check(html2 is None, "error 响应被识别为「没拿到」，不是把错误文本当正文",
              f"error 响应被误用了：{html2}")

        print()
        print(line)
        print("⑤ 渲染超时 —— 不能拖死整轮搜索")
        print(line)
        behavior.mode = "timeout"
        t0 = time.monotonic()
        html3 = await broker.render("https://slow.example.com", timeout_s=1.0)
        elapsed = time.monotonic() - t0
        check(html3 is None, "超时返回 None", "超时没有正确返回 None")
        # 预算是 timeout_s(1.0) + 1.5 硬截止 = 2.5s；假桌面端要卡 3.0s 才回。
        # 上限放宽到 2.9s 只是给线程调度让一点余量，核心断言是
        # "远早于 3.0s 拿到 None"，不是掐着秒表比谁精确
        check(elapsed < 2.9, f"{elapsed:.1f}s 内就返回了，没有傻等假桌面端的 3 秒",
              f"耗时 {elapsed:.1f}s，超时机制没生效（该在 ~2.5s 触发硬截止）")
    finally:
        srv.shutdown()
        thread.join(timeout=3)

    print()
    print(line)
    print("⑥ 端到端接进 MetaSearch —— _pick 会给出清楚的跳过原因")
    print(line)
    ms_no_renderer = MetaSearch(enabled=["google"])
    picked, skipped = ms_no_renderer._pick(["google"])
    check(not picked and len(skipped) == 1,
          f"没连渲染器时 google 被跳过：{skipped[0].error[:40]}",
          "没连渲染器时 google 应该被跳过并给出原因")
    check("需要浏览器渲染" in skipped[0].error and "桌面端" in skipped[0].error,
          "跳过原因写清楚了是什么、怎么解决", "跳过原因不够清楚")

    behavior2 = _Behavior()
    behavior2.mode = "ok"
    srv2, thread2, _ = start_fake_desktop(behavior2)
    try:
        broker2 = RenderBroker()
        broker2.register(srv2.server_address[1])
        ms = MetaSearch(enabled=["google"], renderer=broker2)
        picked2, skipped2 = ms._pick(["google"])
        check(bool(picked2) and not skipped2,
              "连了渲染器之后 google 不再被跳过", "连了渲染器后仍然被跳过")

        res = await ms.search("sqlite wal", engines=["google"], limit=10, use_cache=False)
        check(bool(res.clusters), f"通过完整 MetaSearch.search() 拿到 {len(res.clusters)} 条结果",
              "端到端搜索没拿到结果")
        check(res.replies and res.replies[0].outcome is ParseOutcome.OK,
              "回执状态是 OK", "回执状态不对")
    finally:
        srv2.shutdown()
        thread2.join(timeout=3)

    print()
    print("=" * 70)
    print("⚠ 能力边界：以上验的是协议契约（注册/超时/熔断豁免/解析对接），")
    print("  不是「Google 选择器在真实浏览器渲染结果上解析正确」——")
    print("  本机没有可用的浏览器内核，这一条要接上真桌面端后用真实操作验证。")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ 渲染代理协议契约通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
