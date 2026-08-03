"""
B5 —— 搜索意图分流
====================================================================
判一句查询到底是在「找定义 / 找教程 / 找论文 / 找新闻 / 找代码 / 找产品」，
然后**自动换一套引擎阵容和定向预设**。

**为什么值得做**：同一个词在不同意图下该问的地方完全不同。
「transformer 是什么」问维基和官方文档最快；「transformer 最新进展」
问 arXiv 和 Semantic Scholar；「transformer pytorch 实现」应该去 GitHub。
现在这三句话走的是同一套阵容，等于每次都有三分之二的引擎在做无用功——
它们不是搜不到，是**搜到的东西根本不是这次要的**。

🔴 **纯规则，不上模型**：意图判断要在 1ms 内出结果，任何模型调用都会顶在
X2「联网快搜 P95 ≤3.0s」上。而且判错的代价是「阵容选得不够好」，
不是「结果错了」—— 这种代价下花几百毫秒换准确率是亏的。

🔴 **判不出来就是 `general`，不硬猜**。硬猜的后果是用户搜一个普通问题
却被塞进学术源，返回一堆论文摘要，还找不到为什么。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 六种意图。刻意没有更细的分类 —— 分到十几种以后，每种之间的
#: 边界都要靠猜，而阵容其实只有这几套，再细分也落不到不同的动作上
INTENTS = ("definition", "howto", "scholar", "news", "code", "product", "general")

#: 每种意图的判据。**顺序即优先级**：一句话同时命中多种时取第一个匹配的，
#: 因为「怎么用 pytorch 实现 transformer」既是 howto 又是 code，
#: 而用户真正要的是代码，所以 code 排在 howto 前面
_RULES: list[tuple[str, list[str]]] = [
    ("code", [
        r"github", r"源码", r"源代码", r"代码实现", r"repo\b", r"仓库",
        r"\bnpm\b", r"\bpip install\b", r"\bapi\s*(文档|reference)",
        r"报错", r"traceback", r"stack\s*overflow", r"怎么写", r"示例代码",
        r"\b(sdk|cli)\b", r"how to (implement|code)",
    ]),
    ("scholar", [
        r"论文", r"文献", r"综述", r"研究进展", r"\barxiv\b", r"\bdoi\b",
        r"被引", r"期刊", r"\bpaper[s]?\b", r"survey", r"state[- ]of[- ]the[- ]art",
        r"\bsota\b", r"实验结果", r"数据集", r"benchmark",
    ]),
    ("news", [
        r"最新", r"今天", r"昨天", r"最近", r"新闻", r"发布会", r"上市",
        r"官宣", r"爆料", r"进展如何", r"\b202\d\b", r"latest", r"breaking",
        r"announce", r"发布了",
    ]),
    ("howto", [
        r"怎么", r"如何", r"教程", r"步骤", r"安装", r"配置", r"设置",
        r"入门", r"上手", r"\bhow to\b", r"tutorial", r"guide", r"解决",
        r"修复", r"排查",
    ]),
    ("definition", [
        r"是什么", r"什么是", r"定义", r"含义", r"概念", r"原理",
        r"区别", r"和.{1,12}的?区别", r"\bvs\b", r"对比",
        r"what is\b", r"meaning of", r"difference between",
    ]),
    ("product", [
        r"多少钱", r"价格", r"报价", r"哪个好", r"推荐", r"评测",
        r"参数", r"配置单", r"买", r"优惠", r"\breview\b", r"\bprice\b",
    ]),
]

_COMPILED: list[tuple[str, list[re.Pattern[str]]]] = [
    (name, [re.compile(p, re.I) for p in pats]) for name, pats in _RULES
]

#: 每种意图对应的动作。`engines` 是**加权偏好而不是硬名单** ——
#: 硬名单会在那几家全挂掉时直接搜不到东西，而意图分流本来只是想让
#: 结果更对路，不该变成一个新的单点故障
_PLAN: dict[str, dict[str, Any]] = {
    "definition": {
        "engines": ["wikipedia", "bing", "mojeek", "searxng"],
        "preset": None,
        "limit_boost": 0,
        "why": "找定义：维基和通用引擎的前几条最直接，不需要广撒网",
    },
    "howto": {
        "engines": ["bing", "baidu", "so360", "searxng"],
        "preset": None,
        "limit_boost": 5,
        "why": "找教程：中文引擎的实操内容更多，多要几条便于挑步骤最清楚的那篇",
    },
    "scholar": {
        "engines": ["openalex", "semanticscholar", "arxiv", "crossref", "europepmc"],
        "preset": "academic",
        "limit_boost": 10,
        "why": "找论文：直接走学术源，通用引擎搜论文只会给到二手转述",
    },
    "news": {
        "engines": ["bing", "so360", "baidu", "searxng"],
        "preset": None,
        "limit_boost": 8,
        "time_range": "week",
        "why": "找新闻：限定一周内，并多要几条以便看出哪些是互相转载的",
    },
    "code": {
        "engines": ["bing", "mojeek", "searxng"],
        "preset": "github",
        "limit_boost": 0,
        "why": "找代码：锁定 GitHub 与官方文档，避开内容农场的翻译搬运",
    },
    "product": {
        "engines": ["bing", "baidu", "so360"],
        "preset": None,
        "limit_boost": 5,
        "why": "找产品：中文引擎覆盖更好，但要留神这一类里软文比例最高",
    },
    "general": {
        "engines": [],
        "preset": None,
        "limit_boost": 0,
        "why": "判不出明确意图，保持默认阵容不动",
    },
}


@dataclass
class Intent:
    """一次意图判定的结果。`confidence` 只有三档，不给假精度。"""

    kind: str = "general"
    confidence: float = 0.0
    hits: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    preset: str | None = None
    time_range: str | None = None
    limit_boost: int = 0
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "confidence": round(self.confidence, 2),
            "hits": self.hits,
            "engines": self.engines,
            "preset": self.preset,
            "timeRange": self.time_range,
            "limitBoost": self.limit_boost,
            "why": self.why,
        }


def detect(query: str) -> Intent:
    """
    判一句查询的意图。**永不抛异常，永远返回一个可用的 Intent** ——
    它挂在搜索主路径上，判失败就该安静退回 general，而不是让整次搜索失败。
    """
    q = (query or "").strip()
    if not q:
        return Intent(why="空查询")

    for name, pats in _COMPILED:
        hits = [p.pattern for p in pats if p.search(q)]
        if not hits:
            continue
        # 只有三档置信度：命中 1 条=0.5，2 条=0.75，3 条以上=0.9。
        # 不做更细的分数——判据本身就是拍脑袋定的关键词，
        # 给出 0.83 这种数字是在假装精度
        conf = 0.5 if len(hits) == 1 else (0.75 if len(hits) == 2 else 0.9)
        plan = _PLAN[name]
        return Intent(
            kind=name,
            confidence=conf,
            hits=hits[:4],
            engines=list(plan["engines"]),
            preset=plan.get("preset"),
            time_range=plan.get("time_range"),
            limit_boost=int(plan.get("limit_boost") or 0),
            why=str(plan.get("why") or ""),
        )

    plan = _PLAN["general"]
    return Intent(kind="general", confidence=0.0, why=str(plan["why"]))


def apply_intent(
    intent: Intent,
    *,
    engines: list[str] | None,
    limit: int,
    preset: str | None,
    time_range: str | None,
) -> tuple[list[str] | None, int, str | None, str | None]:
    """
    把意图落到实际参数上，返回 `(engines, limit, preset, time_range)`。

    🔴 **用户显式给了的一律不覆盖**。意图分流是"你没说时我帮你选"，
    不是"我比你更懂你要什么"。用户点名了 google 却被我换成 arXiv，
    是个他完全无法理解、也无从关掉的行为。
    """
    out_engines = engines
    if engines is None and intent.engines:
        out_engines = list(intent.engines)
    out_limit = limit + (intent.limit_boost if intent.kind != "general" else 0)
    out_preset = preset if preset is not None else intent.preset
    out_range = time_range if time_range is not None else intent.time_range
    return out_engines, max(1, min(100, out_limit)), out_preset, out_range


def describe() -> list[dict[str, Any]]:
    """给设置界面用：六种意图各自会做什么，让用户能看懂再决定关不关。"""
    return [
        {
            "kind": k,
            "engines": v["engines"],
            "preset": v.get("preset"),
            "timeRange": v.get("time_range"),
            "why": v["why"],
        }
        for k, v in _PLAN.items()
        if k != "general"
    ]
