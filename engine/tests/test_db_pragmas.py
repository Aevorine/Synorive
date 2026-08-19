"""
连接级 PRAGMA 一致性
====================================================================
盯的是一个**静默失效**：`mmap_size` / `cache_size` / `temp_store` 都是
**每连接**生效的，不写进库文件。它们原来写在 `schema.sql` 里，而那个脚本
只在建库时跑一次、只作用于当时那一条连接 —— 引擎是每线程一条连接，
于是所有工作线程上这些优化一个都没生效，且不报任何错。

这个测试的作用是：谁把它们挪回 schema.sql（或者忘了在 connect 里设），
CI 立刻红，而不是等到某天有人纳闷"为什么大库还是慢"。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from synorive.store.db import Database, _tuning


def _read(conn: sqlite3.Connection) -> dict[str, int | str]:
    return {
        "cache_size": conn.execute("PRAGMA cache_size").fetchone()[0],
        "mmap_size": conn.execute("PRAGMA mmap_size").fetchone()[0],
        "temp_store": conn.execute("PRAGMA temp_store").fetchone()[0],
        "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
        "foreign_keys": conn.execute("PRAGMA foreign_keys").fetchone()[0],
    }


def test_pragmas_same_on_every_thread(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    db.initialize()

    cache_kb, _ = _tuning()
    baseline = _read(db.connect())

    assert baseline["journal_mode"] == "wal", f"WAL 没开：{baseline['journal_mode']}"
    assert baseline["temp_store"] == 2, "temp_store 应为 MEMORY(2)"
    assert baseline["foreign_keys"] == 1, "外键约束应为开"
    assert baseline["cache_size"] == -cache_kb, (
        f"建库那条连接的 cache_size 被别处覆盖了：{baseline['cache_size']} != {-cache_kb}"
    )
    # 编译上限可能低于请求值，会被静默截断；只要求"确实开了映射"
    assert int(baseline["mmap_size"]) > 0, "mmap_size 是 0 —— 内存映射根本没开"

    results: list[dict[str, int | str]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            got = _read(db.connect())
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常要带回主线程
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(got)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"工作线程建连接时抛了异常：{errors}"
    assert len(results) == 4

    for i, got in enumerate(results):
        assert got == baseline, (
            f"工作线程 {i} 的 PRAGMA 和建库线程不一致 —— "
            f"多半是有人把连接级 PRAGMA 写回了 schema.sql。\n"
            f"  建库线程: {baseline}\n"
            f"  线程 {i}  : {got}"
        )


def test_schema_sql_has_no_connection_level_pragmas() -> None:
    """schema.sql 里只允许留库级 PRAGMA（journal_mode）。"""
    sql = (Path(__file__).resolve().parents[1] / "synorive" / "store" / "schema.sql").read_text(
        encoding="utf-8"
    )
    executable = [
        line.split("--")[0].strip()
        for line in sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    banned = ("mmap_size", "cache_size", "temp_store", "synchronous", "foreign_keys")
    offenders = [
        line
        for line in executable
        if line.upper().startswith("PRAGMA") and any(b in line.lower() for b in banned)
    ]
    assert not offenders, (
        "这几条是每连接生效的，写在 schema.sql 里只对建库那一条连接有效，"
        f"工作线程一个都拿不到（而且不报错）。请放回 db.py 的 connect()：{offenders}"
    )
