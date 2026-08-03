"""
链接顺藤摸瓜 —— N4
====================================================================
**要治的病**：`read_url` 只把正文抓回来。但一篇文章真正的价值往往
不在它自己说了什么，而在**它引用了谁**和**谁引用了它**：

  · 它引用的（出链）—— 一篇声称"研究表明 X"的文章，如果一条外链都没有，
    这件事本身就是最有力的判据。而如果它引用的全是同一个内容农场，
    那也说明了很多。
  · 引用它的（反链）—— 拿这个 URL 去搜，能找到谁在讨论它、有没有人反驳它。
    这一步用的是已有的多引擎搜索，不需要任何新能力。

**为什么出链要按来源等级分组而不是平铺**：一篇文章挂 40 条外链是常态，
其中 35 条是站内导航和社交分享。平铺出来用户根本看不出重点。
按等级分组之后，"这篇文章引用了 3 个官方文档"和"这篇文章引用了
12 个不认识的站"是一眼可辨的两件事。

🔴 **站内链接不算引用**。同域名的链接是导航，不是"它引用了谁"——
不剔掉的话，任何一篇文章的出链榜首永远是它自己的首页。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

log = logging.getLogger("synorive.websearch")

#: 一篇文章的出链上限。超过这个数基本都是导航/推荐位，
#: 全抓回来只会把真正的引用淹掉
MAX_OUTLINKS = 60

#: 明显不是"引用"的链接。社交分享按钮、订阅、登录页
_NOISE_HOST = re.compile(
    r"(facebook|twitter|x\.com|linkedin|pinterest|reddit\.com/submit|"
    r"weibo|qzone|t\.qq|api\.addthis|sharethis|instagram|youtube\.com/channel)",
    re.IGNORECASE,
)
_NOISE_PATH = re.compile(
    r"(/login|/signin|/register|/subscribe|/cart|/privacy|/terms|/cookie|"
    r"/rss|\.xml$|/feed/?$|/tag/|/category/|/author/|#)",
    re.IGNORECASE,
)


@dataclass
class Outlink:
    url: str
    site: str
    text: str = ""
    tier: str = ""
    tier_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "site": self.site, "text": self.text,
            "tier": self.tier, "tierLabel": self.tier_label,
        }


@dataclass
class Trail:
    url: str
    outlinks: list[Outlink] = field(default_factory=list)
    #: 按来源等级分组的计数，界面直接显示
    by_tier: dict[str, int] = field(default_factory=dict)
    #: 谁在讨论这个链接（拿 URL 去搜出来的）
    backlinks: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "outlinks": [o.to_dict() for o in self.outlinks],
            "byTier": self.by_tier,
            "backlinks": self.backlinks,
            "note": self.note,
        }


def extract_outlinks(html: str, base_url: str, *, limit: int = MAX_OUTLINKS) -> list[Outlink]:
    """
    从原始 HTML 里抠出**指向站外的**链接。

    走 lxml 而不是正则：`href` 可能带单引号、双引号、无引号，
    还可能出现在注释里 —— 正则要覆盖全这些情况会写成一坨没人敢改的东西，
    而 lxml 本来就是这个项目已有的依赖。
    """
    from lxml import html as lhtml

    from .trust import classify_domain

    try:
        doc = lhtml.fromstring(html)
    except Exception:  # noqa: BLE001 — 页面 HTML 坏掉不该让整个请求失败
        return []

    try:
        base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    except ValueError:
        base_host = ""

    seen: set[str] = set()
    out: list[Outlink] = []
    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            full = urljoin(base_url, href)
        except ValueError:
            continue
        if not full.startswith(("http://", "https://")):
            continue

        host = urlparse(full).netloc.lower().removeprefix("www.")
        # 🔴 同域名 = 导航，不是"它引用了谁"。不剔掉的话，
        # 任何一篇文章的出链榜首永远是它自己的首页
        if not host or host == base_host or host.endswith(f".{base_host}"):
            continue
        if _NOISE_HOST.search(host) or _NOISE_PATH.search(full):
            continue
        key = full.split("#")[0]
        if key in seen:
            continue
        seen.add(key)

        tier = classify_domain(full)
        text = re.sub(r"\s+", " ", a.text_content() or "").strip()[:120]
        out.append(Outlink(
            url=key, site=host, text=text, tier=tier.value, tier_label=tier.label
        ))
        if len(out) >= limit:
            break

    # 权威的排前面 —— 用户想先看的是"它引用了哪些靠谱的东西"
    order = {"official": 0, "academic": 1, "mainstream": 2, "community": 3,
             "unknown": 4, "low": 5}
    out.sort(key=lambda o: (order.get(o.tier, 9), o.site))
    return out


async def find_backlinks(
    web: Any, url: str, *, limit: int = 8, deadline_s: float = 6.0
) -> list[dict[str, Any]]:
    """
    谁在讨论这个链接。拿 URL（和去掉协议的裸地址）去搜。

    **搜两种写法**：带 https 的完整 URL 和裸地址。很多引擎对前者
    会做特殊处理（当成"跳转到这个页面"而不是"搜这个字符串"），
    只搜一种会大量落空。

    整段有硬预算：这是锦上添花，超时就少几条，绝不拖慢读取本身。
    """
    bare = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    if not bare:
        return []
    queries = [f'"{bare}"', bare]

    async def one(q: str) -> list[dict[str, Any]]:
        try:
            res = await web.search(q, limit=limit)
            return [c.to_dict() for c in res.clusters]
        except Exception as e:  # noqa: BLE001
            log.debug("反链检索失败（忽略）：%s", e)
            return []

    try:
        batches = await asyncio.wait_for(
            asyncio.gather(*(one(q) for q in queries), return_exceptions=True),
            timeout=deadline_s,
        )
    except (TimeoutError, asyncio.CancelledError):
        return []

    target_host = urlparse(url).netloc.lower().removeprefix("www.")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for b in batches:
        if not isinstance(b, list):
            continue
        for c in b:
            u = str(c.get("url") or "")
            host = urlparse(u).netloc.lower().removeprefix("www.")
            # 同站的不算"谁在讨论它" —— 那只是它自己的其它页面
            if not u or u in seen or host == target_host:
                continue
            seen.add(u)
            out.append({
                "url": u,
                "title": c.get("title"),
                "site": c.get("site"),
                "snippet": str(c.get("snippet") or "")[:200],
            })
    return out[:limit]


async def build_trail(
    web: Any | None,
    *,
    url: str,
    html: str,
    with_backlinks: bool = True,
) -> Trail:
    """把一条链接摸到底：它引用了谁 + 谁在讨论它。"""
    t = Trail(url=url)
    t.outlinks = extract_outlinks(html, url)
    for o in t.outlinks:
        t.by_tier[o.tier_label] = t.by_tier.get(o.tier_label, 0) + 1

    if with_backlinks and web is not None:
        t.backlinks = await find_backlinks(web, url)

    # 一句人话的结论。**"一条外链都没有"本身就是判据** ——
    # 一篇声称"研究表明"却不给任何出处的文章，这件事值得说出来
    parts: list[str] = []
    if not t.outlinks:
        parts.append(
            "这篇文章**一条站外链接都没有** —— 它说的话没有任何可追溯的出处"
        )
    else:
        strong = sum(t.by_tier.get(k, 0) for k in ("官方", "学术"))
        parts.append(
            f"引用了 {len(t.outlinks)} 个站外链接"
            + (f"，其中 {strong} 个是官方或学术来源" if strong else "，但没有一个是官方或学术来源")
        )
    if t.backlinks:
        parts.append(f"另有 {len(t.backlinks)} 个页面在讨论它")
    elif with_backlinks:
        parts.append("没搜到别的页面在讨论它（不代表没人看过，只是搜索引擎没收录）")
    t.note = "；".join(parts)
    return t
