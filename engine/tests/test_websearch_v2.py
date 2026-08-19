"""
S 组 / V 组 / 提炼层的确定性测试
====================================================================
**全部离线**：不发一个真实请求。原因不是图省事 ——
真实网络的测试今天过明天挂（引擎会改版、会限流），
那样的测试跑红了没人知道是代码坏了还是对方改版了，
最后的结局一定是被人加个 skip。

要验的是**我们自己的判断逻辑**：排班算法选谁、扩写把词改成什么、
反向检索的过滤严不严、溯源能不能识别复制链、矩阵画得对不对。
真实网络那一层由 `websearch/engines.py` 各家的实调验证覆盖（台账阶段 8）。

每个用例都刻意带一个**反向断言**（"不该发生的事没有发生"）——
只测正向的话，一个把所有东西都判成"可疑"的函数也能满分通过。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from synorive.websearch.deepdive import _dedupe, _followup_queries  # noqa: E402
from synorive.websearch.engines import ParseOutcome  # noqa: E402
from synorive.websearch.expand import (  # noqa: E402
    QueryVariant,
    glossary_translate,
    route_variants,
    trim_query,
)
from synorive.websearch.presets import apply_preset, describe_presets  # noqa: E402
from synorive.websearch.research import Evidence, Topic, build_matrix  # noqa: E402
from synorive.websearch.scheduler import EngineScheduler  # noqa: E402
from synorive.websearch.trust import (  # noqa: E402
    TrustProfile,
    ai_flags,
    evaluate,
    rank_with_trust,
)
from synorive.websearch.verify import (  # noqa: E402
    ClaimVerdict,
    Stance,
    _finalize,
    counter_queries,
    counter_search,
    extract_claims,
    trace_origin,
    verify_claims,
)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [!!] {name}" + (f" — {detail}" if detail else ""))


class _FakeEngine:
    def __init__(self, eid: str) -> None:
        self.id = eid
        self.label = eid
        self.kind = "html"
        self.group = "web"
        self.needs_key = False
        self.needs_browser = False
        self.note = ""


# ────────────────────────────────────────────────────────────────
def test_scheduler() -> None:
    print("\n[S1] 引擎健康记分与排班")
    sch = EngineScheduler(None)

    # 一家一直好、一家一直坏、一家老被限流
    for _ in range(10):
        sch.observe("good", ParseOutcome.OK, 500)
        sch.observe("dead", ParseOutcome.BROKEN, 8000)
        sch.observe("limited", ParseOutcome.CHALLENGED, 900)

    check("好引擎分高", sch.score_of("good") > 0.9, f"{sch.score_of('good'):.2f}")
    check("坏引擎分为 0", sch.score_of("dead") == 0.0)
    # 🔴 这是四态区分的全部意义：被限流 ≠ 坏了。
    # 如果这两个分数一样，说明我又把它们压成了一件事
    check(
        "被限流的分高于解析坏的",
        sch.score_of("limited") > sch.score_of("dead"),
        f"limited={sch.score_of('limited'):.2f} vs dead={sch.score_of('dead'):.2f}",
    )
    check("没试过的引擎给中位分（不是 0）", sch.score_of("never") == 0.6)

    # 延迟惩罚：同样全成功，慢的分低
    fast, slow = EngineScheduler(None), EngineScheduler(None)
    for _ in range(10):
        fast.observe("x", ParseOutcome.OK, 400)
        slow.observe("x", ParseOutcome.OK, 7000)
    check(
        "同样成功率下慢的分低",
        slow.score_of("x") < fast.score_of("x"),
        f"{slow.score_of('x'):.2f} < {fast.score_of('x'):.2f}",
    )

    # 排班：3 家里选 2
    cands = [_FakeEngine(i) for i in ("good", "dead", "limited")]
    picked, benched = sch.lineup(cands, size=2, explore=False)
    ids = [e.id for e in picked]
    check("排班选中好引擎", "good" in ids, str(ids))
    check("排班没选已死的引擎", "dead" not in ids, str(ids))
    check("被刷掉的引擎有明确原因", all(w for _, w in benched), str(benched))

    # 探索位：必须让一个位置给最久没试过的
    sch2 = EngineScheduler(None)
    for _ in range(6):
        sch2.observe("a", ParseOutcome.OK, 300)
        sch2.observe("b", ParseOutcome.OK, 300)
    sch2.observe("c", ParseOutcome.BROKEN, 8000)  # c 最近试过但很差
    cands2 = [_FakeEngine(i) for i in ("a", "b", "c", "d")]
    picked2, _ = sch2.lineup(cands2, size=2, explore=True)
    check(
        "探索位让从没试过的引擎有机会",
        "d" in [e.id for e in picked2],
        str([e.id for e in picked2]),
    )
    check("探索位不撑大阵容", len(picked2) == 2, str(len(picked2)))


def test_scheduler_persist(tmp_path: Path) -> None:
    print("\n[S1] 健康状态落盘与读回")
    p = tmp_path / "health.json"
    s1 = EngineScheduler(p)
    for _ in range(5):
        s1.observe("bing", ParseOutcome.OK, 600)
    s1.save()
    check("落盘文件生成且非空", p.exists() and p.stat().st_size > 20,
          f"{p.stat().st_size if p.exists() else 0} 字节")

    s2 = EngineScheduler(p)
    check("重启后分数保住了", abs(s2.score_of("bing") - s1.score_of("bing")) < 1e-9,
          f"{s2.score_of('bing'):.3f}")

    # 文件坏掉不该让引擎起不来
    p.write_text("{ 这不是合法 json", "utf-8")
    s3 = EngineScheduler(p)
    check("状态文件损坏时安静从零开始，不抛异常", s3.score_of("bing") == 0.6)


def test_expand() -> None:
    print("\n[S4] 查询扩写")
    check("去疑问壳", trim_query("怎么解决向量检索太慢呢") == "解决向量检索太慢",
          repr(trim_query("怎么解决向量检索太慢呢")))
    # 🔴 反向断言：削过头会把查询削没，那比不削糟得多
    check("短查询不被削空", trim_query("RAG") is None)
    check("没有壳的查询不产生变体", trim_query("向量检索延迟") is None)

    g = glossary_translate("向量数据库的召回率怎么提升")
    check("术语表命中并替换", g is not None and "vector database" in g[0] and "recall" in g[0],
          repr(g[0]) if g else "None")
    # 长词优先：不能被「向量」先吃掉半截
    check("长词优先替换（向量数据库不被切成 vector+数据库）",
          g is not None and "vector database" in g[0] and "vector数据库" not in g[0])
    check("认不出的词不硬翻", glossary_translate("今天天气不错") is None)

    # 分派：英文变体不发给百度，中文变体不发给 Mojeek
    variants = [
        QueryVariant("原查询", lang="zh", kind="original"),
        QueryVariant("vector database", lang="en", kind="translated", weight=0.75),
    ]
    routed = dict(
        (v.kind, set(eids))
        for v, eids in route_variants(variants, ["baidu", "so360", "mojeek", "bing"])
    )
    check("英文变体不派给百度", "baidu" not in routed.get("translated", set()),
          str(routed.get("translated")))
    check("英文变体派给 Mojeek", "mojeek" in routed.get("translated", set()))
    check("原查询发给所有引擎", len(routed.get("original", set())) == 4)


def test_presets() -> None:
    print("\n[S8] 定向源预设")
    q, p = apply_preset("向量检索", "academic")
    check("预设加上了 site: 限定", "site:arxiv.org" in q, q[:60])
    check("原查询词还在", q.startswith("向量检索"))
    check("预设带上了代价说明", bool(p and p.caveat), (p.caveat[:30] if p else ""))
    # 🔴 反向断言：认不出的预设不该炸，也不该悄悄改查询
    q2, p2 = apply_preset("向量检索", "不存在的预设")
    check("未知预设原样返回不报错", q2 == "向量检索" and p2 is None)
    check("每个预设的站点数不超过 8（超了引擎会忽略）",
          all(len(x["sites"]) <= 8 for x in describe_presets()))


def test_trust_profile() -> None:
    print("\n[V5] 可信度权重可调")
    c = {"title": "某个说法", "snippet": "一段正文" * 10,
         "url": "https://zhihu.com/p/1", "site": "zhihu.com", "siteCount": 1, "score": 1.0}
    base = evaluate(c).score
    # 用户说"我更信社区"→ 把 community 权重拉满
    p = TrustProfile.from_dict({"tierWeights": {"community": 0.95}})
    tuned = evaluate(c, profile=p).score
    check("拧高社区权重后分数变高", tuned > base, f"{base:.2f} → {tuned:.2f}")

    p2 = TrustProfile.from_dict({"blocklist": ["zhihu.com"]})
    check("用户拉黑的域名进「已排除」", evaluate(c, profile=p2).hide)
    # 🔴 反向断言：没拉黑的不该被隐藏
    check("没拉黑的普通结果不被隐藏", not evaluate(c).hide)

    check("认不出的配置字段被忽略而不是抛异常",
          TrustProfile.from_dict({"什么鬼": 1, "rankWeight": "不是数字"}).rank_weight == 0.35)

    shown, dropped = rank_with_trust([c], profile=TrustProfile.from_dict({"rankWeight": 0.9}))
    check("rankWeight 参与最终分计算", shown and "finalScore" in shown[0])


def test_ai_flags() -> None:
    print("\n[V3] AI 生成判据加强")
    zh = ai_flags("行业观察", "在当今社会，随着技术的不断发展，值得注意的是，综上所述，需要注意的是。")
    check("中文套话被识别", bool(zh), str(zh))
    en = ai_flags(
        "Some Post",
        "In today's fast-paced world, it's important to note that this plays a crucial "
        "role. In conclusion, we delve into the ever-evolving landscape.",
    )
    check("英文套话被识别（原来完全没有判据）", bool(en), str(en))

    multi = ai_flags(
        "转载文", "一段被到处转载的内容" * 5,
        cluster={"alsoAt": ["https://a.com/1", "https://b.com/1",
                            "https://c.com/1", "https://d.com/1"]},
    )
    check("多站同文被识别", any("域名" in f for f in multi), str(multi))

    # 🔴 反向断言：正常的技术文章不该被标成 AI 生成
    normal = ai_flags(
        "SQLite WAL 模式实测",
        "在 100 万行的库上实测，WAL 模式下写入吞吐从 1200 提升到 4700 TPS，"
        "checkpoint 间隔设为 1000 页。",
    )
    check("含具体数字的正常文章不被误标", not normal, str(normal))


def test_counter_and_origin() -> None:
    print("\n[V6/V4] 反向检索词与溯源")
    cq = counter_queries("某个说法")
    check("中文查询用中文反向词", any("辟谣" in q for q in cq), str(cq))
    check("中文查询也补一条英文反向词", any("debunked" in q for q in cq), str(cq))
    check("空查询不产生反向词", counter_queries("") == [])

    # 复制链：8 个站两天内发同一件事
    burst = [
        {"published": "2026-07-30", "title": f"文章{i}", "url": f"https://s{i}.com/a",
         "site": f"s{i}.com", "trust": {"tierLabel": "未收录"}}
        for i in range(8)
    ]
    tr = trace_origin(burst)
    check("识别出转载爆发", tr.verdict == "burst", f"{tr.verdict} / {tr.note[:40]}")

    # 全都没日期 → 必须如实说排不出来，绝不编一个
    tr2 = trace_origin([{"title": "无日期", "url": "https://x.com/1", "site": "x.com"}])
    check("没有日期时如实说排不出来", tr2.verdict == "unknown" and tr2.earliest is None,
          tr2.note[:40])

    # 正常情况：时间跨度大
    spread = [
        {"published": "2019-03-01", "title": "早", "url": "https://sqlite.org/a",
         "site": "sqlite.org", "trust": {"tierLabel": "官方"}},
        {"published": "2025-06-01", "title": "晚", "url": "https://blog.com/a",
         "site": "blog.com", "trust": {"tierLabel": "社区/个人"}},
    ]
    tr3 = trace_origin(spread)
    check("时间跨度大的不误报成复制链", tr3.verdict == "ok", tr3.verdict)
    check("最早的那条被正确选出", (tr3.earliest or {}).get("site") == "sqlite.org")


def test_verdict_asymmetry() -> None:
    print("\n[V1] 核查结论的不对称门槛")
    v = ClaimVerdict(claim="X 是 Y")
    v.refute.append(Stance(url="u", title="t", site="a.com", snippet="s", stance="refute"))
    check("有 1 条反驳就标 disputed", _finalize(v).verdict == "disputed")

    v2 = ClaimVerdict(claim="X 是 Y")
    v2.support.append(Stance(url="u1", title="t", site="a.com", snippet="s"))
    check("只有 1 个支持来源标 weak 而不是 supported", _finalize(v2).verdict == "weak")

    v3 = ClaimVerdict(claim="X 是 Y")
    for s in ("a.com", "b.com"):
        v3.support.append(Stance(url=f"https://{s}/1", title="t", site=s, snippet="s"))
    check("2 个独立站点支持且无反驳才算 supported", _finalize(v3).verdict == "supported")

    v4 = ClaimVerdict(claim="X 是 Y")
    check("什么都没搜到标 unverified", _finalize(v4).verdict == "unverified")
    # 🔴 结论措辞不能变成"这是假的"——我们没有那个能力
    check("disputed 的说明里不下真假结论",
          "不代表原说法一定错" in _finalize(v).note, _finalize(v).note[:40])


def test_extract_claims() -> None:
    print("\n[V1] 断言抽取")
    briefing = {
        "disputes": [{"topic": "延迟", "conflicts": [{
            "a": {"text": "实测 100 万块时向量检索要 3070 毫秒", "url": "https://a.com/1"},
            "b": {"text": "官方称百万级仍能保持在 200 毫秒以内", "url": "https://b.com/1"},
        }]}],
        "numbers": [{"sentence": "召回率从 94% 提升到 99%", "url": "https://c.com/1"}],
        "consensus": [{"topic": "索引", "evidence": [
            {"text": "HNSW 索引把扫描量从线性降到对数级", "url": "https://d.com/1"},
        ]}],
    }
    claims = extract_claims(briefing)
    texts = [c for c, _ in claims]
    check("分歧双方都被抽出来核查", len(texts) >= 2, str(len(texts)))
    check("数字类断言被抽出", any("94%" in t for t in texts), str(texts[:2]))
    check("出处一起带出来", all(u.startswith("http") for _, u in claims))

    # 🔴 反向断言：泛泛之谈不该被拿去核查（白花一轮检索）
    vague = extract_claims({"consensus": [{"topic": "t", "evidence": [{"text": "这很重要", "url": "u"}]}]})
    check("太短或无具体信息的句子不被抽取", vague == [], str(vague))


def test_matrix() -> None:
    print("\n[V2] 一致性矩阵")

    def ev(text: str, site: str) -> Evidence:
        return Evidence(text=text, url=f"https://{site}/1", title="t", site=site)

    tp = Topic(keyword="性能")
    tp.evidence = [
        ev("这个方案性能很好，实测提升明显", "a.com"),
        ev("这个方案性能并不好，没有提升，也不能解决问题", "b.com"),
    ]
    m = build_matrix([tp])
    check("矩阵列出了来源", set(m["sites"]) == {"a.com", "b.com"}, str(m["sites"]))
    stances = [cell["stance"] for cell in m["cells"][0]]
    check("否定句被标成 negative", "negative" in stances, str(stances))
    check("肯定句被标成 positive", "positive" in stances, str(stances))
    check("检测到这个话题上有分歧", m["disagreements"] == 1, str(m["disagreements"]))

    # 🔴 "没提"必须是 silent，不能是 neutral
    tp2 = Topic(keyword="价格")
    tp2.evidence = [ev("价格是每月九十九元起", "a.com")]
    m2 = build_matrix([tp, tp2])
    row = m2["cells"][m2["topics"].index("价格")]
    silent = [c for c in row if c["stance"] == "silent"]
    check("没提这个话题的格子标 silent 而不是 neutral", len(silent) >= 1, str(row))
    check("说明里点出空白格的含义", "不是中立" in m2["note"])
    check("证据太少时不硬画矩阵", build_matrix([])["sites"] == [])


def test_dedupe_and_followup() -> None:
    print("\n[S5] 跨轮去重与追问生成")
    merged = _dedupe([
        {"url": "https://www.sqlite.org/wal.html", "score": 0.5,
         "engines": ["bing"], "viaQuery": "原查询"},
        {"url": "https://sqlite.org/wal.html", "score": 0.4,
         "engines": ["mojeek"], "viaQuery": "英文变体"},
    ])
    check("www 与非 www 被认成同一条", len(merged) == 1, f"{len(merged)} 条")
    check("两边的引擎并起来了", set(merged[0]["engines"]) == {"bing", "mojeek"},
          str(merged[0]["engines"]))
    check("被多个查询命中的结果分数更高", merged[0]["score"] > 0.5,
          f"{merged[0]['score']:.3f}")
    check("记下了它是被哪几个查询搜到的", len(merged[0].get("viaQueries") or []) == 2)

    follow = _followup_queries("向量检索慢", {
        "disputes": [{"topic": "量化"}],
        "openQuestions": ["这几个说法只有单一来源，没有第二个站点印证：分片、降维"],
        "consensus": [{"topic": "HNSW"}],
    })
    qs = [q for q, _ in follow]
    check("从分歧生成追问", any("量化" in q for q in qs), str(qs))
    check("每条追问都写了为什么", all(w for _, w in follow), str(follow[:1]))
    check("追问数量有上限", len(follow) <= 3, str(len(follow)))
    # 🔴 反向断言：没有缺口时不该硬凑追问
    check("简报没暴露缺口时不生成追问", _followup_queries("x", {}) == [])


# ── 用假引擎测反向检索的过滤严不严 ──────────────────────────
class _FakeMeta:
    """只实现 `.search()`，形状和 MetaSearch 一样。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.enabled: list[str] = ["fake"]
        self.calls: list[str] = []

    async def search(self, query, *, engines=None, limit=20, lang="zh", **kw):
        self.calls.append(query)

        class _C:
            def __init__(self, d): self._d = d
            def to_dict(self): return dict(self._d)

        class _R:
            pass

        r = _R()
        r.clusters = [_C(d) for d in self.rows]
        r.replies = []
        r.elapsed_ms = 1
        return r


def test_counter_filter() -> None:
    print("\n[V6] 反向检索的过滤（最容易做错的一处）")
    rows = [
        {"title": "关于某说法的辟谣", "url": "https://f.com/1", "site": "f.com",
         "snippet": "经核实，该说法不实"},
        {"title": "某说法的详细介绍", "url": "https://g.com/1", "site": "g.com",
         "snippet": "这是一篇普通的介绍文章"},
    ]
    meta = _FakeMeta(rows)
    got = asyncio.run(counter_search(meta, "某说法"))
    urls = [s.url for s in got]
    check("真正在反驳的被收下", "https://f.com/1" in urls, str(urls))
    # 🔴 这条是重点：搜「X 辟谣」返回的结果里一大半只是标题带这两个字的无关文章。
    # 全收进来等于给每个查询伪造出一批"反驳证据"，比不做还糟
    check("标题只是恰好出现在反向查询里的无关文章被滤掉",
          "https://g.com/1" not in urls, str(urls))
    check("确实发了多条反向查询", len(meta.calls) >= 3, str(meta.calls))


def test_verify_claims_offline() -> None:
    print("\n[V1] 断言核查（假引擎）")
    rows = [
        {"title": "研究证实该结论", "url": "https://a.org/1", "site": "a.org",
         "snippet": "confirmed by multiple studies"},
        {"title": "该结论已被推翻", "url": "https://b.org/1", "site": "b.org",
         "snippet": "后续研究表明这是不实的"},
    ]
    got = asyncio.run(verify_claims(_FakeMeta(rows), [("召回率提升了 5 个百分点", "")]))
    check("跑出了核查结论", len(got) == 1)
    v = got[0]
    check("支持与反驳分开计数", v.support and v.refute, f"支持{len(v.support)}/反驳{len(v.refute)}")
    check("有反驳时结论是 disputed", v.verdict == "disputed", v.verdict)

    # 不能拿原文自己印证自己
    got2 = asyncio.run(verify_claims(_FakeMeta(rows), [("某说法", "https://a.org/1")]))
    urls = [s.url for s in got2[0].support + got2[0].refute]
    check("排除了断言自己的出处", "https://a.org/1" not in urls, str(urls))


def main() -> int:
    import tempfile

    print("=" * 68)
    print("S 组 / V 组 / 提炼层 —— 确定性离线测试")
    print("=" * 68)
    test_scheduler()
    with tempfile.TemporaryDirectory() as td:
        test_scheduler_persist(Path(td))
    test_expand()
    test_presets()
    test_trust_profile()
    test_ai_flags()
    test_counter_and_origin()
    test_verdict_asymmetry()
    test_extract_claims()
    test_matrix()
    test_dedupe_and_followup()
    test_counter_filter()
    test_verify_claims_offline()

    print("\n" + "=" * 68)
    print(f"通过 {PASS} / 失败 {FAIL}")
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
