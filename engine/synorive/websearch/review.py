"""
C4 文献综述自动生成 ＋ C5 多篇对齐抽表
====================================================================
**C4 综述**：把一批文献按主题聚成几簇（复用 `cluster.py`），每簇写一段，
**每句都是从某篇摘要里逐字摘出来的**，句尾挂 `[n]` 指回那一篇。

🔴 **「生成」这个词在这里是误导，必须说清楚**：它不改写、不概括、不总结，
它做的是**挑句子 + 排顺序 + 标出处**。理由和 `search/questions.py` 里
写的是同一条 —— 一份读起来很顺、但某句话原文根本没说的综述，
比没有综述糟得多，因为它看上去可信。

所以每一段的结构是固定的：
    这一簇讲什么（**从关键词拼的，不是写的**）
    ├ 摘录 1  [1]
    ├ 摘录 2  [2]
    └ 分歧：如果簇内有互相矛盾的说法，并排放，不替用户判断

**C5 多篇对齐抽表**：同一个指标（准确率、样本量、参数量、耗时…）在 N 篇
论文里各是多少，抽成一张表，可导出 csv/xlsx。这是读文献时最费时间的
手工活之一 —— 打开十几个 PDF 只为了抄十几个数字。

🔴 抽表**只抽摘要里明确写了的**。摘要没写就是空格，不去正文里猜，
更不做单位换算（92% 和 0.92 保持原样并排放）—— 换算错一个小数点，
整张表就成了误导。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .cluster import cluster_entries

#: 一段综述最多摘几句。超过五句就不是"综述"而是"摘要堆"了
_MAX_QUOTES_PER_CLUSTER = 5

#: 句子长度的可用区间。太短没信息量，太长在界面上会撑破排版
_MIN_SENT, _MAX_SENT = 18, 220

_SENT_SPLIT = re.compile(r"(?<=[。！？；.!?;])\s*")

#: 值得摘的句式：有结论、有数字、有对比的句子优先。
#: **不做语义判断，只看句式** —— 快，而且判据是可解释的
_VALUABLE = [
    (re.compile(r"(结果表明|实验表明|我们发现|研究发现|数据显示|证明了|表明)"), 3.0),
    (re.compile(r"(results? show|we find|we show|demonstrates?|indicates?)", re.I), 3.0),
    (re.compile(r"\d+(\.\d+)?\s*[%％]"), 2.5),
    (re.compile(r"(相比|优于|高于|低于|提升|下降|超过)"), 2.0),
    (re.compile(r"(compared with|outperform|improv|reduc|achiev)", re.I), 2.0),
    (re.compile(r"(提出了|本文提出|我们提出|首次)"), 1.5),
    (re.compile(r"(propose|present|introduce)", re.I), 1.5),
    (re.compile(r"(然而|但是|局限|不足|挑战)"), 1.2),
    (re.compile(r"(however|limitation|challenge|drawback)", re.I), 1.2),
]

#: 矛盾信号：同一簇里一句说"提升"另一句说"没有提升"，摆出来让人自己看
_POSITIVE = re.compile(r"(有效|显著|提升|优于|成功|可行|支持)")
_NEGATIVE = re.compile(r"(无效|不显著|未能|劣于|失败|不可行|反对|没有(发现|显著))")


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for s in _SENT_SPLIT.split(str(text or "")):
        s = re.sub(r"\s+", " ", s).strip()
        if _MIN_SENT <= len(s) <= _MAX_SENT:
            out.append(s)
    return out


def _score_sentence(s: str) -> float:
    return sum(w for pat, w in _VALUABLE if pat.search(s))


@dataclass
class ReviewQuote:
    """综述里的一句摘录。`text` 一定是原文逐字，不做任何改写。"""

    text: str = ""
    ref: int = 0
    title: str = ""
    url: str = ""
    year: str = ""
    stance: str = "neutral"     # positive / negative / neutral

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "ref": self.ref, "title": self.title,
                "url": self.url, "year": self.year, "stance": self.stance}


@dataclass
class ReviewSection:
    """综述的一段，对应一个主题簇。"""

    heading: str = ""
    keywords: list[str] = field(default_factory=list)
    paper_count: int = 0
    year_span: str = ""
    quotes: list[ReviewQuote] = field(default_factory=list)
    disputes: list[tuple[ReviewQuote, ReviewQuote]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading, "keywords": self.keywords,
            "paperCount": self.paper_count, "yearSpan": self.year_span,
            "quotes": [q.to_dict() for q in self.quotes],
            "disputes": [
                {"a": a.to_dict(), "b": b.to_dict()} for a, b in self.disputes
            ],
        }


def build_review(
    entries: list[dict[str, Any]],
    *,
    topic: str = "",
    max_sections: int = 6,
) -> dict[str, Any]:
    """
    C4 主入口 —— 出一份**只摘录不改写**的综述。

    `entries` 用 `merge_scholar()` 的形状。返回值可直接喂给
    `export.py` 转 Markdown/HTML/docx。
    """
    if len(entries) < 3:
        return {
            "topic": topic, "sections": [], "references": [],
            "note": "文献太少，凑不出分主题的综述 —— 三五篇直接读比看综述快",
        }

    grouped = cluster_entries(entries, max_clusters=max_sections, min_size=2)
    refs: list[dict[str, Any]] = []
    ref_index: dict[int, int] = {}       # entries 下标 → 参考文献序号

    def ref_of(i: int) -> int:
        if i not in ref_index:
            e = entries[i]
            meta = e.get("meta") or {}
            refs.append({
                "n": len(refs) + 1,
                "title": e.get("title") or "",
                "url": e.get("url") or "",
                "year": str(meta.get("year") or ""),
                "venue": str(meta.get("venue") or ""),
                "doi": str(meta.get("doi") or ""),
                "authors": meta.get("authors") or [],
            })
            ref_index[i] = len(refs)
        return ref_index[i]

    sections: list[ReviewSection] = []
    for c in grouped.get("clusters") or []:
        sec = ReviewSection(
            heading=str(c.get("label") or ""),
            keywords=list(c.get("keywords") or []),
            paper_count=int(c.get("size") or 0),
            year_span=str(c.get("yearSpan") or ""),
        )
        # 簇内每篇挑一句最有信息量的，再按分数取前几句 ——
        # **每篇最多贡献一句**，否则一篇写得漂亮的论文会占满整段
        candidates: list[tuple[float, ReviewQuote]] = []
        for i in c.get("members") or []:
            e = entries[i]
            meta = e.get("meta") or {}
            best: tuple[float, str] | None = None
            for s in _sentences(e.get("snippet") or ""):
                sc = _score_sentence(s)
                if best is None or sc > best[0]:
                    best = (sc, s)
            if best is None or best[0] <= 0:
                continue
            q = ReviewQuote(
                text=best[1], ref=ref_of(i),
                title=str(e.get("title") or ""), url=str(e.get("url") or ""),
                year=str(meta.get("year") or ""),
                stance=("negative" if _NEGATIVE.search(best[1])
                        else ("positive" if _POSITIVE.search(best[1]) else "neutral")),
            )
            candidates.append((best[0], q))

        candidates.sort(key=lambda x: -x[0])
        sec.quotes = [q for _s, q in candidates[:_MAX_QUOTES_PER_CLUSTER]]

        # 簇内分歧：一正一负并排放，**不判谁对**
        pos = [q for q in sec.quotes if q.stance == "positive"]
        neg = [q for q in sec.quotes if q.stance == "negative"]
        for a, b in zip(pos, neg):
            sec.disputes.append((a, b))

        if sec.quotes:
            sections.append(sec)

    return {
        "topic": topic,
        "sections": [s.to_dict() for s in sections],
        "references": refs,
        "clusterNote": grouped.get("note") or "",
        "note": (
            "这份综述里的**每一句都是从某篇摘要里逐字摘出来的**，"
            "句尾 [n] 指回原文。它没有改写、没有概括、没有做出任何"
            "原文没说的推断 —— 所以读起来会不如人写的连贯，"
            "这是刻意的代价。有分歧的地方并排摆出，不替你判断哪边对"
        ),
    }


# ────────────────────────────────────────────────────────────────
# C5 多篇对齐抽表
# ────────────────────────────────────────────────────────────────
#: 内置指标。每条 `(列名, 正则, 单位提示)`。正则里第一个捕获组是数值。
#: 用户也可以传自己的 `patterns`，见 `align_table(extra=...)`
_METRIC_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("准确率", re.compile(r"(?:准确率|accuracy|acc\.?)\s*(?:为|is|of|=|:)?\s*(\d+(?:\.\d+)?)\s*([%％]?)", re.I), "%"),
    ("F1", re.compile(r"\bF1(?:[- ]score)?\s*(?:为|is|of|=|:)?\s*(\d+(?:\.\d+)?)\s*([%％]?)", re.I), ""),
    ("精确率", re.compile(r"(?:精确率|precision)\s*(?:为|is|of|=|:)?\s*(\d+(?:\.\d+)?)\s*([%％]?)", re.I), "%"),
    ("召回率", re.compile(r"(?:召回率|recall)\s*(?:为|is|of|=|:)?\s*(\d+(?:\.\d+)?)\s*([%％]?)", re.I), "%"),
    ("AUC", re.compile(r"\bAUC\s*(?:为|is|of|=|:)?\s*(0?\.\d+|\d+(?:\.\d+)?)\s*([%％]?)", re.I), ""),
    ("样本量", re.compile(r"(?:样本量|样本数|n\s*=|sample size (?:of|is)?)\s*(\d[\d,]{1,9})\s*()", re.I), "例"),
    ("参数量", re.compile(r"(\d+(?:\.\d+)?)\s*([BMK])\s*(?:参数|parameters?|params)", re.I), ""),
    ("数据集", re.compile(r"(?:数据集|dataset)\s*(?:为|is|:)?\s*([A-Za-z][\w\-]{2,20})\s*()", re.I), ""),
    ("耗时", re.compile(r"(\d+(?:\.\d+)?)\s*(小时|分钟|秒|hours?|minutes?|seconds?|ms)", re.I), ""),
]


def align_table(
    entries: list[dict[str, Any]],
    *,
    metrics: list[str] | None = None,
    extra: dict[str, str] | None = None,
    max_rows: int = 60,
) -> dict[str, Any]:
    """
    C5 主入口 —— 同一指标在 N 篇里各是多少，抽成一张表。

    `metrics` 指定要哪几列（默认全部内置指标）；
    `extra` 可以加自定义列 `{列名: 正则}`，正则第一个捕获组当值。

    返回 `{columns, rows, filled, note}`，`rows` 里每格是
    `{"value": "92.3", "unit": "%", "raw": "准确率为 92.3%"}` 或 `None`。
    **抽不到就是 None，不填 0、不填「未提及」** —— 前者会被误当成真值参与
    比较，后者在导出成 csv 时又要再清洗一遍。
    """
    pats: list[tuple[str, re.Pattern[str], str]] = []
    for name, pat, unit in _METRIC_PATTERNS:
        if metrics is None or name in metrics:
            pats.append((name, pat, unit))
    for name, raw in (extra or {}).items():
        try:
            pats.append((name, re.compile(raw, re.I), ""))
        except re.error:
            continue        # 用户写错正则不该让整张表挂掉

    columns = ["文献", "年份"] + [n for n, _p, _u in pats]
    rows: list[dict[str, Any]] = []
    filled = 0

    for e in entries[:max_rows]:
        meta = e.get("meta") or {}
        text = f"{e.get('title', '')}。{e.get('snippet', '')}"
        cells: dict[str, Any] = {}
        for name, pat, unit in pats:
            m = pat.search(text)
            if not m:
                cells[name] = None
                continue
            val = m.group(1)
            got_unit = (m.group(2) if m.lastindex and m.lastindex >= 2 else "") or unit
            a, b = max(0, m.start() - 10), min(len(text), m.end() + 10)
            cells[name] = {
                "value": val,
                "unit": got_unit.replace("％", "%"),
                "raw": text[a:b].strip(),
            }
            filled += 1
        rows.append({
            "title": e.get("title") or "",
            "url": e.get("url") or "",
            "year": str(meta.get("year") or ""),
            "cells": cells,
        })

    total_cells = max(1, len(rows) * len(pats))
    return {
        "columns": columns,
        "rows": rows,
        "filled": filled,
        "coverage": round(filled / total_cells, 3),
        "note": (
            f"从 {len(rows)} 篇的**标题和摘要**里抽取，填充率 "
            f"{filled}/{total_cells}。空格表示摘要里没写这个指标 —— "
            "**没去正文里猜，也没做单位换算**（92% 和 0.92 保持原样）。"
            "要更全的话得先把 PDF 下下来入库，再对全文抽一次"
        ),
    }


def table_to_csv(table: dict[str, Any]) -> str:
    """把抽表结果转成 csv 文本。带 BOM 由调用方决定（Excel 打开中文要 BOM）。"""
    cols = table.get("columns") or []
    metric_cols = [c for c in cols if c not in ("文献", "年份")]

    def esc(s: Any) -> str:
        t = str(s if s is not None else "")
        return f'"{t}"' if any(ch in t for ch in ',"\n') else t

    lines = [",".join(esc(c) for c in (["文献", "年份"] + metric_cols + ["链接"]))]
    for r in table.get("rows") or []:
        cells = r.get("cells") or {}
        vals = []
        for c in metric_cols:
            cell = cells.get(c)
            vals.append(
                "" if not cell else f"{cell.get('value', '')}{cell.get('unit', '')}"
            )
        lines.append(",".join(esc(x) for x in (
            [r.get("title", ""), r.get("year", "")] + vals + [r.get("url", "")]
        )))
    return "\n".join(lines) + "\n"
