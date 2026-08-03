"""
C3 —— 引用网络图谱：被引 / 参考文献双向展开，找一个领域的奠基论文
====================================================================
从一篇（或一批）论文出发，往**两个方向**各走一层：

  · **往回**（references）：这篇引用了谁 → 一批文献共同引用的那几篇，
    基本就是这个领域的奠基工作
  · **往前**（citations）：谁引用了这篇 → 后续进展、综述、复现与质疑

**「共被引」是这个功能的核心，不是"展开一层"**。单看一篇的参考文献
只是一份书单；把 30 篇的参考文献放在一起数，**被 20 篇同时引用的那一篇**
才是真正绕不过去的那篇。这个数字是自己算出来的，不依赖任何人的推荐。

**数据源是 OpenAlex**：它是唯一免 Key 且同时给出 `referenced_works`
（往回）和 `cited_by_api_url`（往前）的开放图谱。Crossref 有引用数据但
覆盖不全，Semantic Scholar 的引用接口限流很紧。

🔴 **一层就停，不做递归展开**。两层的节点数是 O(n²)，300 篇展开两层要发
几万个请求，既跑不完也没意义 —— 用户要的是「这个领域绕不过去的那几篇」，
那在第一层的共被引统计里就已经浮出来了。
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

import httpx

UA = "Synorive/1.0 (local research tool; https://github.com/Fusheng201)"
OPENALEX = "https://api.openalex.org"

#: 一次批量取详情最多塞多少个 id。OpenAlex 的 filter 用 `|` 分隔，
#: URL 太长会被 414 挡掉，50 是实测安全的上限
_BATCH = 50

#: 并发上限。OpenAlex 没有硬性 QPS 限制，但 polite pool 的建议是别打太狠；
#: 4 路并发在实测里既不会被限速，也能把一层展开压进两三秒
_CONCURRENCY = 4


def _short_id(work_id: str) -> str:
    """`https://openalex.org/W123` → `W123`。图谱里节点 id 用短的那个。"""
    return str(work_id or "").rstrip("/").split("/")[-1]


@dataclass
class PaperNode:
    """图谱里的一个节点。"""

    id: str = ""
    title: str = ""
    year: str = ""
    doi: str = ""
    url: str = ""
    venue: str = ""
    citations: int = 0
    authors: list[str] = field(default_factory=list)
    #: 这一篇被输入集合里的几篇引用了 —— **共被引次数，本模块的核心指标**
    co_cited: int = 0
    #: 它引用了输入集合里的几篇（往前方向用）
    cites_seeds: int = 0
    role: str = "neighbor"          # seed / foundation / followup / neighbor

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "year": self.year,
            "doi": self.doi, "url": self.url, "venue": self.venue,
            "citations": self.citations, "authors": self.authors,
            "coCited": self.co_cited, "citesSeeds": self.cites_seeds,
            "role": self.role,
        }


def _node_from_work(w: dict[str, Any]) -> PaperNode:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    doi = str(w.get("doi") or "").replace("https://doi.org/", "")
    authors = [
        ((a.get("author") or {}).get("display_name") or "")
        for a in (w.get("authorships") or [])
    ]
    return PaperNode(
        id=_short_id(w.get("id") or ""),
        title=str(w.get("title") or w.get("display_name") or ""),
        year=str(w.get("publication_year") or ""),
        doi=doi,
        url=str(loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else "")
                or w.get("id") or ""),
        venue=str(src.get("display_name") or ""),
        citations=int(w.get("cited_by_count") or 0),
        authors=[a for a in authors if a][:6],
    )


_SELECT = (
    "id,doi,title,display_name,publication_year,cited_by_count,"
    "authorships,primary_location,referenced_works"
)


async def _fetch_works(
    client: httpx.AsyncClient, ids: list[str]
) -> list[dict[str, Any]]:
    """按 id 批量取详情。失败返回空列表 —— 图谱缺一块比整个功能报错好。"""
    if not ids:
        return []
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), _BATCH):
        chunk = ids[i:i + _BATCH]
        url = (
            f"{OPENALEX}/works?filter=openalex_id:{'|'.join(chunk)}"
            f"&per-page={len(chunk)}&select={_SELECT}&mailto=synorive@local"
        )
        try:
            r = await client.get(url, headers={"User-Agent": UA})
            if r.status_code == 200:
                out += (r.json().get("results") or [])
        except (httpx.HTTPError, ValueError):
            continue
    return out


async def _resolve_seed(
    client: httpx.AsyncClient, entry: dict[str, Any]
) -> dict[str, Any] | None:
    """
    把一条检索结果解析成 OpenAlex 的 work。

    优先 DOI（精确），没有 DOI 才退回标题搜索（可能匹配错，所以
    只取第一条且要求标题相似 —— 匹配错一篇会污染整张图）。
    """
    meta = entry.get("meta") or {}
    oid = _short_id(str(meta.get("openalexId") or ""))
    doi = str(meta.get("doi") or "").replace("https://doi.org/", "").strip()
    try:
        if oid.startswith("W"):
            r = await client.get(
                f"{OPENALEX}/works/{oid}?select={_SELECT}&mailto=synorive@local",
                headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.json()
        if doi:
            r = await client.get(
                f"{OPENALEX}/works/doi:{doi}?select={_SELECT}&mailto=synorive@local",
                headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.json()
        title = str(entry.get("title") or "").strip()
        if len(title) >= 12:
            r = await client.get(
                f"{OPENALEX}/works?search={quote_plus(title[:120])}"
                f"&per-page=1&select={_SELECT}&mailto=synorive@local",
                headers={"User-Agent": UA})
            if r.status_code == 200:
                res = (r.json().get("results") or [])
                if res:
                    got = str(res[0].get("title") or "").lower()
                    want = title.lower()
                    # 标题必须真的像，否则宁可不要这个种子
                    if _title_close(got, want):
                        return res[0]
    except (httpx.HTTPError, ValueError):
        return None
    return None


def _title_close(a: str, b: str) -> bool:
    ka = set(re.findall(r"[a-z0-9]{3,}|[一-鿿]{2}", a))
    kb = set(re.findall(r"[a-z0-9]{3,}|[一-鿿]{2}", b))
    if not ka or not kb:
        return False
    return len(ka & kb) / max(1, min(len(ka), len(kb))) >= 0.6


async def build_graph(
    entries: list[dict[str, Any]],
    *,
    max_seeds: int = 20,
    direction: str = "both",         # back / forward / both
    top_n: int = 15,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    """
    C3 主入口 —— 建一张一层深的引用图。

    返回 `{seeds, foundations, followups, edges, note}`。
    `foundations` 按**共被引次数**排序，那是这个功能真正的产出；
    `followups` 按被引数排序（后续工作里最有影响力的那几篇）。
    """
    seeds_in = [e for e in entries if isinstance(e, dict)][:max_seeds]
    if not seeds_in:
        return {"seeds": [], "foundations": [], "followups": [], "edges": [],
                "note": "没有可用的种子文献"}

    limits = httpx.Limits(max_connections=_CONCURRENCY)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=5.0), limits=limits,
        follow_redirects=True,
    ) as client:
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def one(e: dict[str, Any]) -> dict[str, Any] | None:
            async with sem:
                return await _resolve_seed(client, e)

        works = [w for w in await asyncio.gather(
            *[one(e) for e in seeds_in], return_exceptions=False
        ) if w]

        seed_nodes = {}
        for w in works:
            n = _node_from_work(w)
            n.role = "seed"
            seed_nodes[n.id] = n

        edges: list[dict[str, str]] = []
        foundations: dict[str, PaperNode] = {}
        followups: dict[str, PaperNode] = {}

        # ── 往回：共被引统计 ────────────────────────────────
        if direction in ("back", "both"):
            ref_counter: Counter[str] = Counter()
            ref_by: dict[str, set[str]] = defaultdict(set)
            for w in works:
                sid = _short_id(w.get("id") or "")
                for ref in (w.get("referenced_works") or [])[:60]:
                    rid = _short_id(ref)
                    if rid and rid not in seed_nodes:
                        ref_counter[rid] += 1
                        ref_by[rid].add(sid)
                        edges.append({"from": sid, "to": rid, "kind": "cites"})
            # 只取共被引 ≥2 的展开详情 —— 只被一篇引用的是那篇自己的书单，
            # 不构成"这个领域绕不过去"的证据，全展开只会白发几百个请求
            hot = [rid for rid, c in ref_counter.most_common(top_n * 3) if c >= 2]
            for w in await _fetch_works(client, hot[:top_n * 2]):
                n = _node_from_work(w)
                n.co_cited = ref_counter.get(n.id, 0)
                n.role = "foundation"
                foundations[n.id] = n

        # ── 往前：谁引用了这些种子 ──────────────────────────
        if direction in ("forward", "both"):
            async def cited_by(sid: str) -> list[dict[str, Any]]:
                async with sem:
                    try:
                        r = await client.get(
                            f"{OPENALEX}/works?filter=cites:{sid}"
                            f"&per-page=25&sort=cited_by_count:desc"
                            f"&select={_SELECT}&mailto=synorive@local",
                            headers={"User-Agent": UA})
                        if r.status_code == 200:
                            return r.json().get("results") or []
                    except (httpx.HTTPError, ValueError):
                        pass
                    return []

            batches = await asyncio.gather(*[cited_by(s) for s in list(seed_nodes)[:12]])
            cite_counter: Counter[str] = Counter()
            for sid, batch in zip(list(seed_nodes)[:12], batches):
                for w in batch:
                    n = _node_from_work(w)
                    if not n.id or n.id in seed_nodes:
                        continue
                    cite_counter[n.id] += 1
                    edges.append({"from": n.id, "to": sid, "kind": "cites"})
                    prev = followups.get(n.id)
                    if prev is None:
                        n.role = "followup"
                        followups[n.id] = n
            for nid, c in cite_counter.items():
                if nid in followups:
                    followups[nid].cites_seeds = c

    found = sorted(foundations.values(),
                   key=lambda n: (-n.co_cited, -n.citations))[:top_n]
    follow = sorted(followups.values(),
                    key=lambda n: (-n.cites_seeds, -n.citations))[:top_n]

    return {
        "seeds": [n.to_dict() for n in seed_nodes.values()],
        "foundations": [n.to_dict() for n in found],
        "followups": [n.to_dict() for n in follow],
        "edges": edges[:800],
        "resolved": len(seed_nodes),
        "requested": len(seeds_in),
        "note": (
            f"从 {len(seed_nodes)}/{len(seeds_in)} 篇解析成功的种子展开一层。"
            "『奠基论文』按**被这批种子共同引用的次数**排序 —— 这个数是自己数出来的，"
            "不是谁的推荐榜。没解析出来的那几篇通常是没有 DOI、"
            "或 OpenAlex 尚未收录（预印本很常见）"
        ),
    }
