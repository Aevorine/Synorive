#!/usr/bin/env python
"""
/upload 磁盘配额检查 —— 单元测 + 真跑一次正常上传
====================================================================
review 里提的问题是：单文件 512MB 上限只挡得住"一个超大文件"，挡不住
"很多个刚好卡在上限以内、但没人来 /ingest 消费掉"的文件把 inbox 目录
堆到把磁盘写满。routes.py 加了两道闸：inbox 目录总量上限、上传前后的
剩余磁盘空间检查。

这里没有真的去填满磁盘或塞 10GB 文件触发 507——那样测试成本太高。
测的是：①算 inbox 用量的纯函数本身对不对 ②正常情况下 /upload 真的能
把文件传上去、路由没被这次改动弄坏。507/413 那两条分支的正确性
只能靠代码走查，本次没有实测触发。

用法：python -m tests.test_upload_quota
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.api.routes import _inbox_usage_bytes  # noqa: E402

MODEL_DIR = ROOT.parent / "data" / "models"
problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'✓' if cond else '✗'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


def test_inbox_usage_bytes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        inbox = Path(tmp)
        check(_inbox_usage_bytes(inbox) == 0, "空目录用量为 0", "空目录用量算错了")

        (inbox / "a.bin").write_bytes(b"x" * 1000)
        (inbox / "b.bin").write_bytes(b"y" * 2000)
        sub = inbox / "not-counted-if-nested"
        sub.mkdir()
        (sub / "c.bin").write_bytes(b"z" * 5000)  # glob("*") 不递归，子目录不算

        got = _inbox_usage_bytes(inbox)
        check(got == 3000, f"只统计顶层文件，3000 字节，实际 {got}", f"用量算错：{got}（应为 3000）")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_upload_still_works() -> None:
    """确认加了配额检查之后，正常小文件上传这条主路径没被弄坏。"""
    # 不用 tempfile.TemporaryDirectory() 的自动清理——引擎子进程退出后
    # Windows 上 synorive.db 的文件锁经常还没真正释放，自动清理会报
    # PermissionError。跟 test_web_api.py 一样，用固定目录 + 开头
    # ignore_errors 清一次，不清尾巴。
    data_dir = Path(os.environ.get("TMP", tempfile.gettempdir())) / "syn-upload-quota-test"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "synorive.main", "--port", str(port),
         "--data-dir", str(data_dir), "--model-dir", str(MODEL_DIR)],
        cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        ready = False
        for _ in range(180):
            try:
                urllib.request.urlopen(f"{base}/health", timeout=3)
                ready = True
                break
            except Exception:
                if proc.poll() is not None:
                    err = (proc.stderr.read() or b"").decode("utf-8", "replace")
                    raise RuntimeError(f"引擎起不来：\n{err[-2000:]}") from None
                time.sleep(1)
        check(ready, "引擎起来了", "引擎没起来")
        if not ready:
            return

        boundary = "----syntest"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="probe.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "hello upload quota test\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/upload", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            ok = resp.status == 200
            payload = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            ok = False
            payload = e.read().decode("utf-8", "replace")
        check(ok, "正常小文件上传返回 200", f"上传失败：{payload}")
        check(
            '"sizeBytes"' in payload and '"path"' in payload,
            "响应里带 path/sizeBytes",
            f"响应字段不对：{payload}",
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _run_all() -> None:
    test_inbox_usage_bytes()
    test_upload_still_works()
    if problems:
        print(f"\n✗ {len(problems)} 个问题")
        sys.exit(1)
    print("\n✓ 全部通过")


if __name__ == "__main__":
    _run_all()
