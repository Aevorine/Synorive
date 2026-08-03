#!/usr/bin/env python
"""
云端简报生成 —— HTTP 接口层（8.8 接出去这一步）
====================================================================
`test_cloud_synthesize.py` 验的是"两条协议+安全边界对不对"，纯函数级别。
这个验的是"接到路由上了没有、两道开关（联网 vs 出网调云端）是不是真的独立"——
和 `test_web_api.py` 同一个道理：模块跑得通 ≠ 真的挂上了接口。

用法：python -m tests.test_cloud_api
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL_DIR = ROOT.parent / "data" / "models"
problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'✓' if cond else '✗'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Engine:
    def __init__(self, data_dir: Path, *, allow_cloud: bool = False) -> None:
        self.data_dir = data_dir
        self.port = free_port()
        self.allow_cloud = allow_cloud
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Engine":
        args = [sys.executable, "-m", "synorive.main", "--port", str(self.port),
                "--data-dir", str(self.data_dir), "--model-dir", str(MODEL_DIR)]
        if self.allow_cloud:
            args.append("--allow-cloud")
        self.proc = subprocess.Popen(
            args, cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(180):
            try:
                self.call("/health", timeout=3)
                return self
            except Exception:
                if self.proc.poll() is not None:
                    err = (self.proc.stderr.read() or b"").decode("utf-8", "replace")
                    raise RuntimeError(f"引擎退出了：\n{err[-2500:]}") from None
                time.sleep(1)
        raise RuntimeError("引擎没起来")

    def __exit__(self, *a: object) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def call(self, path: str, payload: dict | None = None, timeout: float = 60) -> dict:
        d = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=d,
            headers={"Content-Type": "application/json"} if d else {},
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def start_fake_llm(reply_text: str) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            payload = {"choices": [{"message": {"content": reply_text}}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


BRIEFING = {
    "consensus": [{
        "topic": "x", "independentSites": 1,
        "evidence": [{"text": "示例证据句子。", "url": "https://example.org/a",
                      "title": "示例", "site": "example.org"}],
    }],
    "disputes": [], "numbers": [],
}


def main() -> int:
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-cloudapi"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)
    line = "─" * 70

    print(line)
    print("① allow_cloud=False（默认）—— 配置能存，但调用必须被拦")
    print(line)
    with Engine(data_dir) as eng:
        st = eng.call("/api/cloud/status")
        check(st["provider"] == "none" and not st["configured"],
              "初始状态：未配置", "初始状态不对")

        cfg = eng.call("/api/cloud/configure", {
            "provider": "openai-compatible", "apiKey": "sk-whatever",
            "baseUrl": "http://127.0.0.1:1", "chatModel": "gpt-4o-mini",
        })
        check(cfg["configured"] is True, "配置调用本身不受 allow_cloud 影响，能存下来",
              "配置调用被意外拦下")
        check("apiKey" not in cfg and "sk-whatever" not in json.dumps(cfg),
              "响应里不回显 Key 本身", "响应里泄露了 Key 明文")

        blocked = None
        try:
            eng.call("/api/cloud/synthesize", {"query": "x", "briefing": BRIEFING})
        except urllib.error.HTTPError as e:
            blocked = e.code
        check(blocked == 403,
              f"即使配置好了，allow_cloud=False 时调用仍被拦（HTTP {blocked}）",
              f"没有被正确拦下，返回 {blocked} —— 两道开关没有独立生效")

        # schema 校验：非法 provider 值
        bad = None
        try:
            eng.call("/api/cloud/configure", {"provider": "not-a-real-provider"})
        except urllib.error.HTTPError as e:
            bad = e.code
        check(bad == 422, f"非法 provider 值被 schema 拦下（HTTP {bad}）", f"没拦住，返回 {bad}")

    print()
    print(line)
    print("② allow_cloud=True + 真实指向假 LLM —— 端到端走一遍完整链路")
    print(line)
    llm = start_fake_llm("WAL 支持并发读写 [1]。")
    try:
        with Engine(data_dir, allow_cloud=True) as eng:
            eng.call("/api/cloud/configure", {
                "provider": "openai-compatible", "apiKey": "sk-test",
                "baseUrl": f"http://127.0.0.1:{llm.server_address[1]}",
                "chatModel": "gpt-4o-mini",
            })
            result = eng.call("/api/cloud/synthesize",
                              {"query": "wal 模式", "briefing": BRIEFING}, timeout=15)
            check(result.get("kind") == "generated", "端到端拿到 kind=generated",
                  f"没拿到预期结果：{result}")
            check("[1](https://example.org/a)" in result.get("text", ""),
                  "端到端链接替换也生效", f"文本不对：{result.get('text', '')[:120]}")

            eng.call("/api/cloud/clear", {})  # payload={} 只是为了让 call() 用 POST 方法
            st2 = eng.call("/api/cloud/status")
            check(not st2["configured"], "清空后状态归零", "清空没有生效")
    finally:
        llm.shutdown()

    print()
    print("=" * 70)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ 云端接口层通过（两道开关独立生效 + 端到端链路打通）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
