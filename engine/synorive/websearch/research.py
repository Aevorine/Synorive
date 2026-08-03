"""
整合与提炼 —— R5 / R7 / R8 / R9 / W10
====================================================================
从「一堆搜索结果」到「一份能用的简报」。

**左栏（这个文件做的）：纯摘录。** 每一句都是某篇原文里逐字存在的句子，
后面挂着出处，点开能核对。和 D8 秒答卡同一条约束 —— 断网也能用，
而且用户永远能验证我有没有编。

**右栏（`briefing.py` 做的）：云端生成。** 读起来顺，但那是模型改写过的话。
两栏并排是你选的，理由也在这儿：**你随时能对照左边看右边有没有跑偏。**

R5 矛盾并排是这里最要紧的一件事：多篇资料对同一问题给出冲突结论时，
**不替用户挑一个**。挑错了的代价远大于让用户自己看两行字。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

#: 句子切分。和 chunker / answer.py 用同一套规则
_SENT_SPLIT = re.compile(r"(?<=[。！？；…\n])|(?<=[.!?;])\s+")

MIN_SENT = 10
MAX_SENT = 200

#: 表示否定/对立的信号词。R5 判"两句话在打架"要用
_NEG = re.compile(r"(不|无|没有|并非|未必|不能|不会|不是|难以|无法|错误|误区|谣言|辟谣)")

#: 中文停用词。抽关键句和聚类时不能让这些词参与
_STOP = frozenset(
    "的 了 和 是 在 我 有 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己 这 "
    "那 与 及 或 但 而 如果 因为 所以 一些 这些 那些 可以 进行 通过 对于 关于 我们 他们 它 "
    "the a an and or of to in for on is are be this that with as by from at it".split()
)


@dataclass
class Evidence:
    """一条证据。**必须带出处**，否则它就只是一句无从核对的话（R7）。"""

    text: str
    url: str
    title: str
    site: str
    trust_score: float = 0.5
    tier_label: str = ""
    published: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "text": self.text,
            "url": self.url,
            "title": self.title,
            "site": self.site,
            "trustScore": round(self.trust_score, 3),
            "tier": self.tier_label,
        }
        if self.published:
            d["published"] = self.published
        return d


@dataclass
class Topic:
    """一个子话题：一组讲同一件事的证据。"""

    keyword: str
    evidence: list[Evidence] = field(default_factory=list)
    #: R5：这一组里互相冲突的句子对
    conflicts: list[tuple[Evidence, Evidence]] = field(default_factory=list)

    @property
    def sites(self) -> set[str]:
        return {e.site for e in self.evidence if e.site}

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.keyword,
            "independentSites": len(self.sites),
            "evidence": [e.to_dict() for e in self.evidence[:6]],
            "conflicts": [
                {"a": a.to_dict(), "b": b.to_dict()} for a, b in self.conflicts[:3]
            ],
        }


def _sentences(text: str) -> list[str]:
    out = []
    for raw in _SENT_SPLIT.split(text or ""):
        s = (raw or "").strip()
        if s.startswith(("#", "=", "-", "|")):
            continue
        if MIN_SENT <= len(s) <= MAX_SENT:
            out.append(s)
    return out


def _terms(s: str) -> set[str]:
    """
    切词。**必须走 jieba，不能用正则贪婪切汉字** ——
    我第一版写的是 `[一-鿿]{2,8}`，结果「预取缓存未命中率」被切成一整块，
    而句子里的「预取策略能显著降」又被切成另一整块，两边一个字都对不上，
    整个聚类一条都聚不出来。**正则切汉字不是分词，只是按长度截断。**
    """
    from ..store.text import segment

    words: list[str] = []
    for raw in re.findall(r"[\w一-鿿]+", s.lower()):
        words.extend(segment(raw))
    return {w for w in words if len(w) >= 2 and w not in _STOP}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def build_topics(
    query: str,
    docs: list[dict[str, Any]],
    *,
    max_topics: int = 6,
) -> list[Topic]:
    """
    把抓回来的正文切成句子、按共同关键词聚成几个子话题。

    `docs` 每项要有：`text`（正文）、`url`、`title`、`site`，
    可选 `trust`（`trust.evaluate` 的输出）、`published`。

    **为什么按关键词聚而不是按向量聚**：向量聚类要跑模型、要 1~2 秒，
    而这一步的目的只是"把简报分成几段别糊成一坨"，
    关键词共现已经够了，且用户能看懂分组理由（组名就是那个词）。
    """
    q_terms = _terms(query)
    pool: list[tuple[Evidence, set[str]]] = []

    for d in docs:
        trust = d.get("trust") or {}
        for s in _sentences(str(d.get("text") or "")):
            t = _terms(s)
            # 和查询完全不沾边的句子不要 —— 正文里大半是无关内容
            if q_terms and not (t & q_terms):
                continue
            ev = Evidence(
                text=s,
                url=str(d.get("url") or ""),
                title=str(d.get("title") or ""),
                site=str(d.get("site") or ""),
                trust_score=float(trust.get("score") or 0.5),
                tier_label=str(trust.get("tierLabel") or ""),
                published=d.get("published"),
            )
            pool.append((ev, t))

    if not pool:
        return []

    # 按出现频次挑话题词：既要够常见（多篇都提），又不能是查询词本身
    freq: dict[str, set[str]] = defaultdict(set)
    for ev, t in pool:
        for w in t:
            if w in q_terms or len(w) < 2:
                continue
            freq[w].add(ev.site or ev.url)

    ranked = sorted(freq.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    topics: list[Topic] = []
    used: set[int] = set()

    for word, sites in ranked:
        if len(topics) >= max_topics:
            break
        if len(sites) < 2:
            # 只有一个站提到的词不配当话题 —— 那不是"话题"，是某篇文章的口癖
            continue
        tp = Topic(keyword=word)
        for i, (ev, t) in enumerate(pool):
            if i in used or word not in t:
                continue
            # 同一个站在同一话题里最多两句，否则一篇长文会霸占整个话题
            if sum(1 for e in tp.evidence if e.site == ev.site) >= 2:
                continue
            tp.evidence.append(ev)
            used.add(i)
        if len(tp.sites) >= 2:
            tp.evidence.sort(key=lambda e: -e.trust_score)
            tp.conflicts = _find_conflicts(tp.evidence)
            topics.append(tp)

    return topics


def _find_conflicts(evidence: list[Evidence]) -> list[tuple[Evidence, Evidence]]:
    """
    R5 矛盾检测。

    判据刻意保守：两句话**讲的是同一件事**（关键词重叠高）
    但**一句肯定一句否定**（否定词数量差异明显），才算冲突。

    这抓不到所有矛盾 —— 「三个月」和「半年」这种数值冲突就抓不到（下一版补）。
    但它抓到的基本都是真冲突，而**误报一个假冲突比漏报更糟**：
    用户会为一个不存在的分歧浪费时间去核对。
    """
    out: list[tuple[Evidence, Evidence]] = []
    for i in range(len(evidence)):
        for j in range(i + 1, len(evidence)):
            a, b = evidence[i], evidence[j]
            if a.site == b.site:
                continue
            ta, tb = _terms(a.text), _terms(b.text)
            if _overlap(ta, tb) < 0.55:
                continue
            na = len(_NEG.findall(a.text))
            nb = len(_NEG.findall(b.text))
            if abs(na - nb) >= 2 or (na == 0) != (nb == 0):
                out.append((a, b))
            if len(out) >= 5:
                return out
    return out


# ────────────────────────────────────────────────────────────────
# R8 摘录版简报（左栏）
# ────────────────────────────────────────────────────────────────
def build_briefing(
    query: str,
    docs: list[dict[str, Any]],
    *,
    max_topics: int = 6,
) -> dict[str, Any]:
    """
    出一份**纯摘录**的结构化简报。

    五个区块，每个区块都只放原文句子 + 出处：
      共识 —— 至少 2 个独立站点都在说的
      分歧 —— R5 抓到的冲突，并排放，不下结论
      时间线 —— 带日期的资料按时间排
      关键数据 —— 句子里带数字的（R9 的原料）
      还没查清 —— 只有孤证、或整轮就没搜到的方向
    """
    topics = build_topics(query, docs, max_topics=max_topics)

    consensus: list[dict[str, Any]] = []
    disputes: list[dict[str, Any]] = []
    for tp in topics:
        if tp.conflicts:
            disputes.append(tp.to_dict())
        elif len(tp.sites) >= 2 and tp.evidence:
            consensus.append(
                {
                    "topic": tp.keyword,
                    "independentSites": len(tp.sites),
                    "evidence": [e.to_dict() for e in tp.evidence[:3]],
                }
            )

    timeline = [
        {
            "published": d.get("published"),
            "title": d.get("title"),
            "url": d.get("url"),
            "site": d.get("site"),
        }
        for d in docs
        if d.get("published")
    ]
    timeline.sort(key=lambda x: str(x.get("published") or ""), reverse=True)

    numbers = _extract_numbers(docs, query)

    open_questions: list[str] = []
    lone = [tp for tp in topics if len(tp.sites) < 2]
    if lone:
        open_questions.append(
            "这几个说法只有单一来源，没有第二个站点印证：" + "、".join(t.keyword for t in lone[:5])
        )
    if not consensus and not disputes:
        open_questions.append(
            "抓回来的正文里没有找到互相印证的说法 —— 可能是搜索词太窄，"
            "或者这批结果正文没抓到（看每条的抓取状态）"
        )
    if not timeline:
        open_questions.append("这批资料都没有可靠的发布时间，无法判断新旧")

    return {
        "query": query,
        "kind": "extract",  # 界面据此说明"这是原文摘录，不是 AI 写的"
        "consensus": consensus,
        "disputes": disputes,
        "timeline": timeline[:20],
        "numbers": numbers[:20],
        # V2：一致性矩阵。放进简报本身而不是单开一个接口 ——
        # 它的原料（topics）在这里已经算好了，单开接口要么重算一遍，
        # 要么在两个地方各存一份，两种都会让"矩阵和分歧区对不上"
        "matrix": build_matrix(topics),
        "openQuestions": open_questions,
        "docCount": len(docs),
        "siteCount": len({d.get("site") for d in docs if d.get("site")}),
    }


# ────────────────────────────────────────────────────────────────
# V2 一致性矩阵
# ────────────────────────────────────────────────────────────────
def build_matrix(topics: list[Topic], *, max_sites: int = 10) -> dict[str, Any]:
    """
    横轴来源、纵轴话题、格子是态度 —— 一眼看出谁跟谁对着干。

    **为什么值得单独做一个矩阵，而不是让用户读那几段文字**：
    R5 的矛盾并排是一对一对给的，三个话题各有分歧就是六段文字，
    读完还是不知道"到底哪个站老是跟别人不一样"。矩阵能显示出
    **模式**：某一列（某个站）如果整列都是红的，那这个站本身就可疑，
    这个信息在逐对呈现的文字里是看不出来的。

    态度怎么判：这一格的证据句里有没有否定词。
    **和 `_find_conflicts` 用同一套判据** —— 两处用不同标准，
    会出现"矩阵说他们冲突、分歧区却没列出来"这种自相矛盾的界面。

    没有证据的格子是 `silent`（这个站没提这个话题），
    **不是 `neutral`** —— "没说"和"说了但不表态"完全是两回事，
    合成一个值会让"三个站都没提"看起来像"三个站都保持中立"。
    """
    site_hits: dict[str, int] = defaultdict(int)
    for tp in topics:
        for e in tp.evidence:
            if e.site:
                site_hits[e.site] += 1
    sites = [s for s, _ in sorted(site_hits.items(), key=lambda kv: -kv[1])][:max_sites]
    if not sites or not topics:
        return {"sites": [], "topics": [], "cells": [], "note": "证据太少，画不出矩阵"}

    cells: list[list[dict[str, Any]]] = []
    disagreements = 0
    for tp in topics:
        row: list[dict[str, Any]] = []
        stances: list[str] = []
        for site in sites:
            evs = [e for e in tp.evidence if e.site == site]
            if not evs:
                row.append({"stance": "silent"})
                continue
            neg = sum(len(_NEG.findall(e.text)) for e in evs)
            stance = "negative" if neg >= 2 else ("mixed" if neg == 1 else "positive")
            stances.append(stance)
            row.append({
                "stance": stance,
                "text": evs[0].text[:160],
                "url": evs[0].url,
                "trustScore": round(evs[0].trust_score, 3),
            })
        if len({s for s in stances if s != "mixed"}) > 1:
            disagreements += 1
        cells.append(row)

    return {
        "sites": sites,
        "topics": [tp.keyword for tp in topics],
        "cells": cells,
        "disagreements": disagreements,
        "note": (
            f"{len(topics)} 个话题 × {len(sites)} 个来源。"
            + (f"其中 {disagreements} 个话题上各家说法不一致，值得自己看一眼原文"
               if disagreements else "各家说法没有明显冲突")
            + "。空白格 = 这个来源没提这个话题（不是中立）"
        ),
    }


_NUM_SENT = re.compile(
    r"[^。！？；\n]*?(\d+(?:\.\d+)?\s*(?:%|％|万|亿|千|百分点|倍|年|月|日|小时|分钟|秒|"
    r"元|美元|人|次|条|个|GB|MB|TB|km|kg))[^。！？；\n]*"
)


def _extract_numbers(docs: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """R9 的原料：把含具体数字的句子挑出来，带出处。可导出成表格。"""
    q = _terms(query)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for d in docs:
        for s in _sentences(str(d.get("text") or "")):
            if q and not (_terms(s) & q):
                continue
            m = _NUM_SENT.search(s)
            if not m:
                continue
            key = re.sub(r"\s+", "", s)[:40]
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "value": m.group(1).strip(),
                    "sentence": s,
                    "url": d.get("url"),
                    "title": d.get("title"),
                    "site": d.get("site"),
                }
            )
    return out
