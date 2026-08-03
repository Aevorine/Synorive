"""
学术文献源 —— L1 / L2 / L4
====================================================================
五家，**全部免 Key**（2026-08-02 逐个实调确认可达）：

  arXiv       预印本，物理/数学/计算机最全，直接给 PDF 地址
  Crossref    DOI 注册机构本身，几乎所有正式发表的论文都在
  OpenAlex    开放学术图谱，**带被引数和引文关系**（L2 引文图谱靠它）
  DOAJ        开放获取期刊，全文都能免费下
  PubMed      生物医学，两步接口（先查 ID 再取详情）

**和网页搜索的关键区别**：这里每条结果都有 DOI、作者、年份、被引数这些
结构化字段。所以去重不能靠标题 —— **DOI 相同就是同一篇**，
一篇论文同时出现在 Crossref、OpenAlex、PubMed 里是常态而不是异常。

**为什么不接知网/万方**：它们没有开放的检索接口，全文也要机构权限。
硬爬既不稳也不合规。中文文献目前只能靠 Crossref/OpenAlex 里已收录的那部分，
这一点在菜单里就标注过（L6），不是这里偷懒。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, quote_plus

import httpx

from .engines import BaseEngine, ParseOutcome, WebResult

#: 学术接口普遍要求带联系方式的 UA（Crossref 的 polite pool、
#: NCBI 的使用条款都写了）。伪装成浏览器反而会被降速或拒绝
UA = "Synorive/1.0 (local research tool; https://github.com/Fusheng201)"


def _mk(
    engine: str, rank: int, title: str, url: str, snippet: str, **meta: Any
) -> WebResult | None:
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not title or not url:
        return None
    r = WebResult(
        title=title,
        url=url,
        snippet=re.sub(r"\s+", " ", str(snippet or "")).strip()[:600],
        engine=engine,
        rank=rank,
    )
    r.meta = {k: v for k, v in meta.items() if v not in (None, "", [], 0)}
    return r


class ArxivSource(BaseEngine):
    id = "arxiv"
    label = "arXiv"
    kind = "api"
    group = "scholar"
    note = "预印本。物理/数学/计算机最全，每条都直接给 PDF 地址，能一键存进库"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = (
            "http://export.arxiv.org/api/query?search_query=all:"
            f"{quote_plus(query)}&max_results={min(50, max(1, limit))}"
            "&sortBy=relevance"
        )
        return httpx.Request("GET", url, headers={"User-Agent": UA})

    def parse(self, resp):
        from lxml import etree

        try:
            root = etree.fromstring(resp.content)
        except etree.XMLSyntaxError:
            return ParseOutcome.BROKEN, []
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns)
        if not entries:
            return ParseOutcome.EMPTY, []

        out: list[WebResult] = []
        for i, e in enumerate(entries, 1):
            def txt(tag: str) -> str:
                node = e.find(f"a:{tag}", ns)
                return (node.text or "").strip() if node is not None else ""

            link = ""
            pdf = ""
            for lk in e.findall("a:link", ns):
                href = lk.get("href") or ""
                if lk.get("title") == "pdf":
                    pdf = href
                elif lk.get("rel") == "alternate":
                    link = href
            authors = [
                (a.find("a:name", ns).text or "").strip()
                for a in e.findall("a:author", ns)
                if a.find("a:name", ns) is not None
            ]
            published = txt("published")
            doi_node = e.find("{http://arxiv.org/schemas/atom}doi")
            r = _mk(
                self.id, i, txt("title"), link or pdf, txt("summary"),
                authors=authors[:8], pdf=pdf,
                doi=(doi_node.text or "").strip() if doi_node is not None else "",
                year=published[:4] if published else "",
                source="arXiv",
            )
            if r:
                r.published = published or None
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class CrossrefSource(BaseEngine):
    id = "crossref"
    label = "Crossref"
    kind = "api"
    group = "scholar"
    note = "DOI 注册机构本身，正式发表的论文基本都在。带期刊名和被引数"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = (
            f"https://api.crossref.org/works?query={quote_plus(query)}"
            f"&rows={min(50, max(1, limit))}&select="
            "DOI,title,abstract,author,issued,container-title,URL,is-referenced-by-count,type"
        )
        if time_range:
            years = {"year": 1, "month": 1}.get(time_range)
            if years:
                url += "&filter=from-pub-date:2025-01-01"
        return httpx.Request("GET", url, headers={"User-Agent": UA,
                                                  "Accept": "application/json"})

    def parse(self, resp):
        try:
            items = ((resp.json().get("message") or {}).get("items")) or []
        except (json.JSONDecodeError, ValueError, AttributeError):
            return ParseOutcome.BROKEN, []
        if not items:
            return ParseOutcome.EMPTY, []

        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            title = (it.get("title") or [""])[0]
            doi = it.get("DOI") or ""
            # Crossref 的摘要是 JATS XML 片段，标签必须去掉，
            # 否则整段 <jats:p> 会被当成正文喂进检索和简报
            abstract = re.sub(r"<[^>]+>", " ", it.get("abstract") or "")
            authors = [
                " ".join(x for x in (a.get("given"), a.get("family")) if x)
                for a in (it.get("author") or [])
            ]
            issued = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
            year = str(issued[0]) if issued and issued[0] else ""
            r = _mk(
                self.id, i, title, it.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
                abstract, doi=doi, authors=authors[:8], year=year,
                venue=(it.get("container-title") or [""])[0],
                citations=it.get("is-referenced-by-count") or 0,
                type=it.get("type") or "", source="Crossref",
            )
            if r:
                r.published = f"{year}-01-01" if year else None
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class OpenAlexSource(BaseEngine):
    id = "openalex"
    label = "OpenAlex"
    kind = "api"
    group = "scholar"
    note = "开放学术图谱。**带被引数和引文关系** —— L2 引文图谱、找必读论文靠它"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = (
            f"https://api.openalex.org/works?search={quote_plus(query)}"
            f"&per-page={min(50, max(1, limit))}"
            "&select=id,doi,title,publication_year,publication_date,cited_by_count,"
            "authorships,primary_location,open_access,referenced_works"
        )
        # mailto 会把请求放进 polite pool，响应更快也更不容易被限流
        url += "&mailto=synorive@local"
        return httpx.Request("GET", url, headers={"User-Agent": UA,
                                                  "Accept": "application/json"})

    def parse(self, resp):
        try:
            items = resp.json().get("results") or []
        except (json.JSONDecodeError, ValueError, AttributeError):
            return ParseOutcome.BROKEN, []
        if not items:
            return ParseOutcome.EMPTY, []

        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            loc = it.get("primary_location") or {}
            src = loc.get("source") or {}
            oa = it.get("open_access") or {}
            doi = (it.get("doi") or "").replace("https://doi.org/", "")
            url = loc.get("landing_page_url") or it.get("doi") or it.get("id") or ""
            authors = [
                ((a.get("author") or {}).get("display_name") or "")
                for a in (it.get("authorships") or [])
            ]
            r = _mk(
                self.id, i, it.get("title") or "", url, "",
                doi=doi, authors=[a for a in authors if a][:8],
                year=str(it.get("publication_year") or ""),
                venue=src.get("display_name") or "",
                citations=it.get("cited_by_count") or 0,
                pdf=oa.get("oa_url") or "",
                # L2 引文图谱的原料：这篇引用了哪些。取前 40 个够画一层邻居了
                references=(it.get("referenced_works") or [])[:40],
                openalexId=it.get("id") or "", source="OpenAlex",
            )
            if r:
                r.published = it.get("publication_date")
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class DoajSource(BaseEngine):
    id = "doaj"
    label = "DOAJ"
    kind = "api"
    group = "scholar"
    note = "开放获取期刊目录。这里的文章**全文都能免费下**，不会点进去要付费"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = (
            f"https://doaj.org/api/search/articles/{quote(query, safe='')}"
            f"?pageSize={min(50, max(1, limit))}"
        )
        return httpx.Request("GET", url, headers={"User-Agent": UA,
                                                  "Accept": "application/json"})

    def parse(self, resp):
        try:
            items = resp.json().get("results") or []
        except (json.JSONDecodeError, ValueError, AttributeError):
            return ParseOutcome.BROKEN, []
        if not items:
            return ParseOutcome.EMPTY, []

        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            b = it.get("bibjson") or {}
            links = b.get("link") or []
            url = next((x.get("url") for x in links if x.get("url")), "")
            doi = next(
                (x.get("id") for x in (b.get("identifier") or []) if x.get("type") == "doi"),
                "",
            )
            authors = [a.get("name") or "" for a in (b.get("author") or [])]
            r = _mk(
                self.id, i, b.get("title") or "", url or (f"https://doi.org/{doi}" if doi else ""),
                b.get("abstract") or "", doi=doi, authors=[a for a in authors if a][:8],
                year=str(b.get("year") or ""),
                venue=((b.get("journal") or {}).get("title") or ""),
                openAccess=True, source="DOAJ",
            )
            if r:
                r.published = f"{b.get('year')}-01-01" if b.get("year") else None
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class PubMedSource(BaseEngine):
    id = "pubmed"
    label = "PubMed"
    kind = "api"
    group = "scholar"
    note = "生物医学。接口分两步走（先查编号再取详情），所以比其他几家慢一点"

    _BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def build(self, query, *, limit, lang, region, time_range, key):
        # 只有第一步。第二步在 run() 里，因为它依赖第一步的返回
        return httpx.Request(
            "GET",
            f"{self._BASE}/esearch.fcgi?db=pubmed&term={quote_plus(query)}"
            f"&retmax={min(40, max(1, limit))}&retmode=json&sort=relevance",
            headers={"User-Agent": UA},
        )

    def parse(self, resp):
        # 单步解析用不上 —— PubMed 走 run() 的两步流程。
        # 留一个明确的实现而不是 NotImplementedError，免得被基类的默认路径撞上
        return ParseOutcome.BROKEN, []

    async def run(self, client, query, *, limit, lang, region, time_range, key):
        r1 = await client.send(
            self.build(query, limit=limit, lang=lang, region=region,
                       time_range=time_range, key=key)
        )
        if r1.status_code in (403, 429):
            return r1.status_code, ParseOutcome.CHALLENGED, []
        try:
            ids = ((r1.json().get("esearchresult") or {}).get("idlist")) or []
        except (json.JSONDecodeError, ValueError, AttributeError):
            return r1.status_code, ParseOutcome.BROKEN, []
        if not ids:
            return r1.status_code, ParseOutcome.EMPTY, []

        r2 = await client.get(
            f"{self._BASE}/esummary.fcgi?db=pubmed&id={','.join(ids)}&retmode=json",
            headers={"User-Agent": UA},
        )
        if r2.status_code in (403, 429):
            return r2.status_code, ParseOutcome.CHALLENGED, []
        try:
            result = r2.json().get("result") or {}
        except (json.JSONDecodeError, ValueError, AttributeError):
            return r2.status_code, ParseOutcome.BROKEN, []

        out: list[WebResult] = []
        for i, pmid in enumerate(ids, 1):
            it = result.get(pmid) or {}
            if not it.get("title"):
                continue
            doi = next(
                (x.get("value") for x in (it.get("articleids") or [])
                 if x.get("idtype") == "doi"), "",
            )
            authors = [a.get("name") or "" for a in (it.get("authors") or [])]
            r = _mk(
                self.id, i, it.get("title") or "",
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "",
                doi=doi, authors=[a for a in authors if a][:8],
                year=(it.get("pubdate") or "")[:4], venue=it.get("fulljournalname") or "",
                pmid=pmid, source="PubMed",
            )
            if r:
                r.published = it.get("sortpubdate") or None
                out.append(r)
        return r2.status_code, (ParseOutcome.OK if out else ParseOutcome.BROKEN), out


SCHOLAR_ENGINES = (
    ArxivSource(), CrossrefSource(), OpenAlexSource(), DoajSource(), PubMedSource(),
)


# ────────────────────────────────────────────────────────────────
# 学术专用去重：DOI 优先
# ────────────────────────────────────────────────────────────────
def scholar_fold_key(r: WebResult) -> str:
    """
    一篇论文同时出现在 Crossref、OpenAlex、PubMed 里是**常态**。
    所以先按 DOI 折叠 —— 那是全球唯一标识，比标题可靠得多。

    arXiv 预印本常常没有 DOI，退回按标题折叠；标题也要归一化，
    因为各家对大小写、连字符、副标题分隔符的处理都不一样。
    """
    doi = str((r.meta or {}).get("doi") or "").lower().strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    if doi:
        return f"doi:{doi}"
    t = re.sub(r"[^a-z0-9一-鿿]+", "", (r.title or "").lower())[:80]
    return f"ti:{t}" if len(t) >= 8 else f"u:{r.url}"


def merge_scholar(results: list[WebResult]) -> list[dict[str, Any]]:
    """
    按 DOI 合并多家学术源的结果，合成一条信息最全的。

    合并规则是**取并集而不是取第一条**：Crossref 有期刊名、OpenAlex 有被引数、
    arXiv 有 PDF 直链、DOAJ 知道是不是开放获取 —— 每家都缺一块，
    只取第一条等于白问了另外四家。
    """
    merged: dict[str, dict[str, Any]] = {}
    for r in results:
        key = scholar_fold_key(r)
        cur = merged.get(key)
        if cur is None:
            merged[key] = {
                **r.to_dict(),
                "sources": [r.engine],
                "meta": dict(r.meta or {}),
            }
            continue
        cur["sources"].append(r.engine)
        # 摘要取最长的那份
        if len(r.snippet) > len(cur.get("snippet") or ""):
            cur["snippet"] = r.snippet
        for k, v in (r.meta or {}).items():
            # 被引数取最大：各家统计口径不同，取大的那个更接近真实影响力
            if k == "citations":
                cur["meta"][k] = max(int(cur["meta"].get(k) or 0), int(v or 0))
            elif not cur["meta"].get(k):
                cur["meta"][k] = v
        if not cur.get("published") and r.published:
            cur["published"] = r.published

    out = list(merged.values())
    # 排序：先看被几家收录（多家都有 = 更可能是这个领域的正经工作），
    # 再看被引数。**不拿被引数单独排** —— 那样永远是老论文在前面，
    # 而用户搜一个新方向时最需要的恰恰是近两年的
    out.sort(
        key=lambda d: (-len(d["sources"]), -int((d.get("meta") or {}).get("citations") or 0))
    )
    for i, d in enumerate(out, 1):
        d["rank"] = i
        d["sourceCount"] = len(d["sources"])
    return out
