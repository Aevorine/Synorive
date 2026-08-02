#!/usr/bin/env python
"""
A20 中文检索准确率评测 —— 100 题 Top5 命中率
====================================================================
验收标准原文：中文检索 100 题 Top5 ≥85%

做法：
    ① 把 60 篇主题互不重叠的中文短文写成 .md（cn_corpus.py）
    ② 用**真实索引管线**吃进去（真分词、真向量，不是合成数据）
    ③ 跑 100 道题，每题有唯一正确答案，看正确答案在不在前 5 条里
    ④ 按题型分组出数 —— 总分好看但某一类全崩，是必须暴露出来的

为什么按题型分组：
    只看总分会掩盖结构性缺陷。比如关键词一路很强、语义一路是坏的，
    总分照样能到 80%（exact + typo + locator 就占了 38 题），
    而用户真正常用的"换个说法搜"那类题可能一题都不对。
    **分组出数才看得见这种情况。**

用法：
    python -m tests.eval_cn_retrieval --data-dir <临时目录>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.cn_corpus import DISTRACTORS, DOCS, QUERIES  # noqa: E402

TOPK = 5
def _free_port() -> int:
    """
    挑一个真正空闲的端口。

    ⚠️ 别写死端口号。写死时两个评测同时跑，**第二个的请求会全部打到第一个的
       引擎上**，而且不报错 —— 它会读到别人的库、别人的统计，跑出一份看起来
       正常的结果；等前一个跑完关掉引擎，后一个才以 ConnectionReset 崩掉，
       现场已经完全对不上了。栽过一次，排查花的时间比写这个函数多得多。
    """
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


PORT = _free_port()


def write_corpus(dst: Path) -> Path:
    """
    把语料落成 .md。文件名带编号，locator 类题目就是冲着它来的。

    正文和同领域干扰项**混在同一个目录、不做任何标记** —— 检索侧无从区分，
    这才是真实场景。分开放会让干扰形同虚设。
    """
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    written = 0
    for did, title, body in [*DOCS, *DISTRACTORS]:
        (dst / f"{did}_{title}.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        written += 1
    # 落盘数必须对得上，否则后面所有数字都建立在一个没写全的语料上
    assert written == len(DOCS) + len(DISTRACTORS), f"只写了 {written} 篇"
    return dst


class Engine:
    """起一个真引擎，用完关掉。"""

    def __init__(self, data_dir: Path, model_dir: Path) -> None:
        self.data_dir = data_dir
        self.model_dir = model_dir
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Engine":
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        # ⚠️ --model-dir 默认是 <data-dir>/models。评测用的是临时数据目录，
        #    里面没有模型 → 向量算不出来 → 所有条目卡在 status='partial'，
        #    语义那一路全空。而**关键词一路照样有结果**，所以看起来像
        #    "检索能用，只是准确率低"，非常容易误判成算法问题。
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "synorive.main", "--port", str(PORT),
             "--data-dir", str(self.data_dir), "--model-dir", str(self.model_dir)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(180):
            try:
                self.call("/health", timeout=3)
                return self
            except Exception:
                time.sleep(1)
        raise RuntimeError("引擎 180 秒没起来")

    def __exit__(self, *exc: object) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def call(self, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
        import urllib.request
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}{path}", data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())


def doc_id_of(hit: dict) -> str:
    """
    从命中结果里还原是哪一篇。

    ⚠️ 命中对象的形状是 {item, score, highlight}，**locator 在 item 里面**，
       不在顶层。在顶层取会拿到 None → 编号解析成空串 → 每题都算未命中，
       表现出来是"检索准确率 0%"，看起来像检索坏了，其实是评测脚本坏了。
    """
    item = hit.get("item") if isinstance(hit.get("item"), dict) else hit
    loc = item.get("locator") or item.get("path") or ""
    name = Path(str(loc)).name
    if "_" in name:
        return name.split("_", 1)[0]
    title = str(item.get("title") or "")
    return title.split("_", 1)[0] if "_" in title else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--keep", action="store_true", help="跑完不删数据目录，方便人工复查")
    ap.add_argument("--model-dir", default=str(ROOT.parent / "data" / "models"),
                    help="向量模型在哪。默认用项目 data/models，别让它落到临时目录里")
    ap.add_argument("--bulk-dir", default=None,
                    help="再灌一批无关文档做体量干扰（比如压测语料目录）")
    ap.add_argument("--bulk-limit", type=int, default=3000)
    a = ap.parse_args()

    data_dir = Path(a.data_dir)
    corpus = write_corpus(data_dir / "corpus")
    n_bulk = 0
    if a.bulk_dir:
        src = Path(a.bulk_dir)
        bulk = corpus.parent / "bulk"
        bulk.mkdir(parents=True, exist_ok=True)
        for f in src.rglob("*.md"):
            if n_bulk >= a.bulk_limit:
                break
            shutil.copy2(f, bulk / f"bulk_{n_bulk:06d}.md")
            n_bulk += 1

    corpus_total = len(DOCS) + len(DISTRACTORS) + n_bulk
    print(f"语料：正文 {len(DOCS)} 篇 + 同领域干扰 {len(DISTRACTORS)} 篇"
          f" + 体量干扰 {n_bulk} 篇 = {corpus_total} 篇")
    print(f"题目 {len(QUERIES)} 道　Top{TOPK} 相当于要求答案落在前 "
          f"{TOPK / corpus_total * 100:.2f}% —— 这个比例才决定这套题难不难")

    if (data_dir / "synorive.db").exists():
        for p in data_dir.glob("synorive.db*"):
            p.unlink()

    model_dir = Path(a.model_dir)
    if not model_dir.exists():
        print(f"✗ 模型目录不存在：{model_dir}")
        return 1
    print(f"模型目录 {model_dir}")

    with Engine(data_dir, model_dir) as eng:
        # ── 索引 ────────────────────────────────────────────
        t0 = time.perf_counter()
        targets = [str(corpus)] + ([str(corpus.parent / "bulk")] if n_bulk else [])
        eng.call("/api/ingest", {"targets": targets, "source": "file", "recursive": True})
        # 必须等到 ready == 篇数，不能只等 items。
        # ⚠️ items 是"文件登记进来了"，ready 才是"向量算完了、语义那一路能用了"。
        #    中间态是 status='partial'：关键词能查，语义查不到。
        #    第一版这里写的是 `vectors >= chunks`，而 stats 根本没有 vectors 字段，
        #    于是退化成 `chunks >= chunks` —— **恒真，等于没等**，
        #    结果拿着一个语义路全空的库跑完 100 题，报出来 0%。
        #    不可能失败的等待条件，和不可能失败的断言是同一类错。
        s = eng.call("/api/stats")
        for _ in range(3600):
            if s.get("ready", 0) >= corpus_total:
                break
            time.sleep(2)
            s = eng.call("/api/stats")
        if s.get("ready", 0) < corpus_total:
            print(f"✗ 向量没算完就超时了：ready {s.get('ready')}/{corpus_total}，"
                  f"items {s.get('items')}，chunks {s.get('chunks')}")
            print("  这时候跑评测只会量到关键词那一路，数字没有意义，直接停。")
            return 1
        print(f"索引完成 {s['items']} 篇 / {s.get('chunks', 0)} 块 / ready {s['ready']}，"
              f"用时 {time.perf_counter() - t0:.1f}s\n")

        # ── 跑题 ────────────────────────────────────────────
        by_kind: dict[str, list[bool]] = defaultdict(list)
        misses: list[tuple[str, str, str, list[str]]] = []
        ranks: list[int] = []

        for q, want, kind in QUERIES:
            r = eng.call("/api/search", {"query": q, "limit": TOPK, "stage": "semantic"})
            hits = r.get("hits") or r.get("results") or []
            got = [doc_id_of(h) for h in hits]
            ok = want in got[:TOPK]
            by_kind[kind].append(ok)
            if ok:
                ranks.append(got.index(want) + 1)
            else:
                misses.append((q, want, kind, got[:TOPK]))

        # ── 出数 ────────────────────────────────────────────
        total = sum(len(v) for v in by_kind.values())
        hit = sum(sum(v) for v in by_kind.values())
        print("=" * 66)
        print(f"A20 中文检索 {total} 题 · Top{TOPK} 命中率　（语料 {corpus_total} 篇）")
        print("=" * 66)
        order = ["exact", "semantic", "natural", "typo", "compound", "locator"]
        label = {
            "exact": "原词命中", "semantic": "换个说法（文中无此词）", "natural": "口语长句",
            "typo": "错别字", "compound": "概念组合", "locator": "文件名/路径",
        }
        for k in order:
            v = by_kind.get(k, [])
            if not v:
                continue
            pct = sum(v) / len(v) * 100
            bar = "█" * int(pct / 5)
            print(f"  {label[k]:<22} {sum(v):>3}/{len(v):<3} {pct:5.1f}%  {bar}")
        print("-" * 66)
        pct = hit / total * 100
        print(f"  {'总计':<22} {hit:>3}/{total:<3} {pct:5.1f}%　（门槛 ≥85%）")
        if ranks:
            print(f"  命中题的平均排名 {sum(ranks) / len(ranks):.2f}　"
                  f"排第 1 的有 {ranks.count(1)} 题")

        if misses:
            print(f"\n未命中 {len(misses)} 题：")
            for q, want, kind, got in misses:
                print(f"  [{kind}] 「{q}」　应为 {want}，前{TOPK} = {got}")

        print("=" * 66)
        if pct >= 85:
            print(f"✓ A20 通过：{pct:.1f}% ≥ 85%")
        else:
            print(f"✗ A20 未达标：{pct:.1f}% < 85%")

        # 结构性缺陷单独报 —— 总分过了但某一类塌了，同样要暴露
        weak = [k for k in order if by_kind.get(k) and sum(by_kind[k]) / len(by_kind[k]) < 0.6]
        if weak:
            print(f"⚠ 这几类明显偏弱，总分掩盖不了：{'、'.join(label[k] for k in weak)}")

    if not a.keep:
        shutil.rmtree(data_dir, ignore_errors=True)
    return 0 if pct >= 85 else 1


if __name__ == "__main__":
    raise SystemExit(main())
