#!/usr/bin/env python
"""
X3 / G3 深挖出简报 P95 —— 全局死线到底有没有把尾巴按下去
====================================================================
2026-08-03 给 `deep_research` 加了全局死线（`TOTAL_BUDGET_S=8.0`）。
根因是各阶段预算互不知情：第一轮搜索预算 12s（比 8s 目标还大）+ 抓正文 20s
+ 第二轮 9s + 核查，**每段都没超自己的预算，加起来 12.45s**。

改动是否有效**只能靠前后对比数字**，而这个脚本就是那个"后"。
它同时跑两组：
    · `budget`   —— 默认 8s 死线（改动后的行为）
    · `nobudget` —— `budgetS=0` 关掉死线（改动前的行为）
**同一批查询、同一次运行**，这样对比才有意义 ——
分两次跑、中间隔了几小时的话，网络状况变了，数字没法比。

🔴 **它要联网，而且会真的去搜。** 跑之前确认引擎的 `allow_network` 是开的，
   并且你接受这些查询词会被发到搜索引擎（这正是 E12 隐私围栏管的事）。

🔴 **P95 用 6~8 个样本算是不严谨的**，那只是"取最大值"。
   台账里原来的 12.45s 就是 6 样本取最大。要下"达标了"的结论，
   `--n` 至少给 20 —— 样本少的时候，一次 Yandex 弹验证码就能让 P95 翻倍。

用法：
    python -m tests.bench_research --engine-url http://127.0.0.1:8731 --n 20
    python -m tests.bench_research --engine-url ... --n 20 --json x3.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 查询词故意混合难易：全是简单词的话尾部根本不会出现，
#: 而 X3 考的恰恰是尾部
QUERIES = [
    "向量检索为什么慢", "HNSW 参数怎么调", "sqlite-vec 和 faiss 对比",
    "Electron 内存泄漏排查", "中文分词 jieba 和 hanlp 区别",
    "onnxruntime DirectML 性能", "RapidOCR 中文识别准确率",
    "FastAPI websocket 断线重连", "SQLite FTS5 中文分词",
    "usearch 索引持久化", "Compose 重组性能优化", "Room 数据库迁移",
    "electron-updater 差量更新原理", "APK 签名方案 v2 v3 区别",
    "asyncio 超时与取消的区别", "HNSW 召回率和 ef 的关系",
    "PDF 文本抽取有哪些坑", "whisper 中文转写准确率",
    "SSE 和 websocket 怎么选", "内容农场识别有哪些特征",
]


def _post(url: str, body: dict[str, Any], timeout: float) -> tuple[float, dict[str, Any] | None, str]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
        return time.perf_counter() - t, payload, ""
    except urllib.error.HTTPError as e:
        return time.perf_counter() - t, None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t, None, str(e)


def _p95(xs: list[float]) -> float:
    """
    真 P95（线性插值），不是"取最大值"。

    🔴 样本 <20 时它和最大值几乎没区别 —— 这不是这个函数的问题，
    是样本量的问题，所以报告里会把 n 一起打出来。
    """
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = 0.95 * (len(s) - 1)
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * frac


def run_group(base: str, n: int, budget_s: float | None, label: str) -> dict[str, Any]:
    url = base.rstrip("/") + "/api/web/research"
    lat: list[float] = []
    degraded_rounds = 0
    degraded_verify = 0
    errors: list[str] = []

    print(f"\n── {label} ──")
    for i in range(n):
        q = QUERIES[i % len(QUERIES)]
        body: dict[str, Any] = {"query": q, "rounds": 2}
        if budget_s is not None:
            body["budgetS"] = budget_s
        # 超时给得远大于死线：**要量它真实花了多久，不是量我的耐心**。
        # 客户端提前掐断会把一个"12 秒的慢请求"记成"30 秒超时"
        sec, payload, err = _post(url, body, timeout=180)
        if payload is None:
            errors.append(f"{q}: {err}")
            print(f"  {i + 1:>3}. {sec:6.2f}s  ✗ {err}  「{q}」")
            continue
        lat.append(sec)
        b = payload.get("budget") or {}
        degs = b.get("degraded") or []
        for d in degs:
            if "轮追问" in d:
                degraded_rounds += 1
            if "核查" in d:
                degraded_verify += 1
        mark = f"  降级{len(degs)}项" if degs else ""
        print(f"  {i + 1:>3}. {sec:6.2f}s  {len(payload.get('results') or [])} 条{mark}  「{q}」")

    return {
        "label": label,
        "n": len(lat),
        "errors": errors,
        "p50": round(statistics.median(lat), 2) if lat else None,
        "p95": round(_p95(lat), 2) if lat else None,
        "max": round(max(lat), 2) if lat else None,
        "min": round(min(lat), 2) if lat else None,
        "degradedRounds": degraded_rounds,
        "degradedVerify": degraded_verify,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bench_research",
        description="X3/G3：深挖 P95。同一批查询同时量'有死线'和'无死线'两组",
    )
    ap.add_argument("--engine-url", default="http://127.0.0.1:8731")
    ap.add_argument("--n", type=int, default=20,
                    help="每组跑几次。**低于 20 的 P95 基本等于取最大值**，别拿它下结论")
    ap.add_argument("--budget", type=float, default=8.0, help="死线秒数，对应 X3 目标")
    ap.add_argument("--skip-baseline", action="store_true",
                    help="不跑'无死线'那一组（省一半时间，但就没有对比了）")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)

    if a.n < 20:
        print(f"⚠️  n={a.n} < 20：算出来的 P95 基本就是最大值。")
        print("   台账里原来那个 12.45s 正是 6 样本取最大 —— 别用小样本下'达标'的结论。\n")

    after = run_group(a.engine_url, a.n, a.budget, f"有死线 budgetS={a.budget}（改动后）")
    before = None if a.skip_baseline else run_group(
        a.engine_url, a.n, 0.0, "无死线 budgetS=0（改动前的行为）"
    )

    print("\n" + "=" * 62)
    print(f"{'组':<26}{'n':>4}{'P50':>8}{'P95':>8}{'max':>8}")
    for g in (before, after):
        if g:
            print(f"{g['label']:<26}{g['n']:>4}{g['p50'] or 0:>8.2f}{g['p95'] or 0:>8.2f}{g['max'] or 0:>8.2f}")
    print("=" * 62)

    tgt = 8.0
    p95 = after["p95"] or 0
    print(f"\nX3 目标 P95 ≤{tgt}s → 实测 {p95:.2f}s  {'✅ 达标' if p95 <= tgt else '❌ 不达标'}")
    if before and before["p95"]:
        d = before["p95"] - p95
        print(f"对比无死线：{before['p95']:.2f}s → {p95:.2f}s，"
              f"{'降低' if d > 0 else '升高'} {abs(d):.2f}s（{abs(d) / before['p95'] * 100:.0f}%）")
    print(f"\n降级发生了几次：跳过第二轮 {after['degradedRounds']} 次 ｜ 核查降档 {after['degradedVerify']} 次")
    print("🔴 **降级次数如果很高，说明 8s 这个预算对当前网络太紧** ——")
    print("   那时候用户拿到的简报是缩水的，P95 达标没有意义。两个数要一起看。")
    if after["errors"]:
        print(f"\n失败 {len(after['errors'])} 次：")
        for e in after["errors"][:5]:
            print("  ·", e)

    if a.json:
        a.json.write_text(
            json.dumps({"after": after, "before": before, "target": tgt},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n已存 {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
