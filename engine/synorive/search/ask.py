"""
A3 Ask 模式 —— 问一句话，拿一段带出处的答案
====================================================================
和隔壁 `answer.py`（D8 秒答卡）的分工：

    answer.py  只看**第一条**结果，摘 ≤2 句，作为搜索结果页顶部的一张小卡。
    ask.py     横跨**多条**结果，摘若干段并按来源分组，作为一次问答的主体。

拆成两个而不是给 answer.py 加参数，是因为两者的取舍方向相反：
秒答卡宁可不给（错一次用户就再也不信它），Ask 则必须尽量给出东西 ——
用户是**明确带着一个问题来的**，回一句"没有足够依据"必须同时告诉他
还差什么、可以怎么改问法，否则这个模式就等于不能用。

🔴 **它同样只摘录，绝不生成、绝不改写。** 这不是能力不足的借口，是设计约束：
   用户问的是**他自己的资料**，一个被改写过的句子和原文哪怕只差一个字，
   他都无从分辨对错。所以这里返回的每一段，都能在原文里逐字找到，
   并且都带着 itemId + locator + 页码/时间点，点一下就能回到那一行。

   这条约束的代价是：答案读起来不如生成式流畅。**这是刻意付的代价。**
   一段读着舒服但没法核对的话，在"查自己的资料"这个场景里是负资产。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..store.text import highlight_terms
from .answer import (
    MAX_SENT_CHARS,
    MIN_SENT_CHARS,
    _SENT_SPLIT,
    _STOP_TERMS as _QUESTION_STOP,
)

#: 问题**框架词**：描述问题形状的名词，不是问题的主题。
#:
#: 🔴 和 `answer.py` 里那批疑问词（怎么/为什么/哪些）是**同一类东西**，
#:    只是词性不同 —— 那批已经被排除在分母之外，理由写得很清楚：
#:    「把"怎么"也算进分母，会让一句什么都没答上的话因为撞了个疑问词而及格」。
#:
#:    这里是**同一个问题的镜像**：把"因素""影响"算进分母，会让一句
#:    **真的答上了**的话因为没撞到框架词而不及格。
#:    `tests/test_ask.py` 第②项抓到的就是这个：
#:    问「影响景深的因素有哪些」→ 实词 [影响, 景深, 因素]，
#:    而两段正确答案都只命中「景深」→ 总覆盖率 0.333，判成"只答上一部分"，
#:    界面还会挂一条「没找到关于影响、因素的内容」的警示 —— 那是错的。
#:
#:    判据：**换掉这个词，问的还是同一件事吗？**
#:    「影响景深的因素」→「决定景深的条件」→ 同一件事 → 是框架词。
#:    「景深」换掉就不是同一个问题了 → 是主题词。
_FRAME_TERMS = frozenset({
    "影响", "因素", "作用", "原因", "条件", "情况", "方式", "方法",
    "办法", "做法", "步骤", "特点", "特性", "区别", "差异", "关系",
    "意义", "价值", "优点", "缺点", "问题", "内容", "东西", "地方",
})

#: 分母里要排掉的全部词 = 疑问词 ∪ 框架词
_STOP_TERMS = _QUESTION_STOP | _FRAME_TERMS

#: 一次回答最多引用几个不同来源。
#: 超过这个数就不是"答案"而是"文献综述"了 —— 用户还得自己读一遍，
#: 那他不如直接看搜索结果列表。
MAX_SOURCES = 4

#: 每个来源最多摘几段
MAX_PASSAGES_PER_SOURCE = 2

#: 整篇回答最多几段
MAX_PASSAGES = 6

#: 单段覆盖率下限。比秒答卡（0.5）松一档 ——
#: Ask 是多段互补，允许某一段只答上问题的一部分；
#: 秒答卡是"就这一句"，必须自己站得住。
#:
#: 🔴 **这个数必须踩在 1/3 下面，不能是 0.34。**
#:    第一版写的正是 0.34，被 `tests/test_ask.py` 的第②项当场抓出来：
#:    问「影响景深的因素有哪些」，实词是 [影响, 景深, 因素] 三个，
#:    而语料里那句直接答案「除了光圈，焦距和拍摄距离同样决定景深」
#:    只命中「景深」→ 覆盖率 0.3333，**比门槛差 0.0067，被整段丢掉**。
#:    结果是一段都摘不到、`enough=false`，而答案明明就在库里。
#:
#:    「三个实词里命中一个」恰恰是"这句话答上了问题的一部分"最常见的形态 ——
#:    把它卡掉，等于把"多段互补"这个模式存在的理由本身否定了。
#:    0.3 让 1/3 过、让 1/4（0.25）不过，是这条线该在的位置。
MIN_COVERAGE = 0.3

#: 低于这个总覆盖率就明说"依据不足"，不假装答上了。
#: 🔴 **这是整个模块最重要的一个数。** 把它调低会让"看起来有答案"的比例上升，
#:    而那些多出来的答案全是撞词撞出来的 —— 用户采信一次就再也不会回来。
WEAK_TOTAL_COVERAGE = 0.45

#: 两段文字相似到什么程度算重复。
#: 同一份资料被索引两遍（改过名、存过副本）在真实库里非常常见，
#: 不去重的话答案会把同一句话说三遍，看起来像坏了。
DEDUPE_RATIO = 0.82


@dataclass
class Passage:
    """一段逐字摘录 + 它的出处。"""

    text: str
    item_id: str
    title: str
    locator: str
    coverage: float
    #: 在这份资料里的位置，界面用它做"点一下跳回原文"
    page: int | None = None
    start_sec: float | None = None
    #: 这一段命中了问题里的哪些词 —— 界面把它们高亮，
    #: 用户一眼能看出"它是凭什么被选中的"
    matched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "text": self.text,
            "itemId": self.item_id,
            "title": self.title,
            "locator": self.locator,
            "coverage": round(self.coverage, 3),
            "matched": self.matched,
            # 界面必须原样显示，不许加"AI 认为""总结如下"之类的前缀 ——
            # 那会把一段摘录包装成一个判断
            "kind": "extract",
        }
        if self.page is not None:
            d["page"] = self.page
        if self.start_sec is not None:
            d["startSec"] = self.start_sec
        return d


def _sentences(text: str) -> list[str]:
    """
    切句。比 answer.py 那份宽一点：这里允许列表项（`- xxx`）进来，
    因为很多技术资料的答案就写在列表里，一律滤掉会把最有用的内容筛没。
    只滤掉纯标题行（`#` / `===`）—— 那些天然含查询词、覆盖率还高，
    是最容易被误选的一类。
    """
    out: list[str] = []
    for raw in _SENT_SPLIT.split(text or ""):
        s = (raw or "").strip()
        if not s:
            continue
        if s.startswith("#") or s.startswith("==="):
            continue
        # 去掉列表符号但保留内容
        s = re.sub(r"^[-*·•]\s*", "", s)
        if MIN_SENT_CHARS <= len(s) <= MAX_SENT_CHARS:
            out.append(s)
    return out


def _coverage(terms: list[str], sent: str) -> tuple[float, list[str]]:
    """返回 (覆盖率, 命中的词)。命中词要带出去给界面做高亮。"""
    if not terms:
        return 0.0, []
    hit = [t for t in terms if t in sent]
    return len(hit) / len(terms), hit


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def _too_similar(a: str, b: str) -> bool:
    """
    二元组 Jaccard。选它不选编辑距离，是因为中文里"同一句话换了个语气词"
    在编辑距离上差得很近，但在二元组上差异明显 —— 而后者才是我们要区分的。
    也比 difflib 快一个量级，这个函数在最坏情况下要跑 MAX_PASSAGES² 次。
    """
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return False
    inter = len(ga & gb)
    return inter / min(len(ga), len(gb)) >= DEDUPE_RATIO


def build(
    question: str,
    hits: list[dict[str, Any]],
    *,
    texts: list[str] | None = None,
    weak: bool = False,
) -> dict[str, Any]:
    """
    从检索结果里组一份带出处的答案。

    **永远返回一个字典，不返回 None** —— 和秒答卡不同，用户是带着问题来的，
    "没答上"本身也是必须传达的信息，而且要说清差在哪。界面靠 `enough`
    字段决定是显示答案还是显示"依据不足 + 怎么改问法"。

    `texts` 是与 hits 一一对应的**原始块正文**。必须用它、不能用 hit 里的
    `highlight`：后者带 `<em>` 标记、两头接了省略号，从它摘出来的句子在原文
    里根本不存在，"只摘不生成"当场就破了。这个坑 answer.py 踩过一次。
    """
    terms = [
        t for t in highlight_terms(question)
        if len(t) >= 2 and t.lower() not in _STOP_TERMS
    ]

    empty: dict[str, Any] = {
        "question": question,
        "passages": [],
        "sources": [],
        "enough": False,
        "coverage": 0.0,
        "kind": "extract",
    }

    if not hits or not terms:
        empty["why"] = "问题里没有可检索的实词" if not terms else "库里没搜到相关内容"
        empty["suggest"] = _suggest(question, terms, no_terms=not terms)
        return empty

    # ── 逐条候选摘段 ──────────────────────────────────────
    per_source: dict[str, list[Passage]] = {}
    order: list[str] = []

    for i, hit in enumerate(hits):
        if len(order) >= MAX_SOURCES and (hit.get("item") or {}).get("id") not in per_source:
            # 已经够 4 个来源了，后面的除非是已选来源的补充段落，否则不再看
            continue

        item = hit.get("item") or {}
        item_id = str(item.get("id") or "")
        if not item_id:
            continue

        text = (texts[i] if texts and i < len(texts) else "") or ""
        if not text.strip():
            # 退路：只有 highlight 时先把标记和省略号剥干净。
            # 剥不干净就宁可跳过这一条 —— 带着 <em> 的"原文"是假的
            text = re.sub(r"<[^>]+>", "", str(hit.get("highlight") or "")).strip("… ")
        if not text.strip():
            continue

        loc = hit.get("location") or {}
        picked: list[Passage] = []
        sents = _sentences(text)
        scored = []
        for s in sents:
            cov, matched = _coverage(terms, s)
            if cov >= MIN_COVERAGE:
                scored.append((s, cov, matched))
        if not scored:
            continue

        scored.sort(key=lambda x: -x[1])
        for s, cov, matched in scored[:MAX_PASSAGES_PER_SOURCE]:
            picked.append(
                Passage(
                    text=s,
                    item_id=item_id,
                    title=str(item.get("title") or ""),
                    locator=str(item.get("locator") or ""),
                    coverage=cov,
                    page=loc.get("page"),
                    start_sec=loc.get("startSec"),
                    matched=matched,
                )
            )

        if not picked:
            continue

        # 同一来源内按原文顺序排回来 —— 按分数排会把因果句倒过来，读着莫名其妙
        pos = {s: n for n, s in enumerate(sents)}
        picked.sort(key=lambda p: pos.get(p.text, 0))

        if item_id not in per_source:
            per_source[item_id] = []
            order.append(item_id)
        per_source[item_id].extend(picked)

    # ── 跨来源合并 + 去重 ────────────────────────────────
    passages: list[Passage] = []
    for item_id in order:
        for p in per_source[item_id]:
            if len(passages) >= MAX_PASSAGES:
                break
            if any(_too_similar(p.text, q.text) for q in passages):
                continue
            passages.append(p)

    if not passages:
        empty["why"] = "搜到了相关资料，但没有一段直接答上这个问题"
        empty["suggest"] = _suggest(question, terms)
        empty["sources"] = _sources(hits[:MAX_SOURCES])
        return empty

    # 总覆盖率：问题里的实词，有多少**至少被某一段**命中。
    # 用并集不用平均 —— 多段互补答上一个问题是正常且理想的情况，
    # 按平均算会把"两段各答一半"判成低分，而那恰恰是最好的答案形态。
    covered = {t for p in passages for t in p.matched}
    total_coverage = len(covered) / len(terms) if terms else 0.0

    enough = (not weak) and total_coverage >= WEAK_TOTAL_COVERAGE

    out: dict[str, Any] = {
        "question": question,
        "passages": [p.to_dict() for p in passages],
        "sources": _sources_from_passages(passages),
        "enough": enough,
        "coverage": round(total_coverage, 3),
        "kind": "extract",
    }
    if not enough:
        missing = [t for t in terms if t not in covered]
        out["why"] = (
            "这几段只答上了一部分"
            + (f"，没找到关于「{ '、'.join(missing[:3]) }」的内容" if missing else "")
        )
        out["suggest"] = _suggest(question, terms, missing=missing)
    return out


def _sources_from_passages(passages: list[Passage]) -> list[dict[str, Any]]:
    """按首次出现顺序去重，界面用它渲染底部的「引用了这几份资料」。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in passages:
        if p.item_id in seen:
            continue
        seen.add(p.item_id)
        out.append({"itemId": p.item_id, "title": p.title, "locator": p.locator})
    return out


def _sources(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in hits:
        item = h.get("item") or {}
        iid = str(item.get("id") or "")
        if not iid or iid in seen:
            continue
        seen.add(iid)
        out.append(
            {"itemId": iid, "title": str(item.get("title") or ""), "locator": str(item.get("locator") or "")}
        )
    return out


def _suggest(
    question: str,
    terms: list[str],
    *,
    no_terms: bool = False,
    missing: list[str] | None = None,
) -> list[str]:
    """
    答不上时给的具体建议。

    🔴 **必须具体到可以直接点。** 「换个说法试试」是一句废话 ——
       用户不知道换成什么，说了等于没说。所以这里给的每一条都要么是
       一个可以直接搜的词，要么是一个明确的动作。
    """
    tips: list[str] = []
    if no_terms:
        tips.append("问题里加一两个具体的名词，比如型号、人名、文件里出现过的说法")
        return tips
    if missing:
        tips.append(f"库里可能没有关于「{missing[0]}」的资料，先投喂相关文件再问一次")
    if len(terms) >= 4:
        tips.append(f"问题里的条件有点多，试试只问「{ ''.join(terms[:2]) }」")
    if len(question) < 8:
        tips.append("问得再具体一点，比如把「怎么做」换成「在什么情况下怎么做」")
    tips.append("切到「找东西」模式看完整结果列表，答案可能在某一条的正文里")
    return tips[:3]
