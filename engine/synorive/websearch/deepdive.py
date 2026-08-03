"""
分层递进深挖 —— S5
====================================================================
**要治的病**：原来的「深挖」是**一轮**——搜一次、抓五篇、出简报，完事。
一轮挖不深，理由很具体：你搜「向量检索为什么慢」，第一轮回来的资料
会告诉你"因为要算距离"，而真正有用的下一步（HNSW 参数怎么调、
量化会掉多少召回）**你在提问的时候根本不知道要问**。
一轮搜索只能回答你已经会问的问题。

所以这一层做的是：**读完第一轮，自己想出第二轮该问什么，再搜一次。**

第二轮的查询词从三个地方来，每个都对应第一轮暴露出的一种缺口：

  · `openQuestions` —— 简报自己说"这几个说法只有单一来源"，
    那就拿这几个说法单独再搜一次，专门去找第二个来源
  · 分歧话题 —— 有冲突的地方最值得多找资料，而且要**带上冲突双方的词**
  · 高频话题词 —— 第一轮反复出现但没被查询词覆盖的概念，
    往往是这个领域的关键术语，用户不知道它存在所以没搜它

**每一轮都有硬预算**，超时就用已有的出简报。深挖的价值是"更全"，
不是"更久" —— 一个挖了三分钟的深挖，用户早就切走了。

**为什么不做成无限轮**：第三轮开始新增的独立站点数急剧下降
（第二轮已经把主要的邻近话题覆盖掉了），而时间是线性涨的。
默认两轮，上限三轮，再多让用户自己开新查询更划算。
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from typing import Any

from .expand import QueryVariant, expand_query, route_variants
from .presets import apply_preset
from .trust import TrustProfile, rank_with_trust, summarize_trust

log = logging.getLogger("synorive.websearch")

#: 每轮搜索的预算（秒）。第二轮给得比第一轮短 —— 它是补充，
#: 不该比主搜索还慢
ROUND_BUDGET_S = (12.0, 9.0, 8.0)
#: 抓正文的整体预算。**这是硬上限不是目标** —— 正常情况下靠下面
#: 的早退提前收手，走到这个数就说明大半的页面都卡住了
FETCH_BUDGET_S = 20.0
MAX_ROUNDS = 3

#: 🔴 **X3 的全局截止时间。这一条是 P95 那个指标的正主。**
#:
#: 在它之前，每个阶段各有各的预算、互相不知道对方花了多少：
#: 光第一轮搜索就 12s（比 8s 的目标还大），后面还叠 20s 抓正文、
#: 9s 第二轮、再加核查。**各阶段都"没超自己的预算"，加起来照样 12.45s。**
#: 分段实测也印证了这一点：核查降级只省 0.5s、不扩写省 1.8s ——
#: 说明时间不是被某一个阶段吃掉的，是**摊在各阶段的尾巴上**。
#:
#: P95 是尾部指标，治尾部的办法是给一条所有人共用的死线，
#: 而不是把每个阶段的平均值再压快一点（那治的是中位数，本来就达标）。
#:
#: 🔴 **超预算时降级，绝不硬截断。** 少一轮追问、核查降档，简报仍然成立；
#: 而把抓正文砍掉，出来的是一份**空简报** —— 那比慢严重得多：
#: 接口 200、内容是空的、用户看不出发生了什么。降级项会写进
#: 响应的 `budget.degraded`，界面必须显示出来。
TOTAL_BUDGET_S = 8.0
#: 无论如何要给抓正文留的时间。没有正文的简报是空的，
#: 所以这一段的优先级高于"再多搜一轮"
RESERVE_FETCH_S = 3.0
#: 搜索阶段最少也要给这么久，再少不如别搜
MIN_ROUND_S = 1.5
#: 剩余时间少于这个数就不开第二轮了（搜 + 抓至少要这么久）
MIN_FOR_ROUND2_S = 4.0
#: 剩余时间少于这个数，核查从 counter/claim 降到 annotate（不出网）
MIN_FOR_VERIFY_S = 2.0

#: 搜索阶段：一个变体够几家引擎就先走（转给 `meta.search`）
ENOUGH_ENGINES = 3
#: 抓正文够几篇就先走（X3）。
#:
#: 🔴 X3 实测 P95 17.8s / 目标 8.0s，差的不是"哪里慢了一点"，是**结构**：
#: 光第一轮的搜索预算就是 12s，比整个目标还大，再叠 20s 抓正文。
#: 而这两段的时间几乎全花在等最后一两个慢的上 —— 抓 8 篇正文，
#: 前 5 篇往往 1~2 秒内齐了，剩下的能拖十几秒。简报是**摘录**式的，
#: 5 篇和 8 篇的差别远小于十几秒的差别，所以够数就收手。
#:
#: 🔴 **门槛算的是"有几个抓完了"，不是"抓成了几篇"** —— 我在 `meta.py`
#: 刚栽过一次同样的跟头，这里又栽了第二次：第一版写 `len(docs) >= 5`，
#: 而实测 8 个页面里只有 2~3 个能抓成（其余 404/超时/正文太短），
#: 门槛永远够不着，于是照样等满 20s 预算。**那个 15.7s 的离群点就是它。**
#: 教训同上：门槛的分母必须是当下的真实情况，抓失败也是一种"抓完了"。
ENOUGH_DOCS_RATIO = 0.6
#: 至少要有这么多个页面有结论（成或败），再少不足以判断"大部分已经回来了"
ENOUGH_DOCS_MIN = 3
#: 够数之后的宽限期。比搜索那边给得宽 —— 抓正文本来就慢，
#: 0.35s 捞不回什么，0.8s 能多捞回一两篇
GRACE_AFTER_ENOUGH_DOCS_S = 0.8


#: 深挖的阶段清单。**界面据此画进度条，所以顺序和名字要稳定**——
#: 每次改动都会让用户看到的步骤数变来变去
STAGES = (
    ("expand", "想清楚该搜什么"),
    ("search", "多引擎并发搜索"),
    ("rank", "判可信度、排序"),
    ("fetch", "抓正文"),
    ("brief", "出摘录简报"),
    ("followup", "读完之后追问一轮"),
    ("verify", "主动核查"),
    ("done", "完成"),
)


async def deep_research(
    web: Any,
    query: str,
    *,
    engines: list[str] | None = None,
    rounds: int = 2,
    fetch: int = 5,
    limit: int = 20,
    lang: str = "zh",
    preset: str | None = None,
    expand: bool = True,
    verify_level: str = "counter",
    profile: TrustProfile | None = None,
    on_progress: Any | None = None,
    total_budget_s: float | None = TOTAL_BUDGET_S,
) -> dict[str, Any]:
    """
    跑一次深挖。返回的结构是 `/api/web/research` 的响应体。

    `web` 是 `MetaSearch`（鸭子类型，只要有 `.search()`）。

    `on_progress(stage, detail, extra)` 是**每一步推一次**的回调（U2）。
    深挖含两轮加核查要十几到三十秒，全程只有一个转圈图标的话，
    用户根本分不清"它在干活"和"它卡死了"——
    而这两种情况下他该做的事完全相反（等 vs 重来）。

    🔴 **进度回调抛异常绝不能影响深挖本身**：它只是"顺便说一声"，
    为了一条播报把整轮检索废掉是本末倒置。所以统一包一层。

    `total_budget_s` 是**全局截止时间**（X3）。各阶段自己的预算仍然生效，
    但都会被"离死线还剩多久"再夹一道。走到预算尽头时**降级而不是截断**：
    先砍第二轮追问、再把核查降到不出网的 annotate，
    抓正文永远保底 —— 没有正文的简报是空的，那比慢严重得多。
    传 `None` 关掉死线，回到老行为。
    降级了哪些写在返回值的 `budget.degraded` 里，**界面必须显示它**，
    否则用户会以为这就是完整结果。
    """
    from ..websearch.research import build_briefing
    from ..websearch.verify import run_verification

    t0 = time.monotonic()
    rounds = max(1, min(MAX_ROUNDS, rounds))
    trace: list[dict[str, Any]] = []
    step = {"n": 0}

    # ── X3 全局死线 ────────────────────────────────────────
    deadline = (t0 + total_budget_s) if total_budget_s and total_budget_s > 0 else None
    degraded: list[str] = []

    def left() -> float:
        """离死线还有多久。没设死线时返回一个大到不会触发任何降级的数。"""
        return 1e9 if deadline is None else deadline - time.monotonic()

    def stage_budget(own: float, *, reserve: float = 0.0, floor: float = MIN_ROUND_S) -> float:
        """
        这个阶段实际能用多久 = min(它自己的预算, 死线剩余 - 给后面留的)。

        `floor` 保证不会夹成 0 —— 夹成 0 的阶段等于没跑，
        而"跑了但一个结果都没有"和"压根没跑"在日志里长得一样，
        排查时会以为是功能坏了。
        """
        if deadline is None:
            return own
        return max(floor, min(own, left() - reserve))

    def tick(stage: str, detail: str, **extra: Any) -> None:
        step["n"] += 1
        if on_progress is None:
            return
        try:
            on_progress({
                "stage": stage,
                "detail": detail,
                "step": step["n"],
                "totalStages": len(STAGES),
                "elapsedMs": int((time.monotonic() - t0) * 1000),
                "query": query,
                **extra,
            })
        except Exception as e:  # noqa: BLE001 — 播报失败不该让检索失败
            log.debug("进度回调出错（忽略）：%s", e)

    # ── 第一轮 ──────────────────────────────────────────────
    tick("expand", "正在想清楚该搜什么" if expand else "直接用你输入的原话")
    variants = (
        await expand_query(query, lang=lang) if expand
        else [QueryVariant(text=query, lang=lang, kind="original", why="你输入的原话")]
    )
    if len(variants) > 1:
        tick(
            "expand",
            "除了原话，还会搜：" + "、".join(v.text for v in variants[1:]),
            variants=[v.to_dict() for v in variants],
        )

    all_clusters: list[dict[str, Any]] = []
    engine_replies: list[dict[str, Any]] = []

    tick("search", f"第 1 轮：{len(variants)} 个查询并发搜出去", round=1)
    # 第一轮必须给抓正文留出 RESERVE_FETCH_S —— 搜到一堆链接却没时间抓正文，
    # 出来的是一份空简报，那是这条链路最坏的结局
    got = await _run_round(
        web, variants, engines=engines, limit=limit, lang=lang,
        preset=preset, budget_s=stage_budget(ROUND_BUDGET_S[0], reserve=RESERVE_FETCH_S),
    )
    all_clusters.extend(got["clusters"])
    engine_replies.extend(got["engines"])
    trace.append({
        "round": 1,
        "queries": [v.to_dict() for v in variants],
        "newResults": len(got["clusters"]),
    })

    tick("rank", f"拿到 {len(all_clusters)} 条，正在判来源可信度")
    shown, dropped = rank_with_trust(_dedupe(all_clusters), profile=profile)

    tick(
        "fetch",
        f"折叠成 {len(shown)} 条，正在抓最靠前 {min(fetch, len(shown))} 篇的正文",
        results=len(shown), excluded=len(dropped),
    )
    docs = await _fetch_docs(
        shown, fetch,
        budget_s=stage_budget(FETCH_BUDGET_S, reserve=MIN_FOR_VERIFY_S, floor=RESERVE_FETCH_S),
    )

    tick("brief", f"抓到 {len(docs)} 篇正文，正在摘录", fetched=len(docs))
    briefing = build_briefing(query, docs)

    # ── 第二轮起：从第一轮的缺口生成新查询 ──────────────────
    for rnd in range(2, rounds + 1):
        # 🔴 时间不够就不开新一轮。第二轮是**补充**，它的价值远小于
        #    "整件事在预算内做完"；而开了一轮又中途被死线掐断，
        #    花掉的时间一点结果都换不回来 —— 那是最差的一种选择
        if left() < MIN_FOR_ROUND2_S:
            degraded.append(f"跳过第 {rnd} 轮追问（剩余 {max(0.0, left()):.1f}s 不够搜一轮再抓正文）")
            trace.append({"round": rnd, "queries": [], "skipped": "时间预算不够，用已有结果出简报"})
            tick("followup", f"时间预算只剩 {max(0.0, left()):.1f}s，不开第 {rnd} 轮了，用已有结果出简报", round=rnd)
            break

        follow = _followup_queries(query, briefing, limit=3)
        if not follow:
            trace.append({"round": rnd, "queries": [], "skipped": "第一轮没有暴露出值得追问的缺口"})
            tick("followup", "前一轮没有暴露出值得追问的缺口，不硬凑第二轮", round=rnd)
            break
        fv = [
            QueryVariant(text=q, lang=lang, kind="term", why=why, weight=0.85)
            for q, why in follow
        ]
        # 把「我为什么要多搜这几个词」实时说出来。等结束再一起给的话，
        # 用户在等待期间看到的仍然只是一个转圈 —— 而这恰恰是最长的一段等待
        tick(
            "followup",
            f"第 {rnd} 轮，读完上一轮后想追问：" + "；".join(f"{q}（{w}）" for q, w in follow),
            round=rnd,
            queries=[{"text": q, "why": w} for q, w in follow],
        )
        got = await _run_round(
            web, fv, engines=engines, limit=max(8, limit // 2), lang=lang,
            preset=preset,
            budget_s=stage_budget(
                ROUND_BUDGET_S[min(rnd - 1, len(ROUND_BUDGET_S) - 1)],
                reserve=RESERVE_FETCH_S,
            ),
        )
        before = len(shown)
        all_clusters.extend(got["clusters"])
        engine_replies.extend(got["engines"])
        shown, dropped = rank_with_trust(_dedupe(all_clusters), profile=profile)
        # 只抓新出现的那几篇，已经抓过的别重抓
        seen_urls = {d["url"] for d in docs}
        fresh = [c for c in shown if c["url"] not in seen_urls][: max(2, fetch // 2)]
        docs.extend(await _fetch_docs(
            fresh, len(fresh),
            budget_s=stage_budget(FETCH_BUDGET_S, reserve=MIN_FOR_VERIFY_S, floor=1.0),
        ))
        briefing = build_briefing(query, docs)
        trace.append({
            "round": rnd,
            "queries": [v.to_dict() for v in fv],
            "newResults": len(shown) - before,
        })

    # ── 核查 ────────────────────────────────────────────────
    dois = [
        m.group(0)
        for c in shown
        for m in [re.search(r"10\.\d{4,9}/[^\s\"'<>]+", str(c.get("url") or ""))]
        if m
    ]
    # 🔴 时间不够就把核查降到 annotate。annotate 是**纯本地静态标注、不出网**，
    #    所以它几乎不花时间，同时"反虚假"这条产品线不会整个消失 ——
    #    降级后简报上仍然有标注，只是少了反向检索那一层。
    #    直接跳过核查的话，用户拿到的是一份**没有任何可信度信息**的简报，
    #    而这个产品的卖点恰恰是"会自己找反驳材料"。
    effective_level = verify_level
    if verify_level != "annotate" and left() < MIN_FOR_VERIFY_S:
        effective_level = "annotate"
        degraded.append(
            f"核查从 {verify_level} 降到 annotate（剩余 {max(0.0, left()):.1f}s 不够出网反查）"
        )

    tick(
        "verify",
        {
            "annotate": "只做静态标注，不额外出网",
            "counter": "正在反向搜辟谣/质疑，并追溯最早出处",
            "claim": "正在逐条核查每个断言（这一步最慢）",
        }.get(effective_level, "核查中"),
        level=effective_level,
        **({"degradedFrom": verify_level} if effective_level != verify_level else {}),
    )
    verification = await run_verification(
        web, query=query, clusters=shown, briefing=briefing,
        level=effective_level, engines=engines, dois=dois[:20],
    )

    tick(
        "done",
        f"完成：{len(shown)} 条结果、{len(docs)} 篇正文、{len(trace)} 轮检索",
        results=len(shown), fetched=len(docs),
    )
    return {
        "query": query,
        "results": shown,
        "excluded": dropped,
        "trustSummary": summarize_trust(shown, dropped),
        "briefing": briefing,
        "verification": verification,
        "rounds": trace,
        "fetched": len(docs),
        "fetchFailed": max(0, min(fetch, len(shown)) - len(docs)),
        "engines": engine_replies,
        "elapsedMs": int((time.monotonic() - t0) * 1000),
        # 🔴 降级必须说出来。用户拿到一份"少了一轮追问、核查只做了标注"的简报，
        #    如果界面不显示这一段，他会把它当成完整结果 ——
        #    那是拿他的信任换我们的 P95 数字
        "budget": {
            "totalS": total_budget_s,
            "usedMs": int((time.monotonic() - t0) * 1000),
            "overrun": bool(deadline is not None and left() < 0),
            "degraded": degraded,
            "verifyLevel": effective_level,
            "verifyRequested": verify_level,
        },
    }


# ────────────────────────────────────────────────────────────────
async def _run_round(
    web: Any,
    variants: list[QueryVariant],
    *,
    engines: list[str] | None,
    limit: int,
    lang: str,
    preset: str | None,
    budget_s: float,
) -> dict[str, Any]:
    """
    一轮：把所有变体并发搜出去，合并结果。

    **变体之间是并发的，不是串行的** —— 串行的话三个变体就是三倍时间，
    而它们互不依赖。合并时按变体权重给分打折：只被英文变体命中的结果
    会排在被原查询命中的后面（翻译永远可能错，原话一定对）。
    """
    enabled = engines if engines is not None else list(getattr(web, "enabled", []) or [])
    routed = route_variants(variants, enabled) if enabled else [(v, engines) for v in variants]

    async def one(v: QueryVariant, eids: list[str] | None) -> dict[str, Any]:
        q, _p = apply_preset(v.text, preset)
        try:
            # 显式开早退（X3）。这里必须显式传 —— `search()` 看到 `engines`
            # 非空会当成"用户点名了引擎"而关掉早退，但这里的 `eids` 是
            # `route_variants` 派的，不是用户点的。不传的话深挖每个变体
            # 都要等齐它那几家，一轮就顶满 12s 预算
            res = await web.search(q, engines=eids, limit=limit, lang=v.lang,
                                   enough=ENOUGH_ENGINES)
        except Exception as e:  # noqa: BLE001 — 一个变体炸了不该让整轮失败
            log.debug("变体检索失败（忽略）：%s / %s", v.text, e)
            return {"clusters": [], "engines": []}
        out: list[dict[str, Any]] = []
        for c in res.clusters:
            d = c.to_dict()
            d["score"] = float(d.get("score") or 0.0) * v.weight
            d["viaQuery"] = v.text
            d["viaKind"] = v.kind
            out.append(d)
        return {
            "clusters": out,
            "engines": [
                {"id": r.engine, "outcome": r.outcome.value, "count": len(r.results),
                 "elapsedMs": r.elapsed_ms, "query": v.text,
                 **({"error": r.error} if r.error else {})}
                for r in res.replies
            ],
        }

    try:
        batches = await asyncio.wait_for(
            asyncio.gather(*(one(v, e) for v, e in routed), return_exceptions=True),
            timeout=budget_s,
        )
    except (TimeoutError, asyncio.CancelledError):
        return {"clusters": [], "engines": [
            {"id": "-", "outcome": "broken", "count": 0, "elapsedMs": int(budget_s * 1000),
             "error": f"这一轮整体超时（>{budget_s:.0f}s），用已有结果继续"}
        ]}

    clusters: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []
    for b in batches:
        if isinstance(b, dict):
            clusters.extend(b["clusters"])
            replies.extend(b["engines"])
    return {"clusters": clusters, "engines": replies}


def _dedupe(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    跨轮次、跨变体的去重。

    同一个页面被多个变体搜到时**取分数更高的那份，并把两边的引擎并起来** ——
    直接留第一份会丢掉"它也被第二个查询命中了"这个信息，
    而那恰恰说明这条结果和话题的相关性更强。
    """
    from .meta import canonical_url

    best: dict[str, dict[str, Any]] = {}
    for c in clusters:
        k = canonical_url(str(c.get("url") or ""))
        cur = best.get(k)
        if cur is None:
            best[k] = dict(c)
            continue
        merged_engines = sorted(set(cur.get("engines") or []) | set(c.get("engines") or []))
        keep = cur if float(cur.get("score") or 0) >= float(c.get("score") or 0) else dict(c)
        keep["engines"] = merged_engines
        keep["engineCount"] = len(merged_engines)
        # 被多个查询命中过 → 相关性更强，加一点分（不覆盖原分，只叠加）
        keep["score"] = float(keep.get("score") or 0) + 0.15 * min(
            float(cur.get("score") or 0), float(c.get("score") or 0)
        )
        vias = {str(cur.get("viaQuery") or ""), str(c.get("viaQuery") or "")}
        keep["viaQueries"] = sorted(v for v in vias if v)
        best[k] = keep
    return sorted(best.values(), key=lambda d: -float(d.get("score") or 0))


async def _fetch_docs(
    clusters: list[dict[str, Any]],
    n: int,
    *,
    budget_s: float = FETCH_BUDGET_S,
) -> list[dict[str, Any]]:
    """
    抓正文。**引擎已经给了正文的就不再抓**（Tavily/Exa 会带 `fulltext`）——
    省掉的是一整个网络往返，深挖里这能省好几秒。

    `budget_s` 由调用方按全局死线夹过（X3）。默认值是老的硬上限，
    这样单独调用它的地方行为不变。
    """
    from ..ingest.web import fetch as fetch_page

    picked = clusters[: max(0, n)]
    if not picked:
        return []

    async def grab(c: dict[str, Any]) -> dict[str, Any] | None:
        pre = (c.get("meta") or {}).get("fulltext")
        if pre and len(str(pre)) > 200:
            return {
                "url": c["url"], "title": c.get("title") or "",
                "site": c.get("site") or "", "text": str(pre)[:40000],
                "published": c.get("published"), "trust": c.get("trust"),
                "viaEngineText": True,
            }
        try:
            page = await asyncio.to_thread(fetch_page, c["url"], save_html=False)
        except Exception as e:  # noqa: BLE001
            log.debug("抓正文失败（忽略）：%s / %s", c.get("url"), e)
            return None
        if not page.text or len(page.text) < 200:
            return None
        return {
            "url": c["url"], "title": page.title or c.get("title") or "",
            "site": c.get("site") or "", "text": page.text[:40000],
            "published": page.published or c.get("published"), "trust": c.get("trust"),
        }

    # 够 ENOUGH_DOCS 篇就先走，不等最后那一两个慢页面（X3）。
    #
    # 🔴 原来这里是 `gather` + `wait_for(20s)`，是**全有或全无**：
    # 20 秒内没抓齐就 `return []` —— 抓到 7 篇也整批丢掉，
    # 白等 20 秒还一篇正文都没有，简报直接变空。
    # 这个静默失败比慢更严重：接口 200、耗时很长、内容是空的。
    tasks = {asyncio.create_task(grab(c)): c for c in picked}
    pending = set(tasks)
    docs: list[dict[str, Any]] = []
    landed = 0          # 有结论的（抓成 + 抓失败都算）
    started = time.monotonic()
    grace_until: float | None = None
    need = min(len(tasks), max(ENOUGH_DOCS_MIN, math.ceil(len(tasks) * ENOUGH_DOCS_RATIO)))

    # 死线夹过之后可能只剩很短一点。夹成 0 或负数的话下面第一轮就 break，
    # 一篇正文都不抓 —— 简报直接变空。给一个不可再小的地板
    budget_s = max(1.0, budget_s)

    while pending:
        now = time.monotonic()
        left = budget_s - (now - started)
        if left <= 0:
            break
        if grace_until is not None:
            left = min(left, grace_until - now)
            if left <= 0:
                break
        batch, pending = await asyncio.wait(
            pending, timeout=left, return_when=asyncio.FIRST_COMPLETED,
        )
        if not batch:
            break
        for t in batch:
            landed += 1
            try:
                d = t.result()
            except Exception as e:      # noqa: BLE001 — 一篇抓不到不该毁掉整轮
                log.debug("抓正文任务异常（忽略）：%s", e)
                continue
            if isinstance(d, dict):
                docs.append(d)
        # 手里至少有一篇正文（没有正文的简报是空的），且大部分页面已有结论
        if grace_until is None and docs and landed >= need:
            grace_until = time.monotonic() + GRACE_AFTER_ENOUGH_DOCS_S

    for t in pending:
        t.cancel()
    if pending:
        # ⚠️ `grab` 里是 `asyncio.to_thread`，取消只是让**我们**不等了，
        # 那个线程还会自己跑完（Python 没法从外面掐断一个正在阻塞的线程）。
        # 这是可接受的代价：连接会在超时后自己断，结果被丢弃。
        # 写在这里是因为"cancel 了就等于停了"这个想当然，正是下一个人会踩的坑
        await asyncio.gather(*pending, return_exceptions=True)
    return docs


# ────────────────────────────────────────────────────────────────
# 第二轮的查询词怎么来
# ────────────────────────────────────────────────────────────────
def _followup_queries(
    query: str, briefing: dict[str, Any], *, limit: int = 3
) -> list[tuple[str, str]]:
    """
    从第一轮的简报里读出缺口，生成第二轮查询。返回 `[(查询词, 为什么)]`。

    **必须带"为什么"**：第二轮是我替用户决定去搜的，他有权知道我为什么
    搜了这些词 —— 否则结果里冒出一批他没搜过的东西，只会让人困惑。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = {query.strip().lower()}

    def add(q: str, why: str) -> None:
        q = re.sub(r"\s+", " ", q).strip()
        if not q or q.lower() in seen or len(out) >= limit:
            return
        seen.add(q.lower())
        out.append((q, why))

    # ① 分歧最值得追 —— 冲突处往往正是这个问题的关键
    for d in (briefing.get("disputes") or [])[:2]:
        topic = str(d.get("topic") or "").strip()
        if topic:
            add(f"{query} {topic} 对比", f"第一轮在「{topic}」上各家说法不一致，专门再查一轮")

    # ② 孤证：只有一个站说的，去找第二个来源
    for line in briefing.get("openQuestions") or []:
        m = re.search(r"没有第二个站点印证：(.+)$", str(line))
        if m:
            for term in m.group(1).split("、")[:2]:
                t = term.strip()
                if t:
                    add(f"{query} {t}", f"「{t}」第一轮只有单一来源，去找第二个站点印证")

    # ③ 共识里出现但查询词没覆盖的高频概念 —— 用户不知道它存在所以没搜
    for c in (briefing.get("consensus") or [])[:3]:
        topic = str(c.get("topic") or "").strip()
        if topic and topic not in query:
            add(f"{query} {topic}", f"第一轮反复提到「{topic}」，但你的原查询没覆盖它")

    return out[:limit]
