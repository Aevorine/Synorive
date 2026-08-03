"""
主动核查 —— V1 / V4 / V6 / V7
====================================================================
`trust.py` 做的是**看着这条结果本身判断它像不像真的**（域名、时间、
文风、几个站在说）。它有个根本局限，那一节开头就写清楚了：
**一篇写得像模像样、来源体面、但事实本身就是错的内容，它识别不了。**

这个模块补的正是那一块 —— 不再只看这条结果，而是**再去搜一次**：

  V6 反向检索：拿同一个话题去搜「质疑 / 辟谣 / 争议 / debunked」。
     这是最便宜也最有效的一招 —— 如果一个说法是已知的谣言，
     几乎必然有人写过辟谣文章，搜一次就能撞上。
  V4 溯源链路：把讲同一件事的资料按发布时间排开，找出最早的那个。
     内容农场的复制链有个稳定特征：**十几个站说同一句话，时间集中在两天内，
     而最早那个往往是个没人听过的站**。看到这个形状基本可以断定是复制链。
  V1 断言级核查：把简报里每一句拆成可核查的断言，逐条去搜支持与反驳。
     最彻底，也最贵 —— 每条断言都要一轮完整检索。
  V7 撤稿检查：论文被撤稿之后还在被引是常态。OpenAlex 有 `is_retracted` 字段，
     免 Key 直接查。

**三档可调**（用户在设置里选，默认中档）：
  annotate  只用 trust.py 的静态标注，不额外出网 —— 零延迟
  counter   加 V6 反向检索 + V4 溯源 + V7 撤稿（默认）—— 约 +1~2 秒
  claim     再加 V1 断言级逐句核查 —— 深挖会从 8 秒变成 30 秒以上

🔴 **能力边界，和 trust.py 同一条**：找到反驳源**不等于**这个说法是假的
（也可能是反驳的人错了）。所以对外一律给「支持 N / 反驳 M / 无证据 K」
这三个数和各自的出处，**不给"这是假的"这种结论**。判断留给用户，
我们负责把两边的证据都摆到他面前。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("synorive.websearch")

#: 三档
LEVELS = ("annotate", "counter", "claim")
DEFAULT_LEVEL = "counter"

#: V6 反向检索的后缀。中英各一组 —— 中文资料的辟谣文多用「辟谣/谣言」，
#: 英文用 debunked/false。只用一种语言会漏掉一半
_COUNTER_SUFFIX_CN = ["辟谣", "质疑", "是假的", "争议", "被推翻"]
_COUNTER_SUFFIX_EN = ["debunked", "false claim", "criticism", "retracted"]

#: 判断一条结果是在**反驳**：标题或摘要里带这些词
_REFUTE_MARK = re.compile(
    r"(辟谣|谣言|不实|假的|误传|误读|纠正|更正|澄清|撤稿|撤回|被推翻|并不|其实不|"
    r"debunk|false|hoax|myth|misleading|retract|incorrect|not true|refut)",
    re.IGNORECASE,
)
#: 判断一条结果是在**支持**：明确的肯定/证实措辞
_SUPPORT_MARK = re.compile(
    r"(证实|确认|属实|已验证|官方回应.{0,6}属实|research shows|confirmed|verified|"
    r"study finds|according to)",
    re.IGNORECASE,
)


# ────────────────────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────────────────────
@dataclass
class Stance:
    """一条结果对某个断言的态度。**必须带原文**，否则没法核对。"""

    url: str
    title: str
    site: str
    snippet: str
    stance: str = "neutral"     # support / refute / neutral
    trust_score: float = 0.5
    tier: str = ""
    published: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "url": self.url, "title": self.title, "site": self.site,
            "snippet": self.snippet, "stance": self.stance,
            "trustScore": round(self.trust_score, 3), "tier": self.tier,
        }
        if self.published:
            d["published"] = self.published
        return d


@dataclass
class ClaimVerdict:
    """一条断言的核查结论。**给数不给判决**。"""

    claim: str
    source_url: str = ""
    support: list[Stance] = field(default_factory=list)
    refute: list[Stance] = field(default_factory=list)
    neutral_count: int = 0
    #: supported / disputed / unverified —— 注意 disputed 是"有人反驳"不是"这是假的"
    verdict: str = "unverified"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "sourceUrl": self.source_url,
            "verdict": self.verdict,
            "supportCount": len(self.support),
            "refuteCount": len(self.refute),
            "neutralCount": self.neutral_count,
            "support": [s.to_dict() for s in self.support[:5]],
            "refute": [s.to_dict() for s in self.refute[:5]],
            "note": self.note,
        }


def _finalize(v: ClaimVerdict) -> ClaimVerdict:
    """
    定结论。**门槛刻意不对称**：只要有 1 条像样的反驳就标 disputed，
    而标 supported 要至少 2 个独立站点支持且无人反驳。

    理由是代价不对称 —— 把一条有争议的信息标成"已证实"，用户会拿它去做决定；
    把一条其实没问题的信息标成"有争议"，用户最多多花两分钟自己看一眼。
    """
    ref_sites = {s.site for s in v.refute if s.site}
    sup_sites = {s.site for s in v.support if s.site}
    if ref_sites:
        v.verdict = "disputed"
        v.note = f"{len(ref_sites)} 个站点提出了相反说法 —— 这不代表原说法一定错，请自己看两边的原文"
    elif len(sup_sites) >= 2:
        v.verdict = "supported"
        v.note = f"{len(sup_sites)} 个独立站点给出一致说法，没有搜到反驳"
    elif sup_sites:
        v.verdict = "weak"
        v.note = "只有一个来源支持，没有第二个站点印证，也没搜到反驳"
    else:
        v.verdict = "unverified"
        v.note = "没有搜到直接相关的支持或反驳材料 —— 可能是这个说法太具体，换个说法再试"
    return v


# ────────────────────────────────────────────────────────────────
# V6 反向检索
# ────────────────────────────────────────────────────────────────
def counter_queries(query: str, *, limit: int = 3) -> list[str]:
    """
    构造反向检索词。

    **不是简单拼接**：中文查询拼英文后缀基本搜不到东西，反之亦然。
    按查询本身的语言选后缀组，中文查询额外补一条英文的（很多技术类
    谣言的辟谣文只有英文版）。
    """
    q = (query or "").strip()
    if not q:
        return []
    is_cn = bool(re.search(r"[一-鿿]", q))
    base = _COUNTER_SUFFIX_CN if is_cn else _COUNTER_SUFFIX_EN
    out = [f"{q} {suf}" for suf in base[:limit]]
    if is_cn and limit > 1:
        out.append(f"{q} debunked")
    return out[: limit + 1]


async def counter_search(
    meta: Any,
    query: str,
    *,
    limit: int = 8,
    engines: list[str] | None = None,
    deadline_s: float = 5.0,
) -> list[Stance]:
    """
    拿反向检索词并发搜一遍，返回**明确在反驳的**那些结果。

    `meta` 是 `MetaSearch` 实例（鸭子类型：只要有 `.search()`）。
    这里不 import MetaSearch，避免 verify ↔ meta 循环依赖。

    整段给硬预算：反向检索是加分项，超时就少几条，
    绝不该把一次正常搜索拖成十几秒。
    """
    queries = counter_queries(query)
    if not queries:
        return []

    async def one(q: str) -> list[dict[str, Any]]:
        try:
            res = await meta.search(q, engines=engines, limit=limit)
            return [c.to_dict() for c in res.clusters]
        except Exception as e:  # noqa: BLE001 — 反向检索失败不该让主流程炸
            log.debug("反向检索失败（忽略）：%s", e)
            return []

    try:
        batches = await asyncio.wait_for(
            asyncio.gather(*(one(q) for q in queries), return_exceptions=True),
            timeout=deadline_s,
        )
    except (TimeoutError, asyncio.CancelledError):
        return []

    seen: set[str] = set()
    out: list[Stance] = []
    for batch in batches:
        if not isinstance(batch, list):
            continue
        for c in batch:
            url = str(c.get("url") or "")
            if not url or url in seen:
                continue
            text = f"{c.get('title') or ''} {c.get('snippet') or ''}"
            # 🔴 只收**真的在反驳**的。搜「X 辟谣」返回的结果里，
            # 有一大半只是标题里恰好有这两个字的无关文章 —— 全收进来
            # 等于给每个查询都伪造出一批"反驳证据"，比不做还糟
            if not _REFUTE_MARK.search(text):
                continue
            seen.add(url)
            out.append(Stance(
                url=url,
                title=str(c.get("title") or ""),
                site=str(c.get("site") or ""),
                snippet=str(c.get("snippet") or "")[:300],
                stance="refute",
                published=c.get("published"),
            ))
    return out


# ────────────────────────────────────────────────────────────────
# V4 溯源链路
# ────────────────────────────────────────────────────────────────
@dataclass
class OriginTrace:
    """一条信息的传播链。"""

    earliest: dict[str, Any] | None = None
    chain: list[dict[str, Any]] = field(default_factory=list)
    #: 没有发布时间、排不进链条的
    undated: int = 0
    verdict: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "earliest": self.earliest,
            "chain": self.chain[:20],
            "undated": self.undated,
            "verdict": self.verdict,
            "note": self.note,
        }


def trace_origin(clusters: list[dict[str, Any]]) -> OriginTrace:
    """
    把一批讲同一件事的结果按发布时间排开，找最早的那个。

    **只用已有数据，不额外出网** —— 发布时间在搜索阶段就拿到了。
    这一步的成本几乎为零，而它能识别出的东西很具体：

      · 十几个站说同一句话、时间挤在两三天内 → 典型的复制链
      · 最早那个站是个没人听过的域名 → 源头可疑
      · 全都没有发布时间 → **如实说"排不出来"**，不猜

    坑记在这儿：早期版本对没有日期的结果**默认给今天**，结果每次
    溯源都指向随便一个刚抓到的站。宁可说"排不出来"也不能编一个日期。
    """
    from .trust import age_days

    dated: list[tuple[int, dict[str, Any]]] = []
    undated = 0
    for c in clusters:
        d = age_days(c.get("published"))
        if d is None:
            undated += 1
            continue
        dated.append((d, c))

    tr = OriginTrace(undated=undated)
    if not dated:
        tr.verdict = "unknown"
        tr.note = f"这 {len(clusters)} 条都没有可靠的发布时间，排不出先后 —— 不猜"
        return tr

    dated.sort(key=lambda x: -x[0])  # age 大 = 早
    tr.chain = [
        {
            "ageDays": d,
            "published": c.get("published"),
            "title": c.get("title"),
            "url": c.get("url"),
            "site": c.get("site"),
            "tier": ((c.get("trust") or {}).get("tierLabel")),
        }
        for d, c in dated
    ]
    tr.earliest = tr.chain[0]

    spread = dated[0][0] - dated[-1][0]
    first_tier = str((tr.earliest.get("tier") or ""))
    if len(dated) >= 5 and spread <= 3:
        tr.verdict = "burst"
        tr.note = (
            f"{len(dated)} 个站在 {spread} 天内发了同一件事 —— 这是典型的转载爆发，"
            "多半来自同一个源头，独立性远不如站点数看起来那么高"
        )
    elif first_tier in ("未收录", "低信誉", "社区/个人") and len(dated) >= 3:
        tr.verdict = "weak-origin"
        tr.note = (
            f"最早的出处是「{first_tier}」级别的 {tr.earliest.get('site')} —— "
            "后面转载它的站再权威，也不会让原始信息变得更可靠"
        )
    else:
        tr.verdict = "ok"
        tr.note = (
            f"最早可查的出处是 {tr.earliest.get('site')}"
            f"（{tr.earliest.get('published')}）"
            + (f"，另有 {undated} 条没有日期排不进来" if undated else "")
        )
    return tr


# ────────────────────────────────────────────────────────────────
# V7 撤稿与勘误
# ────────────────────────────────────────────────────────────────
async def check_retractions(
    dois: list[str], *, timeout_s: float = 6.0
) -> dict[str, dict[str, Any]]:
    """
    查一批 DOI 有没有被撤稿。走 OpenAlex（免 Key，有 `is_retracted` 字段）。

    返回 `{doi: {...}}`，**只包含真的有问题的**。查不到的不放进来 ——
    「没查到撤稿记录」和「确认没被撤稿」是两件事，混在一起会给用户
    一个它给不了的保证。

    为什么不用 Crossref：Crossref 的撤稿信息藏在 `update-to` 里且不统一，
    要逐条判断关系类型；OpenAlex 直接有一个布尔字段，还顺带给了被引数。
    """
    clean = [d.strip().lower().replace("https://doi.org/", "") for d in dois if d]
    clean = [d for d in dict.fromkeys(clean) if d]
    if not clean:
        return {}

    out: dict[str, dict[str, Any]] = {}

    async def one(client: httpx.AsyncClient, doi: str) -> None:
        try:
            r = await client.get(
                f"https://api.openalex.org/works/doi:{doi}",
                params={"select": "id,doi,title,is_retracted,publication_year,cited_by_count"},
                headers={"User-Agent": "Synorive/1.0 (mailto:noreply@synorive.local)"},
            )
            if r.status_code != 200:
                return
            d = r.json()
            if d.get("is_retracted"):
                out[doi] = {
                    "doi": doi,
                    "title": d.get("title"),
                    "year": d.get("publication_year"),
                    "citedBy": d.get("cited_by_count"),
                    "reason": "OpenAlex 标记为已撤稿",
                }
        except (httpx.HTTPError, ValueError, KeyError):
            return

    async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0)) as client:
        try:
            await asyncio.wait_for(
                asyncio.gather(*(one(client, d) for d in clean[:40]),
                               return_exceptions=True),
                timeout=timeout_s,
            )
        except (TimeoutError, asyncio.CancelledError):
            pass
    return out


# ────────────────────────────────────────────────────────────────
# V1 断言级核查
# ────────────────────────────────────────────────────────────────
#: 一条句子要够具体才值得核查。没有数字、没有专名的泛泛之谈，
#: 搜出来的东西也是泛泛之谈，白花一轮检索
_CONCRETE = re.compile(r"\d|[A-Z][a-zA-Z]{2,}|[一-鿿]{4,}")


def extract_claims(
    briefing: dict[str, Any], *, max_claims: int = 8
) -> list[tuple[str, str]]:
    """
    从摘录版简报里挑出值得核查的断言，返回 `[(句子, 出处url)]`。

    **优先挑分歧区和数字区的句子**：共识区里的东西已经有多个站点印证过了，
    再去核查一遍的边际收益很低；而分歧和数字恰恰是最容易出错、
    也最影响判断的两类。
    """
    picked: list[tuple[str, str]] = []
    seen: set[str] = set()

    def take(text: str, url: str) -> None:
        s = (text or "").strip()
        if len(s) < 12 or len(s) > 160 or not _CONCRETE.search(s):
            return
        k = re.sub(r"\s+", "", s)[:40]
        if k in seen:
            return
        seen.add(k)
        picked.append((s, url or ""))

    for d in briefing.get("disputes") or []:
        for pair in d.get("conflicts") or []:
            for side in ("a", "b"):
                ev = pair.get(side) or {}
                take(str(ev.get("text") or ""), str(ev.get("url") or ""))
    for n in briefing.get("numbers") or []:
        take(str(n.get("sentence") or ""), str(n.get("url") or ""))
    for c in briefing.get("consensus") or []:
        for ev in (c.get("evidence") or [])[:1]:
            take(str(ev.get("text") or ""), str(ev.get("url") or ""))

    return picked[:max_claims]


def _claim_query(claim: str) -> str:
    """
    把一句断言压成一个能用的查询词。

    整句丢给搜索引擎召回很差（长句里大半是虚词），所以只留关键词。
    走 jieba 而不是正则切汉字 —— 台账坑 37 记着这一条：
    正则贪婪切汉字不是分词，只是按长度截断。
    """
    from ..store.text import segment
    from .research import _STOP

    words: list[str] = []
    for raw in re.findall(r"[\w一-鿿]+", claim):
        words.extend(segment(raw))
    keep = [w for w in words if len(w) >= 2 and w not in _STOP]
    # 保序去重后取前 8 个 —— 再多引擎就开始忽略尾部词了
    return " ".join(list(dict.fromkeys(keep))[:8]) or claim[:40]


async def verify_claims(
    meta: Any,
    claims: list[tuple[str, str]],
    *,
    engines: list[str] | None = None,
    per_claim_limit: int = 8,
    deadline_s: float = 25.0,
    concurrency: int = 3,
) -> list[ClaimVerdict]:
    """
    逐条核查断言。**每条都是一轮完整检索**，所以这是最贵的一档。

    并发度刻意压到 3：一次对同一批引擎打 8 个并发查询，
    百度和 Bing 都会开始弹验证码 —— 那时候拿到的不是"更快的核查"，
    而是"更快地把引擎搞挂"。
    """
    if not claims:
        return []

    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(claim: str, src: str) -> ClaimVerdict:
        v = ClaimVerdict(claim=claim, source_url=src)
        async with sem:
            try:
                res = await meta.search(
                    _claim_query(claim), engines=engines, limit=per_claim_limit
                )
            except Exception as e:  # noqa: BLE001
                v.note = f"核查这条时检索失败：{type(e).__name__}"
                return v
            for c in res.clusters:
                d = c.to_dict()
                url = str(d.get("url") or "")
                if url and url == src:
                    continue  # 不拿原文自己印证自己
                text = f"{d.get('title') or ''} {d.get('snippet') or ''}"
                st = Stance(
                    url=url, title=str(d.get("title") or ""),
                    site=str(d.get("site") or ""),
                    snippet=str(d.get("snippet") or "")[:300],
                    published=d.get("published"),
                )
                if _REFUTE_MARK.search(text):
                    st.stance = "refute"
                    v.refute.append(st)
                elif _SUPPORT_MARK.search(text) or _topic_match(claim, text):
                    st.stance = "support"
                    v.support.append(st)
                else:
                    v.neutral_count += 1
        return _finalize(v)

    try:
        got = await asyncio.wait_for(
            asyncio.gather(*(one(c, u) for c, u in claims), return_exceptions=True),
            timeout=deadline_s,
        )
    except (TimeoutError, asyncio.CancelledError):
        return [
            _finalize(ClaimVerdict(claim=c, source_url=u, note="核查超时，本轮跳过"))
            for c, u in claims
        ]
    return [g for g in got if isinstance(g, ClaimVerdict)]


def _topic_match(claim: str, text: str) -> float | bool:
    """
    这条结果讲的是不是同一件事。用词重叠判断，门槛定得高（0.45）。

    低门槛会把"沾一点边"的结果全算成支持证据 —— 那样每条断言都会
    显示一堆支持，核查就变成了走过场。
    """
    from .research import _overlap, _terms

    return _overlap(_terms(claim), _terms(text)) >= 0.45


# ────────────────────────────────────────────────────────────────
# 一次跑完（按档位）
# ────────────────────────────────────────────────────────────────
async def run_verification(
    meta: Any,
    *,
    query: str,
    clusters: list[dict[str, Any]],
    briefing: dict[str, Any] | None = None,
    level: str = DEFAULT_LEVEL,
    engines: list[str] | None = None,
    dois: list[str] | None = None,
) -> dict[str, Any]:
    """
    按档位跑核查，返回给界面和 MCP 用的一份结构。

    档位之间是**累加**关系，不是三选一的三套实现 —— counter 包含 annotate
    的全部输出，claim 再包含 counter 的。这样用户调档时看到的是
    "多了什么"，而不是"整个界面变了个样"。
    """
    lvl = level if level in LEVELS else DEFAULT_LEVEL
    out: dict[str, Any] = {"level": lvl, "query": query}

    if lvl == "annotate":
        out["note"] = "只做静态标注（来源分级/农场特征/时效），没有额外出网核查"
        return out

    # counter 档：反向检索 + 溯源 + 撤稿，三件事并发
    tasks: dict[str, Any] = {
        "counter": counter_search(meta, query, engines=engines),
    }
    if dois:
        tasks["retracted"] = check_retractions(dois)

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for name, got in zip(tasks.keys(), results, strict=False):
        if isinstance(got, BaseException):
            log.debug("核查子任务 %s 失败：%s", name, got)
            continue
        if name == "counter":
            out["counterEvidence"] = [s.to_dict() for s in got]
        elif name == "retracted":
            out["retracted"] = got

    out["origin"] = trace_origin(clusters).to_dict()
    out["note"] = _counter_note(out)

    if lvl == "claim" and briefing:
        claims = extract_claims(briefing)
        verdicts = await verify_claims(meta, claims, engines=engines)
        out["claims"] = [v.to_dict() for v in verdicts]
        out["claimSummary"] = {
            "total": len(verdicts),
            "supported": sum(1 for v in verdicts if v.verdict == "supported"),
            "disputed": sum(1 for v in verdicts if v.verdict == "disputed"),
            "weak": sum(1 for v in verdicts if v.verdict == "weak"),
            "unverified": sum(1 for v in verdicts if v.verdict == "unverified"),
        }
    return out


def _counter_note(out: dict[str, Any]) -> str:
    n = len(out.get("counterEvidence") or [])
    ret = len(out.get("retracted") or {})
    parts: list[str] = []
    if n:
        parts.append(f"反向检索找到 {n} 条质疑/辟谣材料 —— 请自己看两边原文再判断")
    else:
        parts.append("反向检索没找到质疑材料（这不等于就是真的，只是没人公开反驳过）")
    if ret:
        parts.append(f"⚠️ 引用的文献里有 {ret} 篇**已被撤稿**")
    origin = out.get("origin") or {}
    if origin.get("note"):
        parts.append(str(origin["note"]))
    return "；".join(parts)
