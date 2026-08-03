#!/usr/bin/env python
"""
A17 索引上限压测 —— 灌到 100 万块，看还撑不撑得住
====================================================================
为什么用批量灌合成数据，而不是真投喂 100 万块：
    本机真实吞吐 47 块/秒（A7 实测），100 万块要跑 5.9 小时。
    **而 A17 考的是"索引到 100 万条还撑不撑得住"，不是重测吞吐**
    —— 吞吐已经由 A7 单独量过了。所以这里绕开分块和向量推理，
    直接在存储层灌，把时间花在真正要验的东西上：
        FTS5 / sqlite-vec 在 100 万行时的检索延迟、磁盘、内存。

诚实声明（会打进报告，不藏着）：
    · 文本是从**真实中文词表**拼的，不是随机字节 —— 否则 FTS 分词行为
      和真实情况对不上，测出来的延迟没有意义。
    · 向量是随机单位向量。它对 KNN 的**计算量**和真向量完全一样
      （维度、距离运算、扫描行数都不变），但**召回质量无从谈起**
      —— 召回质量归 A20 管，这里只量性能。

用法：
    python -m tests.bench_scale build   --data-dir <dir> --target 1000000
    python -m tests.bench_scale measure --data-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DIM = 512
BATCH = 5000
#: 必须和真实文本模型一致，否则引擎会判定模型变了并丢弃全部向量
EMBED_MODEL_ID = "bge-small-zh-v1.5"

# 真实中文词表：从领域词典 + 常用词拼句子。
# 用真词而不是随机字，是因为 jieba 预分词 + unicode61 的行为完全取决于词形，
# 拿随机字节测出来的 FTS 延迟不能代表真实负载。
NOUNS = [
    "多模态", "关键帧", "向量检索", "语义搜索", "倒排索引", "分词器", "嵌入模型",
    "知识图谱", "时间轴", "缩略图", "断点续传", "增量索引", "全文检索", "跨模态",
    "文件管理器", "分析中心", "剪贴板", "快照", "订阅", "指纹", "去重", "召回率",
    "混合检索", "重排序", "候选集", "相似度", "余弦距离", "量化", "推理会话",
]
VERBS = ["解析", "抽取", "归档", "聚合", "过滤", "排序", "压缩", "校验", "同步", "回放"]
ADJS = ["并发的", "极速的", "离线的", "本地的", "增量的", "可中断的", "低延迟的"]
TAILS = [
    "把结果写回数据库。", "支持断点续跑。", "耗时随行数线性增长。",
    "在弱网环境下也能用。", "不占用界面线程。", "结果按相关度倒序。",
]


def make_text(rng: random.Random) -> str:
    """拼一段 200~400 字的中文，词形真实、长度贴近实测均值 293 字。"""
    parts = []
    while sum(len(p) for p in parts) < rng.randint(200, 400):
        parts.append(
            f"{rng.choice(ADJS)}{rng.choice(NOUNS)}{rng.choice(VERBS)}"
            f"{rng.choice(NOUNS)}，{rng.choice(TAILS)}"
        )
    return "".join(parts)


def rand_vec(rng: random.Random) -> bytes:
    """随机单位向量。归一化是必须的 —— 检索侧按 L2 归一化后的余弦距离算。"""
    v = [rng.gauss(0, 1) for _ in range(DIM)]
    n = sum(x * x for x in v) ** 0.5
    return struct.pack(f"{DIM}f", *[x / n for x in v])


def build(data_dir: Path, target: int) -> int:
    from synorive.store.db import Database
    from synorive.store.repository import _path_words
    from synorive.store.text import to_index_text

    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "synorive.db")
    db.initialize()
    # 🔴 必须写**真实模型 id**。engine 启动时会拿 meta_kv.embed_model 和
    #    当前模型比对，对不上就 DROP vec_chunks —— 写个 "bench-synthetic-512"
    #    的话，一起引擎就把刚灌了半小时的 100 万条向量全清掉，
    #    而且不报错，只是检索突然一条都不返回。
    #    向量内容是随机的（见文件头声明），但它占的维度、空间和计算量与真向量一致，
    #    A17 量的是规模下的延迟，不是召回质量（那归 A20）。
    db.ensure_vector_tables(DIM, EMBED_MODEL_ID)

    rng = random.Random(20260802)
    conn = db.connect()

    # 批量灌专用档位。**跑完必须调回去** —— synchronous=OFF 时断电会丢数据，
    # 这只在"数据本来就是造出来的、丢了重造就行"的场景下可接受。
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA cache_size = -262144")   # 256 MB 页缓存

    have = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"[build] 现有 {have:,} 块，目标 {target:,} 块")
    if have >= target:
        print("[build] 已达标，跳过")
        return have

    t0 = time.perf_counter()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    done = have
    batch_no = 0

    # rowid 自己发，不靠自增。
    # ⚠️ 四张索引表全是 contentless FTS / vec0，**靠 rowid 和主表关联**，
    #    让它自增就等于赌"插入顺序和主表一致"，一旦有一批 INSERT OR IGNORE
    #    被忽略就整体错位，而且不报错、只是查不出来。
    next_item_rid = (conn.execute("SELECT COALESCE(MAX(rowid),0) FROM items").fetchone()[0]) + 1
    next_chunk_rid = (conn.execute("SELECT COALESCE(MAX(rowid),0) FROM chunks").fetchone()[0]) + 1

    while done < target:
        n = min(BATCH, target - done)
        items, chunks, ifts, itri, cfts, vecs = [], [], [], [], [], []

        for i in range(n):
            idx = done + i
            irid = next_item_rid + i
            crid = next_chunk_rid + i
            iid = f"bench-{idx:08d}"
            text = make_text(rng)
            title = f"合成文档 {idx:08d}·{rng.choice(NOUNS)}"
            locator = f"D:\\bench\\{idx // 1000:04d}\\doc_{idx:08d}.md"

            items.append((
                irid, iid, f"fp{idx:016x}", "text", "file", "done",
                title, locator, text[:160], "text/markdown", len(text) * 3,
                None, now, now, None, 0, None, "{}", None,
            ))
            chunks.append((crid, f"{iid}-c0", iid, 0, text, "body",
                           None, None, None, None, len(text)))
            # FTS 侧必须走和索引管线同一套预分词，否则索引侧和查询侧对不上，
            # 症状就是"灌进去了但一条也查不出来"。
            ifts.append((irid, to_index_text(title), to_index_text(text[:160]),
                         to_index_text(_path_words(locator))))
            # trigram 表存**原文**，它就是拿来做子串匹配的，分词反而没意义
            itri.append((irid, title, locator))
            cfts.append((crid, to_index_text(text)))
            vecs.append((crid, rand_vec(rng)))

        conn.execute("BEGIN")
        conn.executemany(
            "INSERT INTO items (rowid,id,fingerprint,modality,source,status,title,locator,"
            "snippet,mime,size_bytes,content_time,created_at,updated_at,last_opened_at,"
            "open_count,thumb_path,meta_json,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            items,
        )
        conn.executemany(
            "INSERT INTO chunks (rowid,id,item_id,chunk_index,text,channel,page,"
            "start_sec,end_sec,bbox_json,token_count) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
        batch_no += 1
        if batch_no % 10 == 0 or done >= target:
            el = time.perf_counter() - t0
            rate = (done - have) / el if el else 0
            eta = (target - done) / rate if rate else 0
            print(f"[build] {done:,}/{target:,}　{rate:,.0f} 块/秒　剩余 {eta / 60:.1f} 分", flush=True)

    conn.execute("PRAGMA synchronous = NORMAL")   # 调回安全档
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("ANALYZE")
    conn.close()
    print(f"[build] 完成，用时 {(time.perf_counter() - t0) / 60:.1f} 分")
    return done


def db_size(data_dir: Path) -> float:
    return sum(
        p.stat().st_size for p in data_dir.glob("synorive.db*") if p.is_file()
    ) / 1024 / 1024


def measure(data_dir: Path, model_dir: Path) -> dict:
    """起真引擎，走 HTTP，量用户真正会碰到的那条路径。"""
    import urllib.error
    import urllib.request

    port = 8931
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "synorive.main", "--port", str(port),
         "--data-dir", str(data_dir), "--model-dir", str(model_dir)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    def call(path: str, payload: dict | None = None, timeout: float = 60) -> dict:
        url = f"http://127.0.0.1:{port}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    try:
        boot0 = time.perf_counter()
        for _ in range(180):
            try:
                call("/health", timeout=3)
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("引擎 180 秒没起来")
        boot = time.perf_counter() - boot0

        stats = call("/api/stats")
        print(f"\n引擎就绪 {boot:.1f}s　库里 {stats['items']:,} 条 / {stats.get('chunks', 0):,} 块")

        # A17 追加：库超过 ANN_THRESHOLD 时引擎会在后台自动建一次 usearch 索引
        # （见 runtime.py `_load_ann_index`），这个数据目录之前没有 ANN 索引文件，
        # 100 万个 512 维向量的 HNSW 图要建一阵子。不等它建完就跑语义检索基准，
        # 量到的还是旧的暴力扫描延迟——等于白测，量的不是这次要验的东西。
        ann_t0 = time.perf_counter()
        ann_status = {}
        for _ in range(1800):  # 最多等 30 分钟，先到先停
            try:
                ann_status = call("/api/search/ann/status", timeout=5)
            except Exception:
                ann_status = {}
            if ann_status.get("active"):
                break
            time.sleep(1)
        ann_wait_s = time.perf_counter() - ann_t0
        if ann_status.get("active"):
            print(f"ANN 索引已接管，等了 {ann_wait_s:.0f}s，{ann_status.get('size', 0):,} 个向量")
        else:
            print(
                f"⚠ 等了 {ann_wait_s:.0f}s，ANN 索引仍未接管（状态：{ann_status}）——"
                "下面量到的语义检索延迟可能还是暴力扫描的数字，不是 ANN 加速后的"
            )

        queries = [
            "向量检索", "多模态分析", "关键帧", "断点续传", "语义搜索",
            "并发的分词器", "知识图谱聚合", "低延迟的重排序", "缩略图归档", "跨模态召回",
        ]

        def run(stage: str, rounds: int = 4) -> tuple[list[float], int]:
            lat, hits = [], 0
            for _ in range(rounds):
                for q in queries:
                    t = time.perf_counter()
                    r = call("/api/search", {"query": q, "limit": 30, "stage": stage})
                    lat.append((time.perf_counter() - t) * 1000)
                    hits += len(r.get("hits") or r.get("results") or [])
            return lat, hits

        run("keyword", rounds=1)   # 预热，不计数
        kw, kw_hits = run("keyword")
        sem, sem_hits = run("semantic")

        def pct(xs: list[float], p: float) -> float:
            s = sorted(xs)
            return s[min(len(s) - 1, int(len(s) * p))]

        health = call("/health")
        out = {
            "items": stats["items"],
            "chunks": stats.get("chunks", 0),
            "boot_s": round(boot, 2),
            "disk_mb": round(db_size(data_dir), 1),
            "engine_mem_mb": health.get("memoryMb"),
            "keyword_p50": round(statistics.median(kw), 1),
            "keyword_p95": round(pct(kw, 0.95), 1),
            "semantic_p50": round(statistics.median(sem), 1),
            "semantic_p95": round(pct(sem, 0.95), 1),
            "keyword_hits": kw_hits,
            "semantic_hits": sem_hits,
            "ann_active": bool(ann_status.get("active")),
            "ann_size": ann_status.get("size", 0),
            "ann_wait_s": round(ann_wait_s, 1),
        }
        return out
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["build", "measure"])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--target", type=int, default=1_000_000)
    ap.add_argument("--model-dir", default=str(ROOT.parent / "data" / "models"),
                    help="向量模型在哪。不给对的话查询侧编码不出来，语义那一路是死的")
    a = ap.parse_args()
    d = Path(a.data_dir)

    if a.mode == "build":
        n = build(d, a.target)
        print(f"\n块数 {n:,}　磁盘 {db_size(d):.1f} MB")
        return 0

    md = Path(a.model_dir)
    if not md.exists():
        print(f"✗ 模型目录不存在：{md}")
        return 1
    r = measure(d, md)
    print("\n" + "=" * 68)
    print(f"A17 索引规模 {r['chunks']:,} 块 / {r['items']:,} 条")
    print("=" * 68)
    print(f"  磁盘占用          {r['disk_mb']:,.1f} MB")
    print(f"  引擎内存          {r['engine_mem_mb']} MB")
    print(f"  冷启动            {r['boot_s']} s")
    print(f"  ANN 索引          {'已接管 · ' + format(r['ann_size'], ',') + ' 个向量' if r['ann_active'] else '未接管'}"
          f"（等待 {r['ann_wait_s']}s）")
    print(f"  首屏 keyword      P50 {r['keyword_p50']} ms　P95 {r['keyword_p95']} ms　（A2 门槛 ≤80 / ≤200）")
    print(f"  完整 semantic     P50 {r['semantic_p50']} ms　P95 {r['semantic_p95']} ms　（A3 门槛 P95 ≤500）")
    print(f"  命中总数          keyword {r['keyword_hits']}　semantic {r['semantic_hits']}")

    bad = []
    if r["chunks"] < 1_000_000:
        bad.append(f"块数 {r['chunks']:,} < 100 万")
    if r["keyword_hits"] == 0 or r["semantic_hits"] == 0:
        bad.append("检索一条都没返回 —— 库灌进去了但查不出来")
    if r["keyword_p95"] > 200:
        bad.append(f"首屏 P95 {r['keyword_p95']}ms > 200")
    if r["semantic_p95"] > 500:
        bad.append(f"完整检索 P95 {r['semantic_p95']}ms > 500")
    print("=" * 68)
    for b in bad:
        print(f"✗ {b}")
    if not bad:
        print("✓ A17 通过：100 万块下检索延迟仍在 A2/A3 门槛内")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
