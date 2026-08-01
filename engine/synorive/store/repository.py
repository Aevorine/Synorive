"""
仓储层 —— 内容、分块、关键词索引、向量索引的读写
====================================================================
最重要的一条：**一条内容的正文、FTS 索引、向量，必须在同一个事务里写完。**

分开写会出现"文本索引进去了但向量没进去"的半截状态，症状是
某些内容关键词搜得到、语义搜不到，而且不报任何错。
这种不一致一旦产生，除非全库重建否则发现不了。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import sqlite_vec

from .db import Database
from .text import to_index_text

log = logging.getLogger("synorive.repo")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:24]


@dataclass
class ChunkRow:
    text: str
    channel: str
    index: int
    page: int | None = None
    start_sec: float | None = None
    end_sec: float | None = None
    bbox_json: str | None = None
    token_count: int = 0


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ── 写入 ────────────────────────────────────────────────

    def find_by_fingerprint(self, fingerprint: str) -> sqlite3.Row | None:
        conn = self.db.connect()
        return conn.execute("SELECT * FROM items WHERE fingerprint = ?", (fingerprint,)).fetchone()

    def upsert_item(
        self,
        *,
        fingerprint: str,
        modality: str,
        source: str,
        title: str,
        locator: str,
        snippet: str | None = None,
        mime: str | None = None,
        size_bytes: int | None = None,
        content_time: str | None = None,
        meta: dict[str, Any] | None = None,
        status: str = "queued",
        tags: list[str] | None = None,
    ) -> tuple[str, bool]:
        """
        写入或更新一条内容。返回 (item_id, 是否新建)。

        指纹相同就认为是同一份内容（哪怕路径不同），直接返回已有 id。
        这是「重复投喂不重做」和断点续跑（A13）的地基。
        """
        conn = self.db.connect()
        ts = now_iso()

        existing = self.find_by_fingerprint(fingerprint)
        if existing is not None:
            conn.execute(
                "UPDATE items SET locator = ?, updated_at = ? WHERE id = ?",
                (locator, ts, existing["id"]),
            )
            return str(existing["id"]), False

        item_id = new_id()
        conn.execute(
            """
            INSERT INTO items (
                id, fingerprint, modality, source, status, title, locator,
                snippet, mime, size_bytes, content_time, created_at, updated_at,
                open_count, meta_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
            """,
            (
                item_id, fingerprint, modality, source, status, title, locator,
                snippet, mime, size_bytes, content_time, ts, ts,
                json.dumps(meta, ensure_ascii=False) if meta else None,
            ),
        )
        if tags:
            self._attach_tags(item_id, tags)
        return item_id, True

    def _attach_tags(self, item_id: str, tags: list[str]) -> None:
        conn = self.db.connect()
        for t in tags:
            t = t.strip()
            if not t:
                continue
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (t,))
            row = conn.execute("SELECT id FROM tags WHERE name = ?", (t,)).fetchone()
            if row:
                conn.execute(
                    "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?,?)",
                    (item_id, row["id"]),
                )

    def index_item_text(self, item_id: str) -> None:
        """把一条内容的标题/摘要/路径写进关键词索引。"""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT rowid, title, snippet, locator FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return

        rid = row["rowid"]
        # 先删后插，避免同一条被索引两次（更新场景）
        conn.execute("DELETE FROM items_fts WHERE rowid = ?", (rid,))
        conn.execute("DELETE FROM items_tri WHERE rowid = ?", (rid,))

        conn.execute(
            "INSERT INTO items_fts (rowid, title, snippet, locator) VALUES (?,?,?,?)",
            (
                rid,
                to_index_text(row["title"] or ""),
                to_index_text(row["snippet"] or ""),
                to_index_text(_path_words(row["locator"] or "")),
            ),
        )
        # trigram 表存**原文**，它就是用来做子串匹配的，分了词反而没意义
        conn.execute(
            "INSERT INTO items_tri (rowid, title, locator) VALUES (?,?,?)",
            (rid, row["title"] or "", row["locator"] or ""),
        )

    def write_chunks(
        self,
        item_id: str,
        chunks: list[ChunkRow],
        embeddings: np.ndarray | None = None,
        *,
        model_id: str = "",
    ) -> int:
        """
        一个事务里写完：chunks 表 + FTS 索引 + 向量表。

        embeddings 传 None 表示这批只建关键词索引不建向量
        （比如向量模型还没下载完，先让关键词搜索能用起来）。
        """
        if not chunks:
            return 0
        if embeddings is not None and len(embeddings) != len(chunks):
            raise ValueError(
                f"分块数 {len(chunks)} 与向量数 {len(embeddings)} 对不上 —— "
                "这类数量不匹配如果不当场拦住，会写进去一批错位的向量，"
                "表现是搜索结果驴唇不对马嘴，而且极难定位"
            )

        conn = self.db.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 旧分块先清掉（重新分析同一份内容时）
            old = conn.execute(
                "SELECT rowid FROM chunks WHERE item_id = ?", (item_id,)
            ).fetchall()
            for r in old:
                conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (r["rowid"],))
                conn.execute("DELETE FROM vec_chunks WHERE chunk_rowid = ?", (r["rowid"],))
            conn.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))

            written = 0
            for i, c in enumerate(chunks):
                chunk_id = new_id()
                cur = conn.execute(
                    """
                    INSERT INTO chunks (
                        id, item_id, chunk_index, text, channel,
                        page, start_sec, end_sec, bbox_json, token_count
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        chunk_id, item_id, c.index, c.text, c.channel,
                        c.page, c.start_sec, c.end_sec, c.bbox_json, c.token_count,
                    ),
                )
                rid = cur.lastrowid
                conn.execute(
                    "INSERT INTO chunks_fts (rowid, text) VALUES (?,?)",
                    (rid, to_index_text(c.text)),
                )
                if embeddings is not None:
                    conn.execute(
                        "INSERT INTO vec_chunks (chunk_rowid, embedding) VALUES (?,?)",
                        (rid, sqlite_vec.serialize_float32(embeddings[i].tolist())),
                    )
                written += 1

            if model_id:
                conn.execute(
                    "INSERT INTO meta_kv (key, value) VALUES ('last_embed_model', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (model_id,),
                )
            conn.execute("COMMIT")
            return written
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def set_stage(
        self,
        item_id: str,
        stage: str,
        status: str,
        *,
        error: str | None = None,
        model_id: str | None = None,
    ) -> None:
        """记录某个分析阶段的状态 —— 断点续跑靠它知道哪些阶段已经做完。"""
        conn = self.db.connect()
        ts = now_iso()
        conn.execute(
            """
            INSERT INTO item_stages (item_id, stage, status, started_at, ended_at, error, model_id)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(item_id, stage) DO UPDATE SET
                status = excluded.status,
                ended_at = excluded.ended_at,
                error = excluded.error,
                model_id = COALESCE(excluded.model_id, item_stages.model_id)
            """,
            (
                item_id, stage, status,
                ts if status == "running" else None,
                ts if status in ("done", "failed", "skipped") else None,
                error, model_id,
            ),
        )

    def get_done_stages(self, item_id: str) -> set[str]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT stage FROM item_stages WHERE item_id = ? AND status = 'done'", (item_id,)
        ).fetchall()
        return {str(r["stage"]) for r in rows}

    def get_settled_stages(self, item_id: str) -> set[str]:
        """
        「有结论了」的阶段 = done 或 skipped。

        断点续跑判断要用这个而不是 get_done_stages：
        空文件的 chunk 阶段永远是 skipped，只认 done 的话它每次重跑都要重做一遍。
        """
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT stage FROM item_stages WHERE item_id = ? AND status IN ('done','skipped')",
            (item_id,),
        ).fetchall()
        return {str(r["stage"]) for r in rows}

    def set_item_status(self, item_id: str, status: str, error: str | None = None) -> None:
        conn = self.db.connect()
        conn.execute(
            "UPDATE items SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, now_iso(), item_id),
        )

    def update_item_fields(self, item_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "title", "snippet", "mime", "size_bytes", "content_time",
            "thumb_path", "meta_json", "modality",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        conn = self.db.connect()
        clause = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(
            f"UPDATE items SET {clause}, updated_at = ? WHERE id = ?",
            (*sets.values(), now_iso(), item_id),
        )

    def write_entities(self, item_id: str, entities: list[tuple[str, str, int]]) -> int:
        """
        写入一条内容里抽出的实体，并更新共现边（E6 知识图谱的地基）。

        entities: [(kind, name, count), ...]

        共现边只连**同一条内容里**一起出现的实体，且按名字排序后存单向边，
        避免 A→B 和 B→A 存两条把权重算重。
        """
        if not entities:
            return 0

        conn = self.db.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 重新分析同一条内容时，先撤掉它上次贡献的提及
            old = conn.execute(
                "SELECT entity_id FROM entity_mentions WHERE item_id = ?", (item_id,)
            ).fetchall()
            for r in old:
                conn.execute(
                    "UPDATE entities SET mention_count = MAX(0, mention_count - 1) WHERE id = ?",
                    (r["entity_id"],),
                )
            conn.execute("DELETE FROM entity_mentions WHERE item_id = ?", (item_id,))

            ids: list[str] = []
            for kind, name, count in entities:
                conn.execute(
                    "INSERT OR IGNORE INTO entities (id, kind, name, aliases_json, mention_count) "
                    "VALUES (?,?,?,'[]',0)",
                    (new_id(), kind, name),
                )
                row = conn.execute(
                    "SELECT id FROM entities WHERE kind = ? AND name = ?", (kind, name)
                ).fetchone()
                if row is None:
                    continue
                eid = str(row["id"])
                ids.append(eid)
                conn.execute(
                    "UPDATE entities SET mention_count = mention_count + ? WHERE id = ?",
                    (count, eid),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO entity_mentions (entity_id, item_id, chunk_id) "
                    "VALUES (?,?,NULL)",
                    (eid, item_id),
                )

            # 共现边。实体多的时候两两组合会爆炸（40 个 → 780 条），
            # 所以只连最靠前的那些 —— 尾部的低频实体连起来也没有分析价值。
            top = ids[:12]
            for i, a in enumerate(top):
                for b in top[i + 1 :]:
                    lo, hi = (a, b) if a < b else (b, a)
                    conn.execute(
                        "INSERT INTO entity_edges (from_id, to_id, weight) VALUES (?,?,1) "
                        "ON CONFLICT(from_id, to_id) DO UPDATE SET weight = weight + 1",
                        (lo, hi),
                    )

            conn.execute("COMMIT")
            return len(ids)
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def graph_slice(
        self, entity_id: str | None = None, kind: str | None = None, limit: int = 60
    ) -> dict[str, Any]:
        """取一片子图给 E6 图谱界面。不给 entity_id 就返回全局最热的那些。"""
        conn = self.db.connect()
        if entity_id:
            rows = conn.execute(
                "SELECT e.* FROM entities e WHERE e.id = ? "
                "UNION "
                "SELECT e.* FROM entities e JOIN entity_edges g "
                "  ON (g.to_id = e.id AND g.from_id = ?) OR (g.from_id = e.id AND g.to_id = ?) "
                "ORDER BY mention_count DESC LIMIT ?",
                (entity_id, entity_id, entity_id, limit),
            ).fetchall()
        elif kind:
            rows = conn.execute(
                "SELECT * FROM entities WHERE kind = ? ORDER BY mention_count DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entities ORDER BY mention_count DESC LIMIT ?", (limit,)
            ).fetchall()

        ents = [
            {
                "id": str(r["id"]),
                "kind": str(r["kind"]),
                "name": str(r["name"]),
                "aliases": [],
                "mentionCount": int(r["mention_count"]),
            }
            for r in rows
        ]
        if not ents:
            return {"entities": [], "edges": []}

        ids = [e["id"] for e in ents]
        marks = ",".join("?" * len(ids))
        edges = conn.execute(
            f"SELECT from_id, to_id, weight, relation FROM entity_edges "
            f"WHERE from_id IN ({marks}) AND to_id IN ({marks}) ORDER BY weight DESC LIMIT 400",
            (*ids, *ids),
        ).fetchall()
        return {
            "entities": ents,
            "edges": [
                {
                    "from": str(e["from_id"]),
                    "to": str(e["to_id"]),
                    "weight": int(e["weight"]),
                    "relation": e["relation"],
                }
                for e in edges
            ],
        }

    def timeline(self, bucket: str = "day", limit: int = 400) -> list[dict[str, Any]]:
        """E5 语义时间轴：按时间桶统计内容数量与模态分布。"""
        fmt = {
            "hour": "%Y-%m-%dT%H:00:00",
            "day": "%Y-%m-%d",
            "week": "%Y-W%W",
            "month": "%Y-%m",
            "year": "%Y",
        }.get(bucket, "%Y-%m-%d")

        conn = self.db.connect()
        rows = conn.execute(
            "SELECT strftime(?, COALESCE(content_time, created_at)) AS bucket, "
            "       modality, COUNT(*) AS n "
            "FROM items WHERE COALESCE(content_time, created_at) IS NOT NULL "
            "GROUP BY bucket, modality ORDER BY bucket DESC LIMIT ?",
            (fmt, limit * 6),
        ).fetchall()

        agg: dict[str, dict[str, Any]] = {}
        for r in rows:
            b = str(r["bucket"] or "")
            if not b:
                continue
            slot = agg.setdefault(b, {"at": b, "count": 0, "byModality": {}})
            slot["count"] += int(r["n"])
            slot["byModality"][str(r["modality"])] = int(r["n"])
        return sorted(agg.values(), key=lambda x: str(x["at"]))[-limit:]

    def record_open(self, item_id: str) -> None:
        """E11 热度学习：记一次打开。纯本地统计，设置里可一键清空。"""
        conn = self.db.connect()
        conn.execute(
            "UPDATE items SET open_count = open_count + 1, last_opened_at = ? WHERE id = ?",
            (now_iso(), item_id),
        )

    def delete_item(self, item_id: str) -> None:
        """删一条内容，连带清掉它的全部索引 —— 少清一处就会留下幽灵结果。"""
        conn = self.db.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT rowid FROM items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return
            for r in conn.execute(
                "SELECT rowid FROM chunks WHERE item_id = ?", (item_id,)
            ).fetchall():
                conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (r["rowid"],))
                conn.execute("DELETE FROM vec_chunks WHERE chunk_rowid = ?", (r["rowid"],))
            conn.execute("DELETE FROM items_fts WHERE rowid = ?", (row["rowid"],))
            conn.execute("DELETE FROM items_tri WHERE rowid = ?", (row["rowid"],))
            conn.execute("DELETE FROM vec_items WHERE item_rowid = ?", (row["rowid"],))
            # items 上有外键级联，chunks / item_tags / stages 会自动清
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── 读取 ────────────────────────────────────────────────

    def get_item(self, item_id: str) -> sqlite3.Row | None:
        conn = self.db.connect()
        return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    def get_items(self, item_ids: list[str]) -> dict[str, sqlite3.Row]:
        """批量取，避免 N+1 查询 —— 搜索结果一页 50 条，逐条查会慢 50 倍。"""
        if not item_ids:
            return {}
        conn = self.db.connect()
        marks = ",".join("?" * len(item_ids))
        rows = conn.execute(f"SELECT * FROM items WHERE id IN ({marks})", item_ids).fetchall()
        return {str(r["id"]): r for r in rows}

    def get_chunk_text(self, chunk_rowid: int) -> sqlite3.Row | None:
        conn = self.db.connect()
        return conn.execute(
            "SELECT c.*, i.id AS item_id_str FROM chunks c "
            "JOIN items i ON i.id = c.item_id WHERE c.rowid = ?",
            (chunk_rowid,),
        ).fetchone()

    def item_tags(self, item_id: str) -> list[str]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT t.name FROM tags t JOIN item_tags it ON it.tag_id = t.id "
            "WHERE it.item_id = ? ORDER BY t.name",
            (item_id,),
        ).fetchall()
        return [str(r["name"]) for r in rows]

    def stats(self) -> dict[str, Any]:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT COUNT(*) AS items, "
            "  SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) AS ready, "
            "  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed "
            "FROM items"
        ).fetchone()
        chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        return {
            "items": int(row["items"] or 0),
            "ready": int(row["ready"] or 0),
            "failed": int(row["failed"] or 0),
            "chunks": int(chunks["n"] or 0),
        }


def _path_words(locator: str) -> str:
    """
    把路径拆成可搜的词。
    `D:\\项目\\报告\\2026年度总结.docx` → `D 项目 报告 2026年度总结 docx`
    不拆的话整条路径是一个 token，搜"年度总结"命中不了。
    """
    import re

    return " ".join(w for w in re.split(r"[\\/._\-\s]+", locator) if w)
