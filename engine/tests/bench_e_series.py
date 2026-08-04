#!/usr/bin/env python
"""
E1–E4 实测 —— **无界面**，不抢鼠标
====================================================================
E1–E8 里有四项完全落在引擎侧，不需要启动桌面应用就能量：

  E1 本地搜索延迟 P50 / P95
  E2 索引吞吐（文档/秒、块/秒）
  E3 引擎冷启动到可用
  E4 引擎常驻内存

剩下四项要么要 GUI（E6 帧率），要么要联网（E7），要么是构建期属性
（E5 安装包体积），要么要长时间运行（E8 稳定性）—— 那几项这里如实标"没测"，
**不拿别的数字冒充**。

── 三条量法纪律 ────────────────────────────────────────────
① **先预热再计时。** 第一次查询要加载 ONNX 会话（几百毫秒），
   把它算进 P50 会让延迟看起来比真实使用差好几倍 ——
   而真实使用中那一次只会发生一次。
② **P95 要够样本。** n<20 时的 P95 基本就是最大值，
   拿它下结论是自欺欺人。这里默认跑 60 次。
③ **报的是这台机器上的数，不是"产品指标"。** 换台机器就是另一组数。

用法：python -m tests.bench_e_series [--docs 200]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL_DIR = ROOT.parent / "data" / "models"

#: 目标值。和 `synorive/metrics.py` 的口径一致：目标是目标，实测是实测，
#: **两者永远分开报**，不把"没达标"藏起来也不把"达标"说成产品保证。
TARGETS = {
    "E1_p50": ("本地搜索延迟 P50", 80, "ms", True),
    "E1_p95": ("本地搜索延迟 P95", 300, "ms", True),
    "E2_docs": ("索引吞吐（文档）", 5, "篇/秒", False),
    "E2_chunks": ("索引吞吐（块）", 40, "块/秒", False),
    "E3_boot": ("引擎冷启动到可用", 3000, "ms", True),
    "E4_rss": ("引擎常驻内存", 450, "MB", True),
}

#: 查询词。**故意混合三类** —— 只用一类会把 P95 量成一个漂亮但没意义的数：
#:   精确型号类（关键词路命中）/ 描述类（语义路命中）/ 长问句（两路都跑满）
QUERIES = [
    "缓存一致性", "版本号", "租约", "双写", "机房延迟",
    "怎么保证缓存和数据库的数据一致",
    "分布式系统里最常见的一致性问题是什么",
    "写入的时候应该先删缓存还是先写数据库",
    "热点键会带来什么压力", "缓存重建时的惊群效应怎么避免",
]

BODY = (
    "分布式缓存的一致性在第 {i} 个场景下有它自己的处理方式。"
    "写入路径上先失效再回填，可以避免并发读把旧值又写回缓存里；"
    "读路径上要区分缓存未命中和缓存里存的就是空值这两种情况，否则会出现缓存穿透。"
    "跨机房部署时时序问题会被网络延迟放大，租约和版本号两种方案在这一点上的表现不同。"
    "和数据库事务隔离级别也要一起设计，单看缓存这一侧永远得不出正确结论。"
    "实际落地时还要考虑热点键的单点压力，以及缓存重建时的惊群效应。"
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def call(port: int, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
    d = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=d,
        headers={"Content-Type": "application/json"} if d else {},
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def verdict(key: str, value: float | None) -> str:
    if value is None:
        return "—（没测）"
    label, target, unit, lower_better = TARGETS[key]
    ok = value <= target if lower_better else value >= target
    arrow = "≤" if lower_better else "≥"
    return f"{value:.1f} {unit}　目标 {arrow} {target} {unit}　{'✓ 达标' if ok else '✗ 没达标'}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=200, help="造多少篇语料")
    ap.add_argument("--rounds", type=int, default=60, help="搜索采样次数（P95 至少要 20）")
    ap.add_argument(
        "--paras", type=int, default=8,
        help="每篇几段。**这个数直接决定「块/秒」量的是什么** —— "
             "设成 1 的话每篇只切出一块，块/秒 就等于 篇/秒，"
             "量到的是每篇文件的固定开销（读盘、解析、写库），不是嵌入速度。"
             "第一版跑出来 200 篇/200 块、22.5 块/秒「没达标」，"
             "查下去正是这个原因 —— 那个数根本没在量它声称要量的东西。",
    )
    args = ap.parse_args()

    if not MODEL_DIR.exists():
        print(f"✗ 模型目录不存在：{MODEL_DIR}")
        return 1

    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-bench-e"
    shutil.rmtree(data_dir, ignore_errors=True)
    corpus = data_dir / "corpus"
    corpus.mkdir(parents=True)
    for i in range(args.docs):
        # 每篇 args.paras 段，段间空一行（切块器按段落切）——
        # 这样「块/秒」量的才是嵌入速度，而不是每篇文件的固定开销
        body = "\n\n".join(BODY.format(i=i * 100 + k) for k in range(args.paras))
        (corpus / f"doc_{i:04d}.md").write_text(body + "\n", encoding="utf-8")

    port = free_port()
    results: dict[str, float | None] = {k: None for k in TARGETS}

    # ── E3 冷启动 ────────────────────────────────────────
    # 从进程启动到 /health 能应答。**不含模型预热** ——
    # 预热在后台线程里跑，界面在那之前已经可以用了（这正是 C5 拆线程的目的）
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, "-m", "synorive.main", "--port", str(port),
         "--data-dir", str(data_dir), "--model-dir", str(MODEL_DIR)],
        cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        boot_ms: float | None = None
        for _ in range(600):
            try:
                call(port, "/health", timeout=2)
                boot_ms = (time.perf_counter() - t0) * 1000
                break
            except Exception:
                time.sleep(0.05)
        if boot_ms is None:
            print("✗ 引擎没起来")
            return 1
        results["E3_boot"] = boot_ms

        # ── E2 索引吞吐 ──────────────────────────────────
        t1 = time.perf_counter()
        call(port, "/api/ingest",
             {"targets": [str(corpus)], "source": "file", "recursive": True})
        st: dict = {}
        for _ in range(1200):
            st = call(port, "/api/stats")
            if st.get("ready", 0) >= args.docs:
                break
            time.sleep(0.25)
        ingest_s = time.perf_counter() - t1
        done = int(st.get("ready", 0))
        chunks = int(st.get("chunks", 0))
        if done < args.docs:
            print(f"⚠ 只索引完 {done}/{args.docs} 篇，吞吐数按实际完成量算")
        results["E2_docs"] = done / ingest_s if ingest_s > 0 else None
        results["E2_chunks"] = chunks / ingest_s if ingest_s > 0 else None

        # ── E1 搜索延迟 ──────────────────────────────────
        # 🔴 先预热 5 次再计时。第一次查询要加载 ONNX 会话（几百毫秒），
        #    算进 P50 会让数字比真实使用差好几倍，而那一次一辈子只发生一次
        for q in QUERIES[:5]:
            call(port, "/api/search", {"query": q, "limit": 20, "stage": "semantic"})

        lat: list[float] = []
        for i in range(args.rounds):
            q = QUERIES[i % len(QUERIES)]
            t2 = time.perf_counter()
            call(port, "/api/search", {"query": q, "limit": 20, "stage": "semantic"})
            lat.append((time.perf_counter() - t2) * 1000)
        lat.sort()
        results["E1_p50"] = lat[len(lat) // 2]
        results["E1_p95"] = lat[min(len(lat) - 1, max(0, int(0.95 * len(lat)) - 1))]

        # ── E4 引擎内存 ──────────────────────────────────
        obs = call(port, "/api/metrics/budgets").get("observed") or {}
        rss = obs.get("rssMb")
        results["E4_rss"] = float(rss) if isinstance(rss, (int, float)) else None
        rss_note = obs.get("rssNote")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ── 报告 ─────────────────────────────────────────────
    line = "═" * 74
    print()
    print(line)
    per_doc = chunks / done if done else 0
    print(f"E 系列实测 —— 本机 · 语料 {done} 篇 / {chunks} 块"
          f"（每篇 {per_doc:.1f} 块）· 搜索采样 {len(lat)} 次")
    print(line)
    for k in ("E1_p50", "E1_p95", "E2_docs", "E2_chunks", "E3_boot", "E4_rss"):
        label = TARGETS[k][0]
        print(f"  {label:<22} {verdict(k, results[k])}")
    if results["E4_rss"] is None and rss_note:
        print(f"       └ {rss_note}")
    if per_doc < 2:
        print("       └ ⚠️ 每篇只切出不到 2 块，「块/秒」这一项**没在量嵌入速度**，"
              "量的是每篇的固定开销。用 --paras 8 重跑才有意义")

    print()
    print("  ── 这次**没测**的四项，如实标出来 ─────────────────────────")
    print("  E5 安装包体积       — 构建期属性，运行期量不到；看 Releases 上的 exe")
    print("  E6 界面帧率          — 要启动桌面应用（会抢屏幕），设置页有实时看板")
    print("  E7 联网搜首字节      — 要真的往外发请求，没你点头不跑")
    print("  E8 长时间稳定性      — 要连续跑几小时，不适合放在一次基准里")
    print()
    print(f"  样本量：搜索 {len(lat)} 次（P95 至少要 20 才有意义）")
    print("  ⚠️ 这是**这台机器上的数**，不是产品保证。换机器就是另一组数。")
    print(line)

    # 基准脚本**永远返回 0** —— 它是量数不是判对错。
    # 没达标是一个需要人来判断的结果（也许目标定错了），不是构建失败
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
