"""
D1 内容农场指纹库 ＋ D7 来源利益相关标注
====================================================================
`trust.py` 里已经有一版 `farm_flags()`，它只看**标题和摘要的文字特征**。
这个模块补的是它看不到的两个维度：

  ① **域名指纹** —— 已知的采集站、翻译搬运站、SEO 站群的后缀与命名规律
  ② **发布节律** —— 同一个站在极短时间里发出大量同主题内容

再加上 D7 的**利益相关标注**：这个来源在这个话题上**有没有立场**。
「厂商官网说自家产品最好」不是造假，但读的人有权知道那是谁写的。

🔴 **三条边界，写在最前面，因为它们是这个模块能不能被信任的全部**：

1. **命中指纹 ≠ 内容是假的。** 内容农场也会转载真消息。所以输出的是
   `flags` 和 `penalty`，不是 `fake: true`。降权和判死是两件事。
2. **白名单永远优先。** 知乎、CSDN、简书上有大量高质量原创，只是同时
   也有大量搬运。整站拉黑等于把好东西一起扔了 —— 所以只在**单条**
   同时命中多个信号时才降权，不做站点级封杀。
3. **利益相关只陈述事实，不下判断。** 标「这是厂商官网」是事实；
   标「所以不可信」是我们给不了的结论。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

# ────────────────────────────────────────────────────────────────
# ① 域名指纹
# ────────────────────────────────────────────────────────────────
#: 采集站/站群最常用的这几类后缀。**不是说这些后缀就是农场** ——
#: 而是「后缀 + 别的信号」一起出现时可信度显著下降。
#: 单独命中这一条只加很小的权重，见 `_WEIGHTS`
_CHEAP_TLDS = (
    ".xyz", ".top", ".icu", ".cyou", ".buzz", ".click", ".link",
    ".shop", ".site", ".online", ".website", ".space", ".fun", ".cfd",
)

#: 站群命名规律：纯拼音串、数字尾巴、关键词堆砌式二级域名。
#: 比如 `jiaocheng123.top`、`ai-tools-hub-2024.site`
_FARM_HOST_PAT = re.compile(
    r"("
    r"\d{3,}\."                       # 数字前缀域名
    r"|[a-z]{2,}\d{2,4}\."            # 拼音+数字
    r"|(news|info|tech|ai|tools?|guide|tips|blog|daily|hub|zone|world)"
    r"[-_]?\d{0,4}\."                 # 关键词堆砌
    r")",
    re.I,
)

#: 已知的翻译搬运/内容农场特征词，出现在域名里
_FARM_HOST_WORDS = (
    "aggregat", "curat", "syndicat", "rewrite", "spinner",
    "aizixun", "caiji", "zhuanzai", "wenzhang",
)

#: 明确的高信任域名后缀，命中直接免检（比逐条列白名单省事且不会过期）
_TRUSTED_SUFFIX = (
    ".gov", ".gov.cn", ".edu", ".edu.cn", ".ac.cn", ".ac.uk",
    ".mil", ".int", ".org.cn",
)

# ────────────────────────────────────────────────────────────────
# ② 排版特征（要正文才判得了，只在 read_url 拿到 HTML 时跑）
# ────────────────────────────────────────────────────────────────
_LAYOUT_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("dense_ads", re.compile(r"(googlesyndication|adsbygoogle|pagead2|广告位|advert)", re.I),
     "页面里塞了广告位"),
    ("paginated_bait", re.compile(r"(下一页|第\s*\d+\s*页\s*/\s*共|next\s*page)", re.I),
     "把一篇短文切成多页骗点击"),
    ("scraped_footer", re.compile(r"(本文(转载|来源|整理)自|内容来自网络|版权归原作者所有|侵删)", re.I),
     "自己声明了是转载/采集"),
    ("no_author", re.compile(r"(小编|编辑整理|综合报道)", re.I),
     "没有具名作者，只署『小编』"),
    ("keyword_stuffing", re.compile(r"([一-鿿]{2,6})(、\1){3,}"),
     "同一个词在同一段里反复堆砌"),
]

# ────────────────────────────────────────────────────────────────
# ③ D7 利益相关
# ────────────────────────────────────────────────────────────────
#: 每条：`(标签 id, 中文说明, 判据正则)`。
#: 判的是**这个来源和这个话题的关系**，不是内容质量
_INTEREST_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("vendor", "厂商官网 —— 在谈自家产品",
     re.compile(r"^(www\.)?[a-z0-9-]+\.(com|cn|io|ai)$", re.I)),
    ("ad", "广告或推广页",
     re.compile(r"(/ad[s]?/|utm_(source|medium|campaign)=|/promo|/sponsor|/affiliate)", re.I)),
    ("ecommerce", "电商页面 —— 目的是卖货",
     re.compile(r"(taobao|tmall|jd\.com|amazon|ebay|pinduoduo|item\.|/product/|/dp/)", re.I)),
    ("pr", "公关稿分发平台",
     re.compile(r"(prnewswire|businesswire|globenewswire|美通社|新闻稿)", re.I)),
    ("forum", "论坛/问答 —— 个人经验，不是权威结论",
     re.compile(r"(zhihu|tieba|reddit|quora|stackexchange|v2ex|douban)", re.I)),
    ("selfmedia", "自媒体账号 —— 门槛低，质量方差大",
     re.compile(r"(mp\.weixin\.qq\.com|toutiao|baijiahao|sohu\.com/a/|163\.com/dy/)", re.I)),
]

#: 各信号的权重。总和封顶 0.45 —— **单靠这些信号最多把一条结果
#: 从中位往下压到尾部，不能把它压没**。压没要靠用户明确拉黑
_WEIGHTS: dict[str, float] = {
    "cheap_tld": 0.05,
    "farm_host": 0.10,
    "farm_word": 0.12,
    "dense_ads": 0.06,
    "paginated_bait": 0.08,
    "scraped_footer": 0.12,
    "no_author": 0.05,
    "keyword_stuffing": 0.08,
    "burst": 0.12,
    "clone": 0.15,
}
_MAX_PENALTY = 0.45


@dataclass
class FarmVerdict:
    """一个来源的农场判定。`penalty` 是给排序用的减分，不是"假的概率"。"""

    site: str = ""
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    interest: list[str] = field(default_factory=list)
    interest_labels: list[str] = field(default_factory=list)
    penalty: float = 0.0
    trusted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "flags": self.flags,
            "reasons": self.reasons,
            "interest": self.interest,
            "interestLabels": self.interest_labels,
            "penalty": round(self.penalty, 3),
            "trusted": self.trusted,
        }


def _host(url_or_site: str) -> str:
    s = str(url_or_site or "").strip()
    if not s:
        return ""
    if "://" in s:
        s = urlparse(s).netloc
    return s.lower().split(":")[0].lstrip("www.")


def domain_flags(url_or_site: str) -> tuple[list[str], list[str]]:
    """只看域名能看出来的东西。返回 `(flags, 人话原因)`。"""
    host = _host(url_or_site)
    if not host:
        return [], []
    if host.endswith(_TRUSTED_SUFFIX):
        return [], []

    flags: list[str] = []
    reasons: list[str] = []
    if host.endswith(_CHEAP_TLDS):
        flags.append("cheap_tld")
        reasons.append(f"用的是几块钱一年的域名后缀（{'.' + host.split('.')[-1]}）")
    if _FARM_HOST_PAT.search(host + "."):
        flags.append("farm_host")
        reasons.append("域名是站群常见的『关键词+数字』样式")
    if any(w in host for w in _FARM_HOST_WORDS):
        flags.append("farm_word")
        reasons.append("域名里直接带了采集/搬运类词根")
    return flags, reasons


def layout_flags(html_or_text: str) -> tuple[list[str], list[str]]:
    """
    看正文排版。**要 HTML 才准**，纯文本也能跑但会漏掉广告位那几条。

    只在 `read_url` 真的抓了正文时调用 —— 搜索结果列表里只有标题和
    摘要，在那个阶段跑这个函数会得到一堆假阴性，还白花时间。
    """
    s = str(html_or_text or "")
    if not s:
        return [], []
    flags: list[str] = []
    reasons: list[str] = []
    for key, pat, why in _LAYOUT_PATTERNS:
        if pat.search(s):
            flags.append(key)
            reasons.append(why)
    return flags, reasons


def interest_labels(url: str, *, title: str = "", snippet: str = "") -> tuple[list[str], list[str]]:
    """
    D7 —— 这个来源在这个话题上有没有立场。

    `vendor` 那条判得很松（几乎所有 .com 都会命中），所以它**只在
    没有命中别的更具体的标签时才生效** —— 否则每条商业网站结果都会
    挂一个"厂商官网"，标签就变成噪音了。
    """
    u = str(url or "")
    host = _host(u)
    text = f"{u} {title} {snippet}"
    ids: list[str] = []
    labels: list[str] = []
    for key, label, pat in _INTEREST_RULES:
        if key == "vendor":
            continue
        target = host if key in ("forum", "selfmedia", "pr", "ecommerce") else text
        if pat.search(target):
            ids.append(key)
            labels.append(label)
    if not ids and host and _INTEREST_RULES[0][2].match(host):
        # 只有在别的标签都没命中时，才退回到最笼统的"商业站点"
        ids.append("vendor")
        labels.append(_INTEREST_RULES[0][1])
    return ids, labels


def burst_flags(
    entries: list[dict[str, Any]], *, window_hours: int = 48, min_sites: int = 6
) -> dict[str, list[str]]:
    """
    ③ 发布节律 —— 找「短时间内十几个站发同一件事」。

    返回 `{url: [flags]}`。判据有两条，分开记因为含义不同：
      `burst` = 同一时间窗里挤了太多条（可能是热点，也可能是通稿分发）
      `clone` = 标题几乎一样（这个更硬，正常报道不会撞标题）

    🔴 **`burst` 单独出现时不该重罚**：真热点本来就是这个形状。
    只有 `burst + clone` 同时出现才是复制链的典型特征。
    """
    if len(entries) < min_sites:
        return {}

    def _ts(e: dict[str, Any]) -> float | None:
        p = e.get("published")
        if not p:
            return None
        try:
            s = str(p).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None

    dated = [(t, e) for e in entries if (t := _ts(e)) is not None]
    out: dict[str, list[str]] = defaultdict(list)

    if len(dated) >= min_sites:
        dated.sort(key=lambda x: x[0])
        span = window_hours * 3600
        # 滑窗：窗内不同站点数超过阈值就整窗打标
        i = 0
        for j in range(len(dated)):
            while dated[j][0] - dated[i][0] > span:
                i += 1
            window = dated[i:j + 1]
            sites = {str(e.get("site") or _host(e.get("url", ""))) for _t, e in window}
            if len(sites) >= min_sites:
                for _t, e in window:
                    if "burst" not in out[str(e.get("url") or "")]:
                        out[str(e.get("url") or "")].append("burst")

    # 标题撞车：归一化后完全相同才算，避免把「同一事件的不同报道」误伤
    by_title: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        key = re.sub(r"[^a-z0-9一-鿿]+", "", str(e.get("title") or "").lower())[:60]
        if len(key) >= 8:
            by_title[key].append(e)
    for _k, group in by_title.items():
        sites = {str(g.get("site") or _host(g.get("url", ""))) for g in group}
        if len(sites) >= 3:
            for g in group:
                url = str(g.get("url") or "")
                if "clone" not in out[url]:
                    out[url].append("clone")

    return dict(out)


def judge(
    entry: dict[str, Any],
    *,
    html: str = "",
    burst: dict[str, list[str]] | None = None,
) -> FarmVerdict:
    """
    汇总一条结果的农场判定。这是这个模块唯一对外的入口。

    `entry` 用的是 `WebResult.to_dict()` 的形状（`url` / `title` /
    `snippet` / `site` / `published`）。`html` 可选 —— 有正文时判得更准，
    没有也能跑，不会因为缺 HTML 而报错或返回空。
    """
    url = str(entry.get("url") or "")
    site = str(entry.get("site") or _host(url))
    v = FarmVerdict(site=site)

    if site.endswith(_TRUSTED_SUFFIX):
        v.trusted = True
        v.reasons.append("政府/教育/学术域名，跳过农场判据")
        v.interest, v.interest_labels = interest_labels(
            url, title=str(entry.get("title") or ""), snippet=str(entry.get("snippet") or "")
        )
        return v

    df, dr = domain_flags(url or site)
    v.flags += df
    v.reasons += dr

    if html:
        lf, lr = layout_flags(html)
        v.flags += lf
        v.reasons += lr

    for f in (burst or {}).get(url, []):
        v.flags.append(f)
        v.reasons.append(
            "48 小时内多个站点同时发布同主题内容" if f == "burst"
            else "标题与另外几个站几乎完全一样，像是同一份稿子分发的"
        )

    v.interest, v.interest_labels = interest_labels(
        url, title=str(entry.get("title") or ""), snippet=str(entry.get("snippet") or "")
    )

    v.penalty = min(_MAX_PENALTY, sum(_WEIGHTS.get(f, 0.0) for f in v.flags))
    # burst 单独出现时减半：真热点本来就长这样，不该因为"大家都在报"被罚
    if "burst" in v.flags and "clone" not in v.flags:
        v.penalty = max(0.0, v.penalty - _WEIGHTS["burst"] / 2)
    return v


def annotate(
    entries: list[dict[str, Any]], *, window_hours: int = 48
) -> list[dict[str, Any]]:
    """
    批量标注一整波搜索结果，就地写进每条的 `farm` 字段并返回原列表。

    **就地改而不是返回新列表**：调用方（`trust.rank_with_trust`）已经
    持有这些字典并且还要继续用，复制一份只会让两边不同步。
    """
    burst = burst_flags(entries, window_hours=window_hours)
    for e in entries:
        e["farm"] = judge(e, burst=burst).to_dict()
    return entries


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """给界面一行摘要：这一波里有多少条挂了旗、各是什么旗。"""
    flagged = [e for e in entries if (e.get("farm") or {}).get("flags")]
    counts: dict[str, int] = defaultdict(int)
    for e in flagged:
        for f in (e.get("farm") or {}).get("flags") or []:
            counts[f] += 1
    return {
        "total": len(entries),
        "flagged": len(flagged),
        "byFlag": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "note": "挂旗只表示这条来源有可疑特征，**不代表内容是假的**，排序上降权但不删除",
    }
