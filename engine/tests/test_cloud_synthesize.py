#!/usr/bin/env python
"""
云端简报生成 —— R8 右栏，8.8
====================================================================
没有真实的 OpenAI/Claude Key（那是 🔑 只有用户能给的东西），
所以这里用标准库起两个假服务器，各自照抄两家真实的响应体形状
（`{choices:[{message:{content}}]}` / `{content:[{type:'text',text}]}`），
验证适配器解析对不对。

**重心不在"模型说得好不好"，在"链接安全边界有没有守住"**：
① 模型正常按 [n] 引用 → 必须渲染成真实链接
② 模型不听话、直接写了个网址 → 必须被剥掉，不能渲染出去
③ 模型引用了一个不存在的编号 → 原样保留，不能崩、不能瞎链

用法：python -m tests.test_cloud_synthesize
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.cloud.adapters import (  # noqa: E402
    AnthropicNative, CloudAdapterError, OpenAICompatible,
)
from synorive.cloud.synthesize import synthesize  # noqa: E402

problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'✓' if cond else '✗'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


BRIEFING = {
    "consensus": [
        {
            "topic": "wal",
            "independentSites": 2,
            "evidence": [
                {"text": "WAL 模式允许读写并发，互不阻塞。",
                 "url": "https://sqlite.org/wal.html", "title": "WAL", "site": "sqlite.org"},
                {"text": "WAL is a family of techniques for durability.",
                 "url": "https://en.wikipedia.org/wiki/WAL", "title": "WAL - Wikipedia",
                 "site": "wikipedia.org"},
            ],
        }
    ],
    "disputes": [],
    "numbers": [],
}


def make_openai_handler(reply_text: str, calls: list[dict]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            calls.append(json.loads(self.rfile.read(length) or b"{}"))
            payload = {
                "choices": [{"message": {"content": reply_text}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 60},
            }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def make_anthropic_handler(reply_text: str, calls: list[dict]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            calls.append(json.loads(self.rfile.read(length) or b"{}"))
            payload = {
                "content": [{"type": "text", "text": reply_text}],
                "usage": {"input_tokens": 200, "output_tokens": 80},
            }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def make_error_handler(status: int, body: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def start(handler_cls) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def main() -> int:
    line = "─" * 70

    print(line)
    print("① OpenAI 兼容协议 —— 请求体形状 + 响应解析")
    print(line)
    calls: list[dict] = []
    srv = start(make_openai_handler(
        "WAL 模式支持读写并发 [1]，这在多篇资料里都有印证 [1][2]。", calls,
    ))
    try:
        adapter = OpenAICompatible(api_key="sk-test", base_url=f"http://127.0.0.1:{srv.server_address[1]}")
        result = await synthesize("wal 模式是什么", BRIEFING, adapter=adapter, model="gpt-4o-mini")
        check(len(calls) == 1 and calls[0].get("model") == "gpt-4o-mini",
              "请求体带了正确的 model 字段", "请求体不对")
        check(calls[0]["messages"][0]["role"] == "system",
              "system 提示词单独一条消息", "system 提示词没有正确传递")
        check(result["kind"] == "generated", "kind=generated（和左栏 kind=extract 区分开）",
              "kind 字段不对")
        check("[1](https://sqlite.org/wal.html)" in result["text"],
              "引用 [1] 被换成了真实链接", f"链接替换失败：{result['text'][:100]}")
        check(sum(c["used"] for c in result["citations"]) == 2,
              "两条证据都被标记为「已引用」", "引用标记不对")
    finally:
        srv.shutdown()

    print()
    print(line)
    print("② Claude 原生协议 —— system 独立字段 + content 分段拼接")
    print(line)
    calls2: list[dict] = []
    srv2 = start(make_anthropic_handler("这一点在 [1] 里有说明。", calls2))
    try:
        adapter2 = AnthropicNative(api_key="sk-ant-test", base_url=f"http://127.0.0.1:{srv2.server_address[1]}")
        result2 = await synthesize("wal 模式是什么", BRIEFING, adapter=adapter2, model="claude-opus-5")
        check(calls2[0].get("system", "").startswith("你是一个严谨的研究助理"),
              "system 字段独立传递（不是塞进 messages）", "system 字段没有正确传递")
        check("messages" in calls2[0] and calls2[0]["messages"][0]["role"] == "user",
              "messages 只含 user 一条", "messages 结构不对")
        check("[1](https://sqlite.org/wal.html)" in result2["text"],
              "Claude 通道下引用同样被换成真实链接", "Claude 通道解析失败")
    finally:
        srv2.shutdown()

    print()
    print(line)
    print("③ 🔴 安全边界：模型不听话直接写网址 —— 必须被剥掉")
    print(line)
    calls3: list[dict] = []
    srv3 = start(make_openai_handler(
        "更多信息见 https://totally-fake-source.example.com/article 这篇 [1]。", calls3,
    ))
    try:
        adapter3 = OpenAICompatible(api_key="x", base_url=f"http://127.0.0.1:{srv3.server_address[1]}")
        result3 = await synthesize("x", BRIEFING, adapter=adapter3, model="m")
        check("totally-fake-source" not in result3["text"],
              "模型编的裸链接被剥掉了，没有出现在最终文本里",
              f"编造的链接混进了输出：{result3['text']}")
        check("已剔除" in result3["text"], "剥离处留下了明确说明，不是静默消失",
              "剥离后没有留痕，用户不知道发生了什么")
        check("[1](https://sqlite.org/wal.html)" in result3["text"],
              "同一段里正常的 [1] 引用仍然正确链接（不是整段被误伤）",
              "正常引用被连带误伤了")
    finally:
        srv3.shutdown()

    print()
    print(line)
    print("④ 模型引用了不存在的编号 —— 不能崩，原样保留不瞎链")
    print(line)
    calls4: list[dict] = []
    srv4 = start(make_openai_handler("参考 [1] 和 [99]（这个编号系统里没有）。", calls4))
    try:
        adapter4 = OpenAICompatible(api_key="x", base_url=f"http://127.0.0.1:{srv4.server_address[1]}")
        result4 = await synthesize("x", BRIEFING, adapter=adapter4, model="m")
        check("[99]" in result4["text"] and "](https" not in result4["text"].split("[99]")[1][:5],
              "不存在的编号 [99] 原样保留，没有被链接成任何东西",
              f"不存在的编号被错误处理：{result4['text']}")
    finally:
        srv4.shutdown()

    print()
    print(line)
    print("⑤ 接口报错 —— 必须抛 CloudAdapterError，不能吞掉伪装成成功")
    print(line)
    srv5 = start(make_error_handler(401, '{"error":{"message":"invalid api key"}}'))
    try:
        adapter5 = OpenAICompatible(api_key="bad", base_url=f"http://127.0.0.1:{srv5.server_address[1]}")
        threw = False
        try:
            await synthesize("x", BRIEFING, adapter=adapter5, model="m")
        except CloudAdapterError as e:
            threw = True
            check("401" in str(e), f"错误信息带了状态码：{e}", "错误信息里没有状态码，用户不知道是 Key 错了")
        check(threw, "401 被识别为失败并抛出", "401 响应没有被当成失败处理")
    finally:
        srv5.shutdown()

    print()
    print(line)
    print("⑥ 没有可引用证据时 —— 不该白白调用云端")
    print(line)
    calls6: list[dict] = []
    srv6 = start(make_openai_handler("不该被调用", calls6))
    try:
        adapter6 = OpenAICompatible(api_key="x", base_url=f"http://127.0.0.1:{srv6.server_address[1]}")
        empty_briefing = {"consensus": [], "disputes": [], "numbers": []}
        result6 = await synthesize("x", empty_briefing, adapter=adapter6, model="m")
        check(not calls6, "没有证据时直接返回，没有真的调用云端接口（省一次请求和费用）",
              "没有证据也调用了云端，白花钱")
        check(bool(result6.get("warning")), "给了明确的提示而不是空文本", "没有说明为什么没生成")
    finally:
        srv6.shutdown()

    print()
    print("=" * 70)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ 云端简报生成通过（两条通道协议对接 + 链接安全边界）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
