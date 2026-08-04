"""
搜索引擎适配器 —— W1 / W3 / W9 / W12
====================================================================
把各家引擎抹平成同一个接口：给一个查询词，回一批 `WebResult`。

**为什么不用统一的第三方聚合库**：那类库把「解析失败」和「没有结果」
混成同一件事返回空列表，而这两件事的处理方式完全相反 ——
没有结果是正常的，解析失败意味着对方改版了、这个引擎已经废了，
必须熔断并告诉用户，否则用户会以为「全网没有这个东西」。
所以这里每个解析器都必须能区分这两种情况（见 `ParseOutcome`）。

**分两类**（对应你选的「混合接入」）：
  · API 类（`kind="api"`）—— 官方 JSON 接口，稳定，但多数要 Key
  · 解析类（`kind="html"`）—— 直接读搜索结果页，不要 Key，
    但**对方改版就会失效**。所以每个解析器都写了多套选择器依次兜底，
    并且解析不出来时明确报 `broken` 而不是装作没结果。

Google 和 Yandex 没有免费官方 API，只能走解析类 —— 这一点在
菜单里已经如实标注过，不是这里偷懒。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

log = logging.getLogger("synorive.websearch")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ParseOutcome(str, Enum):
    """
    解析结果的四种状态。**必须分开**，因为处理方式完全不同：

      ok          拿到结果，正常
      empty       页面正常但确实没有结果 —— 这是**有效答案**，不该熔断
      challenged  被要求验证码 / 被限流 —— 慢一点或换时间就好，**不是坏了**
      broken      页面结构不认识了（对方改版）—— 真的坏了，要熔断并提示

    这四种曾经被我压成两种，代价很具体：
    百度触发验证码时报的是"多半是对方改版"，而正确的处置是**降速重试**。
    报错报错了方向，下次排查就会去改一个根本没坏的选择器。
    """

    OK = "ok"
    EMPTY = "empty"
    CHALLENGED = "challenged"
    BROKEN = "broken"


@dataclass
class WebResult:
    """一条联网搜索结果。这一层只负责**如实带回**，不做任何可信度判断。"""

    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    #: 在该引擎结果里的名次，从 1 开始。RRF 融合要用
    rank: int = 0
    site: str = ""
    published: str | None = None
    #: 学术源专用（L1）：DOI / 作者 / 期刊 / 被引数
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.site:
            try:
                self.site = urlparse(self.url).netloc.lower()
            except ValueError:
                self.site = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "engine": self.engine,
            "rank": self.rank,
            "site": self.site,
        }
        if self.published:
            d["published"] = self.published
        if self.meta:
            d["meta"] = self.meta
        return d


@dataclass
class EngineReply:
    """一个引擎跑完一轮的结果 + 它自己的状态。"""

    engine: str
    outcome: ParseOutcome
    results: list[WebResult] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str = ""
    #: 这一轮**有没有真的向对方发过请求**。
    #:
    #: 🔴 False 的那几种（熔断中 / 没配 Key / 没连桌面端 / 被排班挤下场）
    #: **绝不能记进健康档案**。它们原来照样被当成一次观测写进去，后果是
    #: 一条自我实现的下坡路：一家引擎偶然失败三次 → 熔断 → 熔断期间每轮
    #: 再"观测"到一次失败 → 成功率继续掉 → 排班永远不再选它。
    #: 实测健康档案里 DOAJ/OpenAIRE 那种「68 次全空、0 次成功」的记录，
    #: 一部分就是这么攒出来的 —— **没派上场和搜了没结果，被记成了同一件事**。
    attempted: bool = True


# ────────────────────────────────────────────────────────────────
# 解析原因的透传通道
#
# 🔴 **知道原因的地方和写错误消息的地方原来是分开的。**
# `parse()` 明明知道"这是 202 JS 落地页"「这是 enablejs 跳转页」
# 「容器找到了但一条都抽不出来」，但它只能返回 `BROKEN`，
# 于是 `meta._one` 只能对所有 BROKEN 统一写一句
# 「页面结构不认识了 —— 多半是对方改版」。
# 用户看到的于是全是同一句话，而这句话对其中一半情况**是错的**：
# DuckDuckGo 不是改版了，是那个端点本来就不再返回结果了；
# Google 不是改版了，是它要求跑 JS。报错报错方向，排查就一定走错路。
#
# 所以 `parse()` 现在允许多返回一个 `reason`，一路透到界面上。
# 兼容旧形状：只返回两项时 reason 就是空串。
# ────────────────────────────────────────────────────────────────
def split_parse(res: Any) -> tuple[ParseOutcome, list[WebResult], str]:
    """把 `(outcome, results)` 和 `(outcome, results, reason)` 抹平成三元组。"""
    if isinstance(res, tuple) and len(res) == 3:
        outcome, results, reason = res
        return outcome, list(results or []), str(reason or "")
    outcome, results = res
    return outcome, list(results or []), ""


#: 撞上 429/403 之后，最多就地等这么久再补一次。
#: 再长就该让整轮搜索先出结果，而不是整轮卡在一家上等额度回血
MAX_LIMIT_RETRY_WAIT_S = 2.0
#: 对方没给 `Retry-After` 时用的默认等待
DEFAULT_LIMIT_RETRY_S = 1.0

#: 每家引擎的"上次请求时间"和一把锁，用来实现最小请求间隔。
#: 进程内共享 —— 限流是按**来源 IP** 算的，不分是哪一次搜索发起的
_pace_locks: dict[str, asyncio.Lock] = {}
_pace_last: dict[str, float] = {}


async def _pace(engine_id: str, min_interval_s: float) -> None:
    """
    同一家引擎两次请求之间至少隔 `min_interval_s`。

    🔴 **这不是"礼貌"，是止损**。深挖会把一个问题拆成好几个变体同时发出去，
    对 Semantic Scholar / OpenAlex 这类按 IP 限速的接口来说，
    并发四发和串行四发拿到的结果天差地别：前者三个 429 一个成功，
    后者四个都成功，而总耗时只多了不到两秒。
    """
    if min_interval_s <= 0:
        return
    lock = _pace_locks.setdefault(engine_id, asyncio.Lock())
    async with lock:
        wait = _pace_last.get(engine_id, 0.0) + min_interval_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(min(wait, min_interval_s))
        _pace_last[engine_id] = time.monotonic()


#: 预热首页时用的头。要像"一个人刚打开这个网站"，而不是"一个接口调用者"——
#: 少了 `Accept: text/html` 有些站直接不种 cookie
_PRIME_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


#: 预热拿到的 cookie，**按引擎缓存、跨搜索复用**，值是 `(拿到的时刻, cookie)`。
#:
#: 🔴 **没有这层缓存的话预热是净亏的**：`meta.py` 里每次搜索都是
#: `async with httpx.AsyncClient(...)` 现开一个 client，也就是**一个全新的
#: 空 cookie 罐**。只按"每个 client 一次"去重，等于每搜一次就多发一次
#: 百度首页请求 —— 而整轮总截止只有 4 秒（`meta.TOTAL_DEADLINE_S`），
#: 白搭一个往返足以让百度每次都踩线出局。
#: 那会变成一个很难看的结果：**为了修好百度而让百度变慢到用不了。**
_prime_cache: dict[str, tuple[float, httpx.Cookies]] = {}
#: cookie 放多久。百度的 BAIDUID 本身有效期很长，这里卡短一点是为了
#: 让"cookie 被对方作废了"这种情况最多影响 20 分钟，而不是一直到重启
_PRIME_TTL_S = 1200.0


def _domain_cookies(client: httpx.AsyncClient, host: str) -> httpx.Cookies:
    """把 client 罐子里**属于这个域**的 cookie 挑出来单独存一份。

    不整罐存是有意的：一轮搜索里十几家引擎共用一个 client，
    整罐存会把别家的 cookie 也缓存进来，下一轮再灌回去就成了跨站串味。
    """
    picked = httpx.Cookies()
    for c in client.cookies.jar:
        dom = (c.domain or "").lstrip(".")
        if dom and (host == dom or host.endswith(f".{dom}")):
            picked.jar.set_cookie(c)
    return picked


async def _prime(client: httpx.AsyncClient, engine: BaseEngine) -> None:
    """
    发正式请求前先备好这家的 cookie（见 `BaseEngine.prime_url`）。

    两层去重：**这个 client 已经弄过就跳过**（一轮里的多个查询变体不重复发），
    **进程里 20 分钟内拿过就直接灌回去**（跨搜索复用，不再多发一个往返）。

    **预热失败不算这家引擎失败**：正式请求照发，最差也就是退回原来那个
    验证码页 —— 让"拿 cookie 这一步网络抖了一下"直接判死一家引擎，
    是把兜底做成了新的故障源。
    """
    if not engine.prime_url:
        return
    done: set[str] | None = getattr(client, "_syn_primed", None)
    if done is None:
        done = set()
        # httpx 的 AsyncClient 没有 __slots__，可以挂属性
        client._syn_primed = done  # type: ignore[attr-defined]
    if engine.id in done:
        return
    done.add(engine.id)      # 先记后发：失败也不重试，否则整轮卡在预热上

    host = urlparse(engine.prime_url).netloc.lower()
    hit = _prime_cache.get(engine.id)
    if hit and time.monotonic() - hit[0] < _PRIME_TTL_S and len(hit[1].jar):
        for c in hit[1].jar:
            client.cookies.jar.set_cookie(c)
        return

    try:
        await client.get(engine.prime_url, headers=_PRIME_HEADERS)
    except httpx.HTTPError as e:
        log.debug("预热 %s 失败（不影响正式请求）：%s", engine.id, type(e).__name__)
        return
    got = _domain_cookies(client, host)
    if len(got.jar):
        _prime_cache[engine.id] = (time.monotonic(), got)


def _drop_prime(engine_id: str) -> None:
    """把某家缓存的 cookie 作废，下一轮重新去拿。

    在**明知这套 cookie 没起作用**的时候调（撞上验证页）。不作废的话
    一套已经失效的 cookie 会被复用满 20 分钟 —— 那正好是"修好了但看起来
    还是坏的"最容易发生的窗口。
    """
    _prime_cache.pop(engine_id, None)


def _err_hint(resp: httpx.Response) -> str:
    """
    从错误响应里挑一句**能指向根因**的话，给界面显示。

    优先读 JSON 错误体里的 `message`/`error`/`exception` —— 服务端自己写的
    那句话（比如 OpenAIRE 的「expected boolean」）比任何我猜的措辞都准。
    """
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        for k in ("message", "error", "exception", "detail", "reason"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return _clean(v)[:200]
    return _clean(resp.text)[:200] or "（对方没给任何说明）"


def _page_hint(resp: httpx.Response) -> str:
    """
    解析失败时附一句**页面长什么样**：多少字节、标题是什么。

    没有这一句的话，「找不到结果容器」这句话对排查毫无帮助 ——
    分不清是"对方改版了"（正常大小的结果页、标题是搜索词）、
    "被挡了"（几 KB、标题是验证页）还是"网络给了个错误页"。
    这三种的处理方式完全不同，而它们在旧的报错里长得一模一样。
    """
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", resp.text[:4000], re.S | re.I)
    if m:
        title = _clean(m.group(1))[:60]
    size = len(resp.content or b"")
    return f"HTTP {resp.status_code}、{size} 字节" + (f"、标题「{title}」" if title else "")


def _retry_after_s(resp: httpx.Response) -> float:
    """读 `Retry-After`。给的是秒数就用它，给日期或没给就用默认值。"""
    raw = (resp.headers.get("retry-after") or "").strip()
    if raw.isdigit():
        return float(int(raw))
    return DEFAULT_LIMIT_RETRY_S


# ────────────────────────────────────────────────────────────────
# 引擎基类
# ────────────────────────────────────────────────────────────────
class BaseEngine:
    id = ""
    label = ""
    #: "html" = 解析搜索结果页（免费但会随对方改版失效）
    #: "api"  = 官方接口（稳，多数要 Key）
    kind = "html"
    #: "web" = 通用网页搜索 ｜ "scholar" = 学术文献源。
    #: 分组是因为两者的**用法完全不同**：网页搜索要的是广度和时效，
    #: 文献检索要的是 DOI、作者、被引数这些结构化字段，混在一个列表里选不清楚
    group = "web"
    needs_key = False
    #: 默认开关。解析类里最容易被反爬挡住的几家默认关，用户可自己开
    default_on = True
    #: 这家非得跑 JavaScript 才拿得到结果（Google / Yandex）。
    #: 界面据此显示「需要开启浏览器渲染」而不是笼统的一句"这家用不了"
    needs_browser = False
    #: 一句人话，界面直接显示 —— 用户得知道每家的代价
    note = ""
    #: 同一家两次请求之间至少隔这么久（秒）。0 = 不限。
    #: 只给**按 IP 限速的免 Key 接口**设，解析类引擎设了只会白白变慢
    min_interval_s = 0.0
    #: 撞上 429/403 值不值得就地等一小会儿补一发。
    #: 学术接口的限流窗口是秒级，等一秒就好；搜索引擎的验证码是分钟级，
    #: 就地重试只是再撞一次墙，所以默认关
    retry_on_limit = False
    #: 被限流时告诉用户**能做什么**。空串就用通用那句。
    #: 「稍后会自动恢复」对一个每次都撞 429 的接口来说是句废话
    limit_hint = ""
    #: 正式发请求之前先访问一次这个地址，把对方种的 cookie 拿到手。空 = 不预热。
    #:
    #: 🔴 **这不是"伪装成浏览器"，是实测出来的必要步骤**（2026-08-04）：
    #: 百度在**没有 BAIDUID 这个 cookie 的情况下必定**把请求送到 wappass
    #: 安全验证页 —— 换 UA、降频率、加 Referer 全都没用，因为根因不是
    #: "请求太密"而是"这个来源看起来根本没打开过百度"。
    #: 先 GET 一次首页拿到 cookie 再搜，同一台机器同一秒就直接出结果页。
    #: 之前界面上那句「连续请求太密会触发，隔一会儿就恢复」是**误诊**：
    #: 按它去做（等一会儿再搜）永远等不到恢复。
    prime_url = ""

    def build(
        self,
        query: str,
        *,
        limit: int,
        lang: str,
        region: str,
        time_range: str | None,
        key: str | None,
    ) -> httpx.Request:
        raise NotImplementedError

    def parse(self, resp: httpx.Response) -> tuple[ParseOutcome, list[WebResult]]:
        raise NotImplementedError

    async def run(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        limit: int,
        lang: str,
        region: str,
        time_range: str | None,
        key: str | None,
    ) -> tuple[int, ParseOutcome, list[WebResult], str]:
        """
        跑一轮，返回 `(HTTP 状态码, 结果状态, 结果, 一句具体原因)`。

        默认就是「构造一个请求 → 发 → 解析」。**留这个钩子是因为
        PubMed 必须两步**（先 esearch 拿一串 ID，再 esummary 拿元数据），
        硬塞进单请求的接口只会让那一家的代码写得很别扭。

        第四项 `reason` 是给用户看的**具体**原因（见 `split_parse` 上面那段）。
        """
        def _req() -> httpx.Request:
            return self.build(query, limit=limit, lang=lang, region=region,
                              time_range=time_range, key=key)

        await _prime(client, self)
        await _pace(self.id, self.min_interval_s)
        resp = await client.send(_req())

        if resp.status_code in (403, 429) and self.retry_on_limit:
            # 就地补一发。**只补一次**：补第二次就等于自己制造更多请求，
            # 而限流的成因恰恰是请求太多
            delay = _retry_after_s(resp)
            if delay <= MAX_LIMIT_RETRY_WAIT_S:
                await asyncio.sleep(delay)
                resp = await client.send(_req())

        if resp.status_code in (403, 429):
            return resp.status_code, ParseOutcome.CHALLENGED, [], self.limit_hint
        outcome, results, reason = split_parse(self.parse(resp))

        # 🔴 **HTTP 出错永远不许被报成「没有结果」**（2026-08-04 加）。
        # 这条堵的是这一层最阴的一类故障：JSON 接口出错时回的是
        # `{"status":"error","message":"…"}` 这种**结构完全不同**的正常 JSON，
        # 于是 `data.get("results")` 拿到 None、解析器一路走到 `EMPTY`，
        # 界面上显示"这个词没搜到东西"——用户据此以为全网没有，
        # 而真相是这一家压根没执行查询。实测两例：
        #   · OpenAIRE 收到中文裸词 → HTTP 409 `Syntax errors. expected boolean`
        #   · DOAJ 收到带引号/问号的词 → HTTP 400 `disallowed Lucene features`
        # 两家都因此在健康档案里留下几十次"空结果"，看起来像正常运作。
        # 各家解析器自己认出错误体当然更好，但**兜底必须在唯一出口这里**：
        # 指望十几个解析器每一个都记得处理错误体，是这类漏网复发的老原因。
        if resp.status_code >= 400 and outcome is ParseOutcome.EMPTY:
            return resp.status_code, ParseOutcome.BROKEN, [], (
                reason or f"对方回了 HTTP {resp.status_code}：{_err_hint(resp)}"
            )

        # 带着预热 cookie 还是撞上验证页 = 这套 cookie 已经不管用了。
        # 作废掉，下一轮重新去拿 —— 不作废的话它会被复用满 20 分钟，
        # 而这 20 分钟里每一次搜索都注定失败
        if outcome is ParseOutcome.CHALLENGED and self.prime_url:
            _drop_prime(self.id)
        return resp.status_code, outcome, results, reason

    # 子类共用的小工具 ------------------------------------------------
    def _mk(self, title: str, url: str, snippet: str, rank: int) -> WebResult | None:
        title = _clean(title)
        url = _clean(url)
        if not title or not url or not url.startswith(("http://", "https://")):
            return None
        return WebResult(
            title=title, url=url, snippet=_clean(snippet), engine=self.id, rank=rank
        )


def _clean(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def _doc(resp: httpx.Response):
    from lxml import html as lhtml

    return lhtml.fromstring(resp.text)


def _txt(node) -> str:
    return _clean(node.text_content()) if node is not None else ""


def _hc(cls: str) -> str:
    """
    XPath 谓词：class 属性里含某个类名。

    刻意不用 `cssselect` —— 它是 lxml 之外的另一个包，而这里用到的选择器
    翻来覆去就是"某标签带某 class"这一种形状，为它多背一个依赖不划算。
    两头加空格是必须的：`class="result__snippet"` 不该被 `result` 命中。
    """
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {cls} ')"


def _first(node, *xpaths: str) -> list:
    """依次试几套选择器，返回第一套有命中的。对方改版时靠这个兜底。"""
    for xp in xpaths:
        try:
            got = node.xpath(xp)
        except Exception:  # noqa: BLE001 — XPath 写错不该整轮搜索崩掉
            continue
        if got:
            return got
    return []


# ────────────────────────────────────────────────────────────────
# DuckDuckGo（解析类，最稳的免费通道）
# ────────────────────────────────────────────────────────────────
class DuckDuckGo(BaseEngine):
    id = "duckduckgo"
    label = "DuckDuckGo"
    #: 🔴 实测（2026-08-04）：**免 Key 的三条通道同时死了**，逐条试过 ——
    #:   · `html.duckduckgo.com/html/`  → HTTP 202 anomaly 挡人页
    #:   · `lite.duckduckgo.com/lite/`  → 同样 HTTP 202（换端点没用）
    #:   · `links.duckduckgo.com/d.js`  → 拿到 vqd 也只回 `is506`（内部接口也挡）
    #: 三条路的失败点是同一个反自动化判定，不是解析问题，所以**换选择器、
    #: 换端点、换 UA 全都是白费**。这一家因此改走浏览器渲染：让桌面端
    #: 自带的 Chromium 真的打开一次 duckduckgo.com，把渲染完的 HTML 拿回来解析。
    #:
    #: 仍然默认关：它要占一条渲染通道（只有两条，见 `meta.RENDER_PARALLEL`），
    #: 默认开会和 Google/Yandex 抢，而那两家更需要这条通道
    needs_browser = True
    default_on = False
    note = (
        "免 Key 的 html / lite / d.js 三条通道实测全被反自动化挡下（202 与 is506），"
        "已改走**浏览器渲染**：打开时会用桌面端自带的 Chromium 真的访问一次。"
        "默认关是因为渲染通道只有两条，别和 Google/Yandex 抢。也可以走 SearXNG 间接用"
    )

    _TIME = {"day": "d", "week": "w", "month": "m", "year": "y"}

    def build(self, query, *, limit, lang, region, time_range, key):
        # 走渲染通道只用得上这个 URL（`meta._one_browser` 不发这个请求，
        # 只取 `req.url` 交给渲染代理）。所以这里必须是 **GET + 查询串**，
        # 不能再用原来那个 POST 表单 —— 浏览器没法"打开一个 POST"
        url = f"https://duckduckgo.com/?q={quote_plus(query)}&ia=web"
        kl = _ddg_region(lang, region)
        if kl:
            url += f"&kl={kl}"
        if time_range and time_range in self._TIME:
            url += f"&df={self._TIME[time_range]}"
        return httpx.Request(
            "GET", url,
            headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"},
        )

    def parse(self, resp):
        if resp.status_code == 202:
            # 只有**没走渲染**（命令行/MCP 单跑引擎）时才会看到这个分支
            return ParseOutcome.CHALLENGED, [], (
                "DuckDuckGo 的免 Key 端点回了 202 挡人页 —— 这一家现在要靠浏览器渲染，"
                "请开着桌面端；或者改用 SearXNG 间接拿它的结果"
            )
        doc = _doc(resp)
        # 两套结构都留着：**渲染出来的是现在的 SPA 版**（`article[data-testid=result]`），
        # 而 SearXNG 之类的中转、以及万一哪天 html 端点复活，回的是旧版
        # （`div.result` + `a.result__a`）。两套都认才不会因为换了来源就整家报废
        rows = _first(
            doc,
            "//article[@data-testid='result']",
            "//li[@data-layout='organic']",
            f"//div[{_hc('result')}]",
            f"//div[{_hc('web-result')}]",
        )
        if not rows:
            if "No results" in resp.text or "没有找到" in resp.text:
                return ParseOutcome.EMPTY, []
            if "anomaly" in resp.text.lower() or "unusual traffic" in resp.text.lower():
                return ParseOutcome.CHALLENGED, [], (
                    "DuckDuckGo 判定这次访问是自动流量（anomaly 页）—— "
                    "降低频率或换个时间；长期稳定用它建议走 SearXNG"
                )
            return ParseOutcome.BROKEN, [], (
                f"页面里找不到结果容器（article[data-testid=result] / .result）"
                f"（{_page_hint(resp)}）"
            )

        out: list[WebResult] = []
        for i, row in enumerate(rows, 1):
            a = _first(
                row,
                ".//a[@data-testid='result-title-a']",
                f".//a[{_hc('result__a')}]",
                ".//h2//a[@href]",
            )
            if not a:
                continue
            url = _ddg_unwrap(a[0].get("href") or "")
            sn = _first(
                row,
                ".//*[@data-result='snippet']",
                ".//*[@data-testid='result-snippet']",
                f".//*[{_hc('result__snippet')}]",
            )
            r = self._mk(_txt(a[0]), url, _txt(sn[0]) if sn else "", i)
            if r:
                out.append(r)
        if not out:
            return ParseOutcome.BROKEN, [], (
                f"找到 {len(rows)} 个结果容器，但一条标题链接都抽不出来"
                f"（result-title-a / .result__a / h2 a 三套都没命中）"
            )
        return ParseOutcome.OK, out


def _ddg_region(lang: str, region: str) -> str:
    if region and lang:
        return f"{region.lower()}-{lang.lower()}"
    return "wt-wt"


def _ddg_unwrap(href: str) -> str:
    """DDG 的链接包了一层跳转 //duckduckgo.com/l/?uddg=<编码后的真链接>。"""
    if "uddg=" not in href:
        return href if href.startswith("http") else f"https:{href}"
    try:
        q = parse_qs(urlparse(href if href.startswith("http") else f"https:{href}").query)
        return unquote(q.get("uddg", [""])[0]) or href
    except (ValueError, KeyError):
        return href


# ────────────────────────────────────────────────────────────────
# Bing（解析类）—— 你点名要的
# ────────────────────────────────────────────────────────────────
class Bing(BaseEngine):
    id = "bing"
    label = "Bing"
    note = "你点名要的。走 cn.bing.com —— 实测国际站在这台机器上时通时断，国内站稳定且直接给真实链接"

    _TIME = {"day": "ez1", "week": "ez2", "month": "ez3"}
    #: 实测（2026-08-02）：www.bing.com 反复出现 TLS 握手被切断，
    #: cn.bing.com 稳定返回 li.b_algo 结构且链接不带跳转包装。
    #: 谁快用谁是想当然 —— 这里按实测结果直接定死主用国内站
    HOST = "https://cn.bing.com"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = f"{self.HOST}/search?q={quote_plus(query)}&count={max(10, limit)}"
        if lang:
            url += f"&setlang={lang}"
        if time_range in self._TIME:
            url += f"&filters=ex1%3a%22{self._TIME[time_range]}%22"
        return httpx.Request(
            "GET", url,
            headers={"User-Agent": UA, "Accept-Language": _accept_lang(lang)},
        )

    def parse(self, resp):
        doc = _doc(resp)
        rows = _first(doc, f"//li[{_hc('b_algo')}]", "//*[@id='b_results']/li")
        if not rows:
            # 原来这里写 `"b_no" in resp.text`，那是**子串匹配整页 HTML**，
            # 页面里随便一段脚本含 b_no 就会把"解析坏了"误报成"没有结果"，
            # 于是熔断永远不触发。改成只认明确的无结果文案
            if "没有与此相关的结果" in resp.text or "There are no results for" in resp.text:
                return ParseOutcome.EMPTY, []
            return ParseOutcome.BROKEN, [], (
                f"页面里找不到结果条目（li.b_algo / #b_results）（{_page_hint(resp)}）"
            )

        out: list[WebResult] = []
        for i, row in enumerate(rows, 1):
            a = _first(row, ".//h2//a[@href]", f".//a[{_hc('tilk')}]")
            if not a:
                continue
            url = _bing_unwrap(a[0].get("href") or "")
            cap = _first(row, f".//div[{_hc('b_caption')}]//p", ".//p")
            r = self._mk(_txt(a[0]), url, _txt(cap[0]) if cap else "", i)
            if r:
                out.append(r)
        if not out:
            return ParseOutcome.BROKEN, [], (
                f"找到 {len(rows)} 个结果条目，但标题链接抽不出来（h2 a / a.tilk 都没命中）"
            )
        return ParseOutcome.OK, out


def _bing_unwrap(href: str) -> str:
    """
    Bing 的结果链接常是 bing.com/ck/a?...&u=a1<base64 的真链接>。
    解不开就退回原链接 —— 跳转链也能用，只是统计域名时会全变成 bing.com
    """
    if "bing.com/ck/a" not in href:
        return href
    try:
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            raw = u[2:]
            raw += "=" * (-len(raw) % 4)  # base64 补齐，缺一个 = 就整条解不开
            dec = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
            if dec.startswith("http"):
                return dec
    except (ValueError, KeyError, UnicodeDecodeError):
        pass
    return href


# ────────────────────────────────────────────────────────────────
# Google（解析类）—— 你点名要的。**没有免费官方 API**
# ────────────────────────────────────────────────────────────────
class Google(BaseEngine):
    id = "google"
    label = "Google"
    #: 🔴 实测（2026-08-02）：`google.com/search` 已经**强制 JavaScript** ——
    #: 纯 HTTP 拿回来的是一个 `enablejs` 重定向页，里面一条结果都没有。
    #: 这不是选择器写错了，是这条路本身没了。所以默认关，并如实写明原因。
    #: 想要 Google 结果有三条路，都在 note 里告诉用户，不假装能用
    default_on = False
    needs_browser = True
    note = (
        "你点名要的，但实测 Google 已强制 JavaScript，纯 HTTP 只能拿到一个跳转页。"
        "三条可行路：① 开启浏览器渲染（用桌面端自带的 Chromium，不额外下载）"
        "② 用 SearXNG（它在服务端替你问 Google）③ 付费 API"
    )

    _TIME = {"day": "d1", "week": "w1", "month": "m1", "year": "y1"}

    def build(self, query, *, limit, lang, region, time_range, key):
        url = f"https://www.google.com/search?q={quote_plus(query)}&num={max(10, limit)}"
        if lang:
            url += f"&hl={lang}"
        if region:
            url += f"&gl={region}"
        if time_range in self._TIME:
            url += f"&tbs=qdr:{self._TIME[time_range]}"
        return httpx.Request(
            "GET", url,
            headers={"User-Agent": UA, "Accept-Language": _accept_lang(lang)},
        )

    def parse(self, resp):
        doc = _doc(resp)
        # Google 的 class 名是混淆过的、每隔一段时间就换，所以**不按 class 找**，
        # 改成按结构找：带 href 的 <a> 里包着一个 <h3>。这个形状多年没变过。
        anchors = doc.xpath("//a[@href][.//h3]")
        if not anchors:
            # 🔴 **验证码页要排在 enablejs 之前判**（2026-08-04 调序）：
            # 走渲染通道时 `resp.url` 是我们自己拼的搜索地址、**不带 /sorry/**
            # （渲染代理只把 HTML 送回来，不回最终跳转地址），
            # 所以只能靠正文认。而 /sorry/ 页里也常带一段 noscript 提示，
            # 排在 enablejs 后面会被那一条先接走，报成"要开浏览器渲染"——
            # 可用户明明已经开着渲染了，这句话让人无从下手
            if (
                "/sorry/" in str(resp.url)
                or "unusual traffic" in resp.text
                or "检测到异常流量" in resp.text
                or "recaptcha" in resp.text[:6000].lower()
            ):
                # 这是**人机验证**不是改版。分错了会把一家好引擎熔断掉
                return ParseOutcome.CHALLENGED, [], (
                    "Google 判定这台机器流量异常（/sorry/ 验证页）—— "
                    "开着浏览器渲染也一样会遇到，它认的是出口 IP 不是浏览器。"
                    "现实可行的两条路：自建 SearXNG（服务端替你问 Google），或填一个 Serper Key"
                )
            if "enablejs" in resp.text or "Enable JavaScript" in resp.text:
                # 这不是"没结果"也不是"改版了"，是**这条通道要求跑 JS**。
                # 报 BROKEN 会让它被熔断 15 分钟然后再白试一次，
                # 而真相是无论试多少次都一样 —— 所以要给出可操作的下一步
                return ParseOutcome.BROKEN, [], (
                    "Google 回的是 enablejs 跳转页：这条路要求执行 JavaScript。"
                    "开启浏览器渲染（设置里打开、并且桌面端要开着），或改用 SearXNG / Serper"
                )
            if "did not match any documents" in resp.text or "未找到" in resp.text:
                return ParseOutcome.EMPTY, []
            if "consent.google.com" in str(resp.url) or "Before you continue" in resp.text:
                return ParseOutcome.CHALLENGED, [], "被 Google 的 cookie 同意页挡住了"
            return ParseOutcome.BROKEN, [], (
                f"页面里没有 <a><h3> 这种结果标题结构（{_page_hint(resp)}）"
            )

        out: list[WebResult] = []
        seen: set[str] = set()
        for a in anchors:
            url = _google_unwrap(a.get("href") or "")
            if not url.startswith("http") or url in seen:
                continue
            if re.search(r"^https?://(www\.)?google\.", url):
                continue
            seen.add(url)
            h3 = a.xpath(".//h3")
            # 摘要在结果块里，往上找两层再取最长的一段文字
            snippet = ""
            block = a.getparent()
            for _ in range(3):
                if block is None:
                    break
                cands = [
                    _txt(d) for d in block.xpath(".//div[not(.//h3)]")
                    if len(_txt(d)) > 40
                ]
                if cands:
                    snippet = max(cands, key=len)
                    break
                block = block.getparent()
            r = self._mk(_txt(h3[0]) if h3 else "", url, snippet, len(out) + 1)
            if r:
                out.append(r)
        if not out:
            return ParseOutcome.BROKEN, [], (
                f"找到 {len(anchors)} 个候选标题，但全被过滤掉了（都是 google.* 自家链接或无效地址）"
            )
        return ParseOutcome.OK, out


def _google_unwrap(href: str) -> str:
    """老版结果是 /url?q=<真链接>&sa=…，新版直接给真链接。"""
    if href.startswith("/url?"):
        try:
            return unquote(parse_qs(urlparse(href).query).get("q", [""])[0]) or href
        except (ValueError, KeyError):
            return href
    return href


# ────────────────────────────────────────────────────────────────
# Yandex（解析类）—— 你点名要的。图片反查也靠它
# ────────────────────────────────────────────────────────────────
class Yandex(BaseEngine):
    id = "yandex"
    label = "Yandex"
    #: 默认关：Yandex 对自动访问的验证码触发率明显高于其他家，
    #: 默认开会让每次搜索都多等一个必然失败的请求。用户要用可自己打开
    default_on = False
    needs_browser = True
    note = (
        "你点名要的。实测直接请求会被验证码页挡下（返回的页面里带 captcha），默认关。"
        "开启浏览器渲染后可用；它的**图片反查**（W5）是全网最好的，那条通道单独走"
    )

    def build(self, query, *, limit, lang, region, time_range, key):
        url = f"https://yandex.com/search/?text={quote_plus(query)}"
        if lang:
            url += f"&lang={lang}"
        return httpx.Request(
            "GET", url,
            headers={"User-Agent": UA, "Accept-Language": _accept_lang(lang)},
        )

    def parse(self, resp):
        # 🔴 验证码页原来报的是 BROKEN。那是**分错类**，代价很具体：
        # BROKEN 会计入熔断，连撞三次验证码这家就被停用 15 分钟，
        # 而按四态区分的定义（见文件开头）验证码是 CHALLENGED ——
        # 「慢一点就好」的事被当成了「这家废了」。
        # Yandex 恰恰是全阵容里最容易弹验证码的一家，所以它必然被误杀
        if "showcaptcha" in str(resp.url) or "captcha" in resp.text[:4000].lower():
            return ParseOutcome.CHALLENGED, [], (
                "Yandex 弹了人机验证页 —— 它对自动访问的验证码触发率本来就高，"
                "降低频率或换个时间；要长期稳定用它得走带登录态的浏览器渲染"
            )
        doc = _doc(resp)
        rows = _first(doc, f"//li[{_hc('serp-item')}]", f"//div[{_hc('serp-item')}]")
        if not rows:
            return ParseOutcome.BROKEN, [], f"页面里找不到结果条目（.serp-item）（{_page_hint(resp)}）"
        out: list[WebResult] = []
        for i, row in enumerate(rows, 1):
            a = _first(
                row,
                f".//a[{_hc('OrganicTitle-Link')}]",
                ".//h2//a[@href]",
                ".//a[starts-with(@href, 'http')]",
            )
            if not a:
                continue
            sn = _first(
                row,
                f".//*[{_hc('OrganicTextContentSpan')}]",
                f".//*[{_hc('Organic-ContentWrapper')}]",
            )
            r = self._mk(_txt(a[0]), a[0].get("href") or "", _txt(sn[0]) if sn else "", i)
            if r:
                out.append(r)
        if not out:
            return ParseOutcome.BROKEN, [], (
                f"找到 {len(rows)} 个 .serp-item，但标题链接抽不出来"
            )
        return ParseOutcome.OK, out


# ────────────────────────────────────────────────────────────────
# 百度 / 360 —— 实测在这台机器上最稳的两家中文引擎
#
# 加它们不是"顺手多接两家"：你点名的 Google 走不通、Bing 国际站时断，
# 而中文资料的覆盖恰恰是百度和 360 最强。少了这两家，
# 中文查询的召回会明显不如英文 —— 那才是真正影响你日常使用的缺口。
# ────────────────────────────────────────────────────────────────
class Baidu(BaseEngine):
    id = "baidu"
    label = "百度"
    note = "中文覆盖最好的一家。结果链接是跳转地址，会自动解析成真实网址（解析不出的会标出来）"

    #: 🔴 **这一行是百度这一家从"183 次全挂"变成能用的全部原因**（2026-08-04 实测）。
    #: 详见 `BaseEngine.prime_url`：没有 BAIDUID 这个 cookie 就必进验证页，
    #: 跟请求密度没关系 —— 一台整天没搜过的机器，第一次搜也照样被拦。
    prime_url = "https://www.baidu.com/"
    #: 拿到 cookie 之后仍然稍微隔开一点：深挖会把一个问题拆成几个变体同时发，
    #: 一秒内四发是真的会重新触发风控的（这一条才是"请求太密"真正适用的地方）
    min_interval_s = 0.6

    def build(self, query, *, limit, lang, region, time_range, key):
        url = (
            f"https://www.baidu.com/s?ie=utf-8&wd={quote_plus(query)}"
            f"&rn={min(50, max(10, limit))}"
        )
        if time_range:
            days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(time_range)
            if days:
                url += f"&gpc=stf%3D{days}"
        return httpx.Request(
            "GET", url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                # Referer 指向首页 —— 配合上面的预热，整个请求看起来才是
                # "在百度首页搜了一下"而不是"凭空访问一个结果页地址"
                "Referer": "https://www.baidu.com/",
            },
        )

    def parse(self, resp):
        # 🔴 百度把请求送到 wappass 验证页时**HTTP 仍然是 200、长度 1.4KB、
        # 一条结果没有** —— 不专门认出来会被报成"对方改版了"。
        #
        # 措辞已改（2026-08-04）：原来写「连续请求太密会触发，隔一会儿就恢复」，
        # 那是**误诊**。实测冷启动第一发就被拦，等多久都不会好；
        # 真正的根因是没有 cookie（见 `prime_url`）。一句让用户"等一会儿再试"
        # 的提示，比不给提示更糟 —— 它让人去做一件必然无效的事。
        if (
            "百度安全验证" in resp.text
            or "wappass.baidu.com" in str(resp.url)
            or "请输入验证码" in resp.text
        ):
            return ParseOutcome.CHALLENGED, [], (
                "百度把这次请求送到了安全验证页（这时 HTTP 仍然是 200）—— "
                "正常情况下不该出现：搜索前会先访问一次百度首页把 cookie 拿到。"
                "还撞上说明这次预热没成功（多半是网络抖动），下一轮通常就好了"
            )
        doc = _doc(resp)
        anchors = doc.xpath("//h3//a[@href]")
        if not anchors:
            if "没有找到该URL" in resp.text or "抱歉，没有找到" in resp.text:
                return ParseOutcome.EMPTY, []
            return ParseOutcome.BROKEN, [], "页面里找不到 h3 标题链接"
        out: list[WebResult] = []
        for i, a in enumerate(anchors, 1):
            href = a.get("href") or ""
            # 摘要：往上找结果容器再取一段够长的文字
            snippet = ""
            node = a
            for _ in range(4):
                node = node.getparent()
                if node is None:
                    break
                t = _txt(node)
                if len(t) > 60:
                    snippet = t
                    break
            r = self._mk(_txt(a), href, snippet, i)
            if r:
                # 跳转链先原样带回，真实网址由 meta 层批量解析（见 resolve_redirects）
                r.meta["redirect"] = True
                out.append(r)
        if not out:
            return ParseOutcome.BROKEN, [], (
                f"找到 {len(anchors)} 个 h3 链接，但一条有效结果都构造不出来"
            )
        return ParseOutcome.OK, out


class So360(BaseEngine):
    id = "so360"
    label = "360 搜索"
    note = "中文第二路。优先读结果里的真实网址属性，读不到就解它的跳转链"

    prime_url = "https://www.so.com/"
    min_interval_s = 0.6

    def build(self, query, *, limit, lang, region, time_range, key):
        return httpx.Request(
            "GET", f"https://www.so.com/s?q={quote_plus(query)}&rn={min(50, max(10, limit))}",
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": "https://www.so.com/",
            },
        )

    def parse(self, resp):
        if "请输入验证码" in resp.text or "验证码" in resp.text[:2000]:
            return ParseOutcome.CHALLENGED, [], "360 弹了验证码页，降低频率或换个时间"
        doc = _doc(resp)
        # 先按结果容器找，找不到再退回全页扫 h3。
        # **容器优先**是因为全页 h3 里混着知识卡、图片卡、反馈按钮和导航位，
        # 它们和真结果长得一样但没有一个是搜索结果
        rows = _first(
            doc,
            f"//li[{_hc('res-list')}]",
            "//ul[@id='m-result']//li",
            f"//div[{_hc('res-list')}]",
        )
        anchors = [
            a
            for row in rows
            for a in _first(row, ".//h3//a[@href]", f".//a[{_hc('res-title')}]")[:1]
        ] or doc.xpath("//h3//a[@href]")
        if not anchors:
            if "没有找到与" in resp.text:
                return ParseOutcome.EMPTY, []
            return ParseOutcome.BROKEN, [], f"页面里找不到 h3 标题链接（{_page_hint(resp)}）"
        out: list[WebResult] = []
        for a in anchors:
            # 🔴 原来这里**只认 `data-mdurl`**：拿不到这个属性就 `continue`。
            # 于是 360 一改属性名（或者改成只在部分结果上挂），
            # 整页 h3 全被跳过 → `out` 空 → 报"页面结构不认识了"并熔断。
            # 而真相是标题和链接**都还在**，只是取真实网址的那一步换了地方。
            # 一个属性名撑起整个解析器，是这类解析器最典型的脆点。
            real = _so360_real_url(a)
            if not real:
                continue
            snippet = ""
            node = a
            for _ in range(4):
                node = node.getparent()
                if node is None:
                    break
                t = _txt(node)
                if len(t) > 60:
                    snippet = t
                    break
            r = self._mk(_txt(a), real, snippet, len(out) + 1)
            if r:
                if "/link?" in r.url:
                    # 退到跳转链的那几条，交给 meta 层批量解真实网址 ——
                    # 不解的话它们的域名全是 so.com，来源分级和交叉印证会全错
                    r.meta["redirect"] = True
                out.append(r)
        if not out:
            return ParseOutcome.BROKEN, [], (
                f"找到 {len(anchors)} 个 h3 链接，但没有一个能取到可用网址"
                f"（{_page_hint(resp)}）"
            )
        return ParseOutcome.OK, out


#: 360 自家域名里**不算搜索结果**的那几个：跳转中转、图片站、反馈页、导航站。
#:
#: 🔴 原来的规则是一刀切 `host.endswith("so.com")` 就丢掉，代价实测很具体：
#: 搜「量子计算」时首条是 `baike.so.com`（360 百科）、问答结果在
#: `wenda.so.com` —— 这些是**真实内容页**，是 360 在中文长尾上最有价值的
#: 那一部分，全被当成站内链扔了。丢完剩下三两条，再往下就报
#: "没有一个能取到可用网址"并熔断。
#: 判据从"是不是自家域名"改成"**是不是内容页**"：黑名单列非内容的那几个，
#: 其余一律当真实结果。名单写少了最多混进一条导航位，写多了会丢真结果。
_SO360_NON_CONTENT = frozenset({
    "www.so.com", "so.com", "m.so.com",     # 跳转中转（带 /link? 的另算，见下）
    "image.so.com", "info.so.com",          # 图片站、反馈页
    "ai.so.com",                            # AI 回答卡：是 360 自己生成的，不是来源
    "hao.360.com", "ranks.hao.360.com", "s.360.cn",   # 导航站与推广位
})


def _so360_real_url(a: Any) -> str:
    """
    从 360 的一条结果里取真实网址，**按可靠度依次退**：

      ① `data-mdurl` —— 它一直以来写真实网址的地方
      ② `data-url` / `data-res-url` / `data-realurl` —— 换过的几个同义属性
      ③ `href` 是 `so.com/link?...` 跳转链 —— 带回去交给跳转解析那一步
      ④ `href` 指向非内容的自家页面（见 `_SO360_NON_CONTENT`）—— 丢掉
      ⑤ 其余 `href` 一律当真实外链用，**包括 baike/wenda 这些自家内容站**

    ③ 是关键的一条：拿不到真实网址**不等于这条结果没用**，
    跳转链照样能打开，只是要多解一次。最早的写法是直接扔掉。
    """
    for attr in ("data-mdurl", "data-url", "data-res-url", "data-realurl"):
        v = (a.get(attr) or "").strip()
        if v.startswith("http"):
            return v
    href = (a.get("href") or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = f"https:{href}"
    if not href.startswith("http"):
        return ""
    if "/link?" in href:
        # 站内跳转链。留着让上层去解，解不出会标 unresolved
        return href
    return "" if urlparse(href).netloc.lower() in _SO360_NON_CONTENT else href


# ────────────────────────────────────────────────────────────────
# Mojeek（解析类）—— 自建索引，不是二道贩子
# ────────────────────────────────────────────────────────────────
class Mojeek(BaseEngine):
    id = "mojeek"
    label = "Mojeek"
    note = "独立自建索引（不是转发 Bing/Google），能搜出别家漏掉的长尾内容"

    #: 🔴 2026-08-04 实测：现在这套选择器抓下来**命中 10 条**，页面结构没变。
    #: 也就是说健康档案里那串 broken **不是改版**，是间歇性被挡下来的。
    #: 它是全阵容里最快的一家（约 400ms），于是并发时总是第一个撞上去。
    #: 隔开一点比换选择器管用 —— 后者压根没坏。
    min_interval_s = 0.8

    def build(self, query, *, limit, lang, region, time_range, key):
        url = f"https://www.mojeek.com/search?q={quote_plus(query)}"
        if region:
            url += f"&reg={region.lower()}"
        return httpx.Request("GET", url, headers={"User-Agent": UA,
                                                  "Accept": "text/html,application/xhtml+xml"})

    def parse(self, resp):
        doc = _doc(resp)
        # 🔴 原来只有两套选择器，且两套都押在 class 名上。Mojeek 一改版
        # 就整家报废，而报出来的话是"多半是对方改版" —— 说对了但没用，
        # 因为没告诉你**改的是哪一层**。现在按结构一层层退，
        # 最后一层完全不依赖 class 名（结果条目 = 带 h2 标题的容器），
        # 只要还是一份正常的 HTML 结果页就抽得出东西
        rows = _first(
            doc,
            f"//ul[{_hc('results-standard')}]/li",
            f"//li[{_hc('result')}]",
            f"//ul[{_hc('results')}]/li",
            "//div[@id='results']//li",
            "//li[.//h2//a[@href]]",
        )
        if not rows:
            if "No results" in resp.text or "no results found" in resp.text.lower():
                return ParseOutcome.EMPTY, []
            if "captcha" in resp.text[:4000].lower() or "too many requests" in resp.text.lower():
                return ParseOutcome.CHALLENGED, [], "Mojeek 挡下了这次自动访问，降低频率再试"
            # 🔴 一份正常的 Mojeek 结果页约 34 KB。**明显偏小就不是"改版"而是被挡了** ——
            # 这两种的处置完全相反（改版要改选择器，被挡要降频率），
            # 而旧的报错对两者说的是同一句话。带上体积和标题才分得开
            if len(resp.content or b"") < 8000:
                return ParseOutcome.CHALLENGED, [], (
                    f"Mojeek 回的页面明显偏小、没有结果区（{_page_hint(resp)}）—— "
                    "更像是被挡下了而不是改版，降低频率或换个时间再试"
                )
            return ParseOutcome.BROKEN, [], (
                f"页面里找不到任何结果条目容器（{_page_hint(resp)}）"
            )
        out: list[WebResult] = []
        for i, row in enumerate(rows, 1):
            a = _first(
                row,
                f".//a[{_hc('title')}]",
                ".//h2//a[@href]",
                ".//a[@href][string-length(normalize-space(text())) > 8]",
            )
            if not a:
                continue
            href = a[0].get("href") or ""
            if href.startswith("/"):
                # 它的部分版式用站内相对地址，不补全的话 `_mk` 会直接丢掉这条
                href = f"https://www.mojeek.com{href}"
            p = _first(row, f".//p[{_hc('s')}]", ".//p")
            r = self._mk(_txt(a[0]), href, _txt(p[0]) if p else "", i)
            if r:
                out.append(r)
        if not out:
            return ParseOutcome.BROKEN, [], (
                f"找到 {len(rows)} 个结果条目，但标题链接一个都抽不出来"
            )
        return ParseOutcome.OK, out


# ────────────────────────────────────────────────────────────────
# SearXNG（API 类，公共实例免 Key）—— 一家顶好几家
# ────────────────────────────────────────────────────────────────
class SearXNG(BaseEngine):
    id = "searxng"
    label = "SearXNG"
    kind = "api"
    note = (
        "开源元搜索：一个请求让它在服务端替你问 Google/Bing/Brave 等，返回干净 JSON。"
        "**这是拿到 Google 结果最省事的一条路**（不用浏览器、不用 Key）。"
        "默认指向本机自建实例 127.0.0.1:8888（设置页可一键部署），也可填别的地址"
    )
    #: 🔴 实测（2026-08-02）：七个**公共**实例逐个试，全部 429/403 ——
    #: 它们普遍封代理与数据中心 IP。所以公共实例这条路是死的，
    #: 唯一可靠用法是自建。默认地址因此指向本机，而不是某个公共实例：
    #: 指向公共实例等于让用户第一次点开就看到一个必然失败的引擎
    default_on = False
    instance = "http://127.0.0.1:8888"

    def build(self, query, *, limit, lang, region, time_range, key):
        base = (key or self.instance).rstrip("/")
        url = f"{base}/search?q={quote_plus(query)}&format=json"
        if lang:
            url += f"&language={lang}"
        if time_range:
            url += f"&time_range={time_range}"
        return httpx.Request("GET", url, headers={"User-Agent": UA, "Accept": "application/json"})

    def parse(self, resp):
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            # 返回 HTML 说明这个实例禁用了 JSON 接口 —— 是配置问题不是"没结果"
            return ParseOutcome.BROKEN, [], (
                f"实例回的不是 JSON（HTTP {resp.status_code}）—— "
                "这个 SearXNG 没开 json 输出格式，改它的 settings.yml 加上 formats: [html, json]"
            )
        items = data.get("results") or []
        if not items:
            dead = data.get("unresponsive_engines") or []
            if dead:
                # 实例活着但它自己上游全挂了。**这不是"这个词没结果"** ——
                # 报 EMPTY 会让用户以为全网没有这个东西
                return ParseOutcome.BROKEN, [], (
                    f"实例通了但它自己的上游引擎全没响应：{dead[:5]} —— 多半是容器出不了网"
                )
            return ParseOutcome.EMPTY, []
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            r = self._mk(it.get("title", ""), it.get("url", ""), it.get("content", ""), i)
            if r:
                r.published = it.get("publishedDate")
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


# ────────────────────────────────────────────────────────────────
# Brave Search API（要 Key，付费/免费额度）—— W12 稳定通道
# ────────────────────────────────────────────────────────────────
class BraveAPI(BaseEngine):
    id = "brave"
    label = "Brave Search API"
    kind = "api"
    needs_key = True
    default_on = False
    note = "官方 API，稳定不被封，有免费额度。要去 Brave 官网申请 Key"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = f"https://api.search.brave.com/res/v1/web/search?q={quote_plus(query)}&count={min(20, max(1, limit))}"
        if region:
            url += f"&country={region.upper()}"
        if time_range:
            url += f"&freshness={ {'day': 'pd', 'week': 'pw', 'month': 'pm', 'year': 'py'}.get(time_range, '') }"
        return httpx.Request(
            "GET", url,
            headers={"Accept": "application/json", "X-Subscription-Token": key or ""},
        )

    def parse(self, resp):
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return ParseOutcome.BROKEN, []
        items = ((data.get("web") or {}).get("results")) or []
        if not items:
            return ParseOutcome.EMPTY, []
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            r = self._mk(it.get("title", ""), it.get("url", ""), it.get("description", ""), i)
            if r:
                r.published = (it.get("page_age") or None)
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


# ────────────────────────────────────────────────────────────────
# Serper / Tavily / Exa（要 Key）—— S3 付费通道
#
# **为什么接三家而不是一家**：这三家拿到的是**不同东西**，不是同一件事
# 的三个供应商。Serper 转发 Google 的原始结果（你点名要的 Google，
# 这是不用浏览器就能拿到它的唯一一条路）；Tavily 专为 AI 检索设计，
# 直接返回正文而不只是摘要，省掉一次抓取往返；Exa 是语义检索，
# 用一句话描述你要什么它就能找，关键词搜不到的东西它经常能搜到。
#
# 三家都**默认关且要 Key** —— 没填 Key 时 `_pick` 会明确告诉用户
# "去设置里填一个"，而不是静默跳过。
# ────────────────────────────────────────────────────────────────
class SerperAPI(BaseEngine):
    id = "serper"
    label = "Serper（Google 结果）"
    kind = "api"
    needs_key = True
    default_on = False
    note = (
        "转发 Google 的真实结果，**不用浏览器也不会碰到验证码** —— "
        "这是你点名要的 Google 目前最可靠的一条路。有免费额度，超了按次计费"
    )

    _TIME = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}

    def build(self, query, *, limit, lang, region, time_range, key):
        body: dict[str, Any] = {"q": query, "num": min(20, max(1, limit))}
        if lang:
            body["hl"] = lang.split("-")[0]
        if region:
            body["gl"] = region.lower()
        if time_range in self._TIME:
            body["tbs"] = self._TIME[time_range]
        return httpx.Request(
            "POST", "https://google.serper.dev/search",
            json=body,
            headers={"X-API-KEY": key or "", "Content-Type": "application/json"},
        )

    def parse(self, resp):
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return ParseOutcome.BROKEN, []
        items = data.get("organic") or []
        if not items:
            # 有 searchParameters 说明请求本身是通的，只是这个词没结果
            return (ParseOutcome.EMPTY if data.get("searchParameters") else ParseOutcome.BROKEN), []
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            r = self._mk(it.get("title", ""), it.get("link", ""), it.get("snippet", ""), i)
            if r:
                r.published = it.get("date")
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class TavilyAPI(BaseEngine):
    id = "tavily"
    label = "Tavily（带正文）"
    kind = "api"
    needs_key = True
    default_on = False
    note = (
        "专为 AI 检索做的接口，**直接连正文一起返回**，省掉一次抓取往返 —— "
        "深挖模式下这能省掉几秒。有免费额度"
    )

    def build(self, query, *, limit, lang, region, time_range, key):
        body: dict[str, Any] = {
            "api_key": key or "",
            "query": query,
            "max_results": min(20, max(1, limit)),
            "search_depth": "basic",
            "include_raw_content": False,
        }
        if time_range in ("day", "week", "month", "year"):
            body["days"] = {"day": 1, "week": 7, "month": 30, "year": 365}[time_range]
        return httpx.Request(
            "POST", "https://api.tavily.com/search",
            json=body, headers={"Content-Type": "application/json"},
        )

    def parse(self, resp):
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return ParseOutcome.BROKEN, []
        items = data.get("results") or []
        if not items:
            return (ParseOutcome.EMPTY if "query" in data else ParseOutcome.BROKEN), []
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            r = self._mk(it.get("title", ""), it.get("url", ""), it.get("content", ""), i)
            if r:
                r.published = it.get("published_date")
                # 它给了正文就带上 —— 深挖阶段据此跳过抓取
                body = (it.get("raw_content") or "").strip()
                if body:
                    r.meta["fulltext"] = body[:20000]
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class ExaAPI(BaseEngine):
    id = "exa"
    label = "Exa（语义检索）"
    kind = "api"
    needs_key = True
    default_on = False
    note = (
        "按**意思**检索而不是按关键词：用一句话描述你要找什么样的内容即可。"
        "关键词搜不到的长尾资料它常能找到。有免费额度"
    )

    def build(self, query, *, limit, lang, region, time_range, key):
        body: dict[str, Any] = {
            "query": query,
            "numResults": min(20, max(1, limit)),
            "type": "auto",
            "contents": {"text": {"maxCharacters": 2000}},
        }
        if time_range in ("day", "week", "month", "year"):
            from datetime import UTC, datetime, timedelta

            days = {"day": 1, "week": 7, "month": 30, "year": 365}[time_range]
            since = datetime.now(UTC) - timedelta(days=days)
            body["startPublishedDate"] = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return httpx.Request(
            "POST", "https://api.exa.ai/search",
            json=body,
            headers={"x-api-key": key or "", "Content-Type": "application/json"},
        )

    def parse(self, resp):
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return ParseOutcome.BROKEN, []
        items = data.get("results") or []
        if not items:
            return (ParseOutcome.EMPTY if "requestId" in data else ParseOutcome.BROKEN), []
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            text = (it.get("text") or "").strip()
            r = self._mk(it.get("title", ""), it.get("url", ""), text[:400], i)
            if r:
                r.published = it.get("publishedDate")
                if text:
                    r.meta["fulltext"] = text[:20000]
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


# ────────────────────────────────────────────────────────────────
# Wikipedia（API 类，免 Key）—— 背景知识与实体消歧
# ────────────────────────────────────────────────────────────────
class Wikipedia(BaseEngine):
    id = "wikipedia"
    label = "维基百科"
    kind = "api"
    note = "免 Key 官方 API。它不是用来「找答案」的，是用来给一个陌生名词补背景的"

    def build(self, query, *, limit, lang, region, time_range, key):
        wiki_lang = (lang or "zh").split("-")[0]
        url = (
            f"https://{wiki_lang}.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={quote_plus(query)}&srlimit={min(20, max(1, limit))}"
            f"&format=json&utf8=1"
        )
        # 维基媒体的 UA 政策要求带**可联系到人**的标识，
        # 伪装成浏览器反而会被 403。实测把浏览器 UA 换成这条就通了
        return httpx.Request(
            "GET", url,
            headers={
                "User-Agent": "Synorive/1.0 (local research tool; https://github.com/Aevorine/Synorive)",
                "Accept": "application/json",
            },
        )

    def parse(self, resp):
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return ParseOutcome.BROKEN, []
        items = ((data.get("query") or {}).get("search")) or []
        if not items:
            return ParseOutcome.EMPTY, []
        lang = re.match(r"https://([a-z-]+)\.wikipedia", str(resp.url))
        host = lang.group(1) if lang else "zh"
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            title = it.get("title", "")
            url = f"https://{host}.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
            snippet = re.sub(r"<[^>]+>", "", it.get("snippet", ""))
            r = self._mk(title, url, snippet, i)
            if r:
                r.published = it.get("timestamp")
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


def _accept_lang(lang: str) -> str:
    if not lang or lang.startswith("zh"):
        return "zh-CN,zh;q=0.9,en;q=0.8"
    return f"{lang},en;q=0.8"


# ────────────────────────────────────────────────────────────────
# 注册表
# ────────────────────────────────────────────────────────────────
_REGISTRY: dict[str, BaseEngine] = {
    e.id: e
    for e in (
        Bing(), Baidu(), So360(), Mojeek(), SearXNG(), Wikipedia(),
        Google(), Yandex(), DuckDuckGo(),
        BraveAPI(), SerperAPI(), TavilyAPI(), ExaAPI(),
    )
}


def _register_scholar() -> None:
    """
    学术源在 `scholar.py` 里定义，这里注册进同一张表。

    **分两个文件而不是全塞进来**：它们的字段（DOI/作者/被引数）和抓取节奏
    跟网页搜索完全不同，混在一起写会让两边的判据互相污染。
    但注册表只能有一张 —— 否则 `get_engine("arxiv")` 就要看调用方猜去哪张表找。
    """
    from .scholar import SCHOLAR_ENGINES

    for e in SCHOLAR_ENGINES:
        _REGISTRY.setdefault(e.id, e)


_register_scholar()


def all_engines(group: str | None = None) -> list[BaseEngine]:
    return [e for e in _REGISTRY.values() if group is None or e.group == group]


def get_engine(engine_id: str) -> BaseEngine | None:
    return _REGISTRY.get(engine_id)


def describe_engines() -> list[dict[str, Any]]:
    """给界面用：每家是什么、要不要 Key、默认开不开、代价是什么。"""
    return [
        {
            "id": e.id,
            "label": e.label,
            "kind": e.kind,
            "group": e.group,
            "needsKey": e.needs_key,
            "needsBrowser": e.needs_browser,
            "defaultOn": e.default_on,
            "note": e.note,
        }
        for e in _REGISTRY.values()
    ]
