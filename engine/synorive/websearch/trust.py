"""
可信度评估 —— R1 / R2 / R3 / R4 / R6 / R10 / R11
====================================================================
🔴 **先把能力边界说清楚，这一节不是免责声明，是设计约束的来源：**

这个模块**判断不了一段话是不是事实**。它没有外部知识库，没有生成模型，
它看到的只有：域名、发布时间、正文的统计特征、以及同一说法被几个
**互相独立**的来源提到过。

所以它能可靠识别的是这四类：
  · 内容农场（模板化、整段转载、无作者无日期、关键词堆砌）
  · 孤证（只有一个来源说，且那个来源信誉不高）
  · 过期信息（三年前的教程讲的是已经改掉的做法）
  · 已知的虚假信息站点（黑名单）

**识别不了的**：一篇写得像模像样、格式规范、来源体面，但事实本身就是错的内容。
那需要拿外部权威知识去比对，本地做不到。**所以默认只标注不删除**——
用户选的就是这一档。自动隐藏只留给「已知虚假站」和「纯内容农场」两类，
且一律进「已排除」抽屉（R11），随时能看为什么被排除、能一键放回。

**为什么不给一个 0~100 的总分就完事**：单一分数会把"没有作者署名"和
"这个站专门发假消息"压成同一个数字，用户看到 42 分完全不知道该担心什么。
所以对外给的是**分项 + 标签**，总分只用来排序。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse


class Tier(str, Enum):
    """来源分级（R1）。六档，用户可在设置里增删域名。"""

    OFFICIAL = "official"      # 政府、标准组织、企业官网、官方文档
    ACADEMIC = "academic"      # 期刊、预印本、大学、学会
    MAINSTREAM = "mainstream"  # 主流媒体、大型技术社区
    COMMUNITY = "community"    # 论坛、问答、个人博客
    UNKNOWN = "unknown"        # 没见过的域名 —— 不是贬义，只是没有先验
    LOW = "low"                # 内容农场、聚合站、已知虚假信息站

    @property
    def weight(self) -> float:
        return {
            Tier.OFFICIAL: 1.00,
            Tier.ACADEMIC: 0.95,
            Tier.MAINSTREAM: 0.75,
            Tier.COMMUNITY: 0.55,
            Tier.UNKNOWN: 0.45,
            Tier.LOW: 0.12,
        }[self]

    @property
    def label(self) -> str:
        return {
            Tier.OFFICIAL: "官方",
            Tier.ACADEMIC: "学术",
            Tier.MAINSTREAM: "主流媒体",
            Tier.COMMUNITY: "社区/个人",
            Tier.UNKNOWN: "未收录",
            Tier.LOW: "低信誉",
        }[self]


#: 后缀级规则 —— 比逐个列域名可靠得多，也不用维护
_SUFFIX_TIERS: list[tuple[re.Pattern[str], Tier]] = [
    (re.compile(r"(^|\.)(gov|mil)(\.[a-z]{2})?$"), Tier.OFFICIAL),
    (re.compile(r"(^|\.)gov\.[a-z]{2}$"), Tier.OFFICIAL),
    (re.compile(r"(^|\.)(edu|ac)(\.[a-z]{2})?$"), Tier.ACADEMIC),
]

#: 域名级规则。**刻意只列少量高确定性的**——
#: 一份几千条的域名榜单维护不动，而且很快会过期变成误导
_DOMAIN_TIERS: dict[str, Tier] = {
    # 学术
    "arxiv.org": Tier.ACADEMIC, "doi.org": Tier.ACADEMIC,
    "nature.com": Tier.ACADEMIC, "science.org": Tier.ACADEMIC,
    "sciencedirect.com": Tier.ACADEMIC, "springer.com": Tier.ACADEMIC,
    "ieee.org": Tier.ACADEMIC, "acm.org": Tier.ACADEMIC,
    "pubmed.ncbi.nlm.nih.gov": Tier.ACADEMIC, "ncbi.nlm.nih.gov": Tier.ACADEMIC,
    "semanticscholar.org": Tier.ACADEMIC, "openalex.org": Tier.ACADEMIC,
    "crossref.org": Tier.ACADEMIC, "biorxiv.org": Tier.ACADEMIC,
    # 官方/标准
    "w3.org": Tier.OFFICIAL, "ietf.org": Tier.OFFICIAL, "iso.org": Tier.OFFICIAL,
    "python.org": Tier.OFFICIAL, "docs.python.org": Tier.OFFICIAL,
    "developer.mozilla.org": Tier.OFFICIAL, "microsoft.com": Tier.OFFICIAL,
    "learn.microsoft.com": Tier.OFFICIAL, "docs.oracle.com": Tier.OFFICIAL,
    "kernel.org": Tier.OFFICIAL, "postgresql.org": Tier.OFFICIAL,
    "sqlite.org": Tier.OFFICIAL, "who.int": Tier.OFFICIAL,
    # 主流
    "wikipedia.org": Tier.MAINSTREAM, "reuters.com": Tier.MAINSTREAM,
    "apnews.com": Tier.MAINSTREAM, "bbc.com": Tier.MAINSTREAM,
    "xinhuanet.com": Tier.MAINSTREAM, "people.com.cn": Tier.MAINSTREAM,
    "thepaper.cn": Tier.MAINSTREAM, "caixin.com": Tier.MAINSTREAM,
    "github.com": Tier.MAINSTREAM, "stackoverflow.com": Tier.MAINSTREAM,
    # 社区
    "zhihu.com": Tier.COMMUNITY, "juejin.cn": Tier.COMMUNITY,
    "segmentfault.com": Tier.COMMUNITY, "v2ex.com": Tier.COMMUNITY,
    "reddit.com": Tier.COMMUNITY, "medium.com": Tier.COMMUNITY,
    "jianshu.com": Tier.COMMUNITY, "cnblogs.com": Tier.COMMUNITY,
    "bilibili.com": Tier.COMMUNITY, "weibo.com": Tier.COMMUNITY,
}

#: 低信誉：内容农场与聚合抄袭站。
#: 这一档会触发自动隐藏，所以**只放确定性极高的**，宁可漏也不冤枉。
#: 用户可在设置里增删；被隐藏的一律进「已排除」抽屉可放回
_LOW_TRUST: set[str] = {
    "csdn.net",          # 大量机器转载与标题党，原创混在里面，所以只降权不拉黑
}

#: 完全屏蔽（自动隐藏，仍进抽屉）。默认空 —— 由用户自己往里加。
#: **不预置任何"已知虚假站"名单**：那种名单带很强的立场，
#: 预置进一个本地工具里等于替用户做了政治判断，这不是我该做的
_BLOCKLIST: set[str] = set()


@dataclass
class TrustSignals:
    """一条结果的可信度分项。**对外展示的是这些，不是那个总分。**"""

    tier: Tier = Tier.UNKNOWN
    #: 有多少个**互相独立**的来源提到同一件事（R2）
    independent_sources: int = 1
    #: 内容农场特征命中了哪几条（R3）
    farm_flags: list[str] = field(default_factory=list)
    #: 发布距今多少天。抽不出来是 None —— **不猜**
    age_days: int | None = None
    #: 疑似机器批量生成（R6）
    ai_suspect: bool = False
    #: V3：具体命中了哪几条判据。**光说"疑似 AI"用户没法判断要不要信** ——
    #: 「同一篇出现在 5 个域名」和「套话多」是完全不同的两种可疑
    ai_flags: list[str] = field(default_factory=list)
    #: 一句人话，直接显示给用户
    reasons: list[str] = field(default_factory=list)
    score: float = 0.5
    #: 是否建议自动隐藏。上层按用户设置决定要不要真隐藏
    hide: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "tier": self.tier.value,
            "tierLabel": self.tier.label,
            "score": round(self.score, 3),
            "independentSources": self.independent_sources,
            "reasons": self.reasons,
        }
        if self.farm_flags:
            d["farmFlags"] = self.farm_flags
        if self.age_days is not None:
            d["ageDays"] = self.age_days
        if self.ai_suspect:
            d["aiSuspect"] = True
            d["aiFlags"] = self.ai_flags
        if self.hide:
            d["hide"] = True
        return d


def classify_domain(
    url_or_site: str,
    *,
    overrides: dict[str, str] | None = None,
) -> Tier:
    """域名 → 分级（R1）。用户的 overrides 优先于内置规则。"""
    host = _host(url_or_site)
    if not host:
        return Tier.UNKNOWN

    for cand in _domain_candidates(host):
        if overrides and cand in overrides:
            try:
                return Tier(overrides[cand])
            except ValueError:
                pass
        if cand in _BLOCKLIST or cand in _LOW_TRUST:
            return Tier.LOW
        if cand in _DOMAIN_TIERS:
            return _DOMAIN_TIERS[cand]

    for pat, tier in _SUFFIX_TIERS:
        if pat.search(host):
            return tier
    return Tier.UNKNOWN


def _host(url_or_site: str) -> str:
    s = (url_or_site or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        try:
            s = urlparse(s).netloc
        except ValueError:
            return ""
    host = s.split("@")[-1].split("/")[0].split(":")[0]
    # 注意不能写 lstrip("www.") —— 那是按**字符集**剥，
    # "world.com" 会被啃成 "orld.com"。必须按前缀剥
    return host[4:] if host.startswith("www.") else host


def _domain_candidates(host: str) -> list[str]:
    """example.blog.csdn.net → [example.blog.csdn.net, blog.csdn.net, csdn.net]"""
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


# ────────────────────────────────────────────────────────────────
# R3 内容农场判据
# ────────────────────────────────────────────────────────────────
#: 标题党/农场常见句式。命中一条不算什么，命中多条才有意义
_FARM_TITLE = re.compile(
    r"(震惊|速看|删前|必看|太可怕|不看后悔|涨知识|收藏了|万万没想到|"
    r"最全总结|一文看懂|全网最|建议收藏|干货满满)"
)
#: 正文里的转载/采集痕迹
_REPOST_MARK = re.compile(
    r"(本文(转载|转自|来源)|声明[:：]?\s*本文|版权归原作者所有|"
    r"侵删|如有侵权.{0,12}联系|文章来源[:：]|原文链接[:：])"
)


def farm_flags(title: str, snippet: str, *, site: str = "") -> list[str]:
    """
    内容农场特征。返回命中的判据名 —— **返回原因而不是分数**，
    因为用户要能看懂"它为什么被降权"，一个数字说明不了任何事。
    """
    flags: list[str] = []
    t = title or ""
    s = snippet or ""

    if _FARM_TITLE.search(t):
        flags.append("标题党句式")
    if _REPOST_MARK.search(s):
        flags.append("正文带转载声明")
    # 关键词堆砌：标题里同一个词反复出现
    words = re.findall(r"[一-鿿]{2,6}|[a-zA-Z]{3,}", t)
    if words:
        top = max(words.count(w) for w in set(words))
        if top >= 3:
            flags.append("标题关键词堆砌")
    # 摘要几乎就是标题重复一遍 —— 采集站的典型特征
    if s and t and len(s) < 80 and t[:20] and t[:20] in s:
        flags.append("摘要是标题的复读")
    # 分隔符堆成一串（「XX_YY_ZZ_全网最全_2024」这种拼接标题）
    if len(re.findall(r"[_|｜\-–—]", t)) >= 4:
        flags.append("标题为关键词拼接")
    return flags


# ────────────────────────────────────────────────────────────────
# R6 疑似机器批量生成
# ────────────────────────────────────────────────────────────────
_AI_TELLS = re.compile(
    r"(在当今[^，。]{0,8}(社会|时代|背景下)|随着[^，。]{0,10}的(不断)?发展|"
    r"综上所述|总而言之|首先.{0,30}其次.{0,30}最后|"
    r"值得注意的是|需要注意的是|在这个[^，。]{0,6}的时代)"
)
#: V3 新增：英文侧的同类套话。中文判据对英文内容完全失效，
#: 而联网搜索里英文结果占比很高 —— 只做中文等于对一半内容没有判据
_AI_TELLS_EN = re.compile(
    r"(in today'?s (fast-paced|digital|modern) world|it'?s important to note that|"
    r"in conclusion,|delve into|it is worth noting|plays a (crucial|vital|key) role|"
    r"in the ever-evolving|unlock the (power|potential)|game-?changer)",
    re.IGNORECASE,
)


def ai_flags(title: str, snippet: str, *, cluster: dict[str, Any] | None = None) -> list[str]:
    """
    疑似机器批量生成的判据清单（V3）。**返回命中的原因，不返回一个布尔**。

    原来只有一条判据（中文套话 + 无具体信息），漏得厉害。加了三条：

      · 英文套话 —— 原判据是纯中文的，对英文内容等于没有判据
      · 多站同文 —— 同一段摘要出现在多个不同域名。真人写的文章不会
        逐字一样；这个特征**只有在折叠之后才看得到**（要 variants），
        单看一条结果永远发现不了，这也是为什么它必须在这一层做
      · 没有任何出处 —— 通篇没有链接、没有引用、没有作者署名

    判据依然保守：**单条命中不下结论**，`ai_suspect()` 要求至少两条。
    宁可漏也不冤枉 —— 把一篇真人写的文章标成"机器生成"，
    用户会因此忽略掉正确的信息，那比漏标一篇 AI 文糟得多。
    """
    s = f"{title or ''}。{snippet or ''}"
    flags: list[str] = []
    if len(s) < 40:
        return flags

    tells = len(_AI_TELLS.findall(s)) + len(_AI_TELLS_EN.findall(s))
    concrete = len(re.findall(r"\d{2,}|[A-Z][a-zA-Z]{2,}|\d+%|v\d+\.\d+", s))
    if tells >= 2 and concrete == 0:
        flags.append("套话密集且没有任何具体信息")
    elif tells >= 3:
        flags.append("套话句式密集")

    if cluster:
        # 多站同文：折叠后 alsoAt 里有多个不同域名，而摘要几乎一样
        also = cluster.get("alsoAt") or []
        hosts = {_host(u) for u in also if u}
        hosts.discard("")
        if len(hosts) >= 3:
            flags.append(f"同一篇内容出现在 {len(hosts) + 1} 个不同域名（批量转载）")
    return flags


def ai_suspect(title: str, snippet: str, *, cluster: dict[str, Any] | None = None) -> bool:
    """兼容旧调用：命中任一强判据即为疑似。判据明细走 `ai_flags()`。"""
    return bool(ai_flags(title, snippet, cluster=cluster))


# ────────────────────────────────────────────────────────────────
# R4 时效
# ────────────────────────────────────────────────────────────────
def age_days(published: str | None, *, now: datetime | None = None) -> int | None:
    """发布距今天数。解析不出来返回 None —— **绝不用"今天"顶替**。"""
    if not published:
        return None
    s = str(published).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s[: len(fmt) + 6], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            delta = (now or datetime.now(UTC)) - dt
            return max(0, delta.days)
        except ValueError:
            continue
    m = re.match(r"(\d{4})[-/年](\d{1,2})", s)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=UTC)
            return max(0, ((now or datetime.now(UTC)) - dt).days)
        except ValueError:
            return None
    return None


# ────────────────────────────────────────────────────────────────
# 汇总
# ────────────────────────────────────────────────────────────────
@dataclass
class TrustProfile:
    """
    可信度模型的可调参数（V5）。

    **为什么把这些拧出来给用户**：这套权重里没有一个"客观正确"的值。
    查学术问题的人希望官方文档和论文压倒一切；查产品体验的人恰恰
    需要社区和个人博客，把它们压到 0.55 等于把最有用的资料排到后面。
    我定的默认值只是一个中位取舍，不是真理。

    **但有两条不给拧**：① 自动隐藏的两类（黑名单 / 三条以上农场特征叠加）
    ② 「已排除」抽屉必须存在。那两条是 R11 的底线 ——
    可以调整什么算可疑，不能取消"被排除的东西要能看见"。
    """

    #: 六档来源权重。缺的档沿用 `Tier.weight` 的默认
    tier_weights: dict[str, float] = field(default_factory=dict)
    #: 多来源印证的加分（≥3 个独立站点时）
    multi_source_bonus: float = 0.15
    #: 孤证扣分
    lone_source_penalty: float = 0.08
    #: 农场特征每条的扣分基数（实际按 0.10·n^1.4 递增，这里是系数）
    farm_penalty: float = 0.10
    #: 疑似机器生成的扣分
    ai_penalty: float = 0.12
    #: 超过多少天算过时
    stale_days: int = 1095
    stale_penalty: float = 0.10
    #: 可信度在最终排序里占的比重（剩下的是相关性）
    rank_weight: float = 0.35
    #: 用户自定义的域名分级覆盖 {域名: Tier 值}
    overrides: dict[str, str] = field(default_factory=dict)
    #: 用户自定义的完全屏蔽名单
    blocklist: list[str] = field(default_factory=list)

    def weight_of(self, tier: Tier) -> float:
        v = self.tier_weights.get(tier.value)
        return float(v) if v is not None else tier.weight

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> TrustProfile:
        """从设置页传来的字典构造。**认不出的字段一律忽略，不报错** ——
        设置结构以后会变，旧版本的配置文件不该让搜索直接崩。"""
        d = d or {}
        p = cls()
        for f_name in (
            "multi_source_bonus", "lone_source_penalty", "farm_penalty",
            "ai_penalty", "stale_penalty", "rank_weight",
        ):
            camel = _camel(f_name)
            if isinstance(d.get(camel), (int, float)):
                setattr(p, f_name, max(0.0, min(1.0, float(d[camel]))))
        if isinstance(d.get("staleDays"), int):
            p.stale_days = max(30, int(d["staleDays"]))
        if isinstance(d.get("tierWeights"), dict):
            p.tier_weights = {
                str(k): max(0.0, min(1.0, float(v)))
                for k, v in d["tierWeights"].items()
                if isinstance(v, (int, float))
            }
        if isinstance(d.get("overrides"), dict):
            p.overrides = {str(k).lower(): str(v) for k, v in d["overrides"].items()}
        if isinstance(d.get("blocklist"), list):
            p.blocklist = [str(x).lower() for x in d["blocklist"] if x]
        return p


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


DEFAULT_PROFILE = TrustProfile()


def evaluate(
    cluster: dict[str, Any],
    *,
    overrides: dict[str, str] | None = None,
    stale_days: int | None = None,
    profile: TrustProfile | None = None,
) -> TrustSignals:
    """
    给一条（已折叠的）结果算可信度分项。

    `cluster` 用 `meta.Cluster.to_dict()` 的形状，这样这一层不依赖检索层的类，
    单测可以直接喂字典。

    `profile` 是 V5 的可调权重；`overrides`/`stale_days` 保留是为了
    老调用点不用改 —— 两者都给时以显式参数优先。
    """
    p = profile or DEFAULT_PROFILE
    ov = {**p.overrides, **(overrides or {})}
    stale = stale_days if stale_days is not None else p.stale_days

    title = str(cluster.get("title") or "")
    snippet = str(cluster.get("snippet") or "")
    url = str(cluster.get("url") or "")
    site = str(cluster.get("site") or "")

    sig = TrustSignals()
    sig.tier = classify_domain(url or site, overrides=ov)
    sig.farm_flags = farm_flags(title, snippet, site=site)
    ai = ai_flags(title, snippet, cluster=cluster)
    sig.ai_suspect = bool(ai)
    sig.ai_flags = ai
    sig.age_days = age_days(cluster.get("published"))

    # R2：被几家引擎搜到 ≠ 几个独立来源。同一篇文章被 5 家引擎搜到还是一个来源。
    # 真正的独立性看的是**有多少个不同域名**在讲同一件事
    sig.independent_sources = max(1, int(cluster.get("siteCount") or 1))

    # ── 打分。分项各自扣，扣的理由都写出来 ──
    score = p.weight_of(sig.tier)
    sig.reasons.append(f"来源：{sig.tier.label}")

    if sig.independent_sources >= 3:
        score = min(1.0, score + p.multi_source_bonus)
        sig.reasons.append(f"{sig.independent_sources} 个独立站点都有")
    elif sig.independent_sources == 1 and sig.tier in (Tier.UNKNOWN, Tier.COMMUNITY, Tier.LOW):
        score -= p.lone_source_penalty
        sig.reasons.append("孤证：只有这一个来源，且来源不算权威")

    if sig.farm_flags:
        # 命中越多扣越狠，但单条命中影响很小 —— 正经文章偶尔也会中一条
        score -= min(0.45, p.farm_penalty * len(sig.farm_flags) ** 1.4)
        sig.reasons.append("内容农场特征：" + "、".join(sig.farm_flags))

    if sig.ai_suspect:
        score -= p.ai_penalty
        sig.reasons.append(
            "疑似机器批量生成（" + "；".join(sig.ai_flags) + "）—— 仅供参考，不代表内容有错"
        )

    if sig.age_days is not None and sig.age_days > stale:
        score -= p.stale_penalty
        sig.reasons.append(f"这是 {sig.age_days // 365} 年前的内容，做法可能已经变了")
    elif sig.age_days is None:
        sig.reasons.append("抓不到发布时间，无法判断新旧")

    sig.score = max(0.0, min(1.0, score))

    # ── 自动隐藏：只有这两类。用户选的是"标注为主" ──
    host = _host(url or site)
    user_block = {b for b in p.blocklist}
    blocked = any(
        c in _BLOCKLIST or c in user_block for c in _domain_candidates(host)
    )
    pure_farm = len(sig.farm_flags) >= 3 and sig.tier in (Tier.UNKNOWN, Tier.LOW)
    sig.hide = blocked or pure_farm
    if sig.hide:
        sig.reasons.append(
            "已自动折叠到「已排除」抽屉" + ("（在你的屏蔽名单里）" if blocked else "（多条农场特征叠加）")
        )
    return sig


def rank_with_trust(
    clusters: list[dict[str, Any]],
    *,
    overrides: dict[str, str] | None = None,
    weight: float | None = None,
    profile: TrustProfile | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    给一批结果打分并重排，返回 `(显示的, 已排除的)`。

    **两个列表都返回**是 R11 的全部要点：被排除的东西必须能被看到、
    能看到原因、能一键放回。一个悄悄丢结果的搜索工具没法用 ——
    用户永远不知道自己错过了什么。

    `weight` 是可信度在最终排序里占的比重。剩下的是相关性（RRF 分）。
    默认 0.35：可信度重要，但不能重到"相关性差但来源体面"的东西排到前面。
    """
    p = profile or DEFAULT_PROFILE
    w = p.rank_weight if weight is None else weight
    shown: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    rel_max = max((float(c.get("score") or 0.0) for c in clusters), default=0.0) or 1.0
    for c in clusters:
        sig = evaluate(c, overrides=overrides, profile=p)
        c = dict(c)
        c["trust"] = sig.to_dict()
        rel = float(c.get("score") or 0.0) / rel_max
        c["finalScore"] = round((1 - w) * rel + w * sig.score, 6)
        (dropped if sig.hide else shown).append(c)

    shown.sort(key=lambda x: -float(x["finalScore"]))
    dropped.sort(key=lambda x: -float(x["finalScore"]))
    return shown, dropped


def summarize_trust(shown: list[dict[str, Any]], dropped: list[dict[str, Any]]) -> dict[str, Any]:
    """给界面顶部一行概览：这一轮的结果整体成色如何。"""
    tiers: dict[str, int] = {}
    for c in shown:
        t = str(((c.get("trust") or {}).get("tierLabel")) or "未收录")
        tiers[t] = tiers.get(t, 0) + 1
    multi = sum(1 for c in shown if int((c.get("trust") or {}).get("independentSources") or 1) >= 3)
    return {
        "shown": len(shown),
        "excluded": len(dropped),
        "byTier": tiers,
        "multiSourced": multi,
        "note": (
            f"{len(shown)} 条结果，其中 {multi} 条有 3 个以上独立站点印证"
            + (f"；{len(dropped)} 条被折叠到「已排除」（可点开查看和放回）" if dropped else "")
        ),
    }
