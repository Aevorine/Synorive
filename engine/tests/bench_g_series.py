#!/usr/bin/env python
"""
G 组基准 —— 引擎侧那六条（G1 / G2 / G5 / G6 / G7 / G8）
====================================================================
`metrics.py` 定义了 G1~G9 九条指标，但长期只有 G3 有脚本，其余 `hasBench=false`。
「只能你来跑」其实是「没有东西可跑」。这个脚本补上引擎侧能自动量的六条。

**没有覆盖的三条，如实说明为什么：**
  · **G4**（UI 100ms 内有反馈 / 联网期间 ≥55fps）—— 要渲染进程埋点，
    引擎侧量不到。得在 Electron 里用 `requestAnimationFrame` 采样。
  · **G9**（1000 文件全程 ≥55fps）—— 同上，帧率是渲染层的事。
  · **G3**（深挖 P95）—— 已经有专门的 `bench_research.py`，那里还做了
    「有死线 / 无死线」的对照，比塞进这里更有用。

🔴 **跑之前必须知道的三件事：**
  ① **它会往你的库里写东西**（G1 要真投喂一批临时文件，投完不会自动删）。
     🔴 **别对着你天天用的那个库跑** —— 想干净的话，先用
     `--data-dir <临时目录>` 另起一个引擎再对着它跑
     （这个开关在引擎上，不在本脚本上）。
  ② **G2 / G6 / G8 要联网**，查询词会真的发到搜索引擎。
  ③ **样本数**：默认 20。低于 20 的 P95 基本就是最大值，别拿它下"达标"的结论。

用法（引擎要先跑起来）：
    python -m tests.bench_g_series --engine-url http://127.0.0.1:8731
    python -m tests.bench_g_series --only G1,G7 --n 30
    python -m tests.bench_g_series --json g.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QUERIES = [
    "向量检索", "中文分词", "并发控制", "图片识别", "视频转写",
    "数据库索引", "缓存策略", "错误处理", "性能优化", "内存泄漏",
    "异步编程", "单元测试", "接口设计", "日志系统", "配置管理",
    "文件解析", "网络请求", "加密算法", "进程通信", "状态管理",
]


# ── HTTP 小工具 ─────────────────────────────────────────────
def _req(url: str, body: dict[str, Any] | None = None, timeout: float = 120
         ) -> tuple[float, Any, str]:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    hdr = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(url, data=data, headers=hdr,
                               method="POST" if data else "GET")
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return time.perf_counter() - t, (json.loads(raw) if raw else None), ""
    except urllib.error.HTTPError as e:
        return time.perf_counter() - t, None, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t, None, str(e)


def _p95(xs: list[float]) -> float:
    """真 P95（线性插值）。样本少时它约等于最大值 —— 那是样本量的问题，不是算法的。"""
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = 0.95 * (len(s) - 1)
    lo = int(pos)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (pos - lo)


def _verdict(ok: bool | None) -> str:
    return "✅ 达标" if ok else ("❌ 不达标" if ok is False else "— 无法判定")


# ── 各条指标 ────────────────────────────────────────────────
def g1_ingest_to_searchable(base: str, n: int) -> dict[str, Any]:
    """
    G1 投喂到可搜 ≤3s。**首字节式**：不等 OCR/转写，只要关键词能命中就算。

    每轮用一个**内容唯一**的临时文件 —— 内容重复的话第二次投喂会命中
    指纹去重直接跳过，量出来的是"跳过有多快"，那毫无意义。
    """
    tmp = Path(tempfile.mkdtemp(prefix="synorive-g1-"))
    lat: list[float] = []
    fails: list[str] = []
    for i in range(n):
        token = f"zzqg1{i}{int(time.time() * 1000) % 100000}"
        f = tmp / f"g1-{i}.txt"
        f.write_text(f"这是 G1 基准的测试文档。唯一标记 {token}。" * 20, encoding="utf-8")

        t0 = time.perf_counter()
        _, _, err = _req(f"{base}/api/ingest",
                         {"targets": [str(f)], "source": "folder", "recursive": False})
        if err:
            fails.append(f"投喂失败 {err}")
            continue
        # 轮询到搜得到为止。50ms 一次 —— 再密就是在量轮询开销本身
        hit = False
        while time.perf_counter() - t0 < 30:
            _, payload, _ = _req(f"{base}/api/search",
                                 {"query": token, "limit": 5})
            if payload and (payload.get("hits") or payload.get("results")):
                hit = True
                break
            time.sleep(0.05)
        if hit:
            lat.append(time.perf_counter() - t0)
        else:
            fails.append(f"{token} 30s 内没搜到")

    p95 = _p95(lat)
    return {"id": "G1", "label": "投喂到可搜", "target": "≤3s", "n": len(lat),
            "p50": round(statistics.median(lat), 2) if lat else None,
            "p95": round(p95, 2) if lat else None,
            "pass": (p95 <= 3.0) if lat else None, "fails": fails[:5],
            "note": f"临时目录 {tmp}，跑完可以删"}


def g2_web_search_p95(base: str, n: int) -> dict[str, Any]:
    """G2 联网快搜 P95 ≤3.0s。20 次不同查询。"""
    lat, fails = [], []
    for i in range(n):
        sec, payload, err = _req(f"{base}/api/web/search",
                                 {"query": QUERIES[i % len(QUERIES)], "limit": 20})
        if err or payload is None:
            fails.append(f"{QUERIES[i % len(QUERIES)]}: {err}")
            continue
        lat.append(sec)
    p95 = _p95(lat)
    return {"id": "G2", "label": "联网快搜 P95", "target": "≤3.0s", "n": len(lat),
            "p50": round(statistics.median(lat), 2) if lat else None,
            "p95": round(p95, 2) if lat else None,
            "pass": (p95 <= 3.0) if lat else None, "fails": fails[:5]}


def g5_memory(base: str, rounds: int = 10) -> dict[str, Any]:
    """
    G5 研究会话内存 ≤400MB：连续深挖 10 轮后引擎进程 RSS。

    🔴 **要报"涨了多少"而不只是"现在多少"** —— 起点本来就有模型占着几百 MB，
    只看终值分不清"一直就这么高"和"越挖越涨"，而后者才是这条指标防的东西。
    """
    _, before, _ = _req(f"{base}/api/metrics/budgets")
    rss0 = (before or {}).get("observed", {}).get("rssMb")
    for i in range(rounds):
        _req(f"{base}/api/web/research",
             {"query": QUERIES[i % len(QUERIES)], "rounds": 2}, timeout=180)
    _, after, _ = _req(f"{base}/api/metrics/budgets")
    rss1 = (after or {}).get("observed", {}).get("rssMb")
    if rss0 is None or rss1 is None:
        return {"id": "G5", "label": "研究会话内存", "target": "≤400MB", "pass": None,
                "note": "读不到 RSS（引擎侧没装 psutil）—— **不填 0**，那会显示成'远优于目标'"}
    return {"id": "G5", "label": "研究会话内存", "target": "≤400MB",
            "beforeMb": rss0, "afterMb": rss1, "deltaMb": round(rss1 - rss0, 1),
            "rounds": rounds, "pass": rss1 <= 400}


def g6_engine_failure(base: str, n: int) -> dict[str, Any]:
    """
    G6 单引擎失败不拖累：总耗时增量 ≤15%。

    做法是对比"全引擎"和"人为少派一家"的耗时。
    🔴 **这是近似**：真正该做的是让一家**超时**（而不是不派它），
    但从外部没法可靠地制造一家引擎超时。近似结论要标出来，不能当成实测。
    """
    # 🔴 路径是 /api/web/engines，不是 /api/web/health（后者根本不存在）。
    #    写错的话这里拿到 404 → ids 为空 → 直接返回"无法判定"，
    #    **而"无法判定"看起来像是环境问题，不像是我把路径写错了**
    _, info, _ = _req(f"{base}/api/web/engines")
    table = (info or {}).get("engines") or (info or {}).get("table") or []
    ids = [r.get("id") for r in table if isinstance(r, dict) and r.get("id")]
    if len(ids) < 2:
        return {"id": "G6", "label": "单引擎失败不拖累", "target": "总耗时增量 ≤15%",
                "pass": None, "note": f"可用引擎只有 {len(ids)} 家，没法做对比"}

    full = [_req(f"{base}/api/web/search", {"query": QUERIES[i % len(QUERIES)], "limit": 20})[0]
            for i in range(n)]
    subset = ids[:-1]
    less = [_req(f"{base}/api/web/search",
                 {"query": QUERIES[i % len(QUERIES)], "limit": 20, "engines": subset})[0]
            for i in range(n)]
    a, b = statistics.median(full), statistics.median(less)
    delta = (a - b) / b * 100 if b else 0
    return {"id": "G6", "label": "单引擎失败不拖累", "target": "总耗时增量 ≤15%",
            "allEnginesMedianS": round(a, 2), "minusOneMedianS": round(b, 2),
            "deltaPct": round(delta, 1), "pass": abs(delta) <= 15,
            "note": "🔴 近似：用'少派一家'代替'一家超时'，不是严格实测"}


def g7_cache(base: str, n: int) -> dict[str, Any]:
    """
    G7 缓存命中：同一查询 10 分钟内二次返回 ≤200ms。

    🔴 **第一次搜返回空的那些轮次必须排除，而且要报出来排除了几个。**
    `meta.py` 里写缓存的条件是 `if use_cache and clusters:` —— **只有真拿到
    结果才写缓存**。这是对的设计：把一次瞬时失败（引擎全被限流）缓存 10 分钟，
    等于把故障冻住。但基准如果不排除这种轮次，第二次自然又走网络（~900ms），
    于是 P95 被一两个"根本没资格进统计"的样本顶穿。
    2026-08-03 首跑就是这么把 G7 误判成 ❌ 的：中位数 17.6ms（缓存明明在工作），
    P95 却 891.8ms。**排除不等于隐藏 —— skipped 数会一起报出来。**
    """
    seconds, fails = [], []
    skipped = 0
    for i in range(n):
        q = f"{QUERIES[i % len(QUERIES)]} 缓存基准"
        _, first, _ = _req(f"{base}/api/web/search", {"query": q, "limit": 20})
        got = (first or {}).get("clusters") or (first or {}).get("results") or []
        if not got:
            skipped += 1     # 第一次就没结果 → 按设计不会入缓存，这一轮不算数
            continue
        sec, payload, err = _req(f"{base}/api/web/search", {"query": q, "limit": 20})
        if err or payload is None:
            fails.append(f"{q}: {err}")
            continue
        seconds.append(sec * 1000)
    p95 = _p95(seconds)
    return {"id": "G7", "label": "缓存命中", "target": "≤200ms（二次）", "n": len(seconds),
            "skippedEmptyFirst": skipped,
            "p50Ms": round(statistics.median(seconds), 1) if seconds else None,
            "p95Ms": round(p95, 1) if seconds else None,
            "pass": (p95 <= 200) if seconds else None, "fails": fails[:5],
            "note": (f"排除了 {skipped} 轮「第一次搜就没结果」—— 那种按设计不入缓存，"
                     "不是缓存失效") if skipped else None}


def g8_offline(base: str) -> dict[str, Any]:
    """
    G8 断网零空屏：断网后搜索必须出本地结果 + 一行说明，**不能是空白页**。

    🔴 **这个脚本判定不了它。** 真要测得拔网线或断掉引擎出网能力，
    而那不是这个脚本能从外部做到的事 —— 硬凑一个"看起来测过了"的结论
    比留着 null 危险得多。这里只做一件有用的事：确认本地检索这条路
    **本身**是通的（联网挂了它就是兜底），并把该怎么手测写出来。
    """
    _, payload, err = _req(f"{base}/api/search", {"query": QUERIES[0], "limit": 10})
    ok = payload is not None and not err
    return {"id": "G8", "label": "断网零空屏", "target": "降级到本地库并明确告知",
            "localSearchWorks": ok, "pass": None,
            "note": "🔴 自动判定不了。手测：设置里关掉『联网搜索』总闸 → 重启引擎 → "
                    "搜一个词，必须出本地结果 + 一行说明，不能是空白页"}


ALL = {
    "G1": lambda b, n: g1_ingest_to_searchable(b, min(n, 10)),
    "G2": g2_web_search_p95,
    "G5": lambda b, n: g5_memory(b),
    "G6": lambda b, n: g6_engine_failure(b, min(n, 8)),
    "G7": lambda b, n: g7_cache(b, min(n, 10)),
    "G8": lambda b, n: g8_offline(b),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench_g_series",
                                description="G 组引擎侧六条基准（G4/G9 要渲染层，G3 见 bench_research.py）")
    ap.add_argument("--engine-url", default="http://127.0.0.1:8731")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--only", default="", help="只跑其中几条，逗号分隔，如 G1,G7")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)

    _, health, err = _req(f"{a.engine_url}/health", timeout=10)
    if err or not health:
        print(f"✗ 连不上引擎 {a.engine_url}（{err}）。先把引擎跑起来。")
        return 1
    print(f"引擎 v{health.get('version')} ｜ 已索引 {health.get('indexedItems')} 条\n")
    if a.n < 20:
        print(f"⚠️  n={a.n} < 20：P95 基本等于最大值，别拿它下'达标'的结论。\n")

    pick = [x.strip().upper() for x in a.only.split(",") if x.strip()] or list(ALL)
    out = []
    for key in pick:
        fn = ALL.get(key)
        if fn is None:
            print(f"  跳过未知指标 {key}")
            continue
        print(f"── {key} 跑起来了…")
        r = fn(a.engine_url, a.n)
        out.append(r)
        print(f"   {r['label']}  目标 {r['target']}  → {_verdict(r.get('pass'))}")
        for k, v in r.items():
            if k not in ("id", "label", "target", "pass", "fails", "note"):
                print(f"     {k}: {v}")
        if r.get("note"):
            print(f"     · {r['note']}")
        if r.get("fails"):
            print(f"     失败 {len(r['fails'])} 次，前几条：{r['fails'][:3]}")
        print()

    passed = sum(1 for r in out if r.get("pass") is True)
    failed = sum(1 for r in out if r.get("pass") is False)
    unknown = sum(1 for r in out if r.get("pass") is None)
    print("=" * 56)
    print(f"达标 {passed} ｜ 不达标 {failed} ｜ 无法判定 {unknown}")
    print("🔴 『无法判定』不是『达标』—— 它们的 pass 是 null，别在汇报里算成通过。")

    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已存 {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
