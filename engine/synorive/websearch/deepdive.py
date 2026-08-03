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
#: 抓正文的整体预算
FETCH_BUDGET_S = 20.0
MAX_ROUNDS = 3


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
    """
    from ..websearch.research import build_briefing
    from ..websearch.verify import run_verification

    t0 = time.monotonic()
    rounds = max(1, min(MAX_ROUNDS, rounds))
    trace: list[dict[str, Any]] = []
    step = {"n": 0}

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
    got = await _run_round(
        web, variants, engines=engines, limit=limit, lang=lang,
        preset=preset, budget_s=ROUND_BUDGET_S[0],
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
    docs = await _fetch_docs(shown, fetch)

    tick("brief", f"抓到 {len(docs)} 篇正文，正在摘录", fetched=len(docs))
    briefing = build_briefing(query, docs)

    # ── 第二轮起：从第一轮的缺口生成新查询 ──────────────────
    for rnd in range(2, rounds + 1):
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
            preset=preset, budget_s=ROUND_BUDGET_S[min(rnd - 1, len(ROUND_BUDGET_S) - 1)],
        )
        before = len(shown)
        all_clusters.extend(got["clusters"])
        engine_replies.extend(got["engines"])
        shown, dropped = rank_with_trust(_dedupe(all_clusters), profile=profile)
        # 只抓新出现的那几篇，已经抓过的别重抓
        seen_urls = {d["url"] for d in docs}
        fresh = [c for c in shown if c["url"] not in seen_urls][: max(2, fetch // 2)]
        docs.extend(await _fetch_docs(fresh, len(fresh)))
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
    tick(
        "verify",
        {
            "annotate": "只做静态标注，不额外出网",
            "counter": "正在反向搜辟谣/质疑，并追溯最早出处",
            "claim": "正在逐条核查每个断言（这一步最慢）",
        }.get(verify_level, "核查中"),
        level=verify_level,
    )
    verification = await run_verification(
        web, query=query, clusters=shown, briefing=briefing,
        level=verify_level, engines=engines, dois=dois[:20],
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
            res = await web.search(q, engines=eids, limit=limit, lang=v.lang)
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


async def _fetch_docs(clusters: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """
    抓正文。**引擎已经给了正文的就不再抓**（Tavily/Exa 会带 `fulltext`）——
    省掉的是一整个网络往返，深挖里这能省好几秒。
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

    try:
        got = await asyncio.wait_for(
            asyncio.gather(*(grab(c) for c in picked), return_exceptions=True),
            timeout=FETCH_BUDGET_S,
        )
    except (TimeoutError, asyncio.CancelledError):
        return []
    return [d for d in got if isinstance(d, dict)]


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
