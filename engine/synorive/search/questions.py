"""
「这篇能回答哪些问题」—— N6
====================================================================
**要治的病**：一篇四十页的 PDF 躺在库里，你知道它大概讲什么，
但不知道它**具体能回答你哪些问题**。于是每次都要重新打开、重新翻。
搜索解决不了这个 —— 搜索的前提是你已经知道要问什么。

所以这一层反过来做：**从文章里读出它能回答的问题**，摆在你面前。
点一条就直接跳到那一段，不用打开整篇。

**三种问题来源，可靠性从高到低**：

① **章节标题**（最可靠）
   L3 已经把论文按 Abstract/Method/Results 分好节了。一个叫
   「实验设置」的章节，天然对应「实验是怎么设置的？」。
   这不是猜的 —— 章节标题本来就是作者对那一段内容的概括。

② **定义句 / 结论句**（较可靠）
   「X 是指……」「结果表明……」「我们发现……」这类句式，
   本身就是在回答一个隐含的问题，把句式反转过来就是问题。

③ **数字句**（可靠但覆盖窄）
   带具体数值的句子，对应「……是多少？」

🔴 **不做的事：不用模型生成问题。**
本地没有生成模型，而云端生成的问题会**编造这篇文章没有的东西** ——
用户点进去发现那一段根本不讲这个，比没有这个功能更糟。
所以这里生成的每一个问题都**必须能指回一个具体的块**，
指不回去的一律不生成。宁可少给几条，不给一条假的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: 定义句：把它反转成「什么是 X？」
_DEFINE = re.compile(
    r"^(?P<subj>[^，。；:：]{2,20})\s*(是指|指的是|是一种|定义为|称为|是一个)"
)
#: 结论句：把它反转成「结论是什么？」
_FINDING = re.compile(
    r"(结果表明|实验表明|研究发现|我们发现|数据显示|结论是|证明了|"
    r"results show|we find|our results|the experiment shows)"
)
#: 方法句
_METHOD = re.compile(
    r"(我们(采用|使用|提出|设计)|本文(采用|使用|提出)|方法是|做法是|"
    r"we (use|propose|adopt|design)|the method)"
)
#: 对比句
_COMPARE = re.compile(r"(相比|对比|优于|劣于|快\s*\d|慢\s*\d|compared (to|with)|outperform)")
#: 数字句
_NUMBER = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|倍|万|亿|ms|毫秒|秒|分钟|小时|天|年|GB|MB|TB|条|个|次)"
)

#: 章节名 → 问题模板。**只覆盖高确定性的那几个** ——
#: 认不出的章节走通用模板，不硬套
_SECTION_Q: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(abstract|摘要)", re.I), "这篇整体讲了什么、结论是什么？"),
    (re.compile(r"^(introduction|引言|绪论|背景|background)", re.I), "它要解决的是什么问题？为什么重要？"),
    (re.compile(r"^(related work|相关工作|文献综述)", re.I), "在它之前，别人是怎么做的？"),
    (re.compile(r"^(method|methods|方法|模型|approach|设计)", re.I), "它具体是怎么做的？"),
    (re.compile(r"^(experiment|实验|设置|setup|数据集|dataset)", re.I), "实验是怎么设置的？用了什么数据？"),
    (re.compile(r"^(result|results|结果|评测|evaluation)", re.I), "结果是多少？比基线好多少？"),
    (re.compile(r"^(ablation|消融)", re.I), "去掉某个部分之后效果掉多少？"),
    (re.compile(r"^(discussion|讨论|分析)", re.I), "作者自己怎么解释这些结果？"),
    (re.compile(r"^(limitation|局限|不足|threats)", re.I), "它自己承认了哪些局限？"),
    (re.compile(r"^(conclusion|结论|总结)", re.I), "最终结论是什么？"),
    (re.compile(r"^(future work|未来工作|展望)", re.I), "接下来还能做什么？"),
]

MIN_LEN = 12
MAX_LEN = 200


@dataclass
class Question:
    """一个问题 + 它指向的那一块。**必须能指回去**，指不回去就不生成。"""

    question: str
    #: 从哪来的：section / define / finding / method / compare / number
    kind: str
    chunk_rowid: int
    section: str | None = None
    page: int | None = None
    #: 那一段的开头，让用户点之前就知道大概是什么
    preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "question": self.question,
            "kind": self.kind,
            "chunkRowid": self.chunk_rowid,
            "preview": self.preview,
        }
        if self.section:
            d["section"] = self.section
        if self.page is not None:
            d["page"] = self.page
        return d


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"(?<=[。！？；…\n])|(?<=[.!?;])\s+", text or ""):
        s = (raw or "").strip()
        if MIN_LEN <= len(s) <= MAX_LEN:
            out.append(s)
    return out


def build_questions(
    chunks: list[dict[str, Any]], *, limit: int = 20
) -> list[Question]:
    """
    从一篇文档的所有块里读出它能回答的问题。

    `chunks` 每项要有 `rowid` / `text`，可选 `section` / `page`。

    **同一个章节最多出两条问题**：一个 15 页的方法章节能挤出十几个
    "它具体怎么做的"变体，全放出来会把其它章节淹掉，而用户想要的
    恰恰是**这篇文章的全貌**。
    """
    out: list[Question] = []
    per_section: dict[str, int] = {}
    seen: set[str] = set()

    def add(q: Question) -> bool:
        key = re.sub(r"\s+", "", q.question)
        if key in seen:
            return False
        sec = q.section or "-"
        if per_section.get(sec, 0) >= 2:
            return False
        seen.add(key)
        per_section[sec] = per_section.get(sec, 0) + 1
        out.append(q)
        return len(out) < limit

    # ① 章节标题 —— 最可靠，先做
    for c in chunks:
        sec = str(c.get("section") or "").strip()
        if not sec:
            continue
        text = str(c.get("text") or "")
        matched = next((q for pat, q in _SECTION_Q if pat.search(sec)), None)
        q = matched or f"「{sec}」这一节讲了什么？"
        if not add(
            Question(
                question=q, kind="section", chunk_rowid=int(c.get("rowid") or 0),
                section=sec, page=c.get("page"), preview=text[:120],
            )
        ):
            return out

    # ②③ 句式 —— 章节没覆盖到的地方补上
    for c in chunks:
        text = str(c.get("text") or "")
        sec = str(c.get("section") or "").strip() or None
        rowid = int(c.get("rowid") or 0)
        for s in _sentences(text):
            q: str | None = None
            kind = ""
            m = _DEFINE.match(s)
            if m:
                subj = m.group("subj").strip()
                # 主语太长多半是把半句话当成了主语，那种问题读起来很怪
                if 2 <= len(subj) <= 20:
                    q, kind = f"什么是{subj}？", "define"
            elif _FINDING.search(s):
                q, kind = "这篇得出的结论是什么？", "finding"
            elif _METHOD.search(s):
                q, kind = "它具体用了什么方法？", "method"
            elif _COMPARE.search(s):
                q, kind = "它和别的做法比，差别在哪？", "compare"
            elif _NUMBER.search(s):
                mm = _NUMBER.search(s)
                q, kind = f"「{mm.group(0) if mm else ''}」这个数字是怎么来的？", "number"

            if not q:
                continue
            if not add(
                Question(
                    question=q, kind=kind, chunk_rowid=rowid,
                    section=sec, page=c.get("page"), preview=s[:120],
                )
            ):
                return out
    return out


def summarize(questions: list[Question], *, doc_title: str = "") -> str:
    """一句人话的说明。**要说清楚这些问题是怎么来的** ——
    用户得知道它们是从原文里读出来的，不是模型编的。"""
    if not questions:
        return (
            "这篇没能读出可回答的问题 —— 多半是正文太短、"
            "或者它是一篇没有明显结构的散文。这不代表内容有问题。"
        )
    by_section = len([q for q in questions if q.kind == "section"])
    return (
        f"从{doc_title or '这篇'}里读出 {len(questions)} 个它能回答的问题"
        + (f"（其中 {by_section} 个来自章节标题）" if by_section else "")
        + "。**每一条都指向原文里一个具体的段落**，点了直接跳过去 —— "
        "这些问题是从原文里读出来的，不是模型编的。"
    )
