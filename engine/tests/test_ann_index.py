#!/usr/bin/env python
"""
A17 ANN 索引 —— 单元测试 + 距离公式一致性 + 真实规模端到端
====================================================================
三层验证，从最容易出错的地方开始：

  ① AnnIndex 本身：加/删/查/存盘/读盘/全量重建，都是新写的代码，
     没有任何一条是"顺手带出来的"，全部要单独验证。
  ② 🔴 距离公式一致性——**这是最容易埋雷的地方**。usearch 的 cos 距离
     是 `1 - cos_sim`，sqlite-vec 现有的是 `2 - 2·cos_sim`（相差整整一倍），
     两者不统一的话，ANN 接管查询后 `explain.scores.semantic` 会全部错位，
     而且**不会报错、不会崩、只是数字不对**——这类问题最难被发现。
     所以专门拿同一批向量分别过 sqlite-vec 暴力扫描和 AnnIndex，
     断言两条路径对同一个查询给出数值上一致的距离。
  ③ 端到端：真灌一批合成数据过 ANN_THRESHOLD（150,000），起真引擎，
     确认自动重建生效、语义检索真的从 ANN 拿结果、且检索质量没有塌——
     用真实文本查询验证返回的文档确实相关，不是随便返回几条就算数。

用法：python -m tests.test_ann_index
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

problems: list[str] = []
skipped: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'✓' if cond else '✗'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


DIM = 32


def _rand_unit_vecs(n: int, seed: int = 1):
    import numpy as np

    rng = np.random.default_rng(seed)
    v = rng.standard_normal((n, DIM)).astype("float32")
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v


def test_ann_index_basics() -> None:
    print("─" * 70)
    print("① AnnIndex 本身：加/删/查/存盘/读盘")
    print("─" * 70)
    from synorive.search.ann_index import AnnIndex

    tmp = Path(os.environ.get("TMP", "/tmp")) / "syn-ann-basic"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    vecs = _rand_unit_vecs(200)
    ann = AnnIndex(dim=DIM, model_tag="test:32", index_path=tmp / "idx.usearch")
    check(not ann.active, "刚建的空索引不 active", "空索引不该是 active 的")

    ann.add_many(list(range(200)), vecs)
    check(ann.size == 200, f"加了 200 个向量，size={ann.size}", f"size 不对：{ann.size}")

    hits = ann.search(vecs[0].tolist(), 5)
    check(hits and hits[0][0] == 0,
          f"查它自己最相似的是它自己（rowid=0，距离={hits[0][1]:.4f}）",
          f"自己查自己排第一都不对：{hits[:3]}")
    check(hits[0][1] < 0.01, f"自己和自己的距离接近 0（{hits[0][1]:.6f}）", "自查距离不接近 0")

    ann.remove(0)
    check(ann.size == 199, "删掉一个之后 size 减一", f"删除没生效：{ann.size}")
    hits2 = ann.search(vecs[0].tolist(), 5)
    check(all(k != 0 for k, _ in hits2), "删掉的 rowid 不会再出现在查询结果里",
          f"删除后还能查到：{hits2}")

    ann.save()
    # 🔴 这条一开始检查错了文件名：`Path("idx.usearch").with_suffix(".meta")`
    # 是**替换**最后一段后缀，产出 "idx.meta" 而不是 "idx.usearch.meta"——
    # 代码本身（save/load 两边用的是同一个 with_suffix 调用）是自洽的，
    # 是这条测试断言写错了要检查的文件名，不是 AnnIndex 的 bug
    check((tmp / "idx.usearch").exists() and (tmp / "idx.meta").exists(),
          "存盘产出了索引文件和元信息文件", "存盘没有产出文件")

    ann2 = AnnIndex(dim=DIM, model_tag="test:32", index_path=tmp / "idx.usearch")
    loaded = ann2.load()
    check(loaded and ann2.size == 199, f"重新加载后 size 一致：{ann2.size}", "重新加载后数据对不上")

    ann3 = AnnIndex(dim=DIM, model_tag="different-model:64", index_path=tmp / "idx.usearch")
    loaded3 = ann3.load()
    check(not loaded3, "model_tag 不一致时拒绝加载（防止维度不同的向量混进来）",
          "model_tag 不一致时不该加载成功")


def test_distance_formula_parity() -> None:
    print()
    print("─" * 70)
    print("② 🔴 距离公式一致性：ANN 和 sqlite-vec 暴力扫描对同一批向量、")
    print("   同一个查询，必须给出数值一致的距离——这条专治「静默算错」")
    print("─" * 70)
    import numpy as np
    import sqlite_vec

    from synorive.search.ann_index import AnnIndex
    from synorive.store.db import Database

    tmp = Path(os.environ.get("TMP", "/tmp")) / "syn-ann-parity"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    vecs = _rand_unit_vecs(500, seed=7)
    db = Database(tmp / "parity.db")
    db.initialize()
    db.ensure_vector_tables(DIM, "test-model")
    conn = db.connect()
    conn.execute("BEGIN")
    for i, v in enumerate(vecs):
        conn.execute(
            "INSERT INTO vec_chunks (chunk_rowid, embedding) VALUES (?,?)",
            (i, sqlite_vec.serialize_float32(v.tolist())),
        )
    conn.execute("COMMIT")

    ann = AnnIndex(dim=DIM, model_tag="parity", index_path=tmp / "ann.usearch")
    ann.add_many(list(range(500)), vecs)

    query = _rand_unit_vecs(1, seed=999)[0]
    blob = sqlite_vec.serialize_float32(query.tolist())
    sql_rows = conn.execute(
        "SELECT chunk_rowid, distance FROM vec_chunks WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (blob, 10),
    ).fetchall()
    sql_top = [(int(r["chunk_rowid"]), float(r["distance"])) for r in sql_rows]

    ann_top = ann.search(query.tolist(), 10)

    print(f"  sqlite-vec 前 3：{sql_top[:3]}")
    print(f"  ANN       前 3：{ann_top[:3]}")

    sql_ids = {rid for rid, _ in sql_top}
    ann_ids = {rid for rid, _ in ann_top}
    overlap = len(sql_ids & ann_ids)
    check(overlap >= 8, f"Top10 命中集合重叠 {overlap}/10（ANN 是近似的，允许极少数不一致）",
          f"重叠太少（{overlap}/10），ANN 召回质量有问题")

    sql_by_id = dict(sql_top)
    ann_by_id = dict(ann_top)
    common = sql_ids & ann_ids
    max_diff = max(abs(sql_by_id[i] - ann_by_id[i]) for i in common) if common else 999
    check(max_diff < 0.01,
          f"共同命中的那些条目，两条路径算出的距离数值一致（最大误差 {max_diff:.6f}）",
          f"距离数值对不上（最大误差 {max_diff:.4f}）—— 这正是 usearch cos 距离"
          f"是 sqlite-vec 距离一半那个坑，如果这条挂了先检查 ann_index.py 的 ×2 换算",
          )
    db.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def build_synthetic_corpus(data_dir: Path, target: int) -> None:
    """
    直接在存储层灌合成数据，复用 `bench_scale.py` 的做法和理由：
    真投喂这么多块要几个小时，而这里要验的是"规模大了 ANN 接不接管、
    质量掉不掉"，不是重测吞吐（那是 A7 的事）。
    """
    import random

    from synorive.store.db import Database
    from synorive.store.repository import _path_words
    from synorive.store.text import to_index_text

    DIM_REAL = 512
    MODEL_ID = "bge-small-zh-v1.5"
    NOUNS = ["向量检索", "多模态", "知识图谱", "语义搜索", "分块", "嵌入模型", "并发调度", "断点续传"]
    # 埋几个能被真实语义查询命中的"锚点文档"，之后用它们验证 ANN 召回质量
    ANCHORS = [
        ("bench-anchor-0001", "关于 ANN 近似最近邻索引如何加速大规模向量检索的详细说明。"
                              "当数据库规模达到百万级别时，暴力扫描的延迟会线性增长，"
                              "而基于图结构的近似最近邻算法可以把查询延迟维持在毫秒级别。"),
        ("bench-anchor-0002", "剪贴板哨兵功能会在后台持续监听系统剪贴板的变化，"
                              "捕获到文本或链接后不会自动归档，只在内存里保留最近若干条，"
                              "用户主动点击才会真正写入数据库进行索引。"),
        ("bench-anchor-0003", "知识图谱模块从文档里抽取实体后，会计算实体之间的共现关系，"
                              "点击某个实体可以看到与它关联最紧密的其它实体，"
                              "再点一次可以跳转到搜索页面查看所有提到这个实体的原始内容。"),
    ]

    db = Database(data_dir / "synorive.db")
    db.initialize()
    db.ensure_vector_tables(DIM_REAL, MODEL_ID)
    rng = random.Random(2026)
    conn = db.connect()
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA cache_size = -262144")

    have = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    if have >= target:
        print(f"[build] 已有 {have:,} 块 ≥ 目标 {target:,}，跳过")
        return

    def rand_vec(local_rng: random.Random) -> list[float]:
        v = [local_rng.gauss(0, 1) for _ in range(DIM_REAL)]
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v]

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    BATCH = 5000
    next_item_rid = (conn.execute("SELECT COALESCE(MAX(rowid),0) FROM items").fetchone()[0]) + 1
    next_chunk_rid = (conn.execute("SELECT COALESCE(MAX(rowid),0) FROM chunks").fetchone()[0]) + 1
    done = have
    t0 = time.perf_counter()

    # 🔴 第一版这里塞的是"伪向量"（人为构造的正交子空间基向量），
    # 而查询走的是**真实** BGE 模型编码出来的向量——两者根本不在同一个语义空间，
    # 真实查询向量和这种瞎造的向量之间的相似度毫无意义，检索测出来"没命中"
    # 是必然的，不是 ANN 或排序哪里错了。要测"检索质量"，锚点文档就必须
    # 也走**同一个真实嵌入模型**，这是这段测试代码本身的 bug，不是被测代码的。
    from synorive.analyze.embedder import TextEmbedder

    embedder = TextEmbedder(ROOT.parent / "data" / "models" / "bge-small-zh-v1.5")
    embedder.load()

    for j, (aid, text) in enumerate(ANCHORS):
        base = embedder.encode_one(text, is_query=False)

        irid = next_item_rid
        crid = next_chunk_rid
        title = f"锚点文档·{aid}"
        locator = f"D:\\bench\\anchors\\{aid}.md"
        conn.execute(
            "INSERT INTO items (rowid,id,fingerprint,modality,source,status,title,locator,"
            "snippet,mime,size_bytes,content_time,created_at,updated_at,last_opened_at,"
            "open_count,thumb_path,meta_json,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (irid, aid, f"fp-{aid}", "text", "file", "done", title, locator,
             text[:160], "text/markdown", len(text) * 3, None, now, now, None, 0, None, "{}", None),
        )
        conn.execute(
            "INSERT INTO chunks (rowid,id,item_id,chunk_index,text,channel,page,"
            "start_sec,end_sec,bbox_json,section,token_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (crid, f"{aid}-c0", aid, 0, text, "body", None, None, None, None, None, len(text)),
        )
        conn.execute("INSERT INTO items_fts (rowid,title,snippet,locator) VALUES (?,?,?,?)",
                     (irid, to_index_text(title), to_index_text(text[:160]), to_index_text(_path_words(locator))))
        conn.execute("INSERT INTO items_tri (rowid,title,locator) VALUES (?,?,?)", (irid, title, locator))
        conn.execute("INSERT INTO chunks_fts (rowid,text) VALUES (?,?)", (crid, to_index_text(text)))
        import sqlite_vec as _sv
        conn.execute("INSERT INTO vec_chunks (chunk_rowid,embedding) VALUES (?,?)",
                     (crid, _sv.serialize_float32(base.astype("float32").tolist())))
        next_item_rid += 1
        next_chunk_rid += 1
    conn.commit()

    while done < target:
        n = min(BATCH, target - done)
        items, chunks, ifts, itri, cfts, vecs = [], [], [], [], [], []
        for i in range(n):
            idx = done + i
            irid = next_item_rid + i
            crid = next_chunk_rid + i
            iid = f"bench-{idx:08d}"
            text = f"{rng.choice(NOUNS)}相关的合成测试文档第{idx}号，内容用于压测存储层性能。"
            title = f"合成文档 {idx:08d}"
            locator = f"D:\\bench\\{idx // 1000:04d}\\doc_{idx:08d}.md"
            items.append((irid, iid, f"fp{idx:016x}", "text", "file", "done", title, locator,
                         text[:160], "text/markdown", len(text) * 3, None, now, now, None, 0, None, "{}", None))
            chunks.append((crid, f"{iid}-c0", iid, 0, text, "body", None, None, None, None, None, len(text)))
            ifts.append((irid, to_index_text(title), to_index_text(text[:160]), to_index_text(_path_words(locator))))
            itri.append((irid, title, locator))
            cfts.append((crid, to_index_text(text)))
            vecs.append((crid, struct.pack(f"{DIM_REAL}f", *rand_vec(rng))))

        conn.execute("BEGIN")
        conn.executemany(
            "INSERT INTO items (rowid,id,fingerprint,modality,source,status,title,locator,"
            "snippet,mime,size_bytes,content_time,created_at,updated_at,last_opened_at,"
            "open_count,thumb_path,meta_json,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            items,
        )
        conn.executemany(
            "INSERT INTO chunks (rowid,id,item_id,chunk_index,text,channel,page,"
            "start_sec,end_sec,bbox_json,section,token_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            chunks,
        )
        conn.executemany("INSERT INTO items_fts (rowid,title,snippet,locator) VALUES (?,?,?,?)", ifts)
        conn.executemany("INSERT INTO items_tri (rowid,title,locator) VALUES (?,?,?)", itri)
        conn.executemany("INSERT INTO chunks_fts (rowid,text) VALUES (?,?)", cfts)
        conn.executemany("INSERT INTO vec_chunks (chunk_rowid,embedding) VALUES (?,?)", vecs)
        conn.execute("COMMIT")
        next_item_rid += n
        next_chunk_rid += n
        done += n
        rate = (done - have) / (time.perf_counter() - t0)
        print(f"\r[build] {done:,}/{target:,}　{rate:,.0f} 块/秒", end="", flush=True)
    print()
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


class Engine:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Engine":
        model_dir = ROOT.parent / "data" / "models"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "synorive.main", "--port", str(self.port),
             "--data-dir", str(self.data_dir), "--model-dir", str(model_dir)],
            cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
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


def test_end_to_end_at_scale() -> None:
    print()
    print("─" * 70)
    print("③ 真实规模端到端：灌过 ANN_THRESHOLD，起真引擎，验证自动接管 + 质量")
    print("─" * 70)
    from synorive.search.ann_index import ANN_THRESHOLD

    target = ANN_THRESHOLD + 20_000  # 刚过阈值一点，够验证"接管"这件事，不用真的堆到 100 万
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-ann-e2e"
    data_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    build_synthetic_corpus(data_dir, target)
    build_s = time.perf_counter() - t0
    print(f"  构建 {target:,} 块耗时 {build_s:.1f}s")

    with Engine(data_dir) as eng:
        stats = eng.call("/api/stats")
        check(stats.get("chunks", 0) >= target,
              f"库里确实有 {stats.get('chunks', 0):,} 块", f"块数不够：{stats}")

        # ANN 自动重建是后台线程，给它一点时间；轮询状态而不是傻等固定秒数
        ann_status = None
        for _ in range(180):
            ann_status = eng.call("/api/search/ann/status")
            if ann_status.get("active"):
                break
            time.sleep(2)
        print(f"  ANN 状态：{ann_status}")
        if ann_status is None or not ann_status.get("available"):
            skipped.append("ANN 不可用（usearch 没装成功？），③ 后续跳过，不计入通过")
            return
        check(ann_status.get("active") is True,
              f"库超过阈值后 ANN 自动接管了（{ann_status.get('size'):,} 个向量）",
              f"等了 6 分钟 ANN 还没接管：{ann_status}")

        # 用埋好的锚点文档验证检索质量——不是随便查一个词看有没有结果，
        # 而是断言查询结果里排最前的就是语义上真正相关的那篇
        queries_and_anchors = [
            ("近似最近邻索引怎么加速向量检索", "bench-anchor-0001"),
            ("剪贴板监听捕获文本链接自动归档", "bench-anchor-0002"),
            ("知识图谱实体共现关系跳转搜索", "bench-anchor-0003"),
        ]
        lat = []
        for q, want_id in queries_and_anchors:
            t0 = time.perf_counter()
            res = eng.call("/api/search", {"query": q, "limit": 10, "stage": "semantic"})
            lat.append((time.perf_counter() - t0) * 1000)
            hits = res.get("hits") or []
            top_ids = [h["item"]["id"] for h in hits[:3]]
            check(want_id in top_ids,
                  f"「{q[:20]}…」Top3 命中了对应的锚点文档",
                  f"「{q[:20]}…」没有命中 {want_id}，Top3 实际是 {top_ids}")

        p50 = sorted(lat)[len(lat) // 2]
        print(f"  ANN 接管后语义检索延迟：{[f'{x:.0f}ms' for x in lat]}，P50={p50:.0f}ms")
        check(p50 < 1000, f"接管后的查询延迟 P50={p50:.0f}ms，远好于同规模暴力扫描的量级",
              f"延迟异常：{p50:.0f}ms")


def main() -> int:
    test_ann_index_basics()
    test_distance_formula_parity()
    test_end_to_end_at_scale()

    print()
    print("=" * 70)
    for s in skipped:
        print(f"⚠ 跳过（不算通过）：{s}")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ A17 ANN 索引通过" + ("（含上面标注的跳过项）" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
