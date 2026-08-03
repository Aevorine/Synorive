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
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from ..ingest.web import url_fingerprint
from .engines import BaseEngine, EngineReply, ParseOutcome, WebResult, all_engines, get_engine
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

#: 熔断：连续 N 次解析失败就停用一段时间
BREAK_AFTER = 3
BREAK_COOLDOWN_S = 900.0

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

    def record(self, engine_id: str, ok: bool) -> None:
        if ok:
            self._fails[engine_id] = 0
            return
        n = self._fails.get(engine_id, 0) + 1
        self._fails[engine_id] = n
        if n >= BREAK_AFTER:
            self._open_until[engine_id] = time.monotonic() + BREAK_COOLDOWN_S
            log.warning(
                "引擎 %s 连续 %d 次解析失败，熔断 %d 分钟 —— 多半是对方改版了",
                engine_id, n, int(BREAK_COOLDOWN_S / 60),
            )

    def state(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            eid: {
                "fails": self._fails.get(eid, 0),
                "openFor": max(0, int(self._open_until.get(eid, 0) - now)),
            }
            for eid in set(self._fails) | set(self._open_until)
        }


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
                EngineReply(engine=eid, outcome=ParseOutcome.EMPTY, error=why)
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
        # 只要选中的引擎里有一个要浏览器渲染，就把总预算放宽到能装下它
        needs_longer = any(e.needs_browser for e in picked)
        effective_deadline = max(deadline_s, RENDER_TIMEOUT_S + 2.0) if needs_longer else deadline_s

        t0 = time.monotonic()
        replies = pre_skipped + await self._fan_out(
            query, picked, limit=limit, lang=lang, region=region,
            time_range=time_range, deadline_s=effective_deadline,
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
            if self._breaker.is_open(eid):
                st = self._breaker.state().get(eid, {})
                wait_min = max(1, int(st.get("openFor", 0) // 60) + 1)
                skipped.append(EngineReply(
                    engine=eid, outcome=ParseOutcome.BROKEN,
                    error=f"熔断中，约 {wait_min} 分钟后自动恢复",
                ))
                continue
            if e.needs_key and not self.keys.get(eid):
                skipped.append(EngineReply(
                    engine=eid, outcome=ParseOutcome.BROKEN,
                    error="没有配置 API Key，去设置里填一个才能用",
                ))
                continue
            if e.needs_browser and not (self.renderer and self.renderer.available):
                skipped.append(EngineReply(
                    engine=eid, outcome=ParseOutcome.BROKEN,
                    error="需要浏览器渲染，但没有连接到桌面端"
                          "（命令行/MCP 单独跑引擎时这条走不通，开着桌面端就行）",
                ))
                continue
            usable.append(e)
        return usable, skipped

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
            done, pending = await asyncio.wait(tasks.keys(), timeout=deadline_s)

            replies: list[EngineReply] = []
            for t in done:
                try:
                    replies.append(t.result())
                except Exception as exc:  # noqa: BLE001
                    eid = tasks[t].id
                    self._breaker.record(eid, ok=False)
                    replies.append(
                        EngineReply(engine=eid, outcome=ParseOutcome.BROKEN,
                                    error=f"{type(exc).__name__}: {exc}")
                    )
            # 到点还没回来的：取消并如实记一条超时，**不装作它没参与**。
            # 少了这条，用户会以为"这家引擎没搜到东西"，而真相是根本没等它
            for t in pending:
                t.cancel()
                eid = tasks[t].id
                replies.append(
                    EngineReply(engine=eid, outcome=ParseOutcome.BROKEN,
                                error=f"超时（>{deadline_s:.1f}s），本轮放弃")
                )
                self._breaker.record(eid, ok=False)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        # S1：喂给排班器。**放在这里而不是 `_one` 里面**，因为超时那一路
        # 根本不经过 `_one` —— 记在 `_one` 里的话，一家老是超时的引擎
        # 永远不会被记一次失败，排班就成了摆设
        for r in replies:
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
            status, outcome, results = await engine.run(
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

        if outcome is ParseOutcome.CHALLENGED:
            # 被限流 ≠ 解析器坏了。**不计入熔断的失败次数** ——
            # 熔断是为了停用一个已经废掉的解析器，而限流过一阵就恢复。
            # 把它算进去会让好引擎被永久停用
            return EngineReply(
                engine=engine.id, outcome=outcome,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                error=f"被限流（HTTP {status}）—— 稍后会自动恢复，不是这家坏了",
            )

        # 只有 BROKEN 才算失败。EMPTY 是有效答案（这个词确实没结果），
        # CHALLENGED 是"稍后再来"，两者都不该把引擎熔断掉
        if outcome is ParseOutcome.BROKEN:
            self._breaker.record(engine.id, ok=False)
            err = "页面结构不认识了 —— 多半是对方改版，这家已暂时熔断"
        elif outcome is ParseOutcome.CHALLENGED:
            err = "被要求人机验证 —— 换个时间或降低频率就能恢复，不是这家坏了"
        else:
            self._breaker.record(engine.id, ok=True)
            err = ""
        return EngineReply(
            engine=engine.id, outcome=outcome, results=results,
            elapsed_ms=int((time.monotonic() - t0) * 1000), error=err,
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
            return EngineReply(
                engine=engine.id, outcome=ParseOutcome.BROKEN, elapsed_ms=elapsed_ms,
                error="桌面端没有返回渲染结果（超时，或者刚好这时候断开了）",
            )

        resp = httpx.Response(200, request=req, content=html.encode("utf-8"))
        try:
            outcome, results = engine.parse(resp)
        except Exception as e:  # noqa: BLE001
            self._breaker.record(engine.id, ok=False)
            return EngineReply(
                engine=engine.id, outcome=ParseOutcome.BROKEN, elapsed_ms=elapsed_ms,
                error=f"渲染后解析异常：{type(e).__name__}: {e}",
            )

        if outcome is ParseOutcome.BROKEN:
            self._breaker.record(engine.id, ok=False)
            err = "渲染后页面结构仍不认识 —— 多半是真的改版了（这不是渲染失败，页面已经拿到了）"
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
