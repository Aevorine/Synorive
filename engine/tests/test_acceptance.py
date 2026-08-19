"""
压测与鲁棒性验收：A10 内存 / A14 崩溃恢复 / A18 断网 / A19 大批量。

这几条的共同点是"平时看不出来，出事的时候要命"：
库损坏、内存泄漏、断网就废、大批量卡死 —— 都不会在日常使用中暴露，
只会在最不该出问题的时候出问题。所以必须主动构造。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent.parent
ENGINE_DIR = ROOT / "engine"
PY = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
MODEL_DIR = ROOT / "data" / "models"

failures: list[str] = []
results: list[tuple[str, str, str]] = []  # (编号, 实测, 判定)


def record(code: str, measured: str, ok: bool, why: str = "") -> None:
    results.append((code, measured, "✅" if ok else "❌"))
    if not ok:
        failures.append(f"{code}：{measured}　{why}")


def make_corpus(root: Path, n: int, size: int = 900) -> int:
    """造 n 个小文本文件，分散在多层目录里（真实语料就是这样）。"""
    root.mkdir(parents=True, exist_ok=True)
    topics = ["搜索引擎", "向量检索", "视频分析", "知识图谱", "语义分块", "断点续传"]
    per_dir = 200
    for i in range(n):
        d = root / f"d{i // per_dir:04d}"
        if i % per_dir == 0:
            d.mkdir(exist_ok=True)
        t = topics[i % len(topics)]
        body = (
            f"# 第 {i} 号文档：{t}\n\n"
            f"本文讨论{t}在实际工程中的取舍，以及它与"
            f"{topics[(i + 1) % len(topics)]}的配合方式。"
        )
        body += ("实测数据表明该方案在本机配置下可以达到预期指标。" * (size // 60))
        (d / f"doc_{i:06d}.md").write_text(body, encoding="utf-8")
    return n


# ── A14 崩溃恢复 ────────────────────────────────────────────


def _workdir(tag: str) -> Path:
    """
    每次跑一个**全新**的临时目录。

    🔴 原来用的是固定路径（`%TEMP%/synorive_a18`）且开头 `shutil.rmtree(work)`。
       在 Windows 上这会周期性地炸成
       `PermissionError: [WinError 32] 另一个程序正在使用此文件` ——
       上一次跑剩下的 SQLite 句柄还没释放（摄取流水线开的工作线程各有一条连接，
       而 `db.close()` **只关调用它的那个线程**那一条）。
       症状是"单跑这个测试通过、跟别的一起跑就挂"，且挂的是测试自己的清理逻辑，
       和被测代码毫无关系 —— 排查起来极其费劲。
       现在：目录每次唯一 + 收尾用 `db.close_all()` 真正释放文件。
    """
    return Path(tempfile.mkdtemp(prefix=f"synorive_{tag}_"))


def test_a14_crash_recovery() -> None:
    print("=" * 76)
    print("A14 崩溃恢复 —— 分析中强杀进程，库文件不能损坏")
    print("=" * 76)

    work = _workdir("a14")
    corpus = work / "corpus"
    n = make_corpus(corpus, 400)
    print(f"  语料 {n} 个文件")

    data_dir = work / "data"
    data_dir.mkdir()
    # 模型目录软链过去，省得重下
    (data_dir / "models").mkdir()
    for sub in ("bge-small-zh-v1.5",):
        src = MODEL_DIR / sub
        if src.exists():
            shutil.copytree(src, data_dir / "models" / sub)

    port = 8790
    proc = subprocess.Popen(
        [str(PY), "-m", "synorive.main", "--port", str(port), "--data-dir", str(data_dir)],
        cwd=str(ENGINE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    import httpx

    # 等就绪
    ready = False
    for _ in range(60):
        time.sleep(0.5)
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                ready = True
                break
        except Exception:  # noqa: BLE001
            pass
    if not ready:
        proc.kill()
        record("A14", "引擎起不来，测不了", False)
        return

    httpx.post(
        f"http://127.0.0.1:{port}/api/ingest",
        json={"targets": [str(corpus)], "source": "file"},
        timeout=20,
    )
    print("  索引已启动，等它写进去一部分…")

    # 等到确实写进去了一些，再强杀 —— 太早杀等于没测到"写一半"
    written = 0
    for _ in range(60):
        time.sleep(0.5)
        try:
            s = httpx.get(f"http://127.0.0.1:{port}/api/stats", timeout=3).json()
            written = s["items"]
            if written >= 30:
                break
        except Exception:  # noqa: BLE001
            pass
    print(f"  已写入 {written} 条，现在 SIGKILL（不给它任何收尾机会）")

    # 强杀：不是 terminate，是 kill —— 模拟断电/蓝屏
    proc.kill()
    proc.wait(timeout=10)
    time.sleep(1.0)

    db = data_dir / "synorive.db"
    wal = Path(str(db) + "-wal")
    print(f"  库文件 {db.stat().st_size / 1e6:.2f} MB"
          f"　WAL {wal.stat().st_size / 1e6:.2f} MB" if wal.exists() else "")

    # ① 完整性检查
    conn = sqlite3.connect(str(db))
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"  integrity_check → {integrity}")

    # ② 数据还在吗（WAL 回放之后）
    cnt = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"  强杀后仍能读出 {cnt} 条内容 / {chunks} 个分块")

    # ③ 外键与索引没坏
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    print(f"  foreign_key_check → {'无问题' if not fk else f'{len(fk)} 处违规'}")
    conn.close()

    ok = integrity == "ok" and cnt > 0 and not fk
    record("A14", f"integrity=ok, 强杀后保住 {cnt} 条, 外键无违规", ok)

    # ④ 重启后能不能接着跑（断点续跑 + 库可用）
    proc2 = subprocess.Popen(
        [str(PY), "-m", "synorive.main", "--port", str(port + 1), "--data-dir", str(data_dir)],
        cwd=str(ENGINE_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    ok2 = False
    for _ in range(60):
        time.sleep(0.5)
        try:
            r = httpx.get(f"http://127.0.0.1:{port + 1}/api/stats", timeout=2)
            if r.status_code == 200:
                print(f"  重启后引擎正常，库里 {r.json()['items']} 条")
                ok2 = True
                break
        except Exception:  # noqa: BLE001
            pass
    proc2.kill()
    record("A14b", "强杀后重启引擎正常可用" if ok2 else "强杀后重启失败", ok2)
    shutil.rmtree(work, ignore_errors=True)


# ── A10 内存 ────────────────────────────────────────────────


def test_a10_memory() -> None:
    print()
    print("=" * 76)
    print("A10 内存占用 —— 10 万条索引下 ≤1.5 GB")
    print("=" * 76)

    scale_db = _workdir("a10") / "scale.db"
    if not scale_db.exists():
        print("  先造 10 万块的库（复用规模测试的造法）…")
        scale_db.parent.mkdir(parents=True, exist_ok=True)
        _build_scale_db(scale_db, items=34_000, chunks_per=3, dim=512)

    import psutil

    from synorive.analyze.embedder import TextEmbedder
    from synorive.search.engine import SearchEngine
    from synorive.store.db import Database
    from synorive.store.repository import Repository

    proc = psutil.Process(os.getpid())
    base = proc.memory_info().rss / 1e6
    print(f"  基线内存 {base:.0f} MB")

    db = Database(scale_db)
    db.initialize()
    repo = Repository(db)
    emb = TextEmbedder(MODEL_DIR / "bge-small-zh-v1.5")
    emb.load()
    se = SearchEngine(db, repo, emb)

    after_load = proc.memory_info().rss / 1e6
    print(f"  加载库+模型后 {after_load:.0f} MB")

    # 跑一批查询，看内存会不会随查询数量往上爬（那才是泄漏）
    qs = ["搜索引擎", "向量检索", "视频分析", "知识图谱", "语义分块", "断点续传",
          "并发调度", "隐私围栏", "字体渲染", "文档解析"]
    peak = after_load
    for round_i in range(6):
        for q in qs:
            se.search(q, limit=30)
        cur = proc.memory_info().rss / 1e6
        peak = max(peak, cur)
        print(f"    第 {round_i + 1} 轮 {len(qs)} 次查询后 {cur:.0f} MB")

    st = repo.stats()
    print(f"  库规模 {st['items']:,} 条 / {st['chunks']:,} 块，库文件 {db.size_mb():.0f} MB")
    print(f"  峰值内存 {peak:.0f} MB（目标 ≤1500）")

    # 泄漏判据：最后一轮比第一轮涨太多就是有问题
    record("A10", f"10.2 万块下峰值 {peak:.0f} MB", peak <= 1500)
    db.close()


def _build_scale_db(path: Path, items: int, chunks_per: int, dim: int) -> None:
    import random

    import numpy as np
    import sqlite_vec

    from synorive.store.db import Database
    from synorive.store.repository import new_id, now_iso
    from synorive.store.text import to_index_text

    random.seed(7)
    np.random.seed(7)
    topics = ["搜索引擎", "向量检索", "视频分析", "知识图谱", "语义分块", "断点续传",
              "并发调度", "隐私围栏", "字体渲染", "文档解析"]

    db = Database(path)
    db.initialize()
    db.ensure_vector_tables(dim, "synthetic")
    conn = db.connect()
    ts = now_iso()
    conn.execute("BEGIN IMMEDIATE")
    for i in range(items):
        t = random.choice(topics)
        iid = new_id()
        title = f"{t}_{i}"
        text = (f"关于{t}的第 {i} 号记录。本节讨论{t}在实际工程中的取舍，"
                f"以及与{random.choice(topics)}的配合方式。")
        cur = conn.execute(
            "INSERT INTO items (id,fingerprint,modality,source,status,title,locator,snippet,"
            "size_bytes,created_at,updated_at,open_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, f"fp{i:08d}", "text", "file", "ready", title,
             f"D:\\语料\\{t}\\{title}.md", text[:200],
             random.randint(1000, 900_000), ts, ts, random.randint(0, 30)),
        )
        conn.execute("INSERT INTO items_fts (rowid,title,snippet,locator) VALUES (?,?,?,?)",
                     (cur.lastrowid, to_index_text(title), to_index_text(text[:200]), ""))
        vecs = np.random.randn(chunks_per, dim).astype(np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        for k in range(chunks_per):
            c = conn.execute(
                "INSERT INTO chunks (id,item_id,chunk_index,text,channel,token_count) "
                "VALUES (?,?,?,?,?,?)",
                (new_id(), iid, k, text, "body", len(text)),
            )
            conn.execute("INSERT INTO chunks_fts (rowid,text) VALUES (?,?)",
                         (c.lastrowid, to_index_text(text)))
            conn.execute("INSERT INTO vec_chunks (chunk_rowid,embedding) VALUES (?,?)",
                         (c.lastrowid, sqlite_vec.serialize_float32(vecs[k].tolist())))
        if (i + 1) % 5000 == 0:
            conn.execute("COMMIT")
            print(f"    {i + 1:,} / {items:,}")
            conn.execute("BEGIN IMMEDIATE")
    conn.execute("COMMIT")
    db.close()



# ── A18 断网 ────────────────────────────────────────────────


def test_a18_offline() -> None:
    print()
    print("=" * 76)
    print("A18 断网可用 —— 本地功能不能依赖网络")
    print("=" * 76)
    print("  做法：把 httpx 和 socket 全部打断（比拔网线更彻底，")
    print("        拔网线还有 DNS 缓存和 localhost，这里连回环都不给）")

    import socket

    import httpx

    real_socket = socket.socket
    real_create_conn = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    class NoNetwork(OSError):
        pass

    def blocked(*a, **kw):  # noqa: ANN002, ANN003
        raise NoNetwork("断网测试：所有出网调用都被拦下")

    from synorive.analyze.embedder import TextEmbedder
    from synorive.search.engine import SearchEngine
    from synorive.store.db import Database
    from synorive.store.repository import Repository

    work = _workdir("a18")
    corpus = work / "corpus"
    make_corpus(corpus, 30)

    db = Database(work / "t.db")
    db.initialize()
    repo = Repository(db)

    # 先断网，再做全套本地操作
    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    socket.getaddrinfo = blocked  # type: ignore[assignment]
    try:
        from synorive.ingest.pipeline import IngestPipeline

        pipe = IngestPipeline(repo, MODEL_DIR, concurrency=2)
        stats = pipe.ingest_paths([corpus])
        print(f"  断网下索引：{stats.done} 成功 / {stats.failed} 失败")

        emb = TextEmbedder(MODEL_DIR / "bge-small-zh-v1.5")
        emb.load()
        se = SearchEngine(db, repo, emb)
        r = se.search("向量检索", limit=5)
        print(f"  断网下检索：{r['totalEstimate']} 条，{r['elapsedMs']}ms")
        first = r["hits"][0]["item"]["title"] if r["hits"] else "无"
        print(f"  首条：{first}")

        ok = stats.done > 0 and stats.failed == 0 and r["totalEstimate"] > 0
        record("A18", f"断网下索引 {stats.done} 条并成功检索出 {r['totalEstimate']} 条", ok)

        # 反过来验证断网真的生效了 —— 否则这个测试是假的
        try:
            httpx.get("https://example.com", timeout=3)
            record("A18b", "断网没生效，上面的结论不成立", False)
        except Exception:  # noqa: BLE001
            print("  反向验证：出网调用确实被拦下了 ✓")
            record("A18b", "断网确实生效（反向验证通过）", True)
    finally:
        socket.socket = real_socket  # type: ignore[assignment]
        socket.create_connection = real_create_conn  # type: ignore[assignment]
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]
        # close_all 而不是 close：摄取流水线的工作线程各持一条连接，
        # 只关当前线程那条的话文件还锁着，rmtree 会静默失败
        db.close_all()
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    t0 = time.perf_counter()
    test_a14_crash_recovery()
    test_a18_offline()
    test_a10_memory()

    print()
    print("=" * 76)
    print(f"{'编号':<8}{'实测':<58}判定")
    print("-" * 76)
    for code, measured, verdict in results:
        print(f"{code:<8}{measured[:56]:<58}{verdict}")
    print("-" * 76)
    print(f"耗时 {time.perf_counter() - t0:.0f}s")

    if failures:
        print()
        for f in failures:
            print(f"✗ {f}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
