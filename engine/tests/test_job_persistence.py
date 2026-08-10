#!/usr/bin/env python
"""
任务状态持久化 —— 引擎重启后还查得到
====================================================================
之前 `jobs` 表在 schema.sql 里定义好了，但没有代码真的往里写过东西，
是张死表——任务状态只活在内存字典里，引擎一重启，`/api/ingest/{jobId}`
就只能回 404，界面上正在跑的进度直接凭空消失。

这里测两件事：
① 单元测 `_reconcile_stale_jobs()`：上次运行时还标着 running 的任务，
   启动时应该被改判成 failed（线程/JobControl 早就没了，没法真的继续）。
② 端到端：真起一个引擎、真跑一个摄取任务到完成、关掉引擎、
   用同一个数据目录重新起一个引擎，确认同一个 jobId 还能查到——
   而不是 404。

用法：python -m tests.test_job_persistence
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.runtime import EngineConfig, Runtime  # noqa: E402

MODEL_DIR = ROOT.parent / "data" / "models"
problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'PASS' if cond else 'FAIL'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


def test_reconcile_stale_jobs() -> None:
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-job-persist-unit"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)

    config = EngineConfig(data_dir=data_dir, model_dir=MODEL_DIR)
    rt = Runtime(config)
    rt.db.initialize()

    conn = rt.db.connect()
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO jobs (id, created_at, updated_at, status, source, total_items,
                           done_items, failed_items, skipped_items, targets_json, allow_cloud)
        VALUES ('stale-job-1', ?, ?, 'running', 'file', 100, 37, 0, 0, '[]', 0)
        """,
        (now, now),
    )
    # 一条已经正常结束的任务——不该被 reconcile 碰
    conn.execute(
        """
        INSERT INTO jobs (id, created_at, updated_at, status, source, total_items,
                           done_items, failed_items, skipped_items, targets_json, allow_cloud)
        VALUES ('done-job-1', ?, ?, 'done', 'file', 10, 10, 0, 0, '[]', 0)
        """,
        (now, now),
    )

    rt._reconcile_stale_jobs()

    row = conn.execute("SELECT status, detail_json FROM jobs WHERE id = 'stale-job-1'").fetchone()
    check(row["status"] == "failed", "running 状态的旧任务被改判成 failed", f"没被改判，还是 {row['status']}")
    detail = json.loads(row["detail_json"] or "{}")
    check(
        "37/100" in (detail.get("error") or ""),
        f"错误信息带上了中断时的进度：{detail.get('error')}",
        f"错误信息里没有进度：{detail.get('error')}",
    )

    row2 = conn.execute("SELECT status FROM jobs WHERE id = 'done-job-1'").fetchone()
    check(row2["status"] == "done", "已经 done 的任务没被 reconcile 碰", f"被误改成了 {row2['status']}")

    detail_via_load = rt._load_persisted_job("stale-job-1")
    check(detail_via_load is not None and detail_via_load["status"] == "failed",
          "_load_persisted_job 读回来的状态也是 failed",
          f"读回来的是：{detail_via_load}")


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

    def call(self, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
        d = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=d,
            headers={"Content-Type": "application/json"} if d else {},
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def test_job_survives_restart() -> None:
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-job-persist-e2e"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)

    probe = data_dir.parent / "syn-job-persist-e2e-source"
    shutil.rmtree(probe, ignore_errors=True)
    probe.mkdir(parents=True)
    sample = probe / "note.txt"
    sample.write_text("这是一条用来测试任务持久化的示例内容。" * 5, encoding="utf-8")

    job_id = None
    with Engine(data_dir) as eng:
        resp = eng.call("/api/ingest", {"targets": [str(sample)], "recursive": False, "source": "file"})
        job_id = resp.get("jobId")
        check(bool(job_id), f"拿到了 jobId：{job_id}", f"没拿到 jobId：{resp}")

        done = False
        for _ in range(60):
            detail = eng.call(f"/api/ingest/{job_id}")
            if detail.get("status") in ("done", "failed", "cancelled"):
                done = True
                check(detail.get("status") == "done", f"任务正常跑完：{detail.get('status')}",
                      f"任务没有正常跑完：{detail}")
                break
            time.sleep(0.5)
        check(done, "任务在超时前跑完了", "任务一直卡在 running，等超时了")

    if job_id is None:
        return

    # 关掉引擎重新起一个（同一个 data_dir）——这才是原来 404 的那个场景
    with Engine(data_dir) as eng2:
        try:
            detail2 = eng2.call(f"/api/ingest/{job_id}")
            check(
                detail2.get("status") == "done",
                f"引擎重启后同一个 jobId 还能查到，状态：{detail2.get('status')}",
                f"重启后状态不对：{detail2}",
            )
            check(
                detail2.get("done") == 1,
                f"done 计数也保住了：{detail2.get('done')}",
                f"done 计数丢了：{detail2}",
            )
        except urllib.error.HTTPError as e:
            check(False, "", f"引擎重启后查任务变成了 {e.code}（应该还能查到，不是 404）")


def _run_all() -> None:
    test_reconcile_stale_jobs()
    test_job_survives_restart()
    if problems:
        print(f"\n{len(problems)} 个问题")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    _run_all()
