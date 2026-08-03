"""
D8 秒答卡 —— 把答案那句话抬到眼前
====================================================================
🔴 它**只摘录，不生成**。这条是设计约束，不是偷懒：

本地没有生成模型，任何"总结成一句话"都只能靠云端；而这个应用的定位是
本地优先、断网可用（A18）。更要紧的是 —— 用户搜的是**他自己的资料**，
一个被改写过的句子和原文哪怕只差一个字，他都无从分辨。
所以秒答卡给的永远是原文里真实存在的那几句，并且必须指明出处。

判据也刻意保守：拿不准就**不给卡**。
一张错的秒答卡比没有秒答卡糟得多 —— 用户会直接采信它，不再往下看。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..store.text import highlight_terms

#: 句子切分。和 chunker 用同一套规则，免得两边对"一句话"的理解不一致。
_SENT_SPLIT = re.compile(r"(?<=[。！？；…\n])|(?<=[.!?;])\s+")

#: 一张卡最多摘几句。多了就不叫"秒答"了，用户还是得读一段
MAX_SENTENCES = 2
#: 单句太短没有信息量（"是的。"），太长等于把整段搬过来
MIN_SENT_CHARS = 8
MAX_SENT_CHARS = 160

#: 覆盖率门槛：查询里的实词有多少出现在这句里。
#: 0.5 是实测折中 —— 再低会开始摘出"沾一个字就算"的句子。
MIN_COVERAGE = 0.5

#: 问句特征。不是问句就不给卡 —— 用户搜「光圈」是想浏览一批结果，
#: 直接甩一句话给他反而挡路；搜「光圈怎么影响景深」才是在问问题。
_QUESTION_HINTS = (
    "怎么", "如何", "为什么", "为何", "什么", "哪个", "哪些", "是不是",
    "能不能", "要不要", "多少", "多久", "几个", "区别", "差别", "吗", "呢",
    "?", "？", "how", "why", "what", "which", "when",
)

#: 疑问词本身不参与覆盖率计算。
#: 「光圈怎么影响景深」里真正要命中的是"光圈/影响/景深"，
#: 把"怎么"也算进分母，会让一句什么都没答上的话因为撞了个疑问词而及格。
_STOP_TERMS = frozenset({
    "怎么", "如何", "为什么", "为何", "什么", "哪个", "哪些", "是不是",
    "能不能", "要不要", "多少", "多久", "几个", "区别", "差别",
    "how", "why", "what", "which", "when", "the", "and", "for",
})


@dataclass
class AnswerCard:
    text: str
    item_id: str
    title: str
    locator: str
    #: 0~1，仅供界面决定要不要弱化显示。**不对用户展示为"置信度"** ——
    #: 那会暗示这是模型算出来的答案，而它只是摘录
    coverage: float
    page: int | None = None
    start_sec: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "text": self.text,
            "itemId": self.item_id,
            "title": self.title,
            "locator": self.locator,
            "coverage": round(self.coverage, 3),
            # 界面必须原样显示这句话，别改成"AI 总结"之类
            "kind": "extract",
        }
        if self.page is not None:
            d["page"] = self.page
        if self.start_sec is not None:
            d["startSec"] = self.start_sec
        return d


def looks_like_question(q: str) -> bool:
    s = q.strip().lower()
    if len(s) < 4:
        return False
    return any(h in s for h in _QUESTION_HINTS)


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for raw in _SENT_SPLIT.split(text or ""):
        s = (raw or "").strip()
        # 标题行不是答案。摘出「# 旅行_倒时差」这种，用户看了等于没看，
        # 而且它天然含查询词，覆盖率算出来还挺高，最容易被误选
        if s.startswith("#") or s.startswith("=") or s.startswith("-"):
            continue
        if MIN_SENT_CHARS <= len(s) <= MAX_SENT_CHARS:
            out.append(s)
    return out


def _coverage(terms: list[str], sent: str) -> float:
    if not terms:
        return 0.0
    hit = sum(1 for t in terms if t in sent)
    return hit / len(terms)


def build(
    query: str,
    hits: list[dict[str, Any]],
    *,
    weak: bool,
    texts: list[str] | None = None,
) -> dict[str, Any] | None:
    """
    从检索结果里摘一张秒答卡。拿不准就返回 None。

    weak=True（这一轮全是弱匹配）时直接不给 —— 连"真的匹配上了"都谈不上，
    摘出来的句子只会是巧合撞词。

    `texts` 是与 hits 一一对应的**原始块正文**。必须用它、不能用 hit 里的
    `highlight` —— 后者是给界面看的片段：带 `<em>` 标记、两头还接了省略号，
    从它摘出来的句子在原文里根本不存在，"只摘不生成"这条约束当场就破了。
    """
    if weak or not hits:
        return None
    if not looks_like_question(query):
        return None

    # 只看第一条。第二条开始就已经不是"最相关"了，
    # 从那里摘句子等于在赌，而赌错的代价是用户直接采信一个错答案。
    top = hits[0]
    item = top.get("item") or {}
    text = (texts[0] if texts else "") or ""
    if not text.strip():
        # 退路：只有 highlight 可用时先把标记和省略号剥干净再摘
        text = re.sub(r"<[^>]+>", "", str(top.get("highlight") or "")).strip("… ")
    if not text.strip():
        return None

    terms = [
        t for t in highlight_terms(query)
        if len(t) >= 2 and t.lower() not in _STOP_TERMS
    ]
    if not terms:
        return None

    scored = [(s, _coverage(terms, s)) for s in _sentences(text)]
    scored = [(s, c) for s, c in scored if c >= MIN_COVERAGE]
    if not scored:
        return None

    scored.sort(key=lambda x: -x[1])
    picked = scored[:MAX_SENTENCES]
    # 按原文顺序排回来 —— 按分数排会把因果句倒过来，读着莫名其妙
    order = {s: i for i, s in enumerate(_sentences(text))}
    picked.sort(key=lambda x: order.get(x[0], 0))

    card = AnswerCard(
        text="".join(s for s, _ in picked),
        item_id=str(item.get("id") or ""),
        title=str(item.get("title") or ""),
        locator=str(item.get("locator") or ""),
        coverage=max(c for _, c in picked),
        page=(top.get("location") or {}).get("page"),
        start_sec=(top.get("location") or {}).get("startSec"),
    )
    if not card.item_id or not card.text:
        return None
    return card.to_dict()
