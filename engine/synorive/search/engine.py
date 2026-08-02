"""
混合检索 —— D1 关键词+语义融合，D2 三级瀑布，D4 多指标可调排序
====================================================================
为什么必须混合：
  · 只用向量：搜「BGE-small-zh-v1.5」这种精确型号会漏，
    因为向量把它理解成"某个模型名"，和别的模型名都很像。
  · 只用关键词：搜「怎么把 PDF 里的字提出来」找不到标题写着
    「文档解析方案对比」的文章，因为一个词都没重合。

融合用 **RRF（倒数排名融合）**：两路各自的排名取倒数相加。
不用分数加权是因为 BM25 的分数和余弦相似度量纲完全不同，
归一化怎么做都是拍脑袋；而排名是可比的。

    RRF(d) = Σ  weight_i / (K + rank_i(d))

K=60 是文献里的常用值，作用是压低头部排名的差距 ——
第1名和第2名的差距不该比第10名和第20名的差距大那么多。
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import sqlite_vec

from ..store.db import Database
from ..store.repository import Repository
from ..store.text import highlight_terms, to_query, to_trigram_query
from .query_syntax import describe, parse_query

log = logging.getLogger("synorive.search")

#: RRF 的平滑常数
RRF_K = 60
#: 每一路召回多少条送去融合。太少会漏，太多融合和取详情变慢。
RECALL_LIMIT = 200


@dataclass
class Weights:
    """D4 多指标排序权重 —— 界面上就是几个滑块。"""

    semantic: float = 1.0
    keyword: float = 1.0
    recency: float = 0.3
    source_trust: float = 0.2
    popularity: float = 0.2
    title_boost: float = 0.5

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Weights:
        if not d:
            return cls()
        return cls(
            semantic=float(d.get("semantic", 1.0)),
            keyword=float(d.get("keyword", 1.0)),
            recency=float(d.get("recency", 0.3)),
            source_trust=float(d.get("sourceTrust", 0.2)),
            popularity=float(d.get("popularity", 0.2)),
            title_boost=float(d.get("titleBoost", 0.5)),
        )


PRESETS: dict[str, Weights] = {
    "balanced": Weights(),
    # 求准：关键词为主，语义只做补充
    "precise": Weights(semantic=0.4, keyword=1.5, recency=0.1, title_boost=0.8),
    # 求全：语义为主，能理解同义和近义
    "semantic": Weights(semantic=1.5, keyword=0.5, recency=0.1),
    # 找最近的：时间权重拉满
    "recent": Weights(semantic=0.8, keyword=0.8, recency=1.5),
}


@dataclass
class Filters:
    modalities: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    time_from: str | None = None
    time_to: str | None = None
    size_min: int | None = None
    size_max: int | None = None
    scopes: list[str] = field(default_factory=list)
    exclude_scopes: list[str] = field(default_factory=list)
    #: D10 的 type:pdf 走这里 —— 扩展名不是 modality，库里 pdf 的 modality 是 text
    extensions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Filters:
        d = d or {}
        return cls(
            modalities=list(d.get("modalities") or []),
            sources=list(d.get("sources") or []),
            tags=list(d.get("tags") or []),
            time_from=d.get("timeFrom"),
            time_to=d.get("timeTo"),
            size_min=d.get("sizeMinBytes"),
            size_max=d.get("sizeMaxBytes"),
            scopes=list(d.get("scopes") or []),
            exclude_scopes=list(d.get("excludeScopes") or []),
            extensions=list(d.get("extensions") or []),
        )

    def merged_with(self, other: dict[str, Any] | None) -> Filters:
        """
        和另一组筛选合并（列表求并，标量以 other 为准）。

        用于把「查询串里写的 D10 指令」和「界面上点选的筛选」叠加。
        两边冲突时以查询串为准 —— 用户刚敲进去的东西优先级更高。
        """
        if not other:
            return self
        o = Filters.from_dict(other)
        return Filters(
            modalities=list({*self.modalities, *o.modalities}),
            sources=list({*self.sources, *o.sources}),
            tags=list({*self.tags, *o.tags}),
            time_from=o.time_from or self.time_from,
            time_to=o.time_to or self.time_to,
            size_min=o.size_min if o.size_min is not None else self.size_min,
            size_max=o.size_max if o.size_max is not None else self.size_max,
            scopes=list({*self.scopes, *o.scopes}),
            exclude_scopes=list({*self.exclude_scopes, *o.exclude_scopes}),
            extensions=list({*self.extensions, *o.extensions}),
        )

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.modalities, self.sources, self.tags, self.time_from, self.time_to,
                self.size_min, self.size_max, self.scopes, self.exclude_scopes,
                self.extensions,
            )
        )

    def sql(self, alias: str = "i") -> tuple[str, list[Any]]:
        """拼成 WHERE 片段。返回 (子句, 参数)。"""
        parts: list[str] = []
        args: list[Any] = []

        if self.modalities:
            parts.append(f"{alias}.modality IN ({','.join('?' * len(self.modalities))})")
            args += self.modalities
        if self.sources:
            parts.append(f"{alias}.source IN ({','.join('?' * len(self.sources))})")
            args += self.sources
        if self.time_from:
            parts.append(f"COALESCE({alias}.content_time, {alias}.created_at) >= ?")
            args.append(self.time_from)
        if self.time_to:
            parts.append(f"COALESCE({alias}.content_time, {alias}.created_at) <= ?")
            args.append(self.time_to)
        if self.size_min is not None:
            parts.append(f"{alias}.size_bytes >= ?")
            args.append(self.size_min)
        if self.size_max is not None:
            parts.append(f"{alias}.size_bytes <= ?")
            args.append(self.size_max)
        for s in self.scopes:
            parts.append(f"{alias}.locator LIKE ?")
            args.append(f"{s}%")
        for s in self.exclude_scopes:
            parts.append(f"{alias}.locator NOT LIKE ?")
            args.append(f"{s}%")
        if self.tags:
            marks = ",".join("?" * len(self.tags))
            parts.append(
                f"{alias}.id IN (SELECT it.item_id FROM item_tags it "
                f"JOIN tags t ON t.id = it.tag_id WHERE t.name IN ({marks}))"
            )
            args += self.tags
        if self.extensions:
            # 扩展名是 OR 关系（type:pdf,docx 是"这两种都要"不是"两种都是"）
            ors = " OR ".join(f"LOWER({alias}.locator) LIKE ?" for _ in self.extensions)
            parts.append(f"({ors})")
            args += [f"%{e.lower()}" for e in self.extensions]

        return (" AND ".join(parts) if parts else ""), args


@dataclass
class Candidate:
    item_id: str
    chunk_rowid: int | None = None
    #: 各路召回里的排名（1 起），没被这一路召回就是 None
    rank_keyword: int | None = None
    rank_vector: int | None = None
    rank_trigram: int | None = None
    #: 原始分，用于可解释
    bm25: float | None = None
    distance: float | None = None
    matched_via: set[str] = field(default_factory=set)
    best_text: str = ""
    page: int | None = None
    start_sec: float | None = None


class SearchEngine:
    def __init__(self, db: Database, repo: Repository, embedder: Any | None = None) -> None:
        self.db = db
        self.repo = repo
        self.embedder = embedder

    # ── 各路召回 ────────────────────────────────────────────

    def recall_keyword(self, query: str, filters: Filters, limit: int = RECALL_LIMIT) -> list[Candidate]:
        """FTS5 + BM25。分块级召回，一条内容可能命中多个块，取最好的那个。"""
        expr = to_query(query)
        if not expr:
            return []

        conn = self.db.connect()
        where, args = filters.sql("i")
        sql = f"""
            SELECT c.rowid AS chunk_rowid, c.item_id, c.text, c.channel, c.page, c.start_sec,
                   bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            JOIN items  i ON i.id = c.item_id
            WHERE chunks_fts MATCH ?
              {'AND ' + where if where else ''}
            ORDER BY score
            LIMIT ?
        """
        try:
            rows = conn.execute(sql, (expr, *args, limit)).fetchall()
        except sqlite3.OperationalError as e:
            # 用户输入里可能有 FTS5 不认的语法，别把异常抛给界面
            log.debug("关键词召回语法错误（查询=%r）：%s", expr, e)
            return []

        out: list[Candidate] = []
        seen: dict[str, Candidate] = {}
        for rank, r in enumerate(rows, start=1):
            iid = str(r["item_id"])
            if iid in seen:
                continue
            c = Candidate(
                item_id=iid,
                chunk_rowid=int(r["chunk_rowid"]),
                rank_keyword=len(seen) + 1,
                bm25=float(r["score"]),
                best_text=str(r["text"]),
                page=r["page"],
                start_sec=r["start_sec"],
            )
            c.matched_via.add(str(r["channel"]) or "body")
            seen[iid] = c
            out.append(c)
        return out

    def recall_title(self, query: str, filters: Filters, limit: int = 60) -> list[Candidate]:
        """
        标题/路径的子串召回（trigram）。

        专治「我只记得文件名里那几个字」—— jieba 分完词之后
        「年度总」这种词内片段是匹配不到的，得靠 trigram。
        查询短于 3 字符时 trigram 无能为力，直接返回空。
        """
        expr = to_trigram_query(query)
        if not expr:
            return []

        conn = self.db.connect()
        where, args = filters.sql("i")
        sql = f"""
            SELECT i.id AS item_id, bm25(items_tri) AS score
            FROM items_tri
            JOIN items i ON i.rowid = items_tri.rowid
            WHERE items_tri MATCH ?
              {'AND ' + where if where else ''}
            ORDER BY score
            LIMIT ?
        """
        try:
            rows = conn.execute(sql, (expr, *args, limit)).fetchall()
        except sqlite3.OperationalError:
            return []

        out: list[Candidate] = []
        for rank, r in enumerate(rows, start=1):
            c = Candidate(item_id=str(r["item_id"]), rank_trigram=rank)
            c.matched_via.add("filename")
            out.append(c)
        return out

    def recall_vector(self, query: str, filters: Filters, limit: int = RECALL_LIMIT) -> list[Candidate]:
        """向量近邻召回。"""
        if self.embedder is None or not query.strip():
            return []

        conn = self.db.connect()
        try:
            qv = self.embedder.encode_one(query, is_query=True)
        except Exception as e:  # noqa: BLE001
            log.warning("查询向量化失败，本次只走关键词：%s", e)
            return []

        blob = sqlite_vec.serialize_float32(qv.tolist())

        # 先做 KNN 再关联过滤：sqlite-vec 的 vec0 表要求 k 是常量条件，
        # 把过滤条件塞进 KNN 查询里会让它退化成全表扫。
        # 有筛选时多召回一些，过滤完还够用。
        knn_limit = limit * 3 if not filters.empty else limit
        try:
            rows = conn.execute(
                "SELECT chunk_rowid, distance FROM vec_chunks "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (blob, knn_limit),
            ).fetchall()
        except sqlite3.OperationalError as e:
            log.debug("向量召回失败（多半是还没建向量表）：%s", e)
            return []

        if not rows:
            return []

        rowids = [int(r["chunk_rowid"]) for r in rows]
        dist = {int(r["chunk_rowid"]): float(r["distance"]) for r in rows}

        where, args = filters.sql("i")
        marks = ",".join("?" * len(rowids))
        detail = conn.execute(
            f"""
            SELECT c.rowid AS chunk_rowid, c.item_id, c.text, c.channel, c.page, c.start_sec
            FROM chunks c JOIN items i ON i.id = c.item_id
            WHERE c.rowid IN ({marks})
              {'AND ' + where if where else ''}
            """,
            (*rowids, *args),
        ).fetchall()

        by_rowid = {int(r["chunk_rowid"]): r for r in detail}
        out: list[Candidate] = []
        seen: set[str] = set()
        for rid in rowids:  # 保持距离升序
            r = by_rowid.get(rid)
            if r is None:
                continue
            iid = str(r["item_id"])
            if iid in seen:
                continue
            seen.add(iid)
            c = Candidate(
                item_id=iid,
                chunk_rowid=rid,
                rank_vector=len(seen),
                distance=dist[rid],
                best_text=str(r["text"]),
                page=r["page"],
                start_sec=r["start_sec"],
            )
            c.matched_via.add("vector")
            out.append(c)
            if len(out) >= limit:
                break
        return out

    # ── 融合与排序 ──────────────────────────────────────────

    def fuse(self, groups: dict[str, list[Candidate]], weights: Weights) -> list[Candidate]:
        """RRF 融合多路召回。"""
        merged: dict[str, Candidate] = {}
        rrf: dict[str, float] = {}

        route_weight = {
            "keyword": weights.keyword,
            "vector": weights.semantic,
            "trigram": weights.keyword * 0.6,  # 子串路是兜底，权重打折
        }

        for route, cands in groups.items():
            w = route_weight.get(route, 1.0)
            if w <= 0:
                continue
            for rank, c in enumerate(cands, start=1):
                rrf[c.item_id] = rrf.get(c.item_id, 0.0) + w / (RRF_K + rank)

                if c.item_id not in merged:
                    merged[c.item_id] = c
                else:
                    m = merged[c.item_id]
                    m.matched_via |= c.matched_via
                    m.rank_keyword = m.rank_keyword or c.rank_keyword
                    m.rank_vector = m.rank_vector or c.rank_vector
                    m.rank_trigram = m.rank_trigram or c.rank_trigram
                    m.bm25 = m.bm25 if m.bm25 is not None else c.bm25
                    m.distance = m.distance if m.distance is not None else c.distance
                    # 有正文片段的优先留着，纯标题命中的片段是空的
                    if not m.best_text and c.best_text:
                        m.best_text = c.best_text
                        m.page = c.page
                        m.start_sec = c.start_sec

        ordered = sorted(merged.values(), key=lambda c: -rrf[c.item_id])
        for c in ordered:
            setattr(c, "_rrf", rrf[c.item_id])
        return ordered

    def apply_signals(
        self, cands: list[Candidate], weights: Weights, query: str
    ) -> list[tuple[Candidate, float, dict[str, float]]]:
        """
        在 RRF 基础上叠加时间新鲜度、热度、标题命中三个信号。

        这三个是「加分项」不是「主排序」：它们的量纲统一在 0~1，
        乘上各自权重后加到 RRF 分上。RRF 分本身量级约 0.01~0.03，
        所以这里的加分要缩到同一量级，否则一个热门文件会永远霸榜。
        """
        if not cands:
            return []

        rows = self.repo.get_items([c.item_id for c in cands])
        now = datetime.now(UTC)
        terms = [t for t in highlight_terms(query) if len(t) >= 2]

        max_open = 1
        for r in rows.values():
            max_open = max(max_open, int(r["open_count"] or 0))

        out: list[tuple[Candidate, float, dict[str, float]]] = []
        for c in cands:
            r = rows.get(c.item_id)
            if r is None:
                continue

            base = float(getattr(c, "_rrf", 0.0))
            parts: dict[str, float] = {"rrf": base}

            # 时间新鲜度：半衰期 180 天的指数衰减，落在 0~1
            ts = r["content_time"] or r["created_at"]
            fresh = 0.0
            if ts:
                try:
                    dt = datetime.fromisoformat(str(ts))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    days = max(0.0, (now - dt).total_seconds() / 86400)
                    fresh = math.exp(-days / 180.0)
                except (ValueError, TypeError):
                    fresh = 0.0
            parts["recency"] = fresh

            # 热度：相对最热的那条归一化，取 log 压平长尾
            opens = int(r["open_count"] or 0)
            pop = math.log1p(opens) / math.log1p(max_open) if max_open > 1 else 0.0
            parts["popularity"] = pop

            # 标题命中
            title = str(r["title"] or "")
            hit = sum(1 for t in terms if t in title)
            title_hit = min(1.0, hit / len(terms)) if terms else 0.0
            parts["titleBoost"] = title_hit
            if title_hit > 0:
                c.matched_via.add("title")

            # 来源权重：本机文件比网页可信一点（用户自己存下来的东西）
            trust = {"file": 1.0, "chat-export": 0.9, "mail": 0.8, "clipboard": 0.7}.get(
                str(r["source"]), 0.6
            )
            parts["sourceTrust"] = trust

            # 加分项统一缩到 RRF 的量级（RRF 满打满算约 0.03）
            SCALE = 0.01
            score = base + SCALE * (
                weights.recency * fresh
                + weights.popularity * pop
                + weights.title_boost * title_hit
                + weights.source_trust * trust
            )
            out.append((c, score, parts))

        out.sort(key=lambda x: -x[1])
        return out

    # ── 对外：一次完整检索 ──────────────────────────────────

    def search(
        self,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        weights: dict[str, Any] | None = None,
        preset: str | None = None,
        limit: int = 30,
        offset: int = 0,
        explain: bool = False,
        stage: str = "semantic",
    ) -> dict[str, Any]:
        """
        stage 控制跑哪几路 —— D2 三级瀑布靠它分次返回：
          keyword  → 只跑关键词和子串（快，15~50ms）
          semantic → 全跑（150ms 级）
        """
        t0 = time.perf_counter()

        # D10：先把查询串里的 type:/date:/size:/in:/tag:/src: 指令拆出来，
        # 剩下的才是真正要拿去检索的词。解析不了的指令原样留在查询词里，
        # 不报错也不吞掉整条查询。
        parsed = parse_query(query)
        text_query = parsed.text
        f = Filters.from_dict(parsed.filters).merged_with(filters)

        w = PRESETS.get(preset or "", Weights()) if preset else Weights()
        if weights:
            w = Weights.from_dict(weights)

        groups: dict[str, list[Candidate]] = {
            "keyword": self.recall_keyword(text_query, f),
            "trigram": self.recall_title(text_query, f),
        }
        if stage == "semantic":
            groups["vector"] = self.recall_vector(text_query, f)

        # 只有筛选没有查询词（比如光敲 `type:pdf date:今天`）→ 直接按筛选列内容，
        # 否则三路召回全空，用户会以为筛选坏了
        if not text_query.strip() and not f.empty:
            groups = {"filter": self.recall_by_filter(f, limit=max(limit * 3, 90))}

        fused = self.fuse(groups, w)
        scored = self.apply_signals(fused, w, text_query)
        page = scored[offset : offset + limit]

        rows = self.repo.get_items([c.item_id for c, _, _ in page])
        terms = highlight_terms(text_query)

        hits: list[dict[str, Any]] = []
        for c, score, parts in page:
            r = rows.get(c.item_id)
            if r is None:
                continue
            hit: dict[str, Any] = {
                "item": _row_to_item(r, self.repo.item_tags(c.item_id)),
                "score": round(score, 6),
                "highlight": _highlight(c.best_text, terms),
            }
            loc: dict[str, Any] = {}
            if c.page is not None:
                loc["page"] = c.page
            if c.start_sec is not None:
                loc["startSec"] = c.start_sec
            if loc:
                hit["location"] = loc

            if explain:
                hit["explain"] = {
                    "scores": {
                        "keyword": round(-(c.bm25 or 0), 4) if c.bm25 is not None else None,
                        "semantic": round(1 - (c.distance or 0) / 2, 4)
                        if c.distance is not None
                        else None,
                        "recency": round(parts.get("recency", 0), 4),
                        "popularity": round(parts.get("popularity", 0), 4),
                        "sourceTrust": round(parts.get("sourceTrust", 0), 4),
                    },
                    "matchedTerms": terms[:12],
                    "matchedVia": sorted(c.matched_via),
                    "reason": _reason(c, parts),
                }
            hits.append(hit)

        out: dict[str, Any] = {
            "stage": stage,
            "final": stage == "semantic",
            "hits": hits,
            "totalEstimate": len(scored),
            "elapsedMs": round((time.perf_counter() - t0) * 1000, 1),
        }
        # 界面把这些渲染成一排可点掉的小标签，让用户看见"我刚才那句话被理解成了什么"
        if parsed.has_filters or parsed.unknown:
            out["parsedQuery"] = {
                "text": text_query,
                "filters": describe(parsed),
                "unknown": parsed.unknown,
            }
        return out

    # ── D3 跨模态互搜 ───────────────────────────────────────

    def search_by_image(
        self,
        image_vector: np.ndarray,
        *,
        limit: int = 30,
        include_scenes: bool = True,
        exclude_item: str = "",
    ) -> dict[str, Any]:
        """
        用一张图去搜：既搜库里的图片，也搜视频里的**镜头**。

        视频那一路是这个功能最有意思的地方 —— 拿一张截图丢进来，
        它能告诉你"这个画面出现在某个视频的第 3 分 24 秒"。
        图片向量和场景关键帧向量用的是同一个 CLIP 模型，
        所以两者在同一个语义空间里，可以直接比距离、直接混排。
        """
        t0 = time.perf_counter()
        conn = self.db.connect()
        blob = sqlite_vec.serialize_float32(list(image_vector))

        hits: list[dict[str, Any]] = []

        # ① 库里的图片
        try:
            rows = conn.execute(
                "SELECT item_rowid, distance FROM vec_items "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (blob, limit * 2),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

        if rows:
            rowids = [int(r["item_rowid"]) for r in rows]
            dist = {int(r["item_rowid"]): float(r["distance"]) for r in rows}
            marks = ",".join("?" * len(rowids))
            detail = conn.execute(
                f"SELECT rowid, id FROM items WHERE rowid IN ({marks})", rowids
            ).fetchall()
            by_rowid = {int(r["rowid"]): str(r["id"]) for r in detail}
            for rid in rowids:
                iid = by_rowid.get(rid)
                if not iid or iid == exclude_item:
                    continue
                hits.append({"itemId": iid, "distance": dist[rid], "kind": "image"})

        # ② 视频里的镜头
        if include_scenes:
            try:
                srows = conn.execute(
                    "SELECT vec_rowid, distance FROM vec_scenes "
                    "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (blob, limit * 2),
                ).fetchall()
            except sqlite3.OperationalError:
                srows = []

            if srows:
                vrowids = [int(r["vec_rowid"]) for r in srows]
                sdist = {int(r["vec_rowid"]): float(r["distance"]) for r in srows}
                marks = ",".join("?" * len(vrowids))
                smap = conn.execute(
                    f"SELECT m.vec_rowid, m.item_id, m.scene_index, "
                    f"       s.start_sec, s.end_sec, s.keyframe_path, s.transcript "
                    f"FROM scene_vec_map m "
                    f"JOIN video_scenes s ON s.item_id = m.item_id "
                    f"                   AND s.scene_index = m.scene_index "
                    f"WHERE m.vec_rowid IN ({marks})",
                    vrowids,
                ).fetchall()
                for r in smap:
                    iid = str(r["item_id"])
                    if iid == exclude_item:
                        continue
                    hits.append({
                        "itemId": iid,
                        "distance": sdist[int(r["vec_rowid"])],
                        "kind": "scene",
                        "sceneIndex": int(r["scene_index"]),
                        "startSec": float(r["start_sec"]),
                        "endSec": float(r["end_sec"]),
                        "keyframePath": r["keyframe_path"],
                        "transcript": r["transcript"] or "",
                    })

        # 混排：图片和镜头在同一个语义空间，直接按距离排
        hits.sort(key=lambda h: h["distance"])

        # 同一个视频只保留最相似的那一个镜头 ——
        # 不去重的话一个视频会用它的 20 个镜头霸占整页结果
        seen_video: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for h in hits:
            if h["kind"] == "scene":
                if h["itemId"] in seen_video:
                    continue
                seen_video.add(h["itemId"])
            deduped.append(h)
            if len(deduped) >= limit:
                break

        rows2 = self.repo.get_items([h["itemId"] for h in deduped])
        out: list[dict[str, Any]] = []
        for h in deduped:
            r = rows2.get(h["itemId"])
            if r is None:
                continue
            # L2 距离转相似度：向量已归一化，距离范围 [0,2]
            score = max(0.0, 1.0 - h["distance"] / 2.0)
            item = {
                "item": _row_to_item(r, self.repo.item_tags(h["itemId"])),
                "score": round(score, 6),
            }
            if h["kind"] == "scene":
                item["location"] = {"startSec": h["startSec"], "endSec": h["endSec"]}
                item["highlight"] = (h.get("transcript") or "")[:160]
                item["sceneKeyframe"] = h.get("keyframePath")
            out.append(item)

        return {
            "stage": "semantic",
            "final": True,
            "hits": out,
            "totalEstimate": len(out),
            "elapsedMs": round((time.perf_counter() - t0) * 1000, 1),
            "mode": "by-image",
        }

    def recall_by_filter(self, f: Filters, limit: int = 90) -> list[Candidate]:
        """光有筛选没有查询词时，按时间倒序列出符合条件的内容。"""
        conn = self.db.connect()
        where, args = f.sql("i")
        rows = conn.execute(
            f"SELECT i.id AS item_id FROM items i "
            f"{'WHERE ' + where if where else ''} "
            f"ORDER BY COALESCE(i.content_time, i.created_at) DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        out: list[Candidate] = []
        for rank, r in enumerate(rows, start=1):
            c = Candidate(item_id=str(r["item_id"]), rank_keyword=rank)
            c.matched_via.add("filter")
            out.append(c)
        return out


# ── 辅助 ────────────────────────────────────────────────────


def _row_to_item(r: sqlite3.Row, tags: list[str]) -> dict[str, Any]:
    import json as _json

    meta = None
    if r["meta_json"]:
        try:
            meta = _json.loads(str(r["meta_json"]))
        except _json.JSONDecodeError:
            meta = None
    return {
        "id": r["id"],
        "fingerprint": r["fingerprint"],
        "modality": r["modality"],
        "source": r["source"],
        "status": r["status"],
        "title": r["title"],
        "locator": r["locator"],
        "snippet": r["snippet"],
        "mime": r["mime"],
        "sizeBytes": r["size_bytes"],
        "contentTime": r["content_time"],
        "createdAt": r["created_at"],
        "updatedAt": r["updated_at"],
        "lastOpenedAt": r["last_opened_at"],
        "openCount": r["open_count"],
        "tags": tags,
        "thumbPath": r["thumb_path"],
        "meta": meta,
    }


def _highlight(text: str, terms: list[str], window: int = 160) -> str:
    """
    截一段包含命中词的片段并打标。

    截取窗口以第一个命中词为中心 —— 从头截的话，
    命中词在第 800 字的文档，用户在摘要里看不到任何相关内容。
    """
    if not text:
        return ""
    if not terms:
        return text[:window] + ("…" if len(text) > window else "")

    pos = -1
    for t in terms:
        if len(t) < 2:
            continue
        p = text.find(t)
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        pos = 0

    start = max(0, pos - window // 3)
    snippet = text[start : start + window]
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + window < len(text) else ""

    escaped = [re.escape(t) for t in terms if len(t) >= 2]
    if escaped:
        snippet = re.sub(f"({'|'.join(escaped)})", r"<em>\1</em>", snippet)
    return prefix + snippet + suffix


def _reason(c: Candidate, parts: dict[str, float]) -> str:
    """一句人话解释为什么这条能排上来。"""
    bits: list[str] = []
    if c.rank_keyword:
        bits.append(f"关键词第 {c.rank_keyword} 名")
    if c.rank_vector:
        sim = 1 - (c.distance or 0) / 2
        bits.append(f"语义相似 {sim:.2f}")
    if c.rank_trigram:
        bits.append("文件名含查询串")
    if parts.get("titleBoost", 0) > 0:
        bits.append("标题命中")
    if parts.get("recency", 0) > 0.6:
        bits.append("内容较新")
    if parts.get("popularity", 0) > 0.5:
        bits.append("你常打开")
    return "；".join(bits) or "综合相关"
