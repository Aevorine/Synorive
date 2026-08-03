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


class SemanticScholarSource(BaseEngine):
    """C1 —— 语义学者。免 Key（有速率限制），字段最全的一家之一。"""

    id = "semanticscholar"
    label = "Semantic Scholar"
    kind = "api"
    group = "scholar"
    note = "语义学者。摘要、被引、开放获取 PDF 一次给全，计算机与生医覆盖最好"

    def build(self, query, *, limit, lang, region, time_range, key):
        fields = (
            "title,abstract,year,publicationDate,venue,citationCount,"
            "externalIds,openAccessPdf,authors"
        )
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={quote_plus(query)}&limit={min(100, max(1, limit))}&fields={fields}"
        )
        headers = {"User-Agent": UA, "Accept": "application/json"}
        # 有 Key 就带上 —— 免 Key 时 429 很常见，那是限流不是故障
        if key:
            headers["x-api-key"] = key
        return httpx.Request("GET", url, headers=headers)

    def parse(self, resp):
        try:
            items = resp.json().get("data") or []
        except (json.JSONDecodeError, ValueError, AttributeError):
            return ParseOutcome.BROKEN, []
        if not items:
            return ParseOutcome.EMPTY, []
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            ext = it.get("externalIds") or {}
            oa = it.get("openAccessPdf") or {}
            doi = str(ext.get("DOI") or "")
            pid = str(it.get("paperId") or "")
            url = (
                f"https://doi.org/{doi}" if doi
                else f"https://www.semanticscholar.org/paper/{pid}"
            )
            authors = [(a.get("name") or "") for a in (it.get("authors") or [])]
            r = _mk(
                self.id, i, it.get("title") or "", url, it.get("abstract") or "",
                doi=doi, authors=[a for a in authors if a][:8],
                year=str(it.get("year") or ""), venue=it.get("venue") or "",
                citations=it.get("citationCount") or 0,
                pdf=oa.get("url") or "",
                arxivId=str(ext.get("ArXiv") or ""),
                # C6 预印本合并的原料：有 arXiv 号却没 DOI，基本就是还没正式发表
                preprint=bool(ext.get("ArXiv") and not doi),
                source="Semantic Scholar",
            )
            if r:
                r.published = it.get("publicationDate")
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class EuropePmcSource(BaseEngine):
    """
    C1 —— Europe PMC。**bioRxiv / medRxiv 的预印本走这条线拿**。

    为什么不直接接 bioRxiv 官方接口：它的 `/details/biorxiv/` 只能按日期区间
    翻页，**根本没有关键词检索**。想按主题找预印本，官方接口帮不上忙。
    Europe PMC 把 bioRxiv、medRxiv 的预印本全收了并且支持全文检索，
    还顺带给 PMC 全文链接 —— 这才是能用的那条路。
    """

    id = "europepmc"
    label = "Europe PMC"
    kind = "api"
    group = "scholar"
    note = "生医文献 + bioRxiv/medRxiv 预印本。带全文链接，很多能直接读正文"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query={quote_plus(query)}&format=json&resultType=core"
            f"&pageSize={min(100, max(1, limit))}"
        )
        return httpx.Request("GET", url, headers={"User-Agent": UA,
                                                  "Accept": "application/json"})

    def parse(self, resp):
        try:
            items = ((resp.json().get("resultList") or {}).get("result")) or []
        except (json.JSONDecodeError, ValueError, AttributeError):
            return ParseOutcome.BROKEN, []
        if not items:
            return ParseOutcome.EMPTY, []
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            doi = str(it.get("doi") or "")
            pmcid = str(it.get("pmcid") or "")
            if doi:
                url = f"https://doi.org/{doi}"
            elif pmcid:
                url = f"https://europepmc.org/article/PMC/{pmcid}"
            else:
                url = (f"https://europepmc.org/article/"
                       f"{it.get('source', 'MED')}/{it.get('id', '')}")
            pdf = ""
            for ft in ((it.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
                if str(ft.get("documentStyle")) == "pdf":
                    pdf = str(ft.get("url") or "")
                    break
            authors = [
                a.strip() for a in str(it.get("authorString") or "").split(",") if a.strip()
            ]
            r = _mk(
                self.id, i, it.get("title") or "", url, it.get("abstractText") or "",
                doi=doi, year=str(it.get("pubYear") or ""),
                venue=it.get("journalTitle") or "",
                citations=it.get("citedByCount") or 0,
                authors=authors[:8], pdf=pdf, pmcid=pmcid,
                # PPR 是 Europe PMC 给预印本的来源码
                preprint=(str(it.get("source") or "") == "PPR"),
                source="Europe PMC",
            )
            if r:
                r.published = it.get("firstPublicationDate")
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class OpenAireSource(BaseEngine):
    """
    C1 —— OpenAIRE。欧盟资助成果聚合，**社科与人文的覆盖比另外几家好**。

    ⚠️ 它的 JSON 是从 XML 机械转过来的，嵌套七八层，同一个字段时而是对象
    时而是数组。所以下面全程走 `_oa_text` / `_oa_list` 防御性取值 ——
    直接写 `d["a"]["b"][0]` 在这家身上必炸，而且炸得毫无规律可循。
    """

    id = "openaire"
    label = "OpenAIRE"
    kind = "api"
    group = "scholar"
    default_on = False
    note = "欧盟开放科研聚合。社科人文覆盖较好，但返回结构很乱，偶尔会漏字段"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = (
            "https://api.openaire.eu/search/publications"
            f"?keywords={quote_plus(query)}&format=json&size={min(50, max(1, limit))}"
        )
        return httpx.Request("GET", url, headers={"User-Agent": UA,
                                                  "Accept": "application/json"})

    def parse(self, resp):
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return ParseOutcome.BROKEN, []
        results = _oa_list(
            ((data.get("response") or {}).get("results") or {}).get("result")
        )
        if not results:
            return ParseOutcome.EMPTY, []
        out: list[WebResult] = []
        for i, it in enumerate(results, 1):
            entity = (it.get("metadata") or {}).get("oaf:entity") or {}
            work = entity.get("oaf:result") or {}
            title = _oa_text(work.get("title"))
            doi = ""
            for pid in _oa_list(work.get("pid")):
                if _oa_attr(pid, "classid").lower() == "doi":
                    doi = _oa_text(pid)
                    break
            url = f"https://doi.org/{doi}" if doi else _oa_instance_url(work)
            if not url:
                continue
            authors = [_oa_text(a) for a in _oa_list(work.get("creator"))]
            date = _oa_text(work.get("dateofacceptance"))
            r = _mk(
                self.id, i, title, url, _oa_text(work.get("description")),
                doi=doi, year=date[:4], authors=[a for a in authors if a][:8],
                source="OpenAIRE",
            )
            if r:
                r.published = date or None
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


class CoreSource(BaseEngine):
    """
    C1 —— CORE。全球开放获取仓储聚合，**两亿多篇全文**，量最大的一家。

    要 Key，但 **core.ac.uk 免费注册就给**（不是付费墙）。所以
    `needs_key=True` 配 `default_on=False`：没填 Key 时 `_pick` 会明确挡下
    并说明原因，而不是每次都去撞一个必然 401 的请求。
    """

    id = "core"
    label = "CORE"
    kind = "api"
    group = "scholar"
    needs_key = True
    default_on = False
    note = "开放获取全文聚合，量最大（两亿+）。要 Key，但 core.ac.uk 免费注册就给"

    def build(self, query, *, limit, lang, region, time_range, key):
        url = (
            "https://api.core.ac.uk/v3/search/works"
            f"?q={quote_plus(query)}&limit={min(100, max(1, limit))}"
        )
        return httpx.Request("GET", url, headers={
            "User-Agent": UA, "Accept": "application/json",
            "Authorization": f"Bearer {key or ''}",
        })

    def parse(self, resp):
        try:
            items = resp.json().get("results") or []
        except (json.JSONDecodeError, ValueError, AttributeError):
            return ParseOutcome.BROKEN, []
        if not items:
            return ParseOutcome.EMPTY, []
        out: list[WebResult] = []
        for i, it in enumerate(items, 1):
            doi = str(it.get("doi") or "")
            url = f"https://doi.org/{doi}" if doi else str(it.get("downloadUrl") or "")
            if not url:
                srcs = it.get("sourceFulltextUrls") or []
                url = str(srcs[0]) if isinstance(srcs, list) and srcs else ""
            if not url:
                continue
            authors = [
                (a.get("name") or "") for a in (it.get("authors") or [])
                if isinstance(a, dict)
            ]
            r = _mk(
                self.id, i, it.get("title") or "", url, it.get("abstract") or "",
                doi=doi, year=str(it.get("yearPublished") or ""),
                authors=[a for a in authors if a][:8],
                pdf=str(it.get("downloadUrl") or ""),
                venue=str(it.get("publisher") or ""),
                source="CORE",
            )
            if r:
                r.published = it.get("publishedDate")
                out.append(r)
        return (ParseOutcome.OK, out) if out else (ParseOutcome.BROKEN, [])


# ── OpenAIRE 专用的防御性取值（它的 JSON 形状不稳定）────────────────
def _oa_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x is not None]
    return [v]


def _oa_text(v: Any) -> str:
    """从 `"x"` / `{"$": "x"}` / `[{"$": "x"}, …]` 三种形状里都能取出文本。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return str(v.get("$") or v.get("content") or "").strip()
    if isinstance(v, list):
        for x in v:
            t = _oa_text(x)
            if t:
                return t
    return ""


def _oa_attr(v: Any, name: str) -> str:
    if isinstance(v, dict):
        return str(v.get(f"@{name}") or v.get(name) or "")
    return ""


def _oa_instance_url(work: dict[str, Any]) -> str:
    """没有 DOI 时退回找一个能打开的落地页。找不到就返回空串，由调用方丢弃。"""
    for inst in _oa_list(work.get("instance")):
        if not isinstance(inst, dict):
            continue
        for wr in _oa_list(inst.get("webresource")):
            u = _oa_text(wr.get("url") if isinstance(wr, dict) else wr)
            if u.startswith(("http://", "https://")):
                return u
    return ""


SCHOLAR_ENGINES = (
    ArxivSource(), CrossrefSource(), OpenAlexSource(), DoajSource(), PubMedSource(),
    # C1 扩容：5 家 → 9 家
    SemanticScholarSource(), EuropePmcSource(), OpenAireSource(), CoreSource(),
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


# ────────────────────────────────────────────────────────────────
# C6 预印本与正式版合并
# ────────────────────────────────────────────────────────────────
#: 标题归一化时要扔掉的噪声：版本号、期刊排版前缀、方括号标注
_TITLE_NOISE = re.compile(
    r"(\bv\d+\b|\[preprint\]|\[预印本\]|\(preprint\)|supplementary|附录)", re.I
)


def _title_norm(title: str) -> str:
    """
    标题归一化。**只留字母数字和汉字**，因为各家对副标题分隔符
    （`:` `—` `--`）、大小写、连字符的处理都不一样，
    同一篇论文在五家源里能长出五个不同的标题字符串。
    """
    t = _TITLE_NOISE.sub(" ", str(title or "").lower())
    return re.sub(r"[^a-z0-9一-鿿]+", "", t)[:90]


def merge_preprints(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    C6 —— 把预印本和它的正式发表版算成**一篇**。

    `merge_scholar()` 按 DOI 折叠，但**预印本经常没有 DOI，
    或者拿到的是 arXiv 自己签发的 DOI（和正式版的完全不同）**。
    结果就是同一项工作在列表里出现两次，一次挂 arXiv 一次挂期刊 ——
    用户看到的"找到 40 篇"里可能有 8 篇是重复的。

    **合并方向永远是「预印本并进正式版」，不是反过来**：
    正式版有期刊名、卷期页码、正式的 DOI，是引用时该写的那一份；
    预印本的价值是「可能有免费全文」和「更早的时间戳」，
    所以只把 `pdf` 和 `preprintDate` 这两样搬过去，别的字段一律不覆盖。

    🔴 判据保守是刻意的：只有**标题归一化后完全相同**才合并。
    用相似度阈值会把「XX 方法综述」和「XX 方法综述（第二部分）」并成一篇——
    错误合并比重复显示糟得多，因为用户根本看不出少了一篇。
    """
    if not entries:
        return []

    by_title: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        key = _title_norm(e.get("title") or "")
        if len(key) >= 10:          # 太短的标题不参与合并，误合并风险太高
            by_title.setdefault(key, []).append(i)

    dropped: set[int] = set()
    for _key, idxs in by_title.items():
        if len(idxs) < 2:
            continue
        # 正式版 = 有 DOI 且不是 arXiv 自签的那个；找不到就保留排名最前的
        formal = None
        for i in idxs:
            meta = entries[i].get("meta") or {}
            doi = str(meta.get("doi") or "")
            if doi and not doi.lower().startswith("10.48550"):   # arXiv 自签前缀
                formal = i
                break
        if formal is None:
            formal = idxs[0]

        target = entries[formal]
        tmeta = target.setdefault("meta", {})
        for i in idxs:
            if i == formal:
                continue
            src = entries[i]
            smeta = src.get("meta") or {}
            # 只搬这三样，别的一律不覆盖正式版
            if not tmeta.get("pdf") and smeta.get("pdf"):
                tmeta["pdf"] = smeta["pdf"]
            if smeta.get("arxivId") and not tmeta.get("arxivId"):
                tmeta["arxivId"] = smeta["arxivId"]
            if src.get("published") and (
                not tmeta.get("preprintDate") or src["published"] < tmeta["preprintDate"]
            ):
                tmeta["preprintDate"] = src["published"]
            # 来源列表取并集，好让界面上仍然显示"这几家都收录了"
            for s in src.get("sources") or []:
                if s not in target.setdefault("sources", []):
                    target["sources"].append(s)
            dropped.add(i)

        if dropped:
            tmeta["mergedPreprint"] = True

    out = [e for i, e in enumerate(entries) if i not in dropped]
    for i, d in enumerate(out, 1):
        d["rank"] = i
        d["sourceCount"] = len(d.get("sources") or [])
    return out


# ────────────────────────────────────────────────────────────────
# C9 引用格式导出：BibTeX / GB-T 7714
# ────────────────────────────────────────────────────────────────
def _bib_key(entry: dict[str, Any]) -> str:
    """`第一作者姓 + 年份 + 标题首词`，全部降成 ASCII 安全字符。"""
    meta = entry.get("meta") or {}
    authors = meta.get("authors") or []
    first = str(authors[0]) if authors else ""
    surname = re.sub(r"[^A-Za-z]", "", first.split()[-1] if first.split() else "") or "anon"
    year = re.sub(r"[^0-9]", "", str(meta.get("year") or ""))[:4] or "nd"
    word = ""
    for w in re.findall(r"[A-Za-z]{4,}", str(entry.get("title") or "")):
        word = w.lower()
        break
    return f"{surname.lower()}{year}{word}"[:40]


def _bib_escape(s: str) -> str:
    # BibTeX 里这几个是控制字符，不转义会让整个 .bib 文件解析失败
    return (
        str(s or "")
        .replace("\\", r"\textbackslash{}")
        .replace("{", r"\{").replace("}", r"\}")
        .replace("&", r"\&").replace("%", r"\%").replace("$", r"\$")
        .replace("#", r"\#").replace("_", r"\_")
        .strip()
    )


def to_bibtex(entries: list[dict[str, Any]]) -> str:
    """
    C9 —— 导出 BibTeX。

    条目类型只在 `@article` 和 `@misc` 之间二选一：有期刊名就是 article，
    没有就是 misc（预印本、会议论文、技术报告全归这里）。
    **不去猜 `@inproceedings`** —— 猜错了会让参考文献里出现不存在的会议名，
    那比统一标成 misc 让用户自己改要糟。
    """
    out: list[str] = []
    seen: set[str] = set()
    for e in entries:
        meta = e.get("meta") or {}
        key = _bib_key(e)
        n = 1
        base = key
        while key in seen:                 # 同姓同年同首词的会撞，加后缀区分
            n += 1
            key = f"{base}{chr(96 + n)}"
        seen.add(key)

        venue = str(meta.get("venue") or "")
        fields: list[tuple[str, str]] = [
            ("title", _bib_escape(e.get("title") or "")),
            ("author", " and ".join(_bib_escape(a) for a in (meta.get("authors") or []))),
            ("year", re.sub(r"[^0-9]", "", str(meta.get("year") or ""))[:4]),
        ]
        if venue:
            fields.append(("journal", _bib_escape(venue)))
        if meta.get("doi"):
            fields.append(("doi", str(meta["doi"])))
        if e.get("url"):
            fields.append(("url", str(e["url"])))
        if meta.get("arxivId"):
            fields.append(("eprint", str(meta["arxivId"])))
            fields.append(("archivePrefix", "arXiv"))

        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields if v)
        out.append(f"@{'article' if venue else 'misc'}{{{key},\n{body}\n}}")
    return "\n\n".join(out) + ("\n" if out else "")


def to_gbt7714(entries: list[dict[str, Any]]) -> str:
    """
    C9 —— 导出 GB/T 7714-2015（国标参考文献格式），中文论文要用的那种。

    格式：`[序号] 作者. 题名[文献类型标志]. 刊名, 年: 页码. DOI`
    作者超过 3 个按国标写「, 等」（英文写 `, et al.`）。
    文献类型只区分 `[J]`（期刊）和 `[EB/OL]`（网络电子资源）——
    和 BibTeX 那边同样的理由：猜不准的宁可标成最通用的那个。
    """
    lines: list[str] = []
    for i, e in enumerate(entries, 1):
        meta = e.get("meta") or {}
        authors = [str(a) for a in (meta.get("authors") or []) if a]
        is_cn = bool(re.search(r"[一-鿿]", "".join(authors[:1]) or ""))
        if len(authors) > 3:
            who = ", ".join(authors[:3]) + ("， 等" if is_cn else ", et al")
        else:
            who = ", ".join(authors)
        venue = str(meta.get("venue") or "")
        year = re.sub(r"[^0-9]", "", str(meta.get("year") or ""))[:4]
        kind = "[J]" if venue else "[EB/OL]"
        parts = [f"[{i}]"]
        if who:
            parts.append(f"{who}.")
        parts.append(f"{e.get('title') or ''}{kind}.")
        if venue:
            parts.append(f"{venue}," if year else f"{venue}.")
        if year:
            parts.append(f"{year}.")
        if meta.get("doi"):
            parts.append(f"DOI:{meta['doi']}.")
        elif e.get("url"):
            parts.append(str(e["url"]) + ".")
        lines.append(" ".join(p for p in parts if p.strip()))
    return "\n".join(lines) + ("\n" if lines else "")
