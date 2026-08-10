#!/usr/bin/env python
"""
目录监控 —— 文件变了自动同步索引
====================================================================
"监听的目录"这个设置项前端早就有完整 UI（加目录、删目录），文案也
明确写着"这些目录里的文件变化会被自动索引"——但引擎侧从来没有任何
代码真的去监控过，是个纯摆设的列表。这里把它接上。

测两层：① watcher.py 的去抖/忽略规则单元测（不用真启动 watchdog 的
文件系统监控，直接调内部方法模拟事件到达）②真实引擎 + 真实文件系统
操作端到端：设置监听目录 → 新建文件 → 等去抖窗口过 → 自动被索引
→ 删除文件 → 自动从库里移除（进回收站）。

用法：python -m tests.test_watcher
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.ingest.watcher import FolderWatcher, _WatchedFolder  # noqa: E402

MODEL_DIR = ROOT.parent / "data" / "models"
problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'PASS' if cond else 'FAIL'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


def test_debounce_merges_rapid_events() -> None:
    """短时间内一堆变化事件，应该合并成一次回调，而不是次次都触发。"""
    changed_calls: list[list[Path]] = []
    removed_calls: list[list[Path]] = []
    w = FolderWatcher(
        on_changed=lambda paths: changed_calls.append(paths),
        on_removed=lambda paths: removed_calls.append(paths),
        debounce_sec=0.2,
    )
    try:
        for i in range(20):
            w._note(Path(f"/fake/dir/file{i % 3}.txt"), removed=False)
            time.sleep(0.01)
        time.sleep(0.5)
        check(len(changed_calls) == 1, f"20 次快速变化合并成 1 次回调：{len(changed_calls)}",
              f"没合并，触发了 {len(changed_calls)} 次")
        if changed_calls:
            check(len(changed_calls[0]) == 3, f"去重后是 3 个不同文件：{len(changed_calls[0])}",
                  f"文件数不对：{changed_calls[0]}")
    finally:
        w.stop()


def test_ignore_patterns() -> None:
    """.synorive-ignore 里的规则、以及 node_modules 这类垃圾目录，都不该触发投喂。"""
    tmp = Path(os.environ.get("TMP", "/tmp")) / "syn-watch-ignore-unit"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    (tmp / ".synorive-ignore").write_text("*.log\ntemp/*\n", encoding="utf-8")
    (tmp / "node_modules").mkdir()

    wf = _WatchedFolder(tmp)
    check(wf.is_ignored(tmp / "debug.log"), "*.log 规则命中 debug.log", "debug.log 应该被忽略但没有")
    check(not wf.is_ignored(tmp / "notes.txt"), "notes.txt 不该被忽略", "notes.txt 被误判成忽略了")
    check(wf.is_ignored(tmp / "temp" / "a.txt"), "temp/* 规则命中子文件", "temp/a.txt 应该被忽略但没有")
    check(
        wf.is_ignored(tmp / "node_modules" / "pkg" / "index.js"),
        "node_modules 下的文件被跳过（跟手动投喂的垃圾目录名单一致）",
        "node_modules 里的文件没被跳过",
    )
    shutil.rmtree(tmp, ignore_errors=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Engine:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Engine":
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "synorive.main", "--port", str(self.port),
             "--data-dir", str(self.data_dir), "--model-dir", str(MODEL_DIR)],
            cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(180):
            try:
                self.call("/health")
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

    def call(self, path: str, payload: dict | None = None, method: str | None = None,
              timeout: float = 30) -> dict:
        d = json.dumps(payload).encode() if payload is not None else None
        m = method or ("POST" if payload is not None else "GET")
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=d,
            headers={"Content-Type": "application/json"} if d else {},
            method=m,
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def test_watch_auto_ingest_and_remove() -> None:
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-watch-e2e"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)
    watched = data_dir.parent / "syn-watch-e2e-folder"
    shutil.rmtree(watched, ignore_errors=True)
    watched.mkdir(parents=True)

    marker = "目录监控自动投喂测试专用标记 zzqqwatchautoingest"
    sample = watched / "auto.txt"

    with Engine(data_dir) as eng:
        r = eng.call("/api/watch/folders", {"folders": [str(watched)]})
        check(str(watched) in r.get("watching", []), f"设置监听目录成功：{r}", f"设置失败：{r}")

        get_r = eng.call("/api/watch/folders")
        check(str(watched) in get_r.get("watching", []), "GET 能查回同一份监听列表", f"查询结果不对：{get_r}")

        # 新建文件——不用手动 /api/ingest，等去抖窗口过了应该自动被索引
        sample.write_text(f"{marker}\n填充内容。" * 3, encoding="utf-8")

        found = False
        item_id = None
        for _ in range(30):
            time.sleep(1)
            resp = eng.call("/api/search", {"query": marker, "stage": "keyword"})
            hits = resp.get("hits", [])
            if hits:
                found = True
                item_id = hits[0]["item"]["id"]
                break
        check(found, "新建文件在没有手动 /api/ingest 的情况下被自动索引了", "等了 30 秒，文件没有被自动索引")
        if not found:
            return

        # 删除文件——应该自动从库里移除（进回收站）
        sample.unlink()
        gone = False
        for _ in range(30):
            time.sleep(1)
            resp = eng.call("/api/search", {"query": marker, "stage": "keyword"})
            if not resp.get("hits"):
                gone = True
                break
        check(gone, "删除文件后，库里也自动跟着移除了", "等了 30 秒，删除的文件在库里还搜得到")

        if item_id is not None:
            trash = eng.call("/api/trash")
            trash_locators = {e["locator"] for e in trash.get("entries", [])}
            check(
                str(sample) in trash_locators,
                "自动移除走的是回收站，不是硬删——原路径在回收站列表里能看到",
                f"回收站里没找到这条：{trash}",
            )

    shutil.rmtree(watched, ignore_errors=True)


def _run_all() -> None:
    test_debounce_merges_rapid_events()
    test_ignore_patterns()
    test_watch_auto_ingest_and_remove()
    if problems:
        print(f"\n{len(problems)} 个问题")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    _run_all()
