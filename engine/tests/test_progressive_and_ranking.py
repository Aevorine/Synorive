#!/usr/bin/env python
"""
A1 渐进索引 + D1 新排序指标 —— 端到端
====================================================================
两件事放一个测试里，因为它们共用同一次引擎启动和同一批语料，
而分开跑要多付一次「起引擎 + 索引语料」的几十秒。

── A1：为什么这条必须测 ────────────────────────────────────
我把摄取流水线的顺序从 `chunk → embed → 写入` 改成了
`chunk → 写入 → embed → 回填向量`。这是**动了主干**：
写坏了的表现不是报错，是**内容悄悄搜不到**，或者向量和分块错位
（搜索结果驴唇不对马嘴，而且极难定位到是这里）。

所以要验三件事：
  ① 关键词层在向量算完之前就能搜到（这是改动的全部目的）
  ② 向量回填之后语义层也能搜到（回填没写错位）
  ③ 最终状态是 ready 而不是卡在 partial（中间态要能收敛）

── D1：新加的两个指标必须真的改变排序 ─────────────────────
`diversity` 和 `lengthPenalty` 如果只是加了滑块而打分不变，
那就是又一个"点了没反应"的开关 —— 这一轮反复在治的正是这种病。
所以测法是：**同一个查询，只改这一个权重，看排序真的不一样。**

用法：python -m tests.test_progressive_and_ranking
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL_DIR = ROOT.parent / "data" / "models"

#: 语料**按目录分组**——这是 D1 多样性真正作用的粒度。
#: 召回和融合都已经按 item_id 去重（同一份资料只出现一次），
#: 所以多样性只有在"一个目录里有一堆相关文件"时才有事可做。
#:
#: 热门目录/ 放 5 篇都沾边的 → 不降权时它会铺满首屏
#: 冷门目录/ 放 1 篇同样沾边的 → 降权后它该往前挪
CORPUS = {
    "热门目录": [
        ("缓存一致性_1.md", "分布式缓存的一致性靠版本号解决。写入路径上先失效再回填，可以避免并发读把旧值又写回缓存里；而读路径上要区分缓存未命中和缓存里存的就是空值这两种情况，否则会出现缓存穿透。跨机房部署时时序问题会被网络延迟放大，租约和版本号两种方案在这一点上的表现不同。和数据库事务隔离级别也要一起设计，单看缓存这一侧永远得不出正确结论。实际落地时还要考虑热点键的单点压力，以及缓存重建时的惊群效应。"),  # noqa: E501
        ("缓存一致性_2.md", "分布式缓存的一致性也可以靠租约解决。写入路径上先失效再回填，可以避免并发读把旧值又写回缓存里；而读路径上要区分缓存未命中和缓存里存的就是空值这两种情况，否则会出现缓存穿透。跨机房部署时时序问题会被网络延迟放大，租约和版本号两种方案在这一点上的表现不同。和数据库事务隔离级别也要一起设计，单看缓存这一侧永远得不出正确结论。实际落地时还要考虑热点键的单点压力，以及缓存重建时的惊群效应。"),  # noqa: E501
        ("缓存一致性_3.md", "分布式缓存的一致性在多机房场景下更难。写入路径上先失效再回填，可以避免并发读把旧值又写回缓存里；而读路径上要区分缓存未命中和缓存里存的就是空值这两种情况，否则会出现缓存穿透。跨机房部署时时序问题会被网络延迟放大，租约和版本号两种方案在这一点上的表现不同。和数据库事务隔离级别也要一起设计，单看缓存这一侧永远得不出正确结论。实际落地时还要考虑热点键的单点压力，以及缓存重建时的惊群效应。"),  # noqa: E501
        ("缓存一致性_4.md", "分布式缓存的一致性和事务隔离级别相关。写入路径上先失效再回填，可以避免并发读把旧值又写回缓存里；而读路径上要区分缓存未命中和缓存里存的就是空值这两种情况，否则会出现缓存穿透。跨机房部署时时序问题会被网络延迟放大，租约和版本号两种方案在这一点上的表现不同。和数据库事务隔离级别也要一起设计，单看缓存这一侧永远得不出正确结论。实际落地时还要考虑热点键的单点压力，以及缓存重建时的惊群效应。"),  # noqa: E501
        ("缓存一致性_5.md", "分布式缓存的一致性里双写是常见错误。写入路径上先失效再回填，可以避免并发读把旧值又写回缓存里；而读路径上要区分缓存未命中和缓存里存的就是空值这两种情况，否则会出现缓存穿透。跨机房部署时时序问题会被网络延迟放大，租约和版本号两种方案在这一点上的表现不同。和数据库事务隔离级别也要一起设计，单看缓存这一侧永远得不出正确结论。实际落地时还要考虑热点键的单点压力，以及缓存重建时的惊群效应。"),  # noqa: E501
    ],
    "冷门目录": [
        ("另一个角度谈缓存.md",
         "从可用性角度看，分布式缓存的一致性可以适当放宽，最终一致往往就够用了。"),
    ],
    "杂项": [
        # 目录式短片段：天然含查询词、覆盖率还高，是弱匹配噪声的典型。
        # 🔴 **必须比正文短得多**：lengthPenalty 的拐点是 240 字，
        #    正文都在拐点以上（short=0，不吃惩罚），这一条只有 28 字（吃满惩罚）。
        #    第一版语料所有文档都是 30 字上下，惩罚项近似常数，
        #    减掉一个常数**不改变相对顺序** —— 测试因此报"滑块没接上"，
        #    而实际是语料没有长短对比。语料设计不对，测出来的结论就是错的。
        ("目录.md", "分布式缓存 一致性 版本号 租约 双写 时序 机房 事务"),
    ],
}
DOC_COUNT = sum(len(v) for v in CORPUS.values())


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
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(180):
            try:
                self.call("/health", timeout=3)
                return self
            except Exception:
                time.sleep(1)
        raise RuntimeError("引擎没起来")

    def __exit__(self, *a: object) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def call(self, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
        d = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=d,
            headers={"Content-Type": "application/json"} if d else {},
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def search(self, q: str, **kw: object) -> dict:
        return self.call("/api/search", {"query": q, "limit": 20, "stage": "semantic", **kw})


def titles(r: dict) -> list[str]:
    return [h["item"]["title"] for h in r.get("hits", [])]


def main() -> int:
    if not MODEL_DIR.exists():
        print(f"✗ 模型目录不存在：{MODEL_DIR}")
        return 1

    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-a1-d1"
    shutil.rmtree(data_dir, ignore_errors=True)
    corpus = data_dir / "corpus"
    for folder, files in CORPUS.items():
        d = corpus / folder
        d.mkdir(parents=True, exist_ok=True)
        for name, body in files:
            (d / name).write_text(body + "\n", encoding="utf-8")

    problems: list[str] = []
    skipped: list[str] = []
    line = "─" * 70

    with Engine(data_dir) as eng:
        # ── A1 ① 关键词层要**在向量算完之前**就能搜到 ─────────
        print(line)
        print("A1① 投喂之后，关键词层要在向量算完之前就能搜到")
        print(line)
        eng.call("/api/ingest", {"targets": [str(corpus)], "source": "file", "recursive": True})

        first_hit_at: float | None = None
        all_ready_at: float | None = None
        t0 = time.time()
        for _ in range(300):
            st = eng.call("/api/stats")
            if first_hit_at is None:
                kw = eng.call("/api/search",
                              {"query": "分布式缓存", "limit": 5, "stage": "keyword"})
                if kw.get("hits"):
                    first_hit_at = time.time() - t0
            if st.get("ready", 0) >= DOC_COUNT:
                all_ready_at = time.time() - t0
                break
            time.sleep(0.5)

        if first_hit_at is None:
            problems.append("整个索引过程中关键词层一次都没搜到东西 —— A1 渐进索引没生效")
            print("  ✗ 关键词层始终搜不到")
        else:
            print(f"  ✓ 关键词层 {first_hit_at:.1f}s 就能搜到")
        if all_ready_at is None:
            problems.append("索引没有收敛到 ready —— 中间那个 partial 态可能卡住了")
            print("  ✗ 没收敛到 ready")
        else:
            print(f"  ✓ 全部 {DOC_COUNT} 篇收敛到 ready，用时 {all_ready_at:.1f}s")
            if first_hit_at is not None and first_hit_at < all_ready_at:
                print(f"  ✓ 可搜时间提前了 {all_ready_at - first_hit_at:.1f}s"
                      "（这就是 A1 改动的全部收益）")
            elif first_hit_at is not None:
                skipped.append("语料太小，可搜时间没比全部完成早 —— 没验到提前量")

        # ── A1 ② 向量回填没写错位 ────────────────────────────
        print()
        print(line)
        print("A1② 向量回填之后，语义层要能搜到，且**内容对得上**")
        print(line)
        r = eng.search("怎么保证缓存和数据库的数据一致")
        t = titles(r)
        print(f"  语义检索命中 {len(t)} 条：{t[:3]}")
        if not t:
            problems.append("回填之后语义检索一条都搜不到 —— 向量没写进去")
        else:
            # 错位的表现：搜"缓存一致性"却把「目录.md」排到第一。
            # 目录那篇只有孤零零的词条，语义上离这个问句最远
            if t[0] == "目录":
                problems.append("语义检索把纯目录页排到第一 —— 很可能向量和分块错位了")
            else:
                print(f"  ✓ 首条是「{t[0]}」，不是目录页")

        # ── D1 ① 多样性真的改变排序 ─────────────────────────
        print()
        print(line)
        print("D1① 结果多样性：调 0 让长文霸屏，调高让更多份资料露头")
        print(line)
        base = {"semantic": 1.0, "keyword": 1.0, "recency": 0.3,
                "sourceTrust": 0.2, "popularity": 0.2, "titleBoost": 0.5,
                "lengthPenalty": 0.3}
        r0 = eng.search("分布式缓存一致性", weights={**base, "diversity": 0.0})
        r1 = eng.search("分布式缓存一致性", weights={**base, "diversity": 1.5})

        def top_n_dirs(r: dict, n: int = 3) -> int:
            """前 n 条来自几个**不同目录** —— 这才是多样性作用的粒度"""
            out = set()
            for h in r.get("hits", [])[:n]:
                loc = h["item"]["locator"].replace("\\", "/")
                out.add(loc.rsplit("/", 1)[0])
            return len(out)

        d0, d1 = top_n_dirs(r0), top_n_dirs(r1)
        print(f"  diversity=0.0 → 前 3 条来自 {d0} 个不同目录")
        print(f"  diversity=1.5 → 前 3 条来自 {d1} 个不同目录")
        if titles(r0) == titles(r1):
            problems.append("diversity 从 0 调到 1.5，排序一个字都没变 —— 这个滑块没接上打分")
        else:
            print("  ✓ 排序确实变了（滑块接上了打分，不是摆设）")
        if d1 < d0:
            problems.append(f"多样性调高之后不同目录反而变少了（{d0}→{d1}）—— 方向反了")

        # ── D1 ② 长度惩罚真的把短片段压下去 ──────────────────
        print()
        print(line)
        print("D1② 忽略短片段：调高之后目录页那类短条目要往后排")
        print(line)
        r_lo = eng.search("分布式缓存", weights={**base, "diversity": 0.5, "lengthPenalty": 0.0})
        r_hi = eng.search("分布式缓存", weights={**base, "diversity": 0.5, "lengthPenalty": 1.0})

        def rank_of(r: dict, needle: str) -> int:
            """按**包含**匹配找名次。真实标题带 .md 后缀，
            精确相等会永远找不到 —— 而"找不到"和"排最后"在断言里长得一样，
            于是一个根本没执行到的检查会显示成通过。"""
            for i, t in enumerate(titles(r), 1):
                if needle in t:
                    return i
            return 999

        def score_of(r: dict, needle: str) -> float | None:
            for h in r.get("hits", []):
                if needle in h["item"]["title"]:
                    return float(h["score"])
            return None

        def median_score(r: dict, exclude: str) -> float | None:
            xs = sorted(float(h["score"]) for h in r.get("hits", [])
                        if exclude not in h["item"]["title"])
            return xs[len(xs) // 2] if xs else None

        lo, hi = rank_of(r_lo, "目录"), rank_of(r_hi, "目录")
        s_lo, s_hi = score_of(r_lo, "目录"), score_of(r_hi, "目录")
        m_lo, m_hi = median_score(r_lo, "目录"), median_score(r_hi, "目录")
        print(f"  lengthPenalty=0.0 → 「目录」第 {lo if lo != 999 else '未命中'} 名，"
              f"分 {s_lo}　其余中位数 {m_lo}")
        print(f"  lengthPenalty=1.0 → 「目录」第 {hi if hi != 999 else '未命中'} 名，"
              f"分 {s_hi}　其余中位数 {m_hi}")

        # 🔴 **直接断言这一项自己的效果，不靠"排序有没有变"间接判断。**
        #    第一版写的是 `titles(r_lo) != titles(r_hi)` 就算通过 ——
        #    那只能证明"有东西变了"，而变的可能是别的因素。实测中「目录」
        #    两次都排第 3、名次一动没动，测试却打印了"方向对"。
        #    **一个不检验自己那句结论的断言，比没有断言更糟：它会给出虚假的安心。**
        if s_lo is None or s_hi is None:
            skipped.append("「目录」两次都没被召回，lengthPenalty 这一项没验到")
        elif s_hi >= s_lo:
            problems.append(
                f"lengthPenalty 从 0 调到 1.0，短片段的分不降反增/持平（{s_lo} → {s_hi}）"
                " —— 这个滑块没接上打分，或者方向反了"
            )
        else:
            drop = (s_lo - s_hi) / s_lo * 100
            print(f"  ✓ 短片段的分被压低了 {drop:.1f}%（{s_lo} → {s_hi}）")

            # 🔴 **要断言的是相对量，不是绝对量。**
            #    第一版断言的是"拐点以上的正文一分不该掉"，结果报红 ——
            #    而查下去是**我的预期写错了**：语料正文约 170 字，本来就在
            #    240 拐点**以下**，按设计就该吃一部分惩罚（这是条软斜坡，不是硬阈值）。
            #    产品是对的，错的是测试。
            #
            #    这一项真正的主张是「**短片段被压得比正文多**」，
            #    所以断言必须写成两者跌幅的比。写成绝对量的话，
            #    要么像刚才那样误报，要么反过来把"两边一样掉分"（等于没效果）
            #    也判成通过 —— 那才是更危险的方向。
            if m_lo is not None and m_hi is not None and m_lo > 0:
                drop_mid = (m_lo - m_hi) / m_lo * 100
                ratio = drop / drop_mid if drop_mid > 0 else float("inf")
                print(f"    正文中位数只掉 {drop_mid:.1f}%，短片段掉得多 {ratio:.1f} 倍")
                if ratio < 2.0:
                    problems.append(
                        f"短片段跌幅（{drop:.1f}%）没有明显大于正文（{drop_mid:.1f}%）"
                        f"，只有 {ratio:.1f} 倍 —— 这个惩罚基本是给所有条目减了个常数，"
                        "对排序没有实际作用"
                    )

        # ── D1 ③ 分数必须非负（多样性是乘法，负分会把顺序整个翻过来）──
        print()
        print(line)
        print("D1③ 分数必须非负 —— 负分乘上 <1 的多样性系数会让排序整个反过来")
        print(line)
        r = eng.search("分布式缓存", weights={**base, "diversity": 1.5, "lengthPenalty": 2.0})
        scores = [h["score"] for h in r.get("hits", [])]
        neg = [s for s in scores if s < 0]
        desc = all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))
        print(f"  {len(scores)} 条，最低分 {min(scores) if scores else 'n/a'}，"
              f"负分 {len(neg)} 条，降序 {desc}")
        if neg:
            problems.append(f"出现了 {len(neg)} 条负分 —— engine.py 里那句 max(1e-9, score) 没生效")
        if scores and not desc:
            problems.append("结果没有按分数降序 —— 多样性重排之后没重新排好")

        # ── D1 ④ 老客户端不带新字段也不能 500 ─────────────────
        print()
        print(line)
        print("D1④ 老客户端（安卓/MCP/CLI）发的 weights 里没有新字段 → 不能报错")
        print(line)
        try:
            old = {"semantic": 1.0, "keyword": 1.0, "recency": 0.3,
                   "sourceTrust": 0.2, "popularity": 0.2, "titleBoost": 0.5}
            r = eng.search("分布式缓存", weights=old)
            print(f"  ✓ 缺 diversity/lengthPenalty 照常返回 {len(r.get('hits', []))} 条")
        except Exception as e:  # noqa: BLE001
            problems.append(f"老格式 weights 直接报错了：{e} —— 手机端一搜就 500")

    print()
    print("=" * 70)
    for sk in skipped:
        print(f"⚠ 跳过（不算通过）：{sk}")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ A1 渐进索引 + D1 新排序指标通过" + ("（含上面标注的跳过项）" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
