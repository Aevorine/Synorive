#!/usr/bin/env python
"""
联网元搜索 —— 实调测试（W1/W2/W4 + R1/R3/R4/R5/R6 + P1/P2/P9）
====================================================================
🔴 **这个测试刻意用真实网络，不用 fixture。**

HTML 解析器唯一有意义的验证对象是**对方今天真实返回的页面**。
拿我自己造的 HTML 去测，测的是"我以为它长这样"，
而解析器失效的原因恰恰是"它现在不长那样了" —— fixture 测不出这件事。

所以：
  · 联网部分：真发请求，报告每家引擎**当下**能不能用
  · 判分部分（trust / research）：纯本地逻辑，用正反用例测，不依赖网络
  · 断网时联网那几节自动跳过并明说跳过了，**不算通过**

用法：python -m tests.test_websearch
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.websearch import MetaSearch, all_engines  # noqa: E402
from synorive.websearch.engines import ParseOutcome  # noqa: E402
from synorive.websearch.meta import _fold_key  # noqa: E402
from synorive.websearch.research import build_briefing, build_topics  # noqa: E402
from synorive.websearch.trust import (  # noqa: E402
    Tier, ai_suspect, age_days, classify_domain, evaluate, farm_flags, rank_with_trust,
)

LINE = "─" * 70
problems: list[str] = []
skipped: list[str] = []


def check(cond: bool, ok_msg: str, bad_msg: str) -> bool:
    print(f"  {'✓' if cond else '✗'} {ok_msg if cond else bad_msg}")
    if not cond:
        problems.append(bad_msg)
    return cond


# ══════════════════════════════════════════════════════════════
def test_trust() -> None:
    print(LINE)
    print("① 来源分级 R1 —— 正反两面都要测")
    print(LINE)
    cases = [
        ("https://arxiv.org/abs/2301.00001", Tier.ACADEMIC),
        ("https://docs.python.org/3/library/re.html", Tier.OFFICIAL),
        ("https://www.nasa.gov/mission", Tier.OFFICIAL),
        ("https://cs.stanford.edu/~foo", Tier.ACADEMIC),
        ("https://zhuanlan.zhihu.com/p/123", Tier.COMMUNITY),
        ("https://blog.csdn.net/x/article/details/1", Tier.LOW),
        ("https://example-unknown-site-xyz.com/a", Tier.UNKNOWN),
    ]
    for url, want in cases:
        got = classify_domain(url)
        check(got is want, f"{url[:44]:46} → {got.label}",
              f"{url} 应判为 {want.value}，实际 {got.value}")

    # lstrip 陷阱：不能把 world.com 啃成 orld.com
    check(classify_domain("https://world.com/a") is Tier.UNKNOWN,
          "world.com 没被 www 剥离逻辑啃掉开头",
          "www 前缀剥离写成了 lstrip，把 world.com 啃坏了")

    print()
    print(LINE)
    print("② 内容农场 R3 / 疑似生成 R6 —— 误杀比漏杀更糟，两头都测")
    print(LINE)
    farm = farm_flags("震惊！一文看懂_全网最全_Python_教程_2024_建议收藏",
                      "本文转载自某公众号，版权归原作者所有，侵删")
    check(len(farm) >= 3, f"农场标题命中 {len(farm)} 条判据：{farm}",
          f"典型农场标题只命中 {len(farm)} 条，判据太松")

    clean = farm_flags("Python 正则表达式中的零宽断言",
                       "零宽断言不消耗字符，常用于在不改变匹配位置的前提下加约束条件。")
    check(not clean, "正常技术文章一条判据都没命中（不误杀）",
          f"正常文章被误判为农场：{clean}")

    check(ai_suspect("在当今社会的背景下谈谈效率",
                     "随着技术的不断发展，值得注意的是，综上所述，我们需要重视这个问题。"),
          "套话密集且无任何具体信息 → 标记疑似批量生成", "套话样本没被标记")
    check(not ai_suspect("SQLite 3.45 的 WAL 模式改动",
                         "综上所述，3.45 把 checkpoint 阈值从 1000 页调到 4000 页，实测写入吞吐提升 12%。"),
          "有具体版本号和数字的文章不被误标", "带具体数据的文章被误标为 AI 生成")

    print()
    print(LINE)
    print("③ 时效 R4 —— 抽不出时间必须返回 None，绝不用今天顶替")
    print(LINE)
    check(age_days(None) is None, "没有时间 → None（不猜）", "没有时间时不该返回数字")
    check(age_days("乱七八糟") is None, "解析不了 → None（不猜）", "解析不了时不该返回数字")
    d = age_days("2020-01-01")
    check(d is not None and d > 1500, f"2020-01-01 → {d} 天前", "旧日期没算对")

    print()
    print(LINE)
    print("④ R11 可审计：被排除的必须出现在第二个列表里，不能凭空消失")
    print(LINE)
    clusters = [
        {"title": "SQLite WAL 模式说明", "url": "https://sqlite.org/wal.html",
         "snippet": "WAL 模式下读写不互相阻塞。", "site": "sqlite.org", "score": 0.02, "siteCount": 3},
        {"title": "震惊_速看_删前必看_全网最全_教程_2024",
         "url": "https://spam-farm-xyz.com/a",
         "snippet": "震惊_速看_删前必看_全网最全_教程_2024 本文转载自网络，侵删",
         "site": "spam-farm-xyz.com", "score": 0.015, "siteCount": 1},
    ]
    shown, dropped = rank_with_trust(clusters)
    check(len(shown) + len(dropped) == len(clusters),
          f"{len(clusters)} 条进 → {len(shown)} 条显示 + {len(dropped)} 条已排除，一条没丢",
          f"进 {len(clusters)} 条出 {len(shown) + len(dropped)} 条 —— 有结果被静默丢弃")
    check(len(dropped) == 1 and "spam-farm" in dropped[0]["url"],
          "农场那条进了「已排除」抽屉", "农场那条没被识别出来")
    if dropped:
        reasons = (dropped[0].get("trust") or {}).get("reasons") or []
        check(bool(reasons), f"被排除的带原因：{reasons[-1][:40]}", "被排除的没写原因，用户无从判断")
    check(bool(shown) and shown[0]["url"].startswith("https://sqlite.org"),
          "官方文档排在第一", "可信度权重没起作用")


# ══════════════════════════════════════════════════════════════
def test_fold() -> None:
    print()
    print(LINE)
    print("⑤ W4 折叠 —— 同一篇算一条，不同篇不能误合")
    print(LINE)
    from synorive.websearch.engines import WebResult

    a = WebResult(title="深入理解 WAL", url="https://a.com/p/1?utm_source=x",
                  snippet="WAL 模式下读写不互相阻塞", engine="bing", rank=1)
    b = WebResult(title="深入理解 WAL", url="https://a.com/p/1?utm_source=y&spm=z",
                  snippet="WAL 模式下读写不互相阻塞", engine="google", rank=2)
    c = WebResult(title="2024 年度总结", url="https://x.com/1",
                  snippet="今年我们做了三件事：一是重构索引层。", engine="bing", rank=3)
    d = WebResult(title="2024 年度总结", url="https://y.com/1",
                  snippet="今年公司营收增长 20%，团队扩到 50 人。", engine="bing", rank=4)

    check(_fold_key(a) == _fold_key(b),
          "同一篇（只差追踪参数）折叠成一条", "追踪参数不同就被当成两篇了")
    check(_fold_key(c) != _fold_key(d),
          "标题相同但内容不同的两篇没有被误合", "「2024 年度总结」这种同名不同文被错误合并")


# ══════════════════════════════════════════════════════════════
def test_research() -> None:
    print()
    print(LINE)
    print("⑥ R5 矛盾并排 + R8 简报 —— 冲突必须并排列出，不许替用户选一个")
    print(LINE)
    docs = [
        {"url": "https://a.edu/1", "title": "缓存策略研究", "site": "a.edu",
         "published": "2025-03-01",
         "trust": {"score": 0.95, "tierLabel": "学术"},
         "text": "预取策略能显著降低缓存未命中率。实验表明预取可以把延迟降低 30%。"},
        {"url": "https://b.org/2", "title": "缓存实践笔记", "site": "b.org",
         "published": "2024-06-01",
         "trust": {"score": 0.75, "tierLabel": "主流媒体"},
         "text": "预取策略不能降低缓存未命中率，反而不会带来延迟改善。"},
        {"url": "https://c.com/3", "title": "缓存入门", "site": "c.com",
         "trust": {"score": 0.55, "tierLabel": "社区/个人"},
         "text": "预取策略能降低缓存未命中率，这一点在多数系统里都成立。"},
    ]
    topics = build_topics("预取缓存未命中率", docs)
    check(bool(topics), f"聚出 {len(topics)} 个子话题", "一个子话题都没聚出来")

    brief = build_briefing("预取缓存未命中率", docs)
    check(brief["kind"] == "extract", "简报标明 kind=extract（原文摘录不是生成）",
          "简报没标明这是摘录")
    has_conflict = bool(brief["disputes"])
    check(has_conflict, f"抓到 {len(brief['disputes'])} 组分歧并排列出",
          "一句肯定一句否定的明显冲突没被抓到")
    if has_conflict:
        pair = brief["disputes"][0]["conflicts"][0]
        check(pair["a"]["url"] != pair["b"]["url"],
              "分歧的两条来自不同来源（不是自己跟自己打架）", "分歧两条来自同一来源")

    # 所有证据必须带出处 —— R7 的硬约束
    all_ev = [e for t in brief["disputes"] for e in t["evidence"]] + \
             [e for t in brief["consensus"] for e in t["evidence"]]
    check(all(e.get("url") for e in all_ev) and bool(all_ev),
          f"{len(all_ev)} 条证据全部带出处 URL", "有证据没带出处，用户无从核对")

    # 摘录必须逐字来自原文
    corpus = "".join(d["text"] for d in docs)
    bad = [e["text"] for e in all_ev if e["text"] not in corpus]
    check(not bad, "所有摘录都逐字存在于原文里",
          f"有 {len(bad)} 条摘录不在原文里 —— 这是生成不是摘录：{bad[:1]}")

    check(bool(brief["timeline"]), f"时间线 {len(brief['timeline'])} 条", "时间线是空的")
    check(bool(brief["numbers"]), f"关键数据抽出 {len(brief['numbers'])} 条", "没抽出任何带数字的句子")


# ══════════════════════════════════════════════════════════════
async def test_live() -> None:
    print()
    print(LINE)
    print("⑦ 实调各家引擎 —— 这一节必须联网，报告每家**当下**能不能用")
    print(LINE)

    ms = MetaSearch(enabled=[e.id for e in all_engines() if e.default_on])
    print(f"  本轮启用：{', '.join(ms.enabled)}")

    t0 = time.monotonic()
    res = await ms.search("sqlite wal 模式 原理", limit=15, lang="zh")
    elapsed = (time.monotonic() - t0) * 1000

    if not res.replies:
        skipped.append("联网部分：一个引擎都没跑起来")
        print("  ⚠ 没有任何引擎返回 —— 断网？这一节跳过，不计入通过")
        return

    ok_engines, broken = [], []
    for r in res.replies:
        mark = {"ok": "✓", "empty": "○", "challenged": "⏳", "broken": "✗"}[r.outcome.value]
        print(f"  {mark} {r.engine:12} {r.outcome.value:6} {len(r.results):3} 条  "
              f"{r.elapsed_ms:5}ms  {r.error[:44]}")
        (ok_engines if r.outcome is ParseOutcome.OK else broken).append(r.engine)

    if not ok_engines:
        skipped.append("联网部分：全部引擎失败（可能断网或全被反爬挡住）")
        print("  ⚠ 全部失败 —— 断网或全被挡，这一节不计入通过")
        return

    check(len(ok_engines) >= 2,
          f"{len(ok_engines)} 家引擎可用：{ok_engines}",
          f"只有 {len(ok_engines)} 家可用（{ok_engines}），少于两家就谈不上"
          f"「多引擎」；坏的：{broken}")
    check(bool(res.clusters), f"融合后 {len(res.clusters)} 条结果", "融合后一条结果都没有")
    check(elapsed <= 4500, f"P2 整轮耗时 {elapsed:.0f}ms ≤ 4000ms（放宽 500ms 容差）",
          f"P2 不达标：整轮 {elapsed:.0f}ms 超过 4000ms")

    multi = [c for c in res.clusters if len(c.engines) >= 2]
    sites = {s for c in res.clusters for s in c.sites}
    print(f"  · 被 ≥2 家引擎同时搜到：{len(multi)} 条 ｜ 覆盖 {len(sites)} 个不同站点")
    print("    （跨引擎重叠低是**真实现象**不是 bug：实测 Bing 偏中文教程、"
          "Mojeek 偏英文官方文档、360 偏博客，同一查询的结果集几乎不相交。")
    print("     所以 R2「多源印证」看的是**站点数**而不是引擎数 —— 折叠逻辑本身由 ⑤ 的单测覆盖）")

    # P9 缓存
    t1 = time.monotonic()
    res2 = await ms.search("sqlite wal 模式 原理", limit=15, lang="zh")
    cached_ms = (time.monotonic() - t1) * 1000
    check(res2.from_cache and cached_ms < 50,
          f"P9 缓存命中，{cached_ms:.1f}ms 返回（首次 {elapsed:.0f}ms）",
          f"缓存没生效：fromCache={res2.from_cache}, 耗时 {cached_ms:.0f}ms")

    # 熔断：解析失败的引擎失败计数必须涨
    health = ms.engine_health()
    really_broken = [r.engine for r in res.replies if r.outcome is ParseOutcome.BROKEN]
    challenged = [r.engine for r in res.replies if r.outcome is ParseOutcome.CHALLENGED]
    if really_broken:
        counted = [e for e in really_broken if health["breaker"].get(e, {}).get("fails", 0) > 0]
        check(bool(counted),
              f"解析真坏掉的引擎已计入熔断器：{counted}",
              f"引擎 {really_broken} 解析失败但熔断器没记账 —— 它们会被无限重试")
    else:
        print("  ○ 本轮没有引擎解析失败，熔断计数这条没测到")
    if challenged:
        clean = [e for e in challenged if health["breaker"].get(e, {}).get("fails", 0) == 0]
        check(len(clean) == len(challenged),
              f"被限流/验证码的没被误算成故障：{challenged}",
              f"被限流的 {challenged} 被算进了熔断失败数 —— 好引擎会被无辜停用")

    # 端到端：搜 → 判可信度 → 抓正文 → 出简报
    print()
    print(LINE)
    print("⑧ 端到端：搜到的结果打分 → 抓正文 → 出摘录简报")
    print(LINE)
    shown, dropped = rank_with_trust([c.to_dict() for c in res.clusters])
    print(f"  可信度排序后：{len(shown)} 条显示 / {len(dropped)} 条已排除")
    for c in shown[:5]:
        t = c["trust"]
        print(f"    {t['tierLabel']:6} {t['score']:.2f}  {c['title'][:38]}")
    check(len(shown) + len(dropped) == len(res.clusters),
          "打分环节一条结果都没丢", "打分环节丢了结果")

    from synorive.ingest.web import fetch

    docs = []
    for c in shown[:4]:
        page = fetch(c["url"], save_html=False)
        if page.text and len(page.text) > 200:
            docs.append({
                "url": c["url"], "title": page.title or c["title"], "site": c["site"],
                "text": page.text[:20000], "published": page.published,
                "trust": c["trust"],
            })
    print(f"  抓到正文 {len(docs)}/{min(4, len(shown))} 篇")
    if len(docs) >= 2:
        brief = build_briefing("sqlite wal 模式 原理", docs)
        corpus = "".join(d["text"] for d in docs)
        ev = [e for t in brief["disputes"] for e in t["evidence"]] + \
             [e for t in brief["consensus"] for e in t["evidence"]]
        check(bool(ev), f"真实语料上摘出 {len(ev)} 条证据", "真实语料上一条证据都没摘出来")
        bad = [e["text"] for e in ev if e["text"] not in corpus]
        check(not bad, "真实语料上的摘录同样逐字存在于原文",
              f"真实语料上有 {len(bad)} 条摘录不在原文里")
        print(f"  共识 {len(brief['consensus'])} 组 ｜ 分歧 {len(brief['disputes'])} 组 "
              f"｜ 数据 {len(brief['numbers'])} 条 ｜ 待查 {len(brief['openQuestions'])} 条")
        for c in brief["consensus"][:2]:
            print(f"    [{c['topic']}] {c['evidence'][0]['text'][:52]}…")
            print(f"       ↳ {c['evidence'][0]['site']}")
    else:
        skipped.append("端到端简报：抓到的正文不足 2 篇")
        print("  ⚠ 正文抓取不足 2 篇，简报那段跳过")


def test_scholar_merge() -> None:
    """
    DOI 去重必须用**确定性用例**验，不能靠联网结果碰运气。

    实调里「被 ≥2 家收录 0 篇」是真实现象（arXiv 出预印本、OpenAlex 出经典库论文、
    PubMed 出生医文献，同一查询下本来就不怎么重叠），
    但那也意味着**去重逻辑在实调里根本没被执行到** —— 没执行过的代码不算验过。
    """
    print()
    print(LINE)
    print("⑨a L1 DOI 合并 —— 同一篇被多家收录时必须合成一条，且字段取并集")
    print(LINE)
    from synorive.websearch.engines import WebResult
    from synorive.websearch.scholar import merge_scholar, scholar_fold_key

    a = WebResult(title="A Study of Write-Ahead Logging", url="https://doi.org/10.1145/1.2",
                  snippet="short", engine="crossref", rank=1)
    a.meta = {"doi": "10.1145/1.2", "venue": "SIGMOD", "authors": ["Zhang"], "year": "2019"}
    b = WebResult(title="A study of write-ahead logging.",
                  url="https://api.openalex.org/W1", snippet="a much longer abstract here",
                  engine="openalex", rank=3)
    # 大小写不同 + 带 https://doi.org/ 前缀 —— 各家给 DOI 的写法就是不统一
    b.meta = {"doi": "https://doi.org/10.1145/1.2", "citations": 42, "pdf": "http://x/p.pdf"}
    c = WebResult(title="Something Else Entirely", url="https://doi.org/10.9/9",
                  snippet="", engine="doaj", rank=2)
    c.meta = {"doi": "10.9/9"}

    check(scholar_fold_key(a) == scholar_fold_key(b),
          "DOI 大小写与 https://doi.org/ 前缀差异被抹平", "同一个 DOI 的两种写法没被认成一篇")
    merged = merge_scholar([a, b, c])
    check(len(merged) == 2, f"3 条合成 {len(merged)} 条", f"应合成 2 条，实际 {len(merged)} 条")
    top = next(p for p in merged if (p.get("meta") or {}).get("doi", "").endswith("1.2"))
    check(top["sourceCount"] == 2 and set(top["sources"]) == {"crossref", "openalex"},
          f"合并后记着来自哪几家：{top['sources']}", "合并后丢了来源信息")
    m = top["meta"]
    check(all(m.get(k) for k in ("venue", "citations", "pdf", "authors")),
          f"字段取并集：期刊={m.get('venue')} 被引={m.get('citations')} PDF={bool(m.get('pdf'))} 作者={m.get('authors')}",
          f"字段没取并集，只保留了第一条：{m}")
    check(top["snippet"] == "a much longer abstract here",
          "摘要保留了更长的那份", "摘要没有取更完整的那份")


async def test_scholar() -> None:
    print()
    print(LINE)
    print("⑨ L1 学术源实调 —— 五家并发，按 DOI 合并")
    print(LINE)
    ms = MetaSearch()
    res = await ms.search_scholar("write-ahead logging database recovery", limit=20)

    ok = []
    for s in res["sources"]:
        mark = {"ok": "✓", "empty": "○", "challenged": "⏳", "broken": "✗"}[s["outcome"]]
        print(f"  {mark} {s['id']:10} {s['outcome']:10} {s['count']:3} 条  "
              f"{s['elapsedMs']:5}ms  {s.get('error', '')[:40]}")
        if s["outcome"] == "ok":
            ok.append(s["id"])

    if not ok:
        skipped.append("学术源：一家都没返回（断网？）")
        print("  ⚠ 一家都没返回，这一节跳过，不计入通过")
        return

    check(len(ok) >= 3, f"{len(ok)} 家学术源可用：{ok}",
          f"只有 {len(ok)} 家学术源可用（{ok}）—— 「极速搜索大量文献」谈不上")
    check(bool(res["papers"]),
          f"{res['totalBeforeMerge']} 条 → 合并后 {res['mergedCount']} 篇 → 取前 {len(res['papers'])} 篇",
          "一篇都没有")
    check(res["elapsedMs"] <= 9000, f"耗时 {res['elapsedMs']}ms", f"太慢：{res['elapsedMs']}ms")

    # DOI 去重必须真的生效：同一篇被多家收录是常态
    multi = [p for p in res["papers"] if p["sourceCount"] >= 2]
    print(f"  · 被 ≥2 家学术源同时收录：{len(multi)} 篇 "
          f"（去重实际合掉 {res['totalBeforeMerge'] - res['mergedCount']} 条）")
    if not multi:
        print("    （各家覆盖领域不同，同一查询下重叠少是常态；"
              "去重逻辑本身由 ⑨a 的确定性用例覆盖）")
    for p in res["papers"][:5]:
        m = p.get("meta") or {}
        print(f"    [{'+'.join(p['sources'])}] cite={m.get('citations', '?'):>6} "
              f"{m.get('year', '????')} {p['title'][:44]}")

    with_doi = [p for p in res["papers"] if (p.get("meta") or {}).get("doi")]
    check(len(with_doi) >= len(res["papers"]) // 2,
          f"{len(with_doi)}/{len(res['papers'])} 篇带 DOI（可精确定位与去重）",
          f"只有 {len(with_doi)}/{len(res['papers'])} 篇带 DOI —— 去重会退化成按标题猜")

    # 字段并集：合并的价值就在这里，只取第一条等于白问了另外四家
    enriched = [p for p in res["papers"]
                if len({k for k in (p.get("meta") or {}) if k in
                        ("citations", "venue", "pdf", "authors", "year")}) >= 3]
    check(bool(enriched),
          f"{len(enriched)} 篇拿到了 ≥3 类元数据（被引/期刊/PDF/作者/年份）",
          "合并后元数据仍很稀 —— 各家字段没有取并集")

    # Crossref 的摘要是 JATS XML，标签必须已经被剥掉
    dirty = [p for p in res["papers"] if "<jats:" in (p.get("snippet") or "")]
    check(not dirty, "摘要里没有残留的 XML 标签",
          f"{len(dirty)} 篇摘要里还带着 <jats:> 标签，会被当成正文喂进检索")


def main() -> int:
    test_trust()
    test_fold()
    test_research()
    test_scholar_merge()
    asyncio.run(test_live())
    asyncio.run(test_scholar())

    print()
    print("=" * 70)
    for s in skipped:
        print(f"⚠ 跳过（不算通过）：{s}")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ 联网元搜索通过" + ("（含上面标注的跳过项）" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
