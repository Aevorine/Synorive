"""
元搜索编排 —— W1 / W2 / W4 / W9 / P1 / P2 / P9
====================================================================
把 N 家引擎的结果并发拿回来、去重折叠、融合成一份排序。

**三个设计决定，每个都有具体理由：**

① **首屏只等最快的几家**（P1 ≤1.5s）
   一起等所有引擎 = 整体速度等于最慢那家。而最慢那家往往是被限流的那家，
   它的等待时间没有上限。所以分两波：先到的先上屏，慢的补进来重排。

② **熔断，而且熔断的是"解析坏了"不是"没结果"**
   引擎适配器返回 `BROKEN` 才计入失败。搜一个冷门词得到 0 条是正常的，
   把它算作失败会让好引擎被无辜熔断。这个区分在 `engines.py` 里做。

③ **折叠而不是删除**
   同一篇文章被五家引擎搜到、又被三个站转载 —— 这是**八条**结果，
   但用户只想看一条。折叠成一条并记下"它出现在 5 家引擎 / 3 个站点"，
   这个数字本身就是 R2 交叉印证的原料，删掉就白丢了。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from ..ingest.web import url_fingerprint
from .engines import (
    BaseEngine,
    EngineReply,
    ParseOutcome,
    WebResult,
    all_engines,
    get_engine,
    split_parse,
)
from .scheduler import EngineScheduler

log = logging.getLogger("synorive.websearch")

#: RRF 的 K。和本地检索 D1 用同一个值 —— 两边都是"名次融合"，
#: 用不同的 K 只会让两套排序的行为对不上，调参时更难判断是哪一路的问题
RRF_K = 60

#: 单家引擎的超时。超过就放弃它，不拖累整体（P2）
ENGINE_TIMEOUT = 8.0
#: 浏览器渲染要等页面真的跑完 JS，比纯 HTTP 请求慢得多，给更宽的预算
RENDER_TIMEOUT_S = 12.0
#: 首屏截止：到点就把已经回来的先给出去（P1 ≤1.5s）
FIRST_SCREEN_S = 1.5
#: 整轮截止（P2 ≤4s）
TOTAL_DEADLINE_S = 4.0

#: 够几家就可以先走（X2）。0 = 关掉早退，退回"等齐所有引擎"的旧行为。
#:
#: 🔴 这条是 X2 不达标的正解，**不是调参能救的**：实测 P95 4.94s 死死卡在
#: 4.0s 的总截止上，说明绝大多数搜索都在等最慢的那一家。而"最慢的那家"
#: 当时是在返验证码页的百度/360 —— 等满 4 秒换回来一个验证码，
#: 纯亏。把 deadline 从 4s 调到 3s 只会让**更多**引擎被砍成超时，
#: 结果更差而不是更好。要快就得在"已经够用了"的那一刻主动收手。
#:
#: 🔴 **这个门槛必须相对于"实际派出去几家"，不能写死。** 第一版写死 3，
#: 实测一次都没触发过 —— 现场是 6 家引擎里 3 家熔断中（`_pick` 阶段就
#: 被挡掉，根本没进 `_fan_out`）、1 家在返验证码，真正派出去的只有 3 家、
#: 其中只有 1 家给了结果。`good >= 3` 永远不成立，改了等于没改。
#: 教训：**门槛的分母得是当下真实的阵容，不是理想阵容。**
ENOUGH_ENGINES = 3
#: 派出去的引擎里回来这个比例就可以走
ENOUGH_RATIO = 0.6
#: 但至少要等到这么多家有回音 —— 只凭一家就走等于放弃交叉印证（R2）
ENOUGH_MIN = 2
#: 拿到第一条真结果之后，最多再等这么久 —— **不管门槛够没够**。
#:
#: 🔴 这条是 X6 逼出来的。只按"landed >= need"早退有个洞：`need` 的分母是
#: 这一轮派出去的家数，**多派一个坏引擎，门槛就跟着涨一档**（3 家派出去
#: need=2，4 家就 need=3），于是多等一家真引擎。实测 X6 因此从 +14.2%
#: 退到 +69.1% —— 一个坏引擎的代价反而被我放大了。
#: 加这条绝对上界，等待时间就与"阵容里混进多少坏引擎"脱钩了。
MAX_WAIT_AFTER_FIRST_GOOD_S = 1.2
#: 够了之后再顺手等一下"将到未到"的那几家。
#:
#: 没有这个宽限期的话，早退会把交叉印证（R2 靠 engineCount）砍得太狠 ——
#: 一家引擎往往就差几十毫秒就回来了，为省这几十毫秒丢掉一路印证不划算。
#: 0.35s 是"几乎不影响体感、但能捞回大部分擦肩而过的引擎"的量级。
GRACE_AFTER_ENOUGH_S = 0.35

#: 熔断：连续 N 次解析失败就停用一段时间
BREAK_AFTER = 3
BREAK_COOLDOWN_S = 900.0

#: 桌面端能同时渲染几个页面。
#:
#: 🔴 **这个数必须和 `apps/desktop/electron/main/render.ts` 里的
#: `RENDER_LANES` 对上。** 对不上的后果是实测栽过的那个：
#: 渲染端只有一条通道时，Google 和 Yandex 同时发起，第二个在队列里
#: **一秒都没开始渲染**，可它这边的 12 秒预算已经在走了 ——
#: 于是它必定超时，而报出来的话是"超时，本轮放弃"，
#: 看起来像是这家引擎慢，其实是它压根没轮上。
#: 两家浏览器引擎因此永远不可能在同一轮里都成功。
RENDER_PARALLEL = 2
#: 渲染引擎的整轮预算 = 每批 12s × 需要几批 + 这个余量。
#:
#: 🔴 余量原来是 2.0s，而 `RenderBroker.render` 自己的硬截止是
#: `RENDER_TIMEOUT_S + 1.5 = 13.5s` —— 中间只剩 0.5 秒。
#: 同一个事件循环里还有五六家引擎在用 lxml 同步解析大页面，
#: 吃掉这 0.5 秒轻而易举，于是渲染结果**刚要回来就被判超时**。
#: 加宽到 4s 不是"调大点试试"，是让这两个截止时间之间有真实的空隙。
RENDER_DEADLINE_MARGIN_S = 4.0

#: 结果缓存（P9）：同一查询 10 分钟内不重复出网
CACHE_TTL_S = 600.0
CACHE_MAX = 200


@dataclass
class Cluster:
    """折叠后的一条。同一篇文章的所有副本都在这里。"""

    best: WebResult
    #: 命中它的引擎（去重）—— R2 交叉印证要用
    engines: set[str] = field(default_factory=set)
    #: 出现过的域名 —— 转载链识别要用
    sites: set[str] = field(default_factory=set)
    #: 各引擎给的名次，用于 RRF
    ranks: list[tuple[str, int]] = field(default_factory=list)
    variants: list[WebResult] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = self.best.to_dict()
        d["score"] = round(self.score, 6)
        d["engines"] = sorted(self.engines)
        d["engineCount"] = len(self.engines)
        d["siteCount"] = len(self.sites)
        if len(self.variants) > 1:
            d["alsoAt"] = [v.url for v in self.variants[1:6]]
        return d


@dataclass
class MetaSearchResult:
    query: str
    clusters: list[Cluster]
    replies: list[EngineReply]
    elapsed_ms: int
    from_cache: bool = False
    partial: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [c.to_dict() for c in self.clusters],
            "engines": [
                {
                    "id": r.engine,
                    "outcome": r.outcome.value,
                    "count": len(r.results),
                    "elapsedMs": r.elapsed_ms,
                    **({"error": r.error} if r.error else {}),
                }
                for r in self.replies
            ],
            "elapsedMs": self.elapsed_ms,
            "fromCache": self.from_cache,
            "partial": self.partial,
        }


def _render_deadline(deadline_s: float, picked: list[BaseEngine]) -> float:
    """
    这一轮的总截止时间。有浏览器渲染引擎时要放宽 —— 放宽多少**取决于
    渲染端能并行几个**，不是固定值。

    两家渲染引擎、渲染端只有两条通道 = 一批跑完，放宽到 12+4；
    四家、两条通道 = 得跑两批，放宽到 24+4。按家数算而不是拍一个数，
    才不会在"用户多开了一家"的时候悄悄退回必然超时的状态。
    """
    n = sum(1 for e in picked if e.needs_browser)
    if n <= 0:
        return deadline_s
    batches = math.ceil(n / max(1, RENDER_PARALLEL))
    return max(deadline_s, RENDER_TIMEOUT_S * batches + RENDER_DEADLINE_MARGIN_S)


class _Breaker:
    """
    每家引擎一个熔断器。

    刻意做得很笨：只数连续失败次数，不做滑动窗口、不做半开状态。
    这里的失败是"对方改版了"或"被反爬挡了"这种**持续几小时到几周**的故障，
    不是毫秒级抖动，精细的熔断算法在这个时间尺度上没有意义。
    """

    def __init__(self) -> None:
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def is_open(self, engine_id: str) -> bool:
        until = self._open_until.get(engine_id, 0.0)
        if until and time.monotonic() < until:
            return True
        if until:
            # 冷却到期，给它一次机会
            self._open_until.pop(engine_id, None)
            self._fails[engine_id] = 0
        return False

    def record(self, engine_id: str, ok: bool) -> bool:
        """
        记一次成败，**返回这一次是不是把它熔断了**。

        🔴 加这个返回值是为了不再撒谎。原来失败消息里写死一句
        "这家已暂时熔断"，可 `BREAK_AFTER` 是 3 —— 第一次、第二次失败时
        这句话是**假的**，引擎下一轮照跑。用户看到"已熔断"却发现它还在被调用，
        只会得出"这个提示不可信"的结论，而那正是最贵的一种代价：
        以后真熔断了他也不会信。
        """
        if ok:
            self._fails[engine_id] = 0
            return False
        n = self._fails.get(engine_id, 0) + 1
        self._fails[engine_id] = n
        if n >= BREAK_AFTER and engine_id not in self._open_until:
            self._open_until[engine_id] = time.monotonic() + BREAK_COOLDOWN_S
            log.warning(
                "引擎 %s 连续 %d 次解析失败，熔断 %d 分钟 —— 多半是对方改版了",
                engine_id, n, int(BREAK_COOLDOWN_S / 60),
            )
            return True
        return False

    def fails(self, engine_id: str) -> int:
        return self._fails.get(engine_id, 0)

    def state(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            eid: {
                "fails": self._fails.get(eid, 0),
                "openFor": max(0, int(self._open_until.get(eid, 0) - now)),
            }
            for eid in set(self._fails) | set(self._open_until)
        }


# ────────────────────────────────────────────────────────────────
# 失败消息
#
# 单独拎出来是因为这几句话是**用户唯一能看到的东西**。搜索失败时
# 界面上就剩这一行字，它写得准不准，直接决定用户下一步是去改设置、
# 去等一会儿、还是去改一个根本没坏的选择器。
# ────────────────────────────────────────────────────────────────
def _challenge_message(status: int, reason: str) -> str:
    """
    被挡下来了。**「被限流」和「被要求人机验证」不是一回事**，
    而区分它们的信号就是 HTTP 状态码：

      429 / 403  对方在协议层明说了"你太快了" —— 等一等真的会好
      其它（多半是 200）  页面本身是一张验证码/安全验证页 ——
                        等不一定管用，可能要降并发、换时间，甚至换条路

    引擎自己给了 `reason` 就用它的，它比这里知道得具体。
    """
    if reason:
        return reason
    if status in (403, 429):
        return f"被限流（HTTP {status}）—— 稍后会自动恢复，不是这家坏了"
    return f"被要求人机验证（页面回了验证码，HTTP {status}）—— 降低频率或换个时间就能恢复"


def _timeout_message(engine: BaseEngine, deadline_s: float) -> str:
    """
    超时。**渲染类引擎要单说**：它们的耗时里有一段是在桌面端的渲染通道
    里排队，说一句"超时，本轮放弃"会让人以为是网络慢，
    而实际能做的事完全不同（关掉另一家渲染引擎、或者干脆别同时开两家）。
    """
    if engine.needs_browser:
        return (
            f"浏览器渲染没在 {deadline_s:.0f}s 内跑完，本轮放弃 —— "
            "同时开多家渲染引擎（Google / Yandex）时它们要排队，"
            "只留一家会明显更稳。这次不计入这家的失败次数"
        )
    return f"超时（>{deadline_s:.1f}s），本轮放弃"


def _broken_message(reason: str, just_opened: bool, fails: int) -> str:
    """
    真出问题了。三段拼起来：**具体原因 + 还剩几次就熔断 + 现在的状态**。

    原来只有一句写死的"页面结构不认识了 —— 多半是对方改版，这家已暂时熔断"，
    两处都不准：原因未必是改版（可能是要求 JS、可能是端点废了），
    而"已暂时熔断"在前两次失败时是假的。
    """
    head = reason or "页面结构不认识了 —— 多半是对方改版"
    if just_opened:
        return f"{head}（连续失败 {fails} 次，已熔断 {int(BREAK_COOLDOWN_S / 60)} 分钟）"
    left = max(0, BREAK_AFTER - fails)
    if left:
        return f"{head}（第 {fails} 次失败，再失败 {left} 次就会暂停这家）"
    return head


class MetaSearch:
    """并发元搜索。一个实例常驻，持有熔断状态和缓存。"""

    def __init__(
        self,
        *,
        enabled: list[str] | None = None,
        keys: dict[str, str] | None = None,
        renderer: Any | None = None,
        state_path: Any | None = None,
        lineup_size: int = 0,
    ) -> None:
        self.keys = dict(keys or {})
        #: S1 引擎排班器。`lineup_size > 0` 时每轮只派表现最好的那几家 +
        #: 一个探索位；0 = 全派（旧行为）。默认 0 是刻意的 ——
        #: 排班要先有历史数据才有意义，冷启动时全派反而更快收敛
        self.scheduler = EngineScheduler(state_path)
        self.lineup_size = max(0, int(lineup_size))
        #: 浏览器渲染代理（`render_broker.RenderBroker`）。
        #: 只要求它有 `.available`（bool 属性）和 `async .render(url, timeout_s=)`——
        #: 用鸭子类型而不是强类型依赖，测试时随便喂一个同形状的假对象就行，
        #: 不用为了测 Google/Yandex 走真的把整个 Electron 拉起来
        self.renderer = renderer
        # 默认阵容只含**网页**引擎。学术源虽然也默认可用，但让「搜个东西」
        # 顺带去问 arXiv 是帮倒忙 —— 查论文和查网页是两件事，
        # 混在一起既慢又把结果搅浑。文献走 search_scholar()
        self.enabled = list(enabled) if enabled is not None else [
            e.id for e in all_engines(group="web") if e.default_on
        ]
        self._breaker = _Breaker()
        self._cache: dict[str, tuple[float, MetaSearchResult]] = {}

    # ── 对外 ────────────────────────────────────────────────
    async def search(
        self,
        query: str,
        *,
        engines: list[str] | None = None,
        limit: int = 20,
        lang: str = "zh",
        region: str = "",
        time_range: str | None = None,
        use_cache: bool = True,
        deadline_s: float = TOTAL_DEADLINE_S,
        enough: int | None = None,
    ) -> MetaSearchResult:
        query = (query or "").strip()
        if not query:
            return MetaSearchResult(query="", clusters=[], replies=[], elapsed_ms=0)

        picked, pre_skipped = self._pick(engines)
        # S1：按最近表现排班。**只在用户没有显式点名引擎时才排** ——
        # 他指名要 google 就该真去跑 google，哪怕它分很低，
        # 否则「我明明选了这家却没搜」是个说不清的行为
        if self.lineup_size and engines is None and len(picked) > self.lineup_size:
            picked, benched = self.scheduler.lineup(picked, size=self.lineup_size)
            pre_skipped += [
                # 被排班挤下场的同样是"没派上场"，`attempted=False`
                EngineReply(engine=eid, outcome=ParseOutcome.EMPTY,
                            attempted=False, error=why)
                for eid, why in benched
            ]
        ck = self._cache_key(query, picked, limit, lang, region, time_range)
        if use_cache:
            hit = self._cache_get(ck)
            if hit is not None:
                return hit

        # 🔴 实测踩到的坑：浏览器渲染的引擎（Google/Yandex）本身给了 12s 预算
        # （RENDER_TIMEOUT_S），但如果直接用默认的 4s 总截止时间，
        # `_fan_out` 会在 4s 整点把它当成"超时的慢引擎"直接砍掉——
        # 实测过一次：Google 请求本身在真实浏览器里跑，7.2s 才有结果，
        # 但整轮 4s 就报了"超时（>4.0s），本轮放弃"，等于这条通道从来没机会跑完。
        effective_deadline = _render_deadline(deadline_s, picked)

        # X2 早退：够 N 家就先走，不陪最慢的那家耗到硬截止。
        #
        # `enough=None` 时自动判：**用户点名了引擎就不早退** —— 沿用上面排班
        # 那条同样的道理：他指名要 google，结果因为百度先回来了就把 google 砍了，
        # 这个行为解释不清。点名 = 我要的就是这几家，慢也等。
        #
        # 🔴 但"点名"有两种，别混：用户点名要尊重；**深挖内部的变体路由
        # 也会传 engines**（`expand.route_variants` 给每个查询变体指派引擎），
        # 那不是用户的意思，是我们自己的实现细节。让它跟着"点名不早退"
        # 一起被禁掉，X3 就永远快不了 —— 所以那条链路显式传 `enough=`。
        if enough is None:
            enough = 0 if engines is not None else ENOUGH_ENGINES
        t0 = time.monotonic()
        replies = pre_skipped + await self._fan_out(
            query, picked, limit=limit, lang=lang, region=region,
            time_range=time_range, deadline_s=effective_deadline,
            enough=enough,
        )
        await self._resolve_redirects(replies)
        clusters = self._fold_and_rank([r for rep in replies for r in rep.results])
        out = MetaSearchResult(
            query=query,
            clusters=clusters[:limit],
            replies=replies,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            partial=any(r.outcome is ParseOutcome.BROKEN or r.error for r in replies),
        )
        if use_cache and clusters:
            self._cache_put(ck, out)
        return out

    async def search_scholar(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        limit: int = 25,
        deadline_s: float = 8.0,
    ) -> dict[str, Any]:
        """
        文献检索（L1）：五家学术源并发，按 DOI 合并成一份。

        截止时间比网页搜索宽（8s 而不是 4s）：学术接口本来就慢，
        而查文献这件事用户的心理预期也不同 —— 他愿意多等两秒换更全的结果。
        **PubMed 要跑两趟请求**，卡死 4 秒它基本一定超时。
        """
        from .scholar import merge_scholar

        picked, pre_skipped = self._pick(
            sources if sources is not None else [e.id for e in all_engines(group="scholar")]
        )
        t0 = time.monotonic()
        replies = pre_skipped + await self._fan_out(
            query, picked, limit=limit, lang="en", region="",
            time_range=None, deadline_s=deadline_s,
            # 🔴 文献**不早退**：按 DOI 合并本来就是要把五家的字段拼全
            #（这家有摘要、那家有引用数），少一家就是少一块，
            # 跟网页搜索"结果重复度高、少一家几乎无损"完全不是一回事
            enough=0,
        )
        papers = merge_scholar([r for rep in replies for r in rep.results])
        return {
            "query": query,
            "papers": papers[:limit],
            "sources": [
                {
                    "id": r.engine, "outcome": r.outcome.value,
                    "count": len(r.results), "elapsedMs": r.elapsed_ms,
                    **({"error": r.error} if r.error else {}),
                }
                for r in replies
            ],
            "elapsedMs": int((time.monotonic() - t0) * 1000),
            "totalBeforeMerge": sum(len(r.results) for r in replies),
            # 合并后的真实条数。**不能拿 len(papers[:limit]) 当合并结果看** ——
            # 截断和合并是两回事，混在一起会把"截掉了 30 条"误读成"合并掉了 30 条"
            "mergedCount": len(papers),
        }

    async def autodetect_local(self, *, timeout_s: float = 3.0) -> dict[str, Any]:
        """
        自动发现本机自建的 SearXNG，活着就自动启用（S2）。

        **为什么值得自动做而不是让用户去设置里勾**：装完 SearXNG 之后
        还要求用户自己去翻设置、找到那个开关、再填一遍地址——
        那是把「自动配置需要的工具与内容」这条要求做了一半就停下。
        用户装它就是为了用它，没有第二种意图。

        🔴 **判据不是"端口有响应"，是"真的搜得出东西"。**
        实测栽过：容器起来了、`/search?format=json` 也通、HTTP 200，
        但每家引擎都超时，**一条结果都没有**。按"有响应就启用"来判，
        会把一个搜不出任何东西的引擎排进默认阵容，然后每轮白等它一次。
        「没反应」和「没问题」是两件事。
        """
        eid = "searxng"
        if eid in self.enabled:
            return {"enabled": True, "reason": "已经在启用列表里"}

        e = get_engine(eid)
        if e is None:
            return {"enabled": False, "reason": "没有这个引擎"}
        base = (self.keys.get(eid) or getattr(e, "instance", "")).rstrip("/")
        if not base:
            return {"enabled": False, "reason": "没有配置实例地址"}

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s, connect=1.5)
            ) as client:
                r = await client.get(
                    f"{base}/search",
                    params={"q": "test", "format": "json"},
                    headers={"Accept": "application/json"},
                )
            if r.status_code != 200:
                return {"enabled": False, "reason": f"HTTP {r.status_code}"}
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return {"enabled": False, "reason": "连不上（没装，或者没在跑）"}

        n = len(data.get("results") or [])
        if n == 0:
            dead = data.get("unresponsive_engines") or []
            return {
                "enabled": False,
                "reason": (
                    "实例活着但一条结果都搜不出来"
                    + (f"（这几家全失败：{dead}）" if dead else "")
                    + " —— 多半是容器出不了网，看 scripts/setup-searxng.mjs 的说明"
                ),
            }

        self.enabled.append(eid)
        log.info("自动启用本机 SearXNG（%s），测试查询返回 %d 条", base, n)
        return {"enabled": True, "reason": f"本机实例可用，测试查询返回 {n} 条", "instance": base}

    def engine_health(self) -> dict[str, Any]:
        """
        引擎健康仪表盘的数据源（S1）。

        `table` 里每家都带一句人话的 `verdict` —— 用户不该需要理解
        「0.73」是什么意思才知道这家能不能用。
        """
        return {
            "enabled": list(self.enabled),
            "lineupSize": self.lineup_size,
            "breaker": self._breaker.state(),
            "cacheEntries": len(self._cache),
            "rendererAvailable": bool(self.renderer and self.renderer.available),
            "table": self.scheduler.table(all_engines()),
        }

    def reset_health(self, engine_id: str | None = None) -> dict[str, Any]:
        """
        清掉记分与熔断，让引擎从零重新学（S1）。

        **熔断状态必须一起清**，否则会出现一个自相矛盾的界面：
        记分表显示"还没有数据"，而这家引擎照样因为熔断被跳过 15 分钟 ——
        用户点了重置却发现什么都没变，只会以为按钮是坏的。
        """
        n = self.scheduler.reset(engine_id)
        if engine_id is None:
            self._breaker = _Breaker()
        else:
            self._breaker._fails.pop(engine_id, None)
            self._breaker._open_until.pop(engine_id, None)
        # 缓存也清：不清的话重置完立刻再搜一次，拿到的是重置前那一版结果，
        # 看起来像"重置没生效"
        self._cache.clear()
        return {"ok": True, "cleared": n, "engine": engine_id or "all"}

    # ── 缓存（P9）───────────────────────────────────────────
    @staticmethod
    def _cache_key(
        query: str, engines: list[BaseEngine], limit: int,
        lang: str, region: str, time_range: str | None,
    ) -> str:
        return _fingerprint(
            query, ",".join(sorted(e.id for e in engines)),
            limit, lang, region, time_range or "-",
        )

    def _cache_get(self, key: str) -> MetaSearchResult | None:
        ent = self._cache.get(key)
        if ent is None:
            return None
        ts, val = ent
        if time.monotonic() - ts > CACHE_TTL_S:
            self._cache.pop(key, None)
            return None
        # 缓存命中要**显式标出来**。不标的话用户会以为这次也真去搜了，
        # 而实际上十分钟内新发布的内容不会出现在结果里
        return MetaSearchResult(
            query=val.query, clusters=val.clusters, replies=val.replies,
            elapsed_ms=val.elapsed_ms, from_cache=True, partial=val.partial,
        )

    def _cache_put(self, key: str, val: MetaSearchResult) -> None:
        if len(self._cache) >= CACHE_MAX:
            oldest = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
            self._cache.pop(oldest, None)
        self._cache[key] = (time.monotonic(), val)

    # ── 内部 ────────────────────────────────────────────────
    def _pick(self, engines: list[str] | None) -> tuple[list[BaseEngine], list[EngineReply]]:
        """
        挑出这一轮能跑的引擎，**顺带把跑不了的原因也交回去**。

        以前这里跳过时什么都不留 —— 用户显式指定 `engines=['google']`
        且没连桌面端时，`_fan_out` 收到空列表直接返回 `[]`，
        最终结果是"0 条结果、原因是什么一个字都没有"。
        这和 R11「不许静默丢弃」是同一个问题，只是丢的是引擎不是结果。
        """
        ids = engines if engines is not None else self.enabled
        usable: list[BaseEngine] = []
        skipped: list[EngineReply] = []
        for eid in ids:
            e = get_engine(eid)
            if e is None:
                continue
            # 🔴 下面三种全部 `attempted=False`：它们是"这一轮没派上场"，
            # 不是"派出去了没搜到"。混为一谈会让健康档案自我实现地越记越差
            # （详见 `EngineReply.attempted`）
            if self._breaker.is_open(eid):
                st = self._breaker.state().get(eid, {})
                wait_min = max(1, int(st.get("openFor", 0) // 60) + 1)
                skipped.append(EngineReply(
                    engine=eid, outcome=ParseOutcome.BROKEN, attempted=False,
                    error=f"熔断中，约 {wait_min} 分钟后自动恢复",
                ))
                continue
            if e.needs_key and not self.keys.get(eid):
                skipped.append(EngineReply(
                    engine=eid, outcome=ParseOutcome.BROKEN, attempted=False,
                    error="没有配置 API Key —— 在设置页「联网搜索 · 引擎 API Key」里填一个就能用",
                ))
                continue
            if e.needs_browser and not (self.renderer and self.renderer.available):
                skipped.append(EngineReply(
                    engine=eid, outcome=ParseOutcome.BROKEN, attempted=False,
                    error="需要浏览器渲染，但没有连接到桌面端"
                          "（命令行/MCP 单独跑引擎时这条走不通，开着桌面端就行）",
                ))
                continue
            usable.append(e)
        return usable, skipped

    # ── B1 首字节竞速 ───────────────────────────────────────
    async def search_stream(
        self,
        query: str,
        *,
        engines: list[str] | None = None,
        limit: int = 20,
        lang: str = "zh",
        region: str = "",
        time_range: str | None = None,
        use_cache: bool = True,
        deadline_s: float = TOTAL_DEADLINE_S,
    ) -> Any:
        """
        B1 —— **哪家先回哪家先画**，不等最慢那家。

        这是一个异步生成器，每次 `yield` 一个事件：

            {"kind": "engines", "pending": [...]}       开跑，告诉界面在等谁
            {"kind": "partial", "engine": "bing", ...}  某家回来了，附当前已折叠结果
            {"kind": "final",   "result": {...}}        全部结束（或到点）

        **为什么这值得单独做一条路径**：现有的 `search()` 要等整波结束才返回，
        而一波里最慢那家（走浏览器渲染的 Google 能跑 7 秒）决定了用户看到
        第一个字的时间。实测阵容里最快的 mojeek 通常 400ms 就回来了 ——
        **中间那 6 秒用户面对的是一个转圈，而结果其实早就有了**。

        🔴 **折叠是增量做的，不是每次全量重算**。每来一家就把它的结果并进
        已有的簇里；全量重排会让已经画在屏幕上的条目跳来跳去，
        那比等结果更让人烦躁。所以已出现的簇**只增不重排**，
        真正的最终排序在 `final` 事件里一次给到。
        """
        query = (query or "").strip()
        if not query:
            yield {"kind": "final", "result": MetaSearchResult(
                query="", clusters=[], replies=[], elapsed_ms=0).to_dict()}
            return

        picked, pre_skipped = self._pick(engines)
        if self.lineup_size and engines is None and len(picked) > self.lineup_size:
            picked, benched = self.scheduler.lineup(picked, size=self.lineup_size)
            pre_skipped += [
                # 被排班挤下场的同样是"没派上场"，`attempted=False`
                EngineReply(engine=eid, outcome=ParseOutcome.EMPTY,
                            attempted=False, error=why)
                for eid, why in benched
            ]

        ck = self._cache_key(query, picked, limit, lang, region, time_range)
        if use_cache:
            hit = self._cache_get(ck)
            if hit is not None:
                # 缓存命中直接给 final，**不伪造 partial 事件** ——
                # 假装它是一家家回来的只会让界面上的动画骗人
                yield {"kind": "final", "result": hit.to_dict(), "fromCache": True}
                return

        effective_deadline = _render_deadline(deadline_s, picked)

        yield {
            "kind": "engines",
            "pending": [e.id for e in picked],
            "skipped": [
                {"id": r.engine, "error": r.error} for r in pre_skipped
            ],
        }

        t0 = time.monotonic()
        replies: list[EngineReply] = list(pre_skipped)
        pooled: list[WebResult] = []

        if picked:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(ENGINE_TIMEOUT, connect=5.0),
            ) as client:
                tasks = {
                    asyncio.create_task(
                        self._one(client, e, query, limit=limit, lang=lang,
                                  region=region, time_range=time_range),
                        name=e.id,
                    ): e
                    for e in picked
                }
                pending = set(tasks.keys())
                while pending:
                    left = effective_deadline - (time.monotonic() - t0)
                    if left <= 0:
                        break
                    done, pending = await asyncio.wait(
                        pending, timeout=left, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not done:
                        break
                    for t in done:
                        try:
                            rep = t.result()
                        except Exception as exc:      # noqa: BLE001
                            eid = tasks[t].id
                            self._breaker.record(eid, ok=False)
                            rep = EngineReply(
                                engine=eid, outcome=ParseOutcome.BROKEN,
                                error=f"{type(exc).__name__}: {exc}")
                        replies.append(rep)
                        pooled += rep.results
                        # 增量折叠：只对目前收到的结果折一次，给界面一版能画的
                        clusters = self._fold_and_rank(pooled)
                        yield {
                            "kind": "partial",
                            "engine": rep.engine,
                            "outcome": rep.outcome.value,
                            "count": len(rep.results),
                            "elapsedMs": rep.elapsed_ms,
                            "totalMs": int((time.monotonic() - t0) * 1000),
                            "results": [c.to_dict() for c in clusters[:limit]],
                            "waiting": [tasks[p].id for p in pending],
                        }
                # 到点没回来的照实记一条超时，理由同 `_fan_out`
                for t in pending:
                    t.cancel()
                    eid = tasks[t].id
                    replies.append(EngineReply(
                        engine=eid, outcome=ParseOutcome.BROKEN,
                        error=_timeout_message(tasks[t], effective_deadline)))
                    if not tasks[t].needs_browser:   # 理由同 `_fan_out`
                        self._breaker.record(eid, ok=False)
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

        for r in replies:
            # `attempted=False` 的一律不记：没派上场不是一次观测（见 `EngineReply.attempted`）。
            # 🔴 这一路原来是漏的 —— `_fan_out` 那条路径的 `replies` 里
            # 本来就不含被跳过的引擎，而流式这条把 `pre_skipped` 一起拼进来了，
            # 于是**同一家引擎走不同路径搜，健康档案记的东西不一样**
            if r.engine and r.attempted:
                self.scheduler.observe(r.engine, r.outcome, r.elapsed_ms)
        self.scheduler.save()

        await self._resolve_redirects(replies)
        pooled = [r for rep in replies for r in rep.results]
        result = MetaSearchResult(
            query=query,
            clusters=self._fold_and_rank(pooled),
            replies=sorted(replies, key=lambda r: r.engine),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
        if use_cache:
            self._cache_put(ck, result)
        yield {"kind": "final", "result": result.to_dict()}

    # ── B4 缓存预热 ─────────────────────────────────────────
    async def prewarm(
        self, queries: list[str], *, limit: int = 20, lang: str = "zh"
    ) -> dict[str, Any]:
        """
        B4 —— 冷启动时把几个常用词先搜一遍填进缓存。

        🔴 **串行跑，不并发**。预热是后台的白工，它唯一不该做的事就是
        跟用户当下的那次搜索抢带宽和连接数。串行慢十几秒无所谓 ——
        没人在等它。

        🔴 **只在联网开关是开的时候才该被调用**，判断在调用方
        （`runtime.warmup_async`）。这里不重复判，避免两处逻辑走岔。
        """
        ok = 0
        errs: list[str] = []
        for q in [x.strip() for x in queries if x and x.strip()][:8]:
            try:
                await self.search(q, limit=limit, lang=lang, use_cache=True)
                ok += 1
            except Exception as exc:      # noqa: BLE001
                errs.append(f"{q}: {type(exc).__name__}")
        return {"warmed": ok, "errors": errs, "cacheEntries": len(self._cache)}

    def cache_stats(self) -> dict[str, Any]:
        """给 G7 指标用：缓存里有多少条、最老的一条还有多久过期。"""
        now = time.monotonic()
        ages = [now - ts for ts, _v in self._cache.values()]
        return {
            "entries": len(self._cache),
            "ttlSeconds": CACHE_TTL_S,
            "oldestAgeSeconds": int(max(ages)) if ages else 0,
            "capacity": CACHE_MAX,
        }

    async def _fan_out(
        self,
        query: str,
        engines: list[BaseEngine],
        *,
        limit: int,
        lang: str,
        region: str,
        time_range: str | None,
        deadline_s: float,
        enough: int = ENOUGH_ENGINES,
    ) -> list[EngineReply]:
        if not engines:
            return []

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(ENGINE_TIMEOUT, connect=5.0),
        ) as client:
            tasks = {
                asyncio.create_task(
                    self._one(client, e, query, limit=limit, lang=lang,
                              region=region, time_range=time_range),
                    name=e.id,
                ): e
                for e in engines
            }

            replies: list[EngineReply] = []
            pending = set(tasks)
            started = time.monotonic()
            good = 0        # 真给了结果的
            landed = 0      # 有回音的（含空结果、含验证码）
            # 门槛按**这一轮真派出去的家数**算，不是按理想阵容
            need = min(len(tasks), max(ENOUGH_MIN, math.ceil(len(tasks) * ENOUGH_RATIO)))
            if enough > 0:
                need = min(need, enough)
            #: 攒够之后的宽限截止；None = 还没攒够
            grace_until: float | None = None
            #: 早退了吗 —— 决定剩下那些没等的引擎该怎么记（见下面）
            bailed_early = False

            first_good_at: float | None = None
            while pending:
                now = time.monotonic()
                left = deadline_s - (now - started)
                if left <= 0:
                    break
                # 三条截止取最早的一条：硬截止 / 够数后的宽限 / 首条结果后的上界
                cutoff = None
                if grace_until is not None:
                    cutoff = grace_until
                if enough > 0 and first_good_at is not None:
                    cap = first_good_at + MAX_WAIT_AFTER_FIRST_GOOD_S
                    cutoff = cap if cutoff is None else min(cutoff, cap)
                if cutoff is not None:
                    left = min(left, cutoff - now)
                    if left <= 0:
                        bailed_early = True
                        break

                batch, pending = await asyncio.wait(
                    pending, timeout=left, return_when=asyncio.FIRST_COMPLETED,
                )
                if not batch:           # 等到点了，一个都没回来
                    break

                for t in batch:
                    try:
                        r = t.result()
                    except Exception as exc:  # noqa: BLE001
                        eid = tasks[t].id
                        self._breaker.record(eid, ok=False)
                        r = EngineReply(engine=eid, outcome=ParseOutcome.BROKEN,
                                        error=f"{type(exc).__name__}: {exc}")
                    replies.append(r)
                    landed += 1
                    if r.outcome is ParseOutcome.OK and r.results:
                        good += 1
                        if first_good_at is None:
                            first_good_at = time.monotonic()

                # 两个条件都要满足才早退：
                #   good >= 1  —— 手里得**真有结果**。全是验证码页也算"够了"
                #                 就走，那是把慢换成了错，比慢严重得多
                #   landed >= need —— 大部分引擎已经有回音了
                # 只用 good 计数是第一版的错：阵容一退化就永远凑不满，
                # 早退形同虚设；只用 landed 又可能在一条结果都没有时收手
                if grace_until is None and enough > 0 and good >= 1 and landed >= need:
                    grace_until = time.monotonic() + GRACE_AFTER_ENOUGH_S

            # 剩下没回来的分两种，**必须分开记**：
            #   早退 —— 我们主动不等它了，它没有任何过错
            #   超时 —— 它确实拖到了硬截止
            # 混为一谈的代价很具体：早退每次都发生在"最慢的那几家"身上，
            # 若按失败记账，排班分会被一路扣到底，最后把它们全踢出阵容 ——
            # 于是它们再没机会证明自己，扣分变成自我实现的预言。
            # 所以早退的那几家**既不熔断也不喂排班器**，只如实告诉用户没等。
            early: set[str] = set()
            for t in pending:
                t.cancel()
                eid = tasks[t].id
                if bailed_early:
                    early.add(eid)
                    replies.append(
                        EngineReply(engine=eid, outcome=ParseOutcome.EMPTY,
                                    error=f"已有 {good} 家回来，本轮没等它")
                    )
                else:
                    replies.append(
                        EngineReply(engine=eid, outcome=ParseOutcome.BROKEN,
                                    error=_timeout_message(tasks[t], deadline_s))
                    )
                    # 🔴 渲染类引擎的超时**不记它的账**：它慢不慢由桌面端的
                    # 渲染通道决定，不由它自己的解析器决定。原来一视同仁地记，
                    # 结果是 Google/Yandex 只要连着三轮排队没轮上就被熔断 15 分钟，
                    # 而它们其实一次都没被真正调用过
                    if not tasks[t].needs_browser:
                        self._breaker.record(eid, ok=False)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        # S1：喂给排班器。**放在这里而不是 `_one` 里面**，因为超时那一路
        # 根本不经过 `_one` —— 记在 `_one` 里的话，一家老是超时的引擎
        # 永远不会被记一次失败，排班就成了摆设
        for r in replies:
            if r.engine in early or not r.attempted:
                continue
            self.scheduler.observe(r.engine, r.outcome, r.elapsed_ms)
        self.scheduler.save()

        replies.sort(key=lambda r: r.engine)
        return replies

    async def _one(
        self,
        client: httpx.AsyncClient,
        engine: BaseEngine,
        query: str,
        *,
        limit: int,
        lang: str,
        region: str,
        time_range: str | None,
    ) -> EngineReply:
        if engine.needs_browser:
            return await self._one_browser(
                engine, query, limit=limit, lang=lang, region=region, time_range=time_range,
            )

        t0 = time.monotonic()
        try:
            status, outcome, results, reason = await engine.run(
                client, query, limit=limit, lang=lang, region=region,
                time_range=time_range, key=self.keys.get(engine.id),
            )
        except httpx.HTTPError as e:
            self._breaker.record(engine.id, ok=False)
            return EngineReply(
                engine=engine.id, outcome=ParseOutcome.BROKEN,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                error=f"请求失败：{type(e).__name__}",
            )
        except Exception as e:  # noqa: BLE001 — 解析器炸了不该让整轮搜索失败
            self._breaker.record(engine.id, ok=False)
            return EngineReply(
                engine=engine.id, outcome=ParseOutcome.BROKEN,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                error=f"解析异常：{type(e).__name__}: {e}",
            )

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if outcome is ParseOutcome.CHALLENGED:
            # 被限流 ≠ 解析器坏了。**不计入熔断的失败次数** ——
            # 熔断是为了停用一个已经废掉的解析器，而限流过一阵就恢复。
            # 把它算进去会让好引擎被永久停用
            #
            # 🔴 这里原来无脑写「被限流（HTTP {status}）」，于是百度那条
            # 被送到验证页、HTTP 仍是 200 的情况，界面上写的是
            # **「被限流（HTTP 200）」** —— 一句自相矛盾的话：
            # 200 是正常响应，怎么会同时是"被限流"？用户看到这句只能困惑。
            # 而下面那个 `elif CHALLENGED` 分支（本来写着正确的措辞）
            # 因为这里已经 return 了，**一次都没执行过**，是死代码。
            return EngineReply(
                engine=engine.id, outcome=outcome, elapsed_ms=elapsed_ms,
                error=_challenge_message(status, reason),
            )

        # 只有 BROKEN 才算失败。EMPTY 是有效答案（这个词确实没结果），
        # CHALLENGED 是"稍后再来"，两者都不该把引擎熔断掉
        if outcome is ParseOutcome.BROKEN:
            just_opened = self._breaker.record(engine.id, ok=False)
            err = _broken_message(reason, just_opened, self._breaker.fails(engine.id))
        else:
            self._breaker.record(engine.id, ok=True)
            err = ""
        return EngineReply(
            engine=engine.id, outcome=outcome, results=results,
            elapsed_ms=elapsed_ms, error=err,
        )

    async def _one_browser(
        self,
        engine: BaseEngine,
        query: str,
        *,
        limit: int,
        lang: str,
        region: str,
        time_range: str | None,
    ) -> EngineReply:
        """
        走浏览器渲染的引擎（Google/Yandex）单独一条路径。

        和普通引擎的关键区别：**渲染失败不算这家引擎的解析器坏了**，
        不能计入熔断 —— 桌面端没连上是环境问题，不是"对方改版了"，
        算进熔断只会让引擎在桌面端重新连上之后还白白停用一段时间。
        """
        t0 = time.monotonic()
        # 只用它算出目标 URL，不发请求 —— 请求交给渲染代理去发
        req = engine.build(query, limit=limit, lang=lang, region=region,
                           time_range=time_range, key=None)
        assert self.renderer is not None  # `_pick` 已经过滤过，这里必然有

        html = await self.renderer.render(str(req.url), timeout_s=RENDER_TIMEOUT_S)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if html is None:
            # 🔴 **不计入熔断**（和这个方法开头说的一样）：渲染没回来是
            # 桌面端那边的事 —— 没连上、在排队、或者这一页确实太重。
            # 把它算成"这家引擎坏了"，等桌面端恢复之后引擎还要白停 15 分钟。
            # 用 EMPTY 而不是 BROKEN，是为了让排班器也别扣它的分
            return EngineReply(
                engine=engine.id, outcome=ParseOutcome.EMPTY, elapsed_ms=elapsed_ms,
                error=(
                    f"桌面端没有在 {RENDER_TIMEOUT_S:.0f}s 内返回渲染结果 —— "
                    "可能是页面太重、渲染通道排队中，或者桌面端刚好断开。"
                    "这不是这家引擎坏了"
                ),
            )

        resp = httpx.Response(200, request=req, content=html.encode("utf-8"))
        try:
            outcome, results, reason = split_parse(engine.parse(resp))
        except Exception as e:  # noqa: BLE001
            self._breaker.record(engine.id, ok=False)
            return EngineReply(
                engine=engine.id, outcome=ParseOutcome.BROKEN, elapsed_ms=elapsed_ms,
                error=f"渲染后解析异常：{type(e).__name__}: {e}",
            )

        if outcome is ParseOutcome.CHALLENGED:
            # 渲染出来的是验证码页。这也不该熔断 —— 同 `_one` 里的道理
            return EngineReply(
                engine=engine.id, outcome=outcome, elapsed_ms=elapsed_ms,
                error=_challenge_message(200, reason),
            )
        if outcome is ParseOutcome.BROKEN:
            just_opened = self._breaker.record(engine.id, ok=False)
            head = reason or "渲染后页面结构仍不认识 —— 多半是真的改版了"
            err = _broken_message(
                f"{head}（页面已经拿到了，这不是渲染失败）",
                just_opened, self._breaker.fails(engine.id),
            )
        else:
            self._breaker.record(engine.id, ok=True)
            err = ""
        return EngineReply(
            engine=engine.id, outcome=outcome, results=results,
            elapsed_ms=elapsed_ms, error=err,
        )

    # ── 跳转链解析 ──────────────────────────────────────────
    async def _resolve_redirects(self, replies: list[EngineReply]) -> None:
        """
        把百度那类 `baidu.com/link?url=…` 的跳转地址换成真实网址。

        **为什么非解不可**：不解的话每条结果的域名都是 baidu.com ——
        来源分级会全判成同一个站、跨引擎折叠会把不相关的文章合并、
        「几个独立来源」永远算成 1。整个可信度体系直接失效。

        只发 HEAD 且不跟随跳转，读 Location 头就够了，比 GET 便宜得多。
        解不出来的**保留原样并标记**，不假装解成功了。
        """
        targets = [
            r for rep in replies for r in rep.results
            if r.meta.get("redirect") and "/link?" in r.url
        ]
        if not targets:
            return

        async def one(client: httpx.AsyncClient, r: WebResult) -> None:
            try:
                resp = await client.head(r.url, follow_redirects=False)
                loc = resp.headers.get("location") or ""
                if loc.startswith("http"):
                    r.url = loc
                    r.site = ""
                    r.__post_init__()  # 重算 site
                    r.meta.pop("redirect", None)
                else:
                    r.meta["unresolved"] = True
            except httpx.HTTPError:
                r.meta["unresolved"] = True

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(4.0, connect=2.5),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            # 整批给 3 秒预算 —— 解析跳转是锦上添花，不该拖慢整轮搜索。
            # 超时的那些保留跳转链并标 unresolved，界面会显示"来源未知"
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(one(client, r) for r in targets),
                                   return_exceptions=True),
                    timeout=3.0,
                )
            except TimeoutError:
                for r in targets:
                    r.meta.setdefault("unresolved", True)

    # ── 折叠 + 融合（W4）────────────────────────────────────
    def _fold_and_rank(self, results: list[WebResult]) -> list[Cluster]:
        # 第一趟：按规范化网址合。同一个地址必然是同一篇，最强的信号先用
        by_url: dict[str, Cluster] = {}
        for r in results:
            key = _url_key(r)
            c = by_url.get(key)
            if c is None:
                by_url[key] = c = Cluster(best=r)
            self._absorb(c, r)

        # 第二趟：网址不同、但标题+摘要都像的，合成一条（转载 / 镜像 / AMP）
        clusters: dict[str, Cluster] = {}
        for key, c in by_url.items():
            tkey = _title_key(c.best)
            merge_key = tkey or key
            got = clusters.get(merge_key)
            if got is None:
                clusters[merge_key] = c
                continue
            got.engines |= c.engines
            got.sites |= c.sites
            got.ranks.extend(c.ranks)
            got.variants.extend(c.variants)
            if len(c.best.snippet) > len(got.best.snippet):
                got.best = c.best

        for c in clusters.values():
            # RRF：每家引擎按名次贡献 1/(K+rank)。
            # 同一家给的多个名次只取最好的那个，否则一家引擎把同一篇
            # 以不同 URL 列了三次就能把它顶上去
            per_engine: dict[str, int] = {}
            for eid, rank in c.ranks:
                per_engine[eid] = min(per_engine.get(eid, 10**6), rank)
            c.score = sum(1.0 / (RRF_K + rank) for rank in per_engine.values())

        out = sorted(clusters.values(), key=lambda c: (-c.score, c.best.title))
        return out

    @staticmethod
    def _absorb(c: Cluster, r: WebResult) -> None:
        c.engines.add(r.engine)
        c.sites.add(r.site)
        c.ranks.append((r.engine, r.rank))
        c.variants.append(r)
        # 保留信息最全的那份做代表：有摘要 > 没摘要，摘要长 > 摘要短
        if len(r.snippet) > len(c.best.snippet):
            c.best = r


_TITLE_NOISE = re.compile(r"[\s\-_—–|｜·・:：,，.。!！?？()（）\[\]【】\"'“”‘’]+")


def canonical_url(url: str) -> str:
    """
    跨引擎折叠专用的规范化。

    比 `url_fingerprint` 更狠一点：**连 www. 和 http/https 的差别也抹掉**。
    实测就栽在这儿 —— Bing 给 `https://sqlite.org/wal.html`、
    Mojeek 给 `https://www.sqlite.org/wal.html`，同一个页面，
    但指纹不同 → 折叠一条都没合上 → 「被几家同时搜到」永远是 0 →
    R2 交叉印证跟着一起失效。

    没有直接改 `url_fingerprint`：那个函数是入库去重在用的，
    在那边 www 与否可能对应真实的不同站点配置，不该被这里的需要牵着改。
    """
    try:
        u = urlparse(url)
        host = u.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return url_fingerprint(f"https://{host}{u.path}" + (f"?{u.query}" if u.query else ""))
    except ValueError:
        return url_fingerprint(url)


def _url_key(r: WebResult) -> str:
    """第一趟：同一个网址一定是同一篇。**最强的信号，必须先用它。**"""
    return "u:" + canonical_url(r.url)


def _title_key(r: WebResult) -> str | None:
    """
    第二趟：网址不同但可能是同一篇（转载、镜像、AMP）。

    只用标题会误折 —— 「2024 年度总结」这种标题，不同站点各有一篇，
    它们是不同文章。所以标题相同时**还要看摘要**，两者都像才敢合并。
    """
    title_key = _TITLE_NOISE.sub("", (r.title or "").lower())[:60]
    if len(title_key) < 6:
        return None
    body_key = _TITLE_NOISE.sub("", (r.snippet or "").lower())[:40]
    if not body_key:
        return None
    return "t:" + hashlib.sha1(f"{title_key}|{body_key}".encode()).hexdigest()[:20]


def _fold_key(r: WebResult) -> str:
    """单条结果的折叠键。测试和调试用；真正的折叠走 `_fold_and_rank` 的两趟。"""
    return _title_key(r) or _url_key(r)


def _fingerprint(*parts: Any) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:24]


# ────────────────────────────────────────────────────────────────
# B7 站点独立性
# ────────────────────────────────────────────────────────────────
#: 同一家公司的多个域名。**手工维护的短名单，不追求全** ——
#: 目的是把最常见的"看起来是三个站其实是一家"揭出来，
#: 而不是建一个永远维护不完的股权关系库
_SAME_OWNER: dict[str, str] = {
    "sina.com.cn": "新浪", "sina.cn": "新浪", "weibo.com": "新浪",
    "qq.com": "腾讯", "tencent.com": "腾讯",
    "163.com": "网易", "126.com": "网易",
    "sohu.com": "搜狐", "focus.cn": "搜狐",
    "baidu.com": "百度", "baijiahao.baidu.com": "百度",
    "toutiao.com": "字节", "ixigua.com": "字节", "douyin.com": "字节",
    "medium.com": "Medium", "substack.com": "Substack",
    "csdn.net": "CSDN", "51cto.com": "51CTO",
}

#: 通稿分发平台。这些站上的内容**默认不算独立来源** ——
#: 它们的商业模式就是把同一份稿子发到尽可能多的地方
_SYNDICATION = (
    "prnewswire", "businesswire", "globenewswire", "美通社",
    "eastmoney.com/a/", "finance.sina", "cnfol", "stockstar",
)


def _registrable(site: str) -> str:
    """
    取可注册域。**只做两段和三段（.com.cn / .co.uk）两种情况** ——
    完整的公共后缀列表有几千条且每月都在变，为了一个"给个大概判断"的
    功能去背那张表不划算，判错的代价只是少合并一组。
    """
    host = str(site or "").lower().strip().lstrip("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if parts[-2] in ("com", "net", "org", "gov", "edu", "co", "ac") and len(parts[-1]) <= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def site_independence(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    """
    B7 —— 同一个说法，**有几个真正独立的站在说**。

    「几个引擎搜到」和「几个站在说」是两回事（坑 43 的结论），
    而「几个站在说」和「几个**独立**的站在说」又是第三回事：
    三个新浪系的站发同一条，独立来源数是 1 不是 3。

    返回每个簇的 `independence` 字段：
        `sites`        去重后的站点数
        `owners`       归并同一集团后的数量  ← **这个才是"独立来源"**
        `syndicated`   其中有几个是通稿分发平台
        `verdict`      一句人话

    🔴 **独立来源少 ≠ 消息是假的**。独家报道天然只有一个来源，
    而那往往是最有价值的那条。所以这里只报事实，不做可信度加减。
    """
    out: list[dict[str, Any]] = []
    for c in clusters:
        sites = set()
        for s in (c.get("sites") or []):
            if s:
                sites.add(_registrable(str(s)))
        best = c.get("best") or {}
        if best.get("site"):
            sites.add(_registrable(str(best["site"])))

        owners = set()
        syndicated = 0
        for s in sites:
            owners.add(_SAME_OWNER.get(s, s))
            if any(k in s for k in _SYNDICATION):
                syndicated += 1

        n = len(owners)
        if n <= 1:
            verdict = "只有一个独立来源在说这件事"
        elif syndicated and syndicated >= n - 1:
            verdict = f"{n} 个来源里 {syndicated} 个是通稿分发平台，实际独立性存疑"
        elif n >= 4:
            verdict = f"{n} 个互相独立的来源都在说"
        else:
            verdict = f"{n} 个独立来源"

        out.append({
            "url": str(best.get("url") or ""),
            "title": str(best.get("title") or ""),
            "sites": sorted(sites),
            "siteCount": len(sites),
            "owners": sorted(owners),
            "ownerCount": n,
            "syndicated": syndicated,
            "verdict": verdict,
        })

    lone = sum(1 for x in out if x["ownerCount"] <= 1)
    return {
        "items": out,
        "loneSourceCount": lone,
        "note": (
            f"{len(out)} 组结果里有 {lone} 组只有单一独立来源。"
            "**单一来源不代表是假的** —— 独家报道天然如此，"
            "而且往往正是最有价值的那条。这里只报数，不加减可信度"
        ),
    }
