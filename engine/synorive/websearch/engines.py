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

import base64
import json
import logging
import re
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
    ) -> tuple[int, ParseOutcome, list[WebResult]]:
        """
        跑一轮，返回 `(HTTP 状态码, 结果状态, 结果)`。

        默认就是「构造一个请求 → 发 → 解析」。**留这个钩子是因为
        PubMed 必须两步**（先 esearch 拿一串 ID，再 esummary 拿元数据），
        硬塞进单请求的接口只会让那一家的代码写得很别扭。
        """
        resp = await client.send(
            self.build(query, limit=limit, lang=lang, region=region,
                       time_range=time_range, key=key)
        )
        if resp.status_code in (403, 429):
            return resp.status_code, ParseOutcome.CHALLENGED, []
        outcome, results = self.parse(resp)
        return resp.status_code, outcome, results

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
    #: 🔴 实测（2026-08-02）：`html.duckduckgo.com/html/` 现在返回 HTTP 202
    #: 加一个 JS 落地页，不再返回结果列表。默认关。
    #: 之前这里默认开且写着"最稳的免费通道"—— 那是我按印象写的，实测推翻了
    default_on = False
    note = "实测其 html 端点已改成返回 JS 落地页（HTTP 202），拿不到结果，默认关。可通过 SearXNG 间接使用"

    _TIME = {"day": "d", "week": "w", "month": "m", "year": "y"}

    def build(self, query, *, limit, lang, region, time_range, key):
        params: dict[str, str] = {"q": query, "kl": _ddg_region(lang, region)}
        if time_range and time_range in self._TIME:
            params["df"] = self._TIME[time_range]
        return httpx.Request(
            "POST",
            "https://html.duckduckgo.com/html/",
            data=params,
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

    def parse(self, resp):
        if resp.status_code == 202:
            # 202 是它的挡人页 —— 不是"没结果"，报 BROKEN 才会熔断
            return ParseOutcome.BROKEN, []
        doc = _doc(resp)
        rows = _first(doc, f"//div[{_hc('result')}]", f"//div[{_hc('web-result')}]")
        if not rows:
            # 没有 result 容器：要么改版了，要么真没结果。
            # DDG 没结果时页面里有明确文案，据此区分
            if "No results" in resp.text or "没有找到" in resp.text:
                return ParseOutcome.EMPTY, []
            return ParseOutcome.BROKEN, []

        out: list[WebResult] = []
        for i, row in enumerate(rows, 1):
            a = _first(row, f".//a[{_hc('result__a')}]")
            if not a:
                continue
            url = _ddg_unwrap(a[0].get("href") or "")
            sn = _first(row, f".//*[{_hc('result__snippet')}]")
            r = self._mk(_txt(a[0]), url, _txt(sn[0]) if sn else "", i)
            if r:
                out.append(r)
        if not out:
            return ParseOutcome.BROKEN, []
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
            return ParseOutcome.BROKEN, []

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
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


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
            if "enablejs" in resp.text or "Enable JavaScript" in resp.text:
                # 这不是"没结果"也不是"改版了"，是**这条通道要求跑 JS**。
                # 报 BROKEN 会让它被熔断 15 分钟然后再白试一次，
                # 而真相是无论试多少次都一样 —— 所以要给出可操作的下一步
                return ParseOutcome.BROKEN, []
            if "did not match any documents" in resp.text or "未找到" in resp.text:
                return ParseOutcome.EMPTY, []
            return ParseOutcome.BROKEN, []

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
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


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
        if "showcaptcha" in str(resp.url) or "captcha" in resp.text[:4000].lower():
            return ParseOutcome.BROKEN, []
        doc = _doc(resp)
        rows = _first(doc, f"//li[{_hc('serp-item')}]", f"//div[{_hc('serp-item')}]")
        if not rows:
            return ParseOutcome.BROKEN, []
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
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


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

    def build(self, query, *, limit, lang, region, time_range, key):
        url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={min(50, max(10, limit))}"
        if time_range:
            days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(time_range)
            if days:
                url += f"&gpc=stf%3D{days}"
        return httpx.Request(
            "GET", url,
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        )

    def parse(self, resp):
        # 🔴 百度对连续请求很敏感：几次之后就把你送到 wappass 验证页。
        # 那个页面**HTTP 200、长度 1.4KB、一条结果没有** ——
        # 不专门认出来的话会被报成"对方改版了"，而真相是"慢一点就好了"。
        # 报错报得不对，下次排查就会往完全错误的方向走
        if "百度安全验证" in resp.text or "wappass.baidu.com" in str(resp.url):
            return ParseOutcome.CHALLENGED, []
        doc = _doc(resp)
        anchors = doc.xpath("//h3//a[@href]")
        if not anchors:
            if "没有找到该URL" in resp.text or "抱歉，没有找到" in resp.text:
                return ParseOutcome.EMPTY, []
            return ParseOutcome.BROKEN, []
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
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class So360(BaseEngine):
    id = "so360"
    label = "360 搜索"
    note = "中文第二路。真实网址直接写在结果的 data-mdurl 属性里，不用额外解析"

    def build(self, query, *, limit, lang, region, time_range, key):
        return httpx.Request(
            "GET", f"https://www.so.com/s?q={quote_plus(query)}&rn={min(50, max(10, limit))}",
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        )

    def parse(self, resp):
        doc = _doc(resp)
        anchors = doc.xpath("//h3//a[@href]")
        if not anchors:
            if "没有找到与" in resp.text:
                return ParseOutcome.EMPTY, []
            return ParseOutcome.BROKEN, []
        out: list[WebResult] = []
        for a in anchors:
            # data-mdurl 就是真实网址。没有这个属性的多半是广告位或站内功能块
            real = a.get("data-mdurl") or ""
            if not real.startswith("http"):
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
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


# ────────────────────────────────────────────────────────────────
# Mojeek（解析类）—— 自建索引，不是二道贩子
# ────────────────────────────────────────────────────────────────
class Mojeek(BaseEngine):
    id = "mojeek"
    label = "Mojeek"
    note = "独立自建索引（不是转发 Bing/Google），能搜出别家漏掉的长尾内容，且不反爬"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = f"https://www.mojeek.com/search?q={quote_plus(query)}"
        if region:
            url += f"&reg={region.lower()}"
        return httpx.Request("GET", url, headers={"User-Agent": UA})

    def parse(self, resp):
        doc = _doc(resp)
        rows = _first(doc, f"//ul[{_hc('results-standard')}]/li", f"//li[{_hc('result')}]")
        if not rows:
            if "No results" in resp.text:
                return ParseOutcome.EMPTY, []
            return ParseOutcome.BROKEN, []
        out: list[WebResult] = []
        for i, row in enumerate(rows, 1):
            a = _first(row, f".//a[{_hc('title')}]", ".//h2//a[@href]")
            if not a:
                continue
            p = _first(row, f".//p[{_hc('s')}]", ".//p")
            r = self._mk(_txt(a[0]), a[0].get("href") or "", _txt(p[0]) if p else "", i)
            if r:
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


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
            return ParseOutcome.BROKEN, []
        items = data.get("results") or []
        if not items:
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
