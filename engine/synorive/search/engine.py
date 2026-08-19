"""
混合检索 —— D1 关键词+语义融合，D2 三级瀑布，D4 多指标可调排序
====================================================================
为什么必须混合：
  · 只用向量：搜「BGE-small-zh-v1.5」这种精确型号会漏，
    因为向量把它理解成"某个模型名"，和别的模型名都很像。
  · 只用关键词：搜「怎么把 PDF 里的字提出来」找不到标题写着
    「文档解析方案对比」的文章，因为一个词都没重合。

融合用 **RRF（倒数排名融合）**：两路各自的排名取倒数相加。
不用分数加权是因为 BM25 的分数和余弦相似度量纲完全不同，
归一化怎么做都是拍脑袋；而排名是可比的。

    RRF(d) = Σ  weight_i / (K + rank_i(d))

K=60 是文献里的常用值，作用是压低头部排名的差距 ——
第1名和第2名的差距不该比第10名和第20名的差距大那么多。
"""

from __future__ import annotations

import html
import logging
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import sqlite_vec

from ..store.db import Database
from ..store.repository import Repository
from ..store.text import highlight_terms, to_query, to_trigram_query
from .query_syntax import describe, parse_query
from . import answer as answer_mod
from . import ask as ask_mod
from .recovery import RecoveryPlanner

log = logging.getLogger("synorive.search")

#: RRF 的平滑常数
#: 一份资料最多留几段其余命中给界面展开。
#: 留太多既没人看，又让每次搜索的响应体白白变大
EXTRA_HITS_KEEP = 3

RRF_K = 60
#: 每一路召回多少条送去融合。太少会漏，太多融合和取详情变慢。
RECALL_LIMIT = 200

#: 判定"这一轮有没有真正匹配上"的语义相似度线。
#:
#: 🔴 它**不是过滤线，是标注线**。这个区别是拿召回换来的教训：
#:
#: 向量 KNN 永远返回最近的 k 条，不管多不相关 —— 搜一个库里完全没有的东西
#: 也会给一整页看着像结果的垃圾。第一版据此做了硬过滤（低于 0.50 直接丢），
#: A20 的 100 题回归立刻从 100% 掉到 94%，"概念组合"那一类掉到 75%。
#:
#: 于是把两类分布量了一遍（BGE-small-zh-v1.5，120 篇跨领域语料）：
#:   正确答案：0.459 0.472 0.479 0.488 0.491 0.493 0.515 0.536 0.558 0.613
#:   纯噪声  ：0.453 0.455 0.455
#: **真答案最低 0.4594，噪声最高 0.4549，只差 0.0045。**
#: 两个分布几乎完全重叠 —— 绝对阈值这个工具本身就不成立，定在哪都会误杀。
#:
#: 所以改成：不删结果，只在"没有任何一条够得着这条线"时给整轮结果打
#: weakMatch 标记，界面据此明说"没有很匹配的，下面是最接近的几条"，
#: 同时挂上 D9 补救建议。用户真正的困扰是"分不清真结果和凑数的"，
#: 那就直接告诉他，而不是替他删掉可能有用的东西。
VECTOR_MATCH_THRESHOLD = float(os.environ.get("SYNORIVE_VECTOR_FLOOR", "0.50"))


def _diversity_bucket(locator: str) -> str:
    """
    D1 多样性的分组键：文件取**父目录**，链接取**域名**。

    为什么不是 item_id：召回和融合都已经按 item 去重，同一份资料只会出现
    一次，按它分组永远只有 n=0，降权分支进不去。分组必须比 item 更粗才有意义。

    为什么是父目录而不是整条路径：整条路径就等于 item 本身（同一目录下
    不同文件的路径也不同），又退化成"每条都是一组"。

    取不到就回一个唯一值（用 locator 本身），效果等同于"这条自成一组、不降权" ——
    **不能回空串**：那样所有取不到目录的条目会被归成同一组互相降权，
    表现是"某些结果莫名其妙排到后面去了"，而且完全看不出规律。
    """
    s = (locator or "").strip()
    if not s:
        return "\x00empty"
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://"):
        rest = s.split("://", 1)[1]
        host = rest.split("/", 1)[0].split("@")[-1].split(":")[0]
        return f"host:{host.lower()}" if host else s
    # 本机路径：取父目录。Windows 反斜杠和 POSIX 斜杠都要认 ——
    # 只切一种的话，另一种平台上整条路径会被当成"目录"，又退化成每条自成一组
    cut = max(s.rfind("\\"), s.rfind("/"))
    return f"dir:{s[:cut].lower()}" if cut > 0 else s


def _similarity(distance: float | None) -> float | None:
    """
    sqlite-vec 给的是 L2 距离（向量已归一化到单位长度），换算成余弦相似度。

    🔴 实测纠过一次错：这里原来写的是 `1 - distance / 2`，是把 sqlite-vec
    的 `distance` 当成了**平方** L2 距离（`2 - 2·cos`，那样才有 `cos = 1 - d/2`）。
    直接拿 sqlite-vec 建一张表量出来的真实数字戳穿了这个假设——它给的是
    **没开方的** L2 距离（`sqrt(2 - 2·cos)`），正确换算是 `cos = 1 - d² / 2`。
    两个公式在 d 很小时数值接近（掩盖了问题），d 越大差得越离谱：
    实测 cos_sim=0.7071 时，旧公式算出 0.6173，正确值是 0.7071。

    这条 bug 不影响 RRF 融合排序本身（两个公式对 d 都是严格单调递减，
    排序结果不变），但影响**任何拿绝对数值做判断**的地方——
    D9 弱匹配阈值判定（`VECTOR_MATCH_THRESHOLD=0.50`）就是其中之一，
    比较的分子从一开始就是算错的。
    """
    return None if distance is None else 1 - distance**2 / 2


@dataclass
class Weights:
    """D4 多指标排序权重 —— 界面上就是几个滑块。"""

    semantic: float = 1.0
    keyword: float = 1.0
    recency: float = 0.3
    source_trust: float = 0.2
    popularity: float = 0.2
    title_boost: float = 0.5

    #: D1 结果多样性：**同一个目录 / 同一个域名**下的第 2、3 条依次降权。
    #:
    #: 治的毛病：在一个按项目分文件夹的库里搜东西，某个文件夹里有二十个
    #: 沾边的文件时，整个首屏会被那一个文件夹占满 ——
    #: 用户以为"库里只有这一堆相关的"，而别的目录里那份更对的排在第 30 位。
    #: 调到 0 = 允许一个目录铺满（已经知道东西在哪个文件夹时要的就是这个）。
    #:
    #: 🔴 **第一版写的是"同一份资料的第 2、3 段降权"，那是死代码。**
    #:    召回路径（`recall_vector`）和融合（`fuse`）**都已经按 item_id 去重**，
    #:    同一份资料在结果里最多出现一次 —— 按"第几段"降权的分支
    #:    结构上永远进不去。滑块拖满也一个字不变。
    #:    是 `tests/test_progressive_and_ranking.py` 的 D1① 当场抓出来的：
    #:    我按错误的心智模型设计了它，而它看起来完全正常。
    diversity: float = 0.5

    #: D1 长度惩罚：很短的片段（目录行、页眉、一句标题）降权。
    #: 它们天然含查询词、天然覆盖率高，是弱匹配里最常见的一类噪声。
    length_penalty: float = 0.3

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Weights:
        if not d:
            return cls()
        return cls(
            semantic=float(d.get("semantic", 1.0)),
            keyword=float(d.get("keyword", 1.0)),
            recency=float(d.get("recency", 0.3)),
            source_trust=float(d.get("sourceTrust", 0.2)),
            popularity=float(d.get("popularity", 0.2)),
            title_boost=float(d.get("titleBoost", 0.5)),
            # 🔴 新增字段必须给默认值再 get —— 老客户端（安卓端 / MCP / CLI）
            #    发上来的 weights 里没有这两个键，不给默认会直接 KeyError，
            #    表现是"手机上一搜就 500"，而桌面端一切正常
            diversity=float(d.get("diversity", 0.5)),
            length_penalty=float(d.get("lengthPenalty", 0.3)),
        )


PRESETS: dict[str, Weights] = {
    "balanced": Weights(),
    # 求准：关键词为主，语义只做补充。短片段惩罚加重 ——
    # 求准场景下，一行目录撞上查询词是最讨厌的一类假阳性
    "precise": Weights(
        semantic=0.4, keyword=1.5, recency=0.1, title_boost=0.8, length_penalty=0.6
    ),
    # 求全：语义为主，能理解同义和近义。多样性拉高，尽量让更多份资料露头
    "semantic": Weights(semantic=1.5, keyword=0.5, recency=0.1, diversity=0.8),
    # 找最近的：时间权重拉满
    "recent": Weights(semantic=0.8, keyword=0.8, recency=1.5, diversity=0.3),
    # D1 新增「深读一处」：多样性关掉，允许同一个目录铺满整屏。
    # 场景是**已经知道东西在哪个文件夹里**，要把那一堆一次看全 ——
    # 这时候"每个目录只露头一两条"恰好是帮倒忙
    "deep": Weights(semantic=1.2, keyword=1.0, diversity=0.0, length_penalty=0.2),
}

#: D-adaptive 自适应权重专用，不进 PRESETS/前端预设下拉——用户不会手动选
#: "事实核查"这种档，只有 classify_intent() 会用到。跟 PRESETS 分开放
#: 是为了不让 RankingPreset 那个面向用户的枚举被内部实现细节污染。
_ADAPTIVE_ONLY: dict[str, Weights] = {
    # 求证/核查：来源可信度拉高，多样性也拉高——核查一件事真假，
    # 单一来源不够，得看到不同信息源怎么说
    "factcheck": Weights(semantic=1.0, keyword=1.1, source_trust=1.2, diversity=0.7),
    # 对比分析：多样性拉满，不让同一份资料/同一个目录挤占前排——
    # 对比 A 和 B，结果里只有 A 相关的东西毫无意义
    "compare": Weights(semantic=1.2, keyword=0.9, diversity=1.0, length_penalty=0.2),
}

#: 对比类查询的信号词
_COMPARE_RE = re.compile(r"(哪个更|哪个好|对比|比较|区别|优劣|\bvs\.?\b)", re.IGNORECASE)
#: 求证类查询的信号词
_FACTCHECK_RE = re.compile(r"(是不是真的|真的假的|求证|核实|是否属实|真伪|有没有证据)")
#: 精确查找：带引号的短语，或者长得像文件名/路径/代码符号
_PRECISE_RE = re.compile(r'["“”]|[/\\]|\.[A-Za-z0-9]{1,5}\b|[a-z]+_[a-z]|[a-z][A-Z]')
#: 模糊探索：以"关于/有什么/有没有/推荐"开头，通常是在探索一个话题而不是找具体东西
_EXPLORE_RE = re.compile(r"^(关于|有什么|有没有|推荐|了解一下)")


def classify_intent(query: str) -> tuple[str, Weights]:
    """
    D-adaptive 查询意图分类——纯规则正则，判不出来就退回 balanced，不硬猜。

    跟 websearch/intent.py 是同一种架构范式（规则表、<1ms、拿不准就退默认），
    但分类轴完全不同：那边按**内容领域**分（论文/教程/新闻……）决定联网搜索
    派哪几个引擎；这里按**检索行为**分（精确查找/模糊探索/求证/对比）决定
    本地检索的排序权重怎么配，两者不能共用同一张规则表。

    只在用户选"自动"档时才会被调用——手动选了具体预设或拖过滑块的话，
    这个函数根本不会被调用，规则判得好不好完全不影响手动路径。
    """
    q = query.strip()
    if not q:
        return "balanced", Weights()
    if _COMPARE_RE.search(q):
        return "compare", _ADAPTIVE_ONLY["compare"]
    if _FACTCHECK_RE.search(q):
        return "factcheck", _ADAPTIVE_ONLY["factcheck"]
    if _PRECISE_RE.search(q):
        return "precise", PRESETS["precise"]
    if _EXPLORE_RE.match(q) or len(q) <= 6:
        return "explore", PRESETS["semantic"]
    return "balanced", Weights()


@dataclass
class Filters:
    modalities: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    time_from: str | None = None
    time_to: str | None = None
    size_min: int | None = None
    size_max: int | None = None
    scopes: list[str] = field(default_factory=list)
    exclude_scopes: list[str] = field(default_factory=list)
    #: D10 的 type:pdf 走这里 —— 扩展名不是 modality，库里 pdf 的 modality 是 text
    extensions: list[str] = field(default_factory=list)
    #: L3-plus 的 section:方法 走这里。
    #: 🔴 **它是唯一一个落在 chunks 上而不是 items 上的筛选** ——
    #: 章节是块的属性不是文件的属性（一个 PDF 里同时有摘要和方法）。
    #: 所以 `sql()` 对它有两套写法，见那里的注释
    sections: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Filters:
        d = d or {}
        return cls(
            modalities=list(d.get("modalities") or []),
            sources=list(d.get("sources") or []),
            tags=list(d.get("tags") or []),
            time_from=d.get("timeFrom"),
            time_to=d.get("timeTo"),
            size_min=d.get("sizeMinBytes"),
            size_max=d.get("sizeMaxBytes"),
            scopes=list(d.get("scopes") or []),
            exclude_scopes=list(d.get("excludeScopes") or []),
            extensions=list(d.get("extensions") or []),
            sections=list(d.get("sections") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        """
        反向转回 from_dict 认得的形状。

        ⚠️ 键名必须和 from_dict 一一对应（timeFrom 不是 time_from）。
           对不上的话 D9 里"去掉某个筛选再数一遍"会拿着一组空筛选去跑，
           每次都返回全库条数 —— 建议看起来正常，其实全是假的。
        """
        return {
            "modalities": self.modalities,
            "sources": self.sources,
            "tags": self.tags,
            "timeFrom": self.time_from,
            "timeTo": self.time_to,
            "sizeMinBytes": self.size_min,
            "sizeMaxBytes": self.size_max,
            "scopes": self.scopes,
            "excludeScopes": self.exclude_scopes,
            "extensions": self.extensions,
            "sections": self.sections,
        }

    def merged_with(self, other: dict[str, Any] | None) -> Filters:
        """
        和另一组筛选合并（列表求并，标量以 other 为准）。

        用于把「查询串里写的 D10 指令」和「界面上点选的筛选」叠加。
        两边冲突时以查询串为准 —— 用户刚敲进去的东西优先级更高。
        """
        if not other:
            return self
        o = Filters.from_dict(other)
        return Filters(
            modalities=list({*self.modalities, *o.modalities}),
            sources=list({*self.sources, *o.sources}),
            tags=list({*self.tags, *o.tags}),
            time_from=o.time_from or self.time_from,
            time_to=o.time_to or self.time_to,
            size_min=o.size_min if o.size_min is not None else self.size_min,
            size_max=o.size_max if o.size_max is not None else self.size_max,
            scopes=list({*self.scopes, *o.scopes}),
            exclude_scopes=list({*self.exclude_scopes, *o.exclude_scopes}),
            extensions=list({*self.extensions, *o.extensions}),
            sections=list({*self.sections, *o.sections}),
        )

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.modalities, self.sources, self.tags, self.time_from, self.time_to,
                self.size_min, self.size_max, self.scopes, self.exclude_scopes,
                self.extensions, self.sections,
            )
        )

    def sql(self, alias: str = "i", chunk_alias: str | None = None) -> tuple[str, list[Any]]:
        """
        拼成 WHERE 片段。返回 (子句, 参数)。

        `chunk_alias` 是 L3-plus 的 `section:` 专用：
        **传了就在块上过滤，没传就退化成在条目上过滤**。

        🔴 这两者不是同一件事，退化是有损的：
        - 传了 `c` → `c.section LIKE '%method%'`，命中的**这一块**必须在方法章节
        - 没传 → `i.id IN (SELECT item_id FROM chunks WHERE section LIKE ...)`，
          只保证这篇论文**有**方法章节，命中的块可能在别的章节
        块级召回（keyword / vector）一律传 `c`；条目级的路径（trigram 只查标题、
        `recall_by_filter` 根本没有查询词）拿不到块，只能走后者。
        **宁可退化也不放弃过滤** —— 完全忽略 `sections` 会让 trigram 那一路
        召回一批不在指定章节的东西，然后混进最终排序，用户看不出是哪一路带进来的。
        """
        parts: list[str] = []
        args: list[Any] = []

        if self.sections:
            # 🔴 `LIKE` 前后都要 `%`：真实章节标题是 `3.2 Experimental Method`，
            # 只在末尾加 `%` 的话一条都匹配不到，而且是**静默**匹配不到
            pats = [f"%{s.lower()}%" for s in self.sections]
            if chunk_alias:
                ors = " OR ".join(f"LOWER({chunk_alias}.section) LIKE ?" for _ in pats)
                parts.append(f"({ors})")
            else:
                ors = " OR ".join("LOWER(section) LIKE ?" for _ in pats)
                parts.append(
                    f"{alias}.id IN (SELECT item_id FROM chunks WHERE section IS NOT NULL AND ({ors}))"
                )
            args += pats

        if self.modalities:
            parts.append(f"{alias}.modality IN ({','.join('?' * len(self.modalities))})")
            args += self.modalities
        if self.sources:
            parts.append(f"{alias}.source IN ({','.join('?' * len(self.sources))})")
            args += self.sources
        if self.time_from:
            parts.append(f"COALESCE({alias}.content_time, {alias}.created_at) >= ?")
            args.append(self.time_from)
        if self.time_to:
            parts.append(f"COALESCE({alias}.content_time, {alias}.created_at) <= ?")
            args.append(self.time_to)
        if self.size_min is not None:
            parts.append(f"{alias}.size_bytes >= ?")
            args.append(self.size_min)
        if self.size_max is not None:
            parts.append(f"{alias}.size_bytes <= ?")
            args.append(self.size_max)
        for s in self.scopes:
            parts.append(f"{alias}.locator LIKE ?")
            args.append(f"{s}%")
        for s in self.exclude_scopes:
            parts.append(f"{alias}.locator NOT LIKE ?")
            args.append(f"{s}%")
        if self.tags:
            marks = ",".join("?" * len(self.tags))
            parts.append(
                f"{alias}.id IN (SELECT it.item_id FROM item_tags it "
                f"JOIN tags t ON t.id = it.tag_id WHERE t.name IN ({marks}))"
            )
            args += self.tags
        if self.extensions:
            # 扩展名是 OR 关系（type:pdf,docx 是"这两种都要"不是"两种都是"）
            ors = " OR ".join(f"LOWER({alias}.locator) LIKE ?" for _ in self.extensions)
            parts.append(f"({ors})")
            args += [f"%{e.lower()}" for e in self.extensions]

        return (" AND ".join(parts) if parts else ""), args


@dataclass
class Candidate:
    item_id: str
    chunk_rowid: int | None = None
    #: 各路召回里的排名（1 起），没被这一路召回就是 None
    rank_keyword: int | None = None
    rank_vector: int | None = None
    rank_trigram: int | None = None
    #: 原始分，用于可解释
    bm25: float | None = None
    distance: float | None = None
    matched_via: set[str] = field(default_factory=set)
    best_text: str = ""
    #: best_text 具体是从哪个内容通道来的——跟 matched_via（这条结果命中过
    #: 的所有通道的并集）不是一回事。同一个 item 可能关键词路命中了正文块、
    #: 语义路命中了它的 OCR 块，合并后 matched_via={body,ocr} 但界面上
    #: 实际显示、高亮的摘录只可能来自其中一个通道——这个字段记的是那一个，
    #: reason()/前端徽标都要按它来说话，不能按 matched_via 整个集合来说话，
    #: 否则会出现"徽标说是图片文字命中，摘录显示的却是正文"这种对不上。
    best_text_channel: str = ""
    page: int | None = None
    start_sec: float | None = None
    #: L3：命中的这一块属于论文的哪个章节（Abstract/Method/Results…）
    section: str | None = None
    #: 同一份资料里**还有几段**也命中了（不含正在显示的这一段）。
    #: 融合早就按 item_id 去重了，所以列表里一份资料只占一行 —— 这没问题，
    #: 问题是那几段就此**彻底看不见了**：用户搜到一份 80 页的报告，
    #: 界面只给他第 12 页那一段，另外 6 处命中他既不知道存在、也够不着。
    extra_hits: int = 0
    #: 其余命中段的摘录，最多留几条给界面展开用
    extra_texts: list[tuple[str, int | None, str | None]] = field(default_factory=list)


def _brute_force_knn(conn: sqlite3.Connection, qv: Any, knn_limit: int) -> list[tuple[int, float]]:
    """sqlite-vec 的暴力扫描 KNN——A17 之前的唯一路径，现在是 ANN 关闭/不可用时的兜底。"""
    blob = sqlite_vec.serialize_float32(qv.tolist())
    try:
        rows = conn.execute(
            "SELECT chunk_rowid, distance FROM vec_chunks "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (blob, knn_limit),
        ).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("向量召回失败（多半是还没建向量表）：%s", e)
        return []
    return [(int(r["chunk_rowid"]), float(r["distance"])) for r in rows]


def _is_strong_match(c: Candidate) -> bool:
    """
    这一条算不算"真的匹配上了"。

    被关键词或标题子串命中 → 用户明确打出了那个词，无条件算；
    只靠向量捞到的 → 要够得着相似度线才算。
    **它只用于判定，不用于过滤** —— 理由见 VECTOR_MATCH_THRESHOLD。
    """
    if c.matched_via - {"vector"}:
        return True
    sim = _similarity(c.distance)
    if sim is None:
        return True          # 拿不到距离就不下判断，宁可当成匹配
    return sim >= VECTOR_MATCH_THRESHOLD


class SearchEngine:
    def __init__(
        self,
        db: Database,
        repo: Repository,
        embedder: Any | None = None,
        reranker: Any | None = None,
    ) -> None:
        self.db = db
        self.repo = repo
        self.embedder = embedder
        #: D7 精排。可以是 None（没配），或加载失败 —— 两种情况都安静退回融合排序
        self.reranker = reranker
        # D9 零结果补救。注入自己的"数一下有几条"，避免两个模块互相 import
        self._recovery = RecoveryPlanner(self.db.connect, self._count_for_recovery)

    def _count_for_recovery(self, query: str, filters: dict[str, Any]) -> int:
        """
        补救建议专用：按给定条件真跑一次，只要条数。

        stage 固定 keyword —— 补救是在用户已经等过一轮之后才发生的，
        这时候再花几百毫秒跑向量只为了数个数，用户会觉得"卡住了"。
        关键词那一路足够回答"换成这样还有没有东西"。
        """
        try:
            f = Filters.from_dict(filters)
            cands = self.recall_keyword(query, f) if query.strip() else self.recall_by_filter(f)
            return len({c.item_id for c in cands})
        except Exception:
            # 补救本身失败绝不能把整个检索带崩 —— 它只是锦上添花
            return 0

    # ── 各路召回 ────────────────────────────────────────────

    def recall_keyword(self, query: str, filters: Filters, limit: int = RECALL_LIMIT) -> list[Candidate]:
        """FTS5 + BM25。分块级召回，一条内容可能命中多个块，取最好的那个。"""
        expr = to_query(query)
        if not expr:
            return []

        conn = self.db.connect()
        # L3-plus：这一路是块级召回，`section:` 要落在**命中的那一块**上
        where, args = filters.sql("i", chunk_alias="c")
        sql = f"""
            SELECT c.rowid AS chunk_rowid, c.item_id, c.text, c.channel, c.page, c.start_sec,
                   c.section, bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            JOIN items  i ON i.id = c.item_id
            WHERE chunks_fts MATCH ?
              {'AND ' + where if where else ''}
            ORDER BY score
            LIMIT ?
        """
        try:
            rows = conn.execute(sql, (expr, *args, limit)).fetchall()
        except sqlite3.OperationalError as e:
            # 用户输入里可能有 FTS5 不认的语法，别把异常抛给界面
            log.debug("关键词召回语法错误（查询=%r）：%s", expr, e)
            return []

        out: list[Candidate] = []
        seen: dict[str, Candidate] = {}
        for rank, r in enumerate(rows, start=1):
            iid = str(r["item_id"])
            if iid in seen:
                # 同一份资料的后续命中段。不再新建一行，但要记下来 ——
                # 直接 continue 会让这几段永远消失（见 Candidate.extra_hits）
                m = seen[iid]
                m.extra_hits += 1
                if len(m.extra_texts) < EXTRA_HITS_KEEP:
                    m.extra_texts.append((str(r["text"]), r["page"], r["section"]))
                continue
            c = Candidate(
                item_id=iid,
                chunk_rowid=int(r["chunk_rowid"]),
                rank_keyword=len(seen) + 1,
                bm25=float(r["score"]),
                best_text=str(r["text"]),
                page=r["page"],
                start_sec=r["start_sec"],
                section=r["section"],
            )
            channel = str(r["channel"]) or "body"
            c.matched_via.add(channel)
            c.best_text_channel = channel
            seen[iid] = c
            out.append(c)
        return out

    def _drop_excluded(self, cands: list[Candidate], excludes: list[str]) -> list[Candidate]:
        """
        把命中任一排除词的内容整条拿掉。

        🔴 **判据是"这条内容里有没有这个词"，不是"召回时用的那一块里有没有"。**
        用户写 `-草稿` 的意思是"别给我看草稿"，不是"别给我看正好命中的那一块里
        写着草稿的"。只看 `best_text` 的话，一份第 3 页写着"草稿"、而第 8 页被
        语义召回捞出来的文件照样会出现 —— 排除等于没排。

        一次 FTS 查询解决，范围限定在候选集里，代价与候选数同阶。
        查询本身出错时**不做任何过滤**并记一条日志：宁可多给几条，
        也不能因为排除逻辑挂了就把结果集清空（那看起来像"库里什么都没有"）。
        """
        if not cands or not excludes:
            return cands
        expr = to_query(" ".join(excludes))
        if not expr:
            return cands
        ids = [c.item_id for c in cands]
        conn = self.db.connect()
        hit: set[str] = set()
        # SQLite 的变量数上限是 999，候选集通常远小于它，超了就分批
        for i in range(0, len(ids), 900):
            batch = ids[i : i + 900]
            marks = ",".join("?" * len(batch))
            try:
                rows = conn.execute(
                    f"""SELECT DISTINCT c.item_id FROM chunks_fts
                        JOIN chunks c ON c.rowid = chunks_fts.rowid
                        WHERE chunks_fts MATCH ? AND c.item_id IN ({marks})""",
                    (expr, *batch),
                ).fetchall()
            except sqlite3.OperationalError as e:
                log.debug("排除词查询语法错误（%r）：%s —— 本次不做排除", expr, e)
                return cands
            hit.update(str(r["item_id"]) for r in rows)
            try:
                rows = conn.execute(
                    f"""SELECT DISTINCT i.id FROM items_fts
                        JOIN items i ON i.rowid = items_fts.rowid
                        WHERE items_fts MATCH ? AND i.id IN ({marks})""",
                    (expr, *batch),
                ).fetchall()
            except sqlite3.OperationalError:
                pass
            else:
                hit.update(str(r["id"]) for r in rows)
        if not hit:
            return cands
        return [c for c in cands if c.item_id not in hit]

    def recall_title(self, query: str, filters: Filters, limit: int = 60) -> list[Candidate]:
        """
        标题/路径的子串召回（trigram）。

        专治「我只记得文件名里那几个字」—— jieba 分完词之后
        「年度总」这种词内片段是匹配不到的，得靠 trigram。
        查询短于 3 字符时 trigram 无能为力，直接返回空。
        """
        expr = to_trigram_query(query)
        if not expr:
            return []

        conn = self.db.connect()
        where, args = filters.sql("i")
        sql = f"""
            SELECT i.id AS item_id, bm25(items_tri) AS score
            FROM items_tri
            JOIN items i ON i.rowid = items_tri.rowid
            WHERE items_tri MATCH ?
              {'AND ' + where if where else ''}
            ORDER BY score
            LIMIT ?
        """
        try:
            rows = conn.execute(sql, (expr, *args, limit)).fetchall()
        except sqlite3.OperationalError:
            return []

        out: list[Candidate] = []
        for rank, r in enumerate(rows, start=1):
            c = Candidate(item_id=str(r["item_id"]), rank_trigram=rank)
            c.matched_via.add("filename")
            out.append(c)
        return out

    def recall_vector(self, query: str, filters: Filters, limit: int = RECALL_LIMIT) -> list[Candidate]:
        """向量近邻召回。"""
        if self.embedder is None or not query.strip():
            return []

        conn = self.db.connect()
        try:
            qv = self.embedder.encode_one(query, is_query=True)
        except Exception as e:  # noqa: BLE001
            log.warning("查询向量化失败，本次只走关键词：%s", e)
            return []

        # 先做 KNN 再关联过滤：sqlite-vec 的 vec0 表要求 k 是常量条件，
        # 把过滤条件塞进 KNN 查询里会让它退化成全表扫。
        # 有筛选时多召回一些，过滤完还够用。
        knn_limit = limit * 3 if not filters.empty else limit

        # A17：库大到阈值以上时，ANN 索引接管这一步——
        # 15 万块以下 ann_index 要么是 None（模型还没就绪过一次）要么 .active
        # 是 False（见 ann_index.py 的阈值判据），两种情况都走原来的暴力扫描，
        # 行为和这个功能上线前完全一样，一行代码都不受影响。
        # 查询向量已经是归一化过的（写入侧和查询侧共用同一套约定，
        # 暴力扫描那条路径也是直接拿它序列化去比对，不额外再归一化一次——
        # 两条路径必须信任同一个前提，不然哪天前提变了只有一条路径悄悄跟着错）
        ann = self.repo.ann_index
        if ann is not None and ann.active:
            try:
                pairs = ann.search(qv.tolist(), knn_limit)
            except Exception as e:  # noqa: BLE001
                log.warning("ANN 召回失败，本次退回暴力扫描：%s", e)
                pairs = _brute_force_knn(conn, qv, knn_limit)
        else:
            pairs = _brute_force_knn(conn, qv, knn_limit)

        if not pairs:
            return []

        rowids = [rid for rid, _ in pairs]
        dist = dict(pairs)

        # 同上：向量召回也是块级的，`section:` 落在块上
        where, args = filters.sql("i", chunk_alias="c")
        marks = ",".join("?" * len(rowids))
        detail = conn.execute(
            f"""
            SELECT c.rowid AS chunk_rowid, c.item_id, c.text, c.channel, c.page, c.start_sec,
                   c.section
            FROM chunks c JOIN items i ON i.id = c.item_id
            WHERE c.rowid IN ({marks})
              {'AND ' + where if where else ''}
            """,
            (*rowids, *args),
        ).fetchall()

        by_rowid = {int(r["chunk_rowid"]): r for r in detail}
        out: list[Candidate] = []
        seen: set[str] = set()
        for rid in rowids:  # 保持距离升序
            r = by_rowid.get(rid)
            if r is None:
                continue
            iid = str(r["item_id"])
            if iid in seen:
                continue
            seen.add(iid)
            c = Candidate(
                item_id=iid,
                chunk_rowid=rid,
                rank_vector=len(seen),
                distance=dist[rid],
                best_text=str(r["text"]),
                page=r["page"],
                start_sec=r["start_sec"],
                section=r["section"],
            )
            # 🔴 这里以前直接 add("vector")，把 SQL 已经查出来的 channel
            # （正文/OCR/字幕/标题……）扔掉了——纯语义命中的结果，用户
            # 永远不知道这条到底是从图片文字还是视频字幕里找到的。
            # 跟关键词召回（上面 recall_keyword）保持同一种做法：
            # matched_via 记的是"内容通道"，不是"走了哪条召回路"。
            channel = str(r["channel"]) or "body"
            c.matched_via.add(channel)
            c.best_text_channel = channel
            out.append(c)
            if len(out) >= limit:
                break
        return out

    # ── 融合与排序 ──────────────────────────────────────────

    def fuse(self, groups: dict[str, list[Candidate]], weights: Weights) -> list[Candidate]:
        """RRF 融合多路召回。"""
        merged: dict[str, Candidate] = {}
        rrf: dict[str, float] = {}

        route_weight = {
            "keyword": weights.keyword,
            "vector": weights.semantic,
            "trigram": weights.keyword * 0.6,  # 子串路是兜底，权重打折
        }

        for route, cands in groups.items():
            w = route_weight.get(route, 1.0)
            if w <= 0:
                continue
            for rank, c in enumerate(cands, start=1):
                rrf[c.item_id] = rrf.get(c.item_id, 0.0) + w / (RRF_K + rank)

                if c.item_id not in merged:
                    merged[c.item_id] = c
                else:
                    m = merged[c.item_id]
                    m.matched_via |= c.matched_via
                    # 另一路召回也数出了几段，取更大的那个（同一份资料的
                    # 命中段数不该因为换一路召回就变少）
                    if c.extra_hits > m.extra_hits:
                        m.extra_hits = c.extra_hits
                        m.extra_texts = c.extra_texts
                    m.rank_keyword = m.rank_keyword or c.rank_keyword
                    m.rank_vector = m.rank_vector or c.rank_vector
                    m.rank_trigram = m.rank_trigram or c.rank_trigram
                    m.bm25 = m.bm25 if m.bm25 is not None else c.bm25
                    m.distance = m.distance if m.distance is not None else c.distance
                    # 有正文片段的优先留着，纯标题命中的片段是空的
                    if not m.best_text and c.best_text:
                        m.best_text = c.best_text
                        m.best_text_channel = c.best_text_channel
                        m.page = c.page
                        m.start_sec = c.start_sec

        ordered = sorted(merged.values(), key=lambda c: -rrf[c.item_id])
        for c in ordered:
            setattr(c, "_rrf", rrf[c.item_id])
        return ordered

    def apply_signals(
        self, cands: list[Candidate], weights: Weights, query: str
    ) -> list[tuple[Candidate, float, dict[str, float]]]:
        """
        在 RRF 基础上叠加时间新鲜度、热度、标题命中三个信号。

        这三个是「加分项」不是「主排序」：它们的量纲统一在 0~1，
        乘上各自权重后加到 RRF 分上。RRF 分本身量级约 0.01~0.03，
        所以这里的加分要缩到同一量级，否则一个热门文件会永远霸榜。
        """
        if not cands:
            return []

        rows = self.repo.get_items([c.item_id for c in cands])
        now = datetime.now(UTC)
        terms = [t for t in highlight_terms(query) if len(t) >= 2]

        max_open = 1
        for r in rows.values():
            max_open = max(max_open, int(r["open_count"] or 0))

        out: list[tuple[Candidate, float, dict[str, float]]] = []
        for c in cands:
            r = rows.get(c.item_id)
            if r is None:
                continue

            base = float(getattr(c, "_rrf", 0.0))
            parts: dict[str, float] = {"rrf": base}

            # 时间新鲜度：半衰期 180 天的指数衰减，落在 0~1
            ts = r["content_time"] or r["created_at"]
            fresh = 0.0
            if ts:
                try:
                    dt = datetime.fromisoformat(str(ts))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    days = max(0.0, (now - dt).total_seconds() / 86400)
                    fresh = math.exp(-days / 180.0)
                except (ValueError, TypeError):
                    fresh = 0.0
            parts["recency"] = fresh

            # 热度：相对最热的那条归一化，取 log 压平长尾
            opens = int(r["open_count"] or 0)
            pop = math.log1p(opens) / math.log1p(max_open) if max_open > 1 else 0.0
            parts["popularity"] = pop

            # 标题命中
            title = str(r["title"] or "")
            hit = sum(1 for t in terms if t in title)
            title_hit = min(1.0, hit / len(terms)) if terms else 0.0
            parts["titleBoost"] = title_hit
            if title_hit > 0:
                c.matched_via.add("title")

            # 来源权重：本机文件比网页可信一点（用户自己存下来的东西）
            trust = {"file": 1.0, "chat-export": 0.9, "mail": 0.8, "clipboard": 0.7}.get(
                str(r["source"]), 0.6
            )
            parts["sourceTrust"] = trust

            # D1 长度惩罚：很短的片段（目录行、页眉、单句标题）降权。
            # 它们天然含查询词、覆盖率还高，是弱匹配噪声里最常见的一类。
            #
            # 用 240 字做拐点：低于它线性扣分，高于它一律不扣。
            # **不做"越长越好"**——那会把整章正文顶上来，同样是错的方向。
            n_chars = len(c.best_text or "")
            short = max(0.0, 1.0 - n_chars / 240.0) if n_chars else 1.0
            parts["lengthPenalty"] = short

            # 加分项统一缩到 RRF 的量级（RRF 满打满算约 0.03）
            SCALE = 0.01
            score = base + SCALE * (
                weights.recency * fresh
                + weights.popularity * pop
                + weights.title_boost * title_hit
                + weights.source_trust * trust
                - weights.length_penalty * short
            )
            # 🔴 **必须夹到非负。** 长度惩罚是唯一一个做减法的项，
            #    base（RRF）小到 0.003 量级、而惩罚满打满算 0.003 时分数会变成负数。
            #    负分本身还排得对，但下面的多样性是**乘一个小于 1 的系数** ——
            #    乘在负数上是把它往 0 推，也就是**越降权排得越前**。
            #    这个 bug 不会报错，只会让"同一份文档的第二段莫名其妙冲到第一"。
            score = max(1e-9, score)
            out.append((c, score, parts))

        out.sort(key=lambda x: -x[1])

        # D1 结果多样性：**同一个目录 / 同一个域名**下的第 2、3 条依次降权，然后重排。
        #
        # 🔴 **不能按 item_id 分组** —— 召回和融合都已经按 item_id 去重了，
        #    同一份资料在这里最多出现一次，按它分组的分支永远进不去（死代码）。
        #    分组键必须是**比 item 更粗的粒度**才有意义，所以取父目录 / 域名。
        #
        # 🔴 **必须在排完序之后做**，因为"第几条"这个概念只有在有序列表里才成立。
        #    放到打分循环里做的话，降权多少取决于遍历顺序，同一次搜索跑两遍
        #    结果可能不一样 —— 而且不报错，只是排名偶尔莫名其妙地变。
        #
        # 衰减用 1/(1+k·n)，不用指数：指数衰减到第 4 条基本归零，
        # 而"一个目录里有 4 份真的都相关"是完全正常的情况。
        if weights.diversity > 0 and out:
            seen: dict[str, int] = {}
            adjusted: list[tuple[Candidate, float, dict[str, float]]] = []
            for c, score, parts in out:
                r = rows.get(c.item_id)
                bucket = _diversity_bucket(str(r["locator"]) if r is not None else "")
                n = seen.get(bucket, 0)
                seen[bucket] = n + 1
                if n:
                    factor = 1.0 / (1.0 + weights.diversity * n)
                    parts["diversity"] = round(factor, 4)
                    score *= factor
                adjusted.append((c, score, parts))
            adjusted.sort(key=lambda x: -x[1])
            out = adjusted

        return out

    # ── 对外：一次完整检索 ──────────────────────────────────

    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        weights: dict[str, Any] | None = None,
        preset: str | None = None,
        limit: int = 30,
        offset: int = 0,
        explain: bool = False,
        stage: str = "semantic",
        rerank: bool = False,
        answer: bool = False,
        ask: bool = False,
    ) -> dict[str, Any]:
        """
        stage 控制跑哪几路 —— D2 三级瀑布靠它分次返回：
          keyword  → 只跑关键词和子串（快，15~50ms）
          semantic → 全跑（150ms 级）
        """
        t0 = time.perf_counter()

        # D10：先把查询串里的 type:/date:/size:/in:/tag:/src: 指令拆出来，
        # 剩下的才是真正要拿去检索的词。解析不了的指令原样留在查询词里，
        # 不报错也不吞掉整条查询。
        parsed = parse_query(query)
        text_query = parsed.text
        f = Filters.from_dict(parsed.filters).merged_with(filters)

        # D-adaptive：preset="auto" 是个哨兵值，不在 PRESETS 表里——
        # 用 .get(preset, Weights()) 的话它会静默查不到、退回 balanced，
        # 表面上"能用"，实际上自动档形同虚设，必须在查表之前单独拦一道。
        auto_intent: str | None = None
        if preset == "auto":
            auto_intent, w = classify_intent(text_query)
        elif preset:
            w = PRESETS.get(preset, Weights())
        else:
            w = Weights()
        # 手动调过滑块（weights 非空）的优先级永远最高——用户显式做过的
        # 选择不能被自动分类覆盖，这是"用户可以覆盖自动选择"这条要求的底线
        if weights:
            w = Weights.from_dict(weights)

        # 🔴 **三路召回喂的不是同一个串。**
        #   关键词路：正向词 + `-排除词`（`to_query` 认这个减号，翻成 FTS 的 NOT）
        #   向量路　：只要正向词，连引号都不要
        # 以前三路都喂同一个 `parsed.text`，而排除词就留在里面 ——
        # 嵌入模型只看到"草稿"两个字，于是 `-草稿` 在语义那一路变成了**正向**信号：
        # 用户想排掉草稿，语义召回反而更倾向于把草稿捞回来。不报错，方向正好相反。
        kw_query = parsed.keyword_text()
        sem_query = parsed.semantic_text()
        groups: dict[str, list[Candidate]] = {
            "keyword": self.recall_keyword(kw_query, f),
            "trigram": self.recall_title(kw_query, f),
        }
        if stage == "semantic":
            groups["vector"] = self.recall_vector(sem_query, f)

        # 没有查询词时（不管有没有筛选）都走"按时间列内容"这条路。
        #
        # ⚠️ 第一版写的是 `not text_query and not f.empty` —— 只在**有筛选**时才走。
        #    结果「文件管理器」页面不加任何筛选时三路召回全空，
        #    界面显示"库里还是空的"，而库里其实有 41 条。
        #    空查询 + 空筛选的正确语义是"把所有内容列出来"，不是"什么都没有"。
        if not text_query.strip():
            groups = {"filter": self.recall_by_filter(f, limit=max(limit * 3, 200))}

        fused = self.fuse(groups, w)
        # 排除项要落到**所有**召回路上，不只是关键词那一路。
        # FTS 的 NOT 只管得住 chunks_fts 那条查询；向量路是按相似度捞回来的，
        # 它根本没经过 FTS —— 不在这里补一刀，`-草稿` 对语义结果就是完全无效。
        if parsed.excludes:
            fused = self._drop_excluded(fused, parsed.excludes)
        scored = self.apply_signals(fused, w, text_query)

        # D7 精排：只对**第一页**重排。
        # 交叉编码器没法预计算，每条都要跑一次前向，对第 50 名重排没有意义 ——
        # 用户看的是前几条。翻页时 offset 不为 0，那时候重排会让翻页变慢且顺序跳动，
        # 所以只在首页做。
        reranked = False
        if rerank and stage == "semantic" and offset == 0 and self.reranker is not None:
            scored, reranked = self._rerank(text_query, scored)

        page = scored[offset : offset + limit]

        rows = self.repo.get_items([c.item_id for c, _, _ in page])
        terms = highlight_terms(text_query)

        hits: list[dict[str, Any]] = []
        # 与 hits 一一对应的原始块正文。D8 秒答卡要用它摘句子 ——
        # 不能用 highlight，那个带标记和省略号，摘出来的句子原文里并不存在
        raw_texts: list[str] = []
        for c, score, parts in page:
            r = rows.get(c.item_id)
            if r is None:
                continue
            raw_texts.append(c.best_text or "")
            hit: dict[str, Any] = {
                "item": _row_to_item(r, self.repo.item_tags(c.item_id)),
                "score": round(score, 6),
                "highlight": _highlight(c.best_text, terms),
            }
            if c.extra_hits > 0:
                hit["moreHits"] = {
                    "count": c.extra_hits,
                    "samples": [
                        {
                            "text": _highlight(t, terms),
                            **({"page": pg} if pg is not None else {}),
                            **({"section": sec} if sec else {}),
                        }
                        for t, pg, sec in c.extra_texts
                    ],
                }
            loc: dict[str, Any] = {}
            if c.page is not None:
                loc["page"] = c.page
            if c.start_sec is not None:
                loc["startSec"] = c.start_sec
            if c.section:
                # L3：这一条命中来自论文的哪个章节，界面据此显示
                # "第 3 页 · Method"，用户不用翻开就知道该看哪一段
                loc["section"] = c.section
            if loc:
                hit["location"] = loc

            if explain:
                # 🔴 以前这里不管哪条命中都塞 `terms[:12]`——整条查询词表，
                # 不是"这一条结果里真出现了哪几个词"。同一页 30 条结果，
                # 这个字段会一模一样，等于没告诉用户任何东西。
                # 现在改成真的在这条命中的原文里查一遍。
                hit_text = c.best_text or ""
                matched_terms = [t for t in terms if t in hit_text]
                hit["explain"] = {
                    "scores": {
                        "keyword": round(-(c.bm25 or 0), 4) if c.bm25 is not None else None,
                        "semantic": (
                            round(sim, 4) if (sim := _similarity(c.distance)) is not None else None
                        ),
                        "recency": round(parts.get("recency", 0), 4),
                        "popularity": round(parts.get("popularity", 0), 4),
                        "sourceTrust": round(parts.get("sourceTrust", 0), 4),
                        # 以前这三项 apply_signals() 里明明算出来了，却从没进过
                        # 返回值——用户调多样性/长度惩罚滑块时，界面上完全看不出
                        # 这两个权重到底对这条结果起没起作用
                        "titleBoost": round(parts.get("titleBoost", 0), 4),
                        "lengthPenalty": round(parts.get("lengthPenalty", 0), 4),
                        "diversity": round(parts.get("diversity", 1), 4) if "diversity" in parts else None,
                    },
                    "matchedTerms": matched_terms,
                    "matchedVia": sorted(c.matched_via),
                    # 上面 matchedTerms/highlight 用的 c.best_text 具体来自
                    # 哪个通道——同一条结果可能在 matched_via 里同时挂着
                    # body 和 ocr（关键词路命中了正文块、语义路命中了它的
                    # OCR 块），但界面摘录只可能显示其中一个，徽标/reason
                    # 必须跟这个字段对齐，不能跟着 matchedVia 整个集合走
                    "textChannel": c.best_text_channel or "body",
                    # 命中的是哪几条召回路（关键词/语义/文件名子串），
                    # 跟 matchedVia（命中的是正文/OCR/字幕哪个内容通道）是两个轴，
                    # 以前只在 reason 那句话里含糊带过，没有结构化字段
                    "routes": [
                        r
                        for r, present in (
                            ("keyword", c.rank_keyword is not None),
                            ("vector", c.rank_vector is not None),
                            ("trigram", c.rank_trigram is not None),
                        )
                        if present
                    ],
                    "reason": _reason(c, parts),
                }
            hits.append(hit)

        out: dict[str, Any] = {
            "stage": "reranked" if reranked else stage,
            "final": stage == "semantic",
            "hits": hits,
            "totalEstimate": len(scored),
            "elapsedMs": round((time.perf_counter() - t0) * 1000, 1),
        }
        # 只有真的走了自动档才带这个字段——用户手动选了具体预设时，
        # 前端不该显示"自动识别为……"这种和用户操作对不上的提示
        if auto_intent is not None:
            out["autoIntent"] = auto_intent

        # 这一轮有没有"真的匹配上"的东西。全是弱匹配时要如实告诉用户，
        # 否则他分不清眼前这几条是真结果还是向量凑数凑出来的。
        weak = bool(hits) and not any(_is_strong_match(c) for c, _, _ in page)
        if weak:
            out["weakMatch"] = True

        # D8 秒答卡：只在最终那一轮、且这一轮真的匹配上了才给。
        # 首屏那一波就甩一句"答案"太急了 —— 语义还没跑完，很可能有更对的在后面。
        if answer and stage == "semantic":
            card = answer_mod.build(text_query, hits, weak=weak, texts=raw_texts)
            if card:
                out["answer"] = card

        # A3 Ask 模式：横跨多条结果摘出一份带出处的答案。
        #
        # 和上面那张秒答卡是**两件事**，可以同时开也可以各开各的：
        # 秒答卡是"顺手给一句"，拿不准就不给；Ask 是用户明确在问问题，
        # 答不上也要回一个说明为什么答不上的对象（所以 build 永不返回 None）。
        #
        # 同样只在最终那一轮算 —— keyword 首屏跑 Ask 会拿一批还没排好序的
        # 候选去摘句子，摘出来的东西下一轮就被推翻，界面上表现为
        # "答案闪了一下变成另一个"，比晚 150ms 出现难受得多。
        if ask and stage == "semantic":
            out["ask"] = ask_mod.build(text_query, hits, texts=raw_texts, weak=weak)

        # D9：搜不到、或者只搜到一堆弱匹配的时候，别让用户自己猜哪儿错了。
        # 只在**最终那一轮**算 —— keyword 首屏为空是正常的（语义还没跑完），
        # 那时候弹补救建议会把用户往错误方向带。
        if (not hits or weak) and stage == "semantic":
            out["recovery"] = self._recovery.plan(
                text_query,
                f.to_dict(),
                total_items=int(self.repo.stats().get("items", 0)),
                weak=weak,
            )
        # 界面把这些渲染成一排可点掉的小标签，让用户看见"我刚才那句话被理解成了什么"
        # 🔴 条件里必须带上 excludes/phrases。只看 has_filters 的话，
        #    一条纯 `-草稿` 的查询会**悄悄少掉一大批结果而界面上一个标签都不显示** ——
        #    用户看到结果变少，完全不知道是那个减号干的。
        if parsed.has_filters or parsed.unknown or parsed.excludes or parsed.phrases:
            out["parsedQuery"] = {
                "text": text_query,
                "filters": describe(parsed),
                "unknown": parsed.unknown,
            }
        return out

    # ── D7 精排 ─────────────────────────────────────────────

    def _rerank(
        self, query: str, scored: list[tuple[Candidate, float, dict[str, float]]]
    ) -> tuple[list[tuple[Candidate, float, dict[str, float]]], bool]:
        """
        用交叉编码器给前 MAX_CANDIDATES 条重打分。

        返回 (新顺序, 是否真的重排了)。模型没装/失败时原样返回，
        调用方据此决定 stage 报 'semantic' 还是 'reranked' ——
        **不能谎报 reranked**，界面上那个标签是给用户看"这次用了精排"的。
        """
        from ..analyze.reranker import MAX_CANDIDATES, MAX_DOC_CHARS

        head = scored[:MAX_CANDIDATES]
        tail = scored[MAX_CANDIDATES:]
        if len(head) < 2:
            return scored, False   # 一条没得排

        docs: list[str] = []
        for c, _, _ in head:
            # 喂给交叉编码器的应该是**命中的那段正文**，不是标题 ——
            # 标题太短，交叉注意力没东西可看，重排会退化成随机扰动
            t = (c.best_text or "").strip()
            docs.append(t[:MAX_DOC_CHARS] if t else "")

        scores = self.reranker.score(query, docs)
        if scores is None or len(scores) != len(head):
            return scored, False

        # 保留原来的 parts（可解释面板要用），只把总分换成精排分。
        # 精排分之间的差距远大于 RRF 分，直接用它排序即可。
        new_head = [
            (c, float(s), {**parts, "rerank": round(float(s), 4)})
            for (c, _, parts), s in zip(head, scores)
        ]
        new_head.sort(key=lambda x: -x[1])
        return new_head + tail, True

    # ── D3 跨模态互搜 ───────────────────────────────────────

    def search_by_image(
        self,
        image_vector: np.ndarray,
        *,
        limit: int = 30,
        include_scenes: bool = True,
        exclude_item: str = "",
    ) -> dict[str, Any]:
        """
        用一张图去搜：既搜库里的图片，也搜视频里的**镜头**。

        视频那一路是这个功能最有意思的地方 —— 拿一张截图丢进来，
        它能告诉你"这个画面出现在某个视频的第 3 分 24 秒"。
        图片向量和场景关键帧向量用的是同一个 CLIP 模型，
        所以两者在同一个语义空间里，可以直接比距离、直接混排。
        """
        t0 = time.perf_counter()
        conn = self.db.connect()
        blob = sqlite_vec.serialize_float32(list(image_vector))

        hits: list[dict[str, Any]] = []

        # ① 库里的图片
        try:
            rows = conn.execute(
                "SELECT item_rowid, distance FROM vec_items "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (blob, limit * 2),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        if rows:
            rowids = [int(r["item_rowid"]) for r in rows]
            dist = {int(r["item_rowid"]): float(r["distance"]) for r in rows}
            marks = ",".join("?" * len(rowids))
            detail = conn.execute(
                f"SELECT rowid, id FROM items WHERE rowid IN ({marks})", rowids
            ).fetchall()
            by_rowid = {int(r["rowid"]): str(r["id"]) for r in detail}
            for rid in rowids:
                iid = by_rowid.get(rid)
                if not iid or iid == exclude_item:
                    continue
                hits.append({"itemId": iid, "distance": dist[rid], "kind": "image"})

        # ② 视频里的镜头
        if include_scenes:
            try:
                srows = conn.execute(
                    "SELECT vec_rowid, distance FROM vec_scenes "
                    "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (blob, limit * 2),
                ).fetchall()
            except sqlite3.OperationalError:
                srows = []

            if srows:
                vrowids = [int(r["vec_rowid"]) for r in srows]
                sdist = {int(r["vec_rowid"]): float(r["distance"]) for r in srows}
                marks = ",".join("?" * len(vrowids))
                smap = conn.execute(
                    f"SELECT m.vec_rowid, m.item_id, m.scene_index, "
                    f"       s.start_sec, s.end_sec, s.keyframe_path, s.transcript "
                    f"FROM scene_vec_map m "
                    f"JOIN video_scenes s ON s.item_id = m.item_id "
                    f"                   AND s.scene_index = m.scene_index "
                    f"WHERE m.vec_rowid IN ({marks})",
                    vrowids,
                ).fetchall()
                for r in smap:
                    iid = str(r["item_id"])
                    if iid == exclude_item:
                        continue
                    hits.append({
                        "itemId": iid,
                        "distance": sdist[int(r["vec_rowid"])],
                        "kind": "scene",
                        "sceneIndex": int(r["scene_index"]),
                        "startSec": float(r["start_sec"]),
                        "endSec": float(r["end_sec"]),
                        "keyframePath": r["keyframe_path"],
                        "transcript": r["transcript"] or "",
                    })

        # 混排：图片和镜头在同一个语义空间，直接按距离排
        hits.sort(key=lambda h: h["distance"])

        # 同一个视频只保留最相似的那一个镜头 ——
        # 不去重的话一个视频会用它的 20 个镜头霸占整页结果
        seen_video: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for h in hits:
            if h["kind"] == "scene":
                if h["itemId"] in seen_video:
                    continue
                seen_video.add(h["itemId"])
            deduped.append(h)
            if len(deduped) >= limit:
                break

        rows2 = self.repo.get_items([h["itemId"] for h in deduped])
        out: list[dict[str, Any]] = []
        for h in deduped:
            r = rows2.get(h["itemId"])
            if r is None:
                continue
            # L2 距离转相似度：和文本那边共用同一个换算（_similarity），
            # vec_items 用的是同一套默认 vec0 度量（未开方 L2），同一条 bug 同一条修法
            score = max(0.0, _similarity(h["distance"]) or 0.0)
            item = {
                "item": _row_to_item(r, self.repo.item_tags(h["itemId"])),
                "score": round(score, 6),
            }
            if h["kind"] == "scene":
                item["location"] = {"startSec": h["startSec"], "endSec": h["endSec"]}
                item["highlight"] = (h.get("transcript") or "")[:160]
                item["sceneKeyframe"] = h.get("keyframePath")
            out.append(item)

        return {
            "stage": "semantic",
            "final": True,
            "hits": out,
            "totalEstimate": len(out),
            "elapsedMs": round((time.perf_counter() - t0) * 1000, 1),
            "mode": "by-image",
        }

    def recall_by_filter(self, f: Filters, limit: int = 90) -> list[Candidate]:
        """光有筛选没有查询词时，按时间倒序列出符合条件的内容。"""
        conn = self.db.connect()
        where, args = f.sql("i")
        rows = conn.execute(
            f"SELECT i.id AS item_id FROM items i "
            f"{'WHERE ' + where if where else ''} "
            f"ORDER BY COALESCE(i.content_time, i.created_at) DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        out: list[Candidate] = []
        for rank, r in enumerate(rows, start=1):
            c = Candidate(item_id=str(r["item_id"]), rank_keyword=rank)
            c.matched_via.add("filter")
            out.append(c)
        return out


# ── 辅助 ────────────────────────────────────────────────────


def _row_to_item(r: sqlite3.Row, tags: list[str]) -> dict[str, Any]:
    import json as _json

    meta = None
    if r["meta_json"]:
        try:
            meta = _json.loads(str(r["meta_json"]))
        except _json.JSONDecodeError:
            meta = None
    return {
        "id": r["id"],
        "fingerprint": r["fingerprint"],
        "modality": r["modality"],
        "source": r["source"],
        "status": r["status"],
        "title": r["title"],
        "locator": r["locator"],
        "snippet": r["snippet"],
        "mime": r["mime"],
        "sizeBytes": r["size_bytes"],
        "contentTime": r["content_time"],
        "createdAt": r["created_at"],
        "updatedAt": r["updated_at"],
        "lastOpenedAt": r["last_opened_at"],
        "openCount": r["open_count"],
        "tags": tags,
        "thumbPath": r["thumb_path"],
        "meta": meta,
    }


def _highlight(text: str, terms: list[str], window: int = 160) -> str:
    """
    截一段包含命中词的片段并打标。

    截取窗口以第一个命中词为中心 —— 从头截的话，
    命中词在第 800 字的文档，用户在摘要里看不到任何相关内容。
    """
    if not text:
        return ""
    if not terms:
        return text[:window] + ("…" if len(text) > window else "")

    pos = -1
    for t in terms:
        if len(t) < 2:
            continue
        p = text.find(t)
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        pos = 0

    start = max(0, pos - window // 3)
    snippet = text[start : start + window]
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + window < len(text) else ""

    escaped_terms = [re.escape(t) for t in terms if len(t) >= 2]
    if not escaped_terms:
        return prefix + html.escape(snippet) + suffix

    # 先在原文（未转义）上匹配命中词，再把两侧的普通文本分别做 HTML 转义——
    # 顺序不能反：文档正文可能带 <img>/<style> 等标签，如果不转义就直接拼进
    # __html，会在界面里原样渲染成真实 DOM（存储型 XSS/界面伪装）。
    pattern = re.compile(f"({'|'.join(escaped_terms)})")
    parts: list[str] = []
    last = 0
    for m in pattern.finditer(snippet):
        parts.append(html.escape(snippet[last : m.start()]))
        parts.append(f"<em>{html.escape(m.group(0))}</em>")
        last = m.end()
    parts.append(html.escape(snippet[last:]))
    return prefix + "".join(parts) + suffix


#: 内容通道 → 人话。跟 recall_vector/recall_keyword 里写进 matched_via 的值一一对应。
#: 不含 "title"——那个由下面的 titleBoost 分支单独覆盖，两条都放会说成
#: "在标题里命中；标题命中"这种重复话
_CHANNEL_LABEL = {
    "ocr": "图片文字里",
    "transcript": "语音字幕里",
    "description": "图片描述里",
}


def _reason(c: Candidate, parts: dict[str, float]) -> str:
    """一句人话解释为什么这条能排上来。"""
    bits: list[str] = []
    if c.rank_keyword:
        bits.append(f"关键词第 {c.rank_keyword} 名")
    if c.rank_vector:
        sim = _similarity(c.distance) or 0.0
        bits.append(f"语义相似 {sim:.2f}")
    if c.rank_trigram:
        bits.append("文件名含查询串")
    # 🔴 按 best_text_channel（摘录实际来自哪个通道）判断，不能按
    # matched_via（这条结果命中过的所有通道的并集）判断——同一个 item
    # 可能关键词路命中正文、语义路命中它的 OCR 块，matched_via 会同时有
    # body 和 ocr，但界面摘录只显示得出其中一个，说错通道就是"人话解释"
    # 和"眼前这段摘录"对不上
    label = _CHANNEL_LABEL.get(c.best_text_channel)
    if label:
        bits.append(f"在{label}命中")
    if parts.get("titleBoost", 0) > 0:
        bits.append("标题命中")
    if parts.get("recency", 0) > 0.6:
        bits.append("内容较新")
    if parts.get("popularity", 0) > 0.5:
        bits.append("你常打开")
    return "；".join(bits) or "综合相关"
