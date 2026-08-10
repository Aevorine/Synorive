#!/usr/bin/env python
"""
回收站 —— 删除进 30 天缓冲区，能按原路径恢复
====================================================================
之前删除是直接清索引和记录，撤不回来。现在删除时索引照常立刻清干净
（搜不到，不留幽灵结果），但原路径/标题记进 trash 表，30 天内能恢复
——恢复 = 把原 locator 重新投喂一次，不是瞬间撤销（向量/FTS 在删除时
就已经清掉了，这是权衡过的取舍，见 repository.py 的 soft_delete_item）。

用法：python -m tests.test_trash
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
from datetime import UTC, datetime, timedelta
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


def test_purge_expired_respects_retention() -> None:
    """单元测：purge_expired_trash 只清真正过期的，没到期的留着。"""
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-trash-unit"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)

    config = EngineConfig(data_dir=data_dir, model_dir=MODEL_DIR)
    rt = Runtime(config)
    rt.db.initialize()
    conn = rt.db.connect()

    now = datetime.now(UTC)
    expired = now - timedelta(days=1)
    not_yet = now + timedelta(days=29)
    conn.execute(
        "INSERT INTO trash (id, item_id, title, locator, modality, source, "
        "size_bytes, deleted_at, purge_at) VALUES "
        "('t1', 'i1', '过期的', '/a.txt', 'text', 'file', 10, ?, ?)",
        (now.isoformat(), expired.isoformat()),
    )
    conn.execute(
        "INSERT INTO trash (id, item_id, title, locator, modality, source, "
        "size_bytes, deleted_at, purge_at) VALUES "
        "('t2', 'i2', '没到期的', '/b.txt', 'text', 'file', 10, ?, ?)",
        (now.isoformat(), not_yet.isoformat()),
    )

    from synorive.store.repository import Repository

    repo = Repository(rt.db)
    n = repo.purge_expired_trash()
    check(n == 1, f"只清掉了 1 条过期的：{n}", f"清理数量不对：{n}")

    remaining = {r["id"] for r in conn.execute("SELECT id FROM trash").fetchall()}
    check(remaining == {"t2"}, f"没到期的那条还在：{remaining}", f"剩下的不对：{remaining}")


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


def test_delete_restore_roundtrip() -> None:
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-trash-e2e"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)
    source = data_dir.parent / "syn-trash-e2e-source"
    shutil.rmtree(source, ignore_errors=True)
    source.mkdir(parents=True)
    sample = source / "垃圾桶测试文档.txt"
    marker = "回收站往返测试专用的独特标记词 zzqqxxtrashroundtrip"
    sample.write_text(f"{marker}\n" + "填充内容。" * 5, encoding="utf-8")

    with Engine(data_dir) as eng:
        ing = eng.call("/api/ingest", {"targets": [str(sample)], "recursive": False, "source": "file"})
        job_id = ing["jobId"]
        item_id = None
        for _ in range(60):
            detail = eng.call(f"/api/ingest/{job_id}")
            if detail.get("status") == "done":
                break
            time.sleep(0.5)
        else:
            check(False, "", "摄取超时没跑完")
            return

        found = eng.call("/api/search", {"query": marker, "stage": "keyword"})
        hits = found.get("hits", [])
        check(len(hits) == 1, f"删除前能搜到这条：{len(hits)} 条", f"删除前搜索结果不对：{hits}")
        if hits:
            item_id = hits[0]["item"]["id"]

        check(item_id is not None, "拿到了 item_id", "没拿到 item_id，后面没法继续测")
        if item_id is None:
            return

        # ① 删除——立刻搜不到，但回收站里有记录
        del_resp = eng.call(f"/api/items/{item_id}", method="DELETE")
        check(bool(del_resp.get("trashId")), f"删除返回了 trashId：{del_resp}", f"没有 trashId：{del_resp}")
        trash_id = del_resp["trashId"]

        after_delete = eng.call("/api/search", {"query": marker, "stage": "keyword"})
        check(
            len(after_delete.get("hits", [])) == 0,
            "删除后立刻搜不到了",
            f"删除后还搜得到：{after_delete.get('hits')}",
        )

        trash_list = eng.call("/api/trash")
        trash_ids = {e["id"] for e in trash_list.get("entries", [])}
        check(trash_id in trash_ids, "回收站列表里有这一条", f"回收站列表里没有：{trash_list}")

        # ② 恢复——重新搜得到
        restore_resp = eng.call(f"/api/trash/{trash_id}/restore", {})
        check(restore_resp.get("ok") is True, f"恢复接口返回成功：{restore_resp}", f"恢复失败：{restore_resp}")

        after_restore = eng.call("/api/search", {"query": marker, "stage": "keyword"})
        check(
            len(after_restore.get("hits", [])) == 1,
            "恢复后又能搜到了",
            f"恢复后搜不到：{after_restore.get('hits')}",
        )

        trash_list2 = eng.call("/api/trash")
        trash_ids2 = {e["id"] for e in trash_list2.get("entries", [])}
        check(trash_id not in trash_ids2, "恢复完这条从回收站列表里消失了", f"恢复完还在回收站里：{trash_list2}")

        # ③ 再删一次，这次直接彻底清掉（不恢复）
        item_id2 = after_restore["hits"][0]["item"]["id"]
        del_resp2 = eng.call(f"/api/items/{item_id2}", method="DELETE")
        trash_id2 = del_resp2["trashId"]
        purge_resp = eng.call(f"/api/trash/{trash_id2}", method="DELETE")
        check(purge_resp.get("ok") is True, "彻底清除接口返回成功", f"彻底清除失败：{purge_resp}")
        trash_list3 = eng.call("/api/trash")
        check(
            trash_id2 not in {e["id"] for e in trash_list3.get("entries", [])},
            "彻底清除后回收站列表里也没有了",
            "彻底清除后回收站列表里还有",
        )

    shutil.rmtree(source, ignore_errors=True)


def _run_all() -> None:
    test_purge_expired_respects_retention()
    test_delete_restore_roundtrip()
    if problems:
        print(f"\n{len(problems)} 个问题")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    _run_all()
