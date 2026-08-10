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
from datetime import UTC, datetime, timedelta
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
    #: L3：PDF 章节名（Abstract/Method/Results…）。非 PDF 内容恒为 None
    section: str | None = None
    token_count: int = 0


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db
        #: A17：大规模向量近似索引。None = 还没建（模型没就绪，或库还没大到
        #: 需要它）。由 `Runtime` 在嵌入模型就绪时创建并挂上来，见 `ann_index.py`
        #: 开头的设计说明——15 万块以下完全不启用，写入路径行为不变
        self.ann_index: Any = None

    # ── 写入 ────────────────────────────────────────────────

    def find_by_fingerprint(self, fingerprint: str) -> sqlite3.Row | None:
        conn = self.db.connect()
        return conn.execute("SELECT * FROM items WHERE fingerprint = ?", (fingerprint,)).fetchone()

    def find_by_locator(self, locator: str) -> sqlite3.Row | None:
        """目录监控用：文件被删了，得先按路径找到是哪条 item 才能把它挪进回收站。"""
        conn = self.db.connect()
        return conn.execute("SELECT * FROM items WHERE locator = ?", (locator,)).fetchone()

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

        # A17：ANN 索引的变更**先攒着，事务真正 COMMIT 了才落地**。
        # usearch 的 Index 不是 SQLite 的一部分，不会跟着 ROLLBACK 自动撤销——
        # 如果在事务中途就直接改了 ANN 索引，一旦这批写入失败回滚，
        # ANN 索引会留着一批 SQLite 里根本不存在的 rowid（脏但不致命：
        # 查询侧按 rowid 关联 chunks 表查不到就跳过，见 recall_vector），
        # 但更纯粹的做法是等 COMMIT 真正成功再落地，两边永远不会不一致。
        ann_removes: list[int] = []
        ann_adds: list[tuple[int, list[float]]] = []

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
                ann_removes.append(int(r["rowid"]))
            conn.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))

            written = 0
            for i, c in enumerate(chunks):
                chunk_id = new_id()
                cur = conn.execute(
                    """
                    INSERT INTO chunks (
                        id, item_id, chunk_index, text, channel,
                        page, start_sec, end_sec, bbox_json, section, token_count
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        chunk_id, item_id, c.index, c.text, c.channel,
                        c.page, c.start_sec, c.end_sec, c.bbox_json, c.section, c.token_count,
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
                    if self.ann_index is not None:
                        ann_adds.append((rid, embeddings[i].tolist()))
                written += 1

            if model_id:
                conn.execute(
                    "INSERT INTO meta_kv (key, value) VALUES ('last_embed_model', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (model_id,),
                )
            conn.execute("COMMIT")

            if self.ann_index is not None:
                for rid in ann_removes:
                    self.ann_index.remove(rid)
                if ann_adds:
                    # 🔴 **批量插，不要一条一条插**（A7）。
                    #
                    # 原来这里是 `for rid, vec in ann_adds: self.ann_index.add(...)`，
                    # 每条都要：抢一次锁 + 一次 `np.asarray` + 一次单向量 HNSW 插入
                    # + 一次 `len(idx)`。而 HNSW 插入是这条链路上最贵的单步操作之一。
                    #
                    # `add_many` 早就写好了（`ann_index.py:140`），走的是
                    # `idx.add(keys_array, matrix, threads=0)` —— 一次锁、一次调用、
                    # 用满所有核。**功能一样，只是原来没用它。**
                    #
                    # 为什么怀疑这里：`embedder.py:44` 的实测注释写着这颗 CPU 上
                    # 嵌入本身能跑 110~247 段/秒，而 A7 端到端只有 47 ——
                    # 差的那 60% 不在模型里，得在模型之外找。
                    self.ann_index.add_many(
                        [rid for rid, _ in ann_adds],
                        [vec for _, vec in ann_adds],
                    )

            return written
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def backfill_vectors(
        self, item_id: str, embeddings: Any, *, model_id: str = ""
    ) -> int:
        """
        A1 渐进索引：给**已经写好的**分块补上向量，不动 chunks 表。

        ── 为什么需要它 ────────────────────────────────────
        原来流水线的顺序是 `chunk → embed → write_chunks`，也就是说
        **一份文件在向量算完之前完全搜不到**。而向量是整条链路里最慢的一步，
        于是"选个文件夹开始索引"之后有很长一段时间界面上什么都搜不出来，
        用户看到的是一个"好像没在干活"的空列表。

        改成 `chunk → write_chunks(无向量) → embed → backfill_vectors` 之后，
        关键词层立刻可用，语义层在后台补齐并静默升级。

        ── 为什么不直接再调一次 write_chunks ──────────────
        那个方法会**先删光旧分块再重插**，于是：
          ① 全部 rowid 变一遍，ANN 索引要整份删掉重建
          ② 中间存在一个"分块已删、新分块还没插完"的窗口，
             这期间搜索会**搜不到这份刚刚还能搜到的文件**
        ②比①严重得多 —— 那是功能在用户眼皮底下闪断，
        而且只在"边索引边搜"时出现，最难复现的一类。

        返回真正写进去的向量条数。**数量对不上直接抛**，不静默截断：
        错位的向量会让搜索结果驴唇不对马嘴，且极难定位到是这里。
        """
        rows = self.db.connect().execute(
            "SELECT rowid FROM chunks WHERE item_id = ? ORDER BY chunk_index",
            (item_id,),
        ).fetchall()
        if not rows:
            return 0
        if embeddings is None:
            return 0
        if len(embeddings) != len(rows):
            raise ValueError(
                f"回填向量数 {len(embeddings)} 与已存分块数 {len(rows)} 对不上（item={item_id}）—— "
                "多半是 chunk 阶段和 embed 阶段之间分块被改过；"
                "宁可整条失败也不能写进去一批错位的向量"
            )

        ann_adds: list[tuple[int, list[float]]] = []
        conn = self.db.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for i, r in enumerate(rows):
                rid = int(r["rowid"])
                vec = embeddings[i].tolist()
                # 先删后插而不是 UPDATE：vec_chunks 是虚拟表，
                # 不同 sqlite_vec 版本对 UPDATE 的支持不一致，
                # 而"删+插"在所有版本上行为相同
                conn.execute("DELETE FROM vec_chunks WHERE chunk_rowid = ?", (rid,))
                conn.execute(
                    "INSERT INTO vec_chunks (chunk_rowid, embedding) VALUES (?,?)",
                    (rid, sqlite_vec.serialize_float32(vec)),
                )
                if self.ann_index is not None:
                    ann_adds.append((rid, vec))

            if model_id:
                conn.execute(
                    "INSERT INTO meta_kv (key, value) VALUES ('last_embed_model', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (model_id,),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        # 与 write_chunks 同一条纪律：ANN 索引不是 SQLite 的一部分，
        # 必须等 COMMIT 真正成功之后才落地，否则回滚时两边会不一致
        if self.ann_index is not None and ann_adds:
            for rid, _ in ann_adds:
                # 这些 rowid 之前是"有分块无向量"，ANN 里本来就没有它们；
                # 但重跑 embed 的情况下可能已经在了，先移除保证不重复
                try:
                    self.ann_index.remove(rid)
                except Exception:  # noqa: BLE001
                    # 不存在时 remove 抛异常是正常的，不该让整批回填失败
                    pass
            self.ann_index.add_many(
                [rid for rid, _ in ann_adds],
                [vec for _, vec in ann_adds],
            )

        return len(ann_adds) if self.ann_index is not None else len(rows)

    def write_phash(self, item_id: str, phash: str) -> None:
        """
        感知哈希按 16 位分段存（E9 近重复检测）。

        分段是为了能先做等值匹配再算汉明距离：
        64 位哈希切成 4 段，两张相似图至少有一段完全相同的概率很高，
        先按段命中候选再精算，比全表扫快两个数量级。
        """
        if not phash or len(phash) < 16:
            return
        conn = self.db.connect()
        conn.execute("DELETE FROM phash_buckets WHERE item_id = ?", (item_id,))
        for seg in range(4):
            chunk = phash[seg * 4 : seg * 4 + 4]
            try:
                conn.execute(
                    "INSERT INTO phash_buckets (item_id, seg, value) VALUES (?,?,?)",
                    (item_id, seg, int(chunk, 16)),
                )
            except (ValueError, sqlite3.IntegrityError):
                continue

    def find_near_duplicates(self, phash: str, exclude_item: str = "", max_dist: int = 10) -> list[str]:
        """按分段命中先取候选，再精算汉明距离。"""
        if not phash or len(phash) < 16:
            return []
        conn = self.db.connect()
        cand: set[str] = set()
        for seg in range(4):
            try:
                v = int(phash[seg * 4 : seg * 4 + 4], 16)
            except ValueError:
                continue
            for r in conn.execute(
                "SELECT item_id FROM phash_buckets WHERE seg = ? AND value = ? LIMIT 500", (seg, v)
            ):
                if str(r["item_id"]) != exclude_item:
                    cand.add(str(r["item_id"]))
        if not cand:
            return []

        from ..analyze.image import hamming

        out: list[tuple[int, str]] = []
        marks = ",".join("?" * len(cand))
        rows = conn.execute(
            f"SELECT id, meta_json FROM items WHERE id IN ({marks})", list(cand)
        ).fetchall()
        for r in rows:
            try:
                other = json.loads(str(r["meta_json"] or "{}")).get("phash")
            except json.JSONDecodeError:
                continue
            if not other:
                continue
            d = hamming(phash, other)
            if d <= max_dist:
                out.append((d, str(r["id"])))
        out.sort()
        return [i for _, i in out]

    def write_item_vector(self, item_id: str, embedding: Any) -> None:
        """条目级向量（图片用）。文本走 chunk 级向量，图片一张就是一个整体。"""
        conn = self.db.connect()
        row = conn.execute("SELECT rowid FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return
        rid = int(row["rowid"])
        conn.execute("DELETE FROM vec_items WHERE item_rowid = ?", (rid,))
        conn.execute(
            "INSERT INTO vec_items (item_rowid, embedding) VALUES (?,?)",
            (rid, sqlite_vec.serialize_float32(list(embedding))),
        )

    # ── C5 人脸聚类 ─────────────────────────────────────────

    #: 两张脸的余弦相似度超过这个才算"同一个人"。
    #: 🔴 **这个数字没有在这个项目里实测校准过**——ArcFace 系模型社区里
    #: 常见的经验阈值落在 0.4~0.5 这个区间，这里取中间值当默认起点，
    #: 但"多少算同一个人"这件事严重依赖具体人群和拍摄条件，
    #: 装完这个功能之后应该拿自己的真实照片库跑几组已知同/不同的人核对，
    #: 太松（阈值太低）会把不同的人并成一类，太紧会把同一个人拆成好几类。
    FACE_MATCH_THRESHOLD = 0.45

    def write_faces(self, item_id: str, faces: list[tuple[tuple[float, float, float, float], float, Any]]) -> int:
        """
        写一张图检测到的所有人脸，每张脸都要经过聚类分配。

        `faces`：`[(bbox, det_score, embedding), ...]`，embedding 已经 L2 归一化过。
        一个事务写完——人脸记录和它所属的聚类必须同时成立，
        不能出现"脸存进去了但没有聚类归属"的半截状态。
        """
        if not faces:
            return 0
        conn = self.db.connect()
        ts = now_iso()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 同一张图重新分析时，先把它名下的旧人脸记录清掉——
            # 不清的话重跑一次人脸检测，同一批脸会在库里翻倍
            conn.execute("DELETE FROM faces WHERE item_id = ?", (item_id,))

            clusters = conn.execute(
                "SELECT id, centroid, face_count FROM face_clusters"
            ).fetchall()
            written = 0
            for bbox, score, embedding in faces:
                emb = np.asarray(embedding, dtype=np.float32)
                cluster_id = self._assign_face_cluster(conn, clusters, emb, ts)
                face_id = new_id()
                conn.execute(
                    "INSERT INTO faces (id, item_id, cluster_id, bbox_json, det_score, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (face_id, item_id, cluster_id, json.dumps(list(bbox)), float(score), ts),
                )
                written += 1
            conn.execute("COMMIT")
            return written
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _assign_face_cluster(
        self, conn: sqlite3.Connection, clusters: list[sqlite3.Row], embedding: np.ndarray, ts: str,
    ) -> str:
        """
        找最相似的已有聚类中心；够相似就归进去并更新中心（运行平均），
        不够相似就新建一类。**`clusters` 是调用方传进来的快照**——
        同一次 `write_faces` 调用里，前面几张脸新建/更新的聚类不会立刻
        反映到这份快照里，这意味着"同一张照片里长得很像的两张脸"
        理论上可能被分进两个刚好都新建的聚类而不是互相匹配到——
        这种情况很少见（同一张照片里两张脸一般是不同的人），
        真发生了也不是数据损坏，顶多是多了一个可以后续合并的聚类。
        """
        best_id: str | None = None
        best_sim = -1.0
        best_centroid: np.ndarray | None = None
        best_count = 0
        for r in clusters:
            centroid = np.frombuffer(r["centroid"], dtype=np.float32)
            sim = float(np.dot(embedding, centroid))
            if sim > best_sim:
                best_sim = sim
                best_id = str(r["id"])
                best_centroid = centroid
                best_count = int(r["face_count"])

        if best_id is not None and best_sim >= self.FACE_MATCH_THRESHOLD and best_centroid is not None:
            new_centroid = (best_centroid * best_count + embedding) / (best_count + 1)
            norm = float(np.linalg.norm(new_centroid))
            if norm > 1e-6:
                new_centroid = new_centroid / norm
            conn.execute(
                "UPDATE face_clusters SET centroid=?, face_count=face_count+1, updated_at=? WHERE id=?",
                (new_centroid.astype(np.float32).tobytes(), ts, best_id),
            )
            return best_id

        cluster_id = new_id()
        conn.execute(
            "INSERT INTO face_clusters (id, label, face_count, centroid, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (cluster_id, None, 1, embedding.astype(np.float32).tobytes(), ts, ts),
        )
        return cluster_id

    def pending_face_items(self, limit: int = 200) -> list[tuple[str, str]]:
        """还没跑人脸检测的图片。判据和 C4 图片描述一样——"从没跑过这个阶段"。"""
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT i.id, i.locator FROM items i
            LEFT JOIN item_stages s ON s.item_id = i.id AND s.stage = 'faces'
            WHERE i.modality = 'image' AND s.stage IS NULL
            ORDER BY i.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(str(r["id"]), str(r["locator"])) for r in rows]

    def list_face_clusters(self, limit: int = 200) -> list[dict[str, Any]]:
        """人物列表：按聚类里的照片数量倒序——照片最多的人物最可能是用户想找的。"""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT id, label, face_count, updated_at FROM face_clusters "
            "ORDER BY face_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "label": r["label"],
                "faceCount": int(r["face_count"]),
                "updatedAt": r["updated_at"],
            }
            for r in rows
        ]

    def label_face_cluster(self, cluster_id: str, label: str | None) -> bool:
        """给一个人物命名（或清空名字）。这是用户唯一能对聚类结果做的事——
        应用自己绝不猜测、绝不自动填一个名字。"""
        conn = self.db.connect()
        cur = conn.execute(
            "UPDATE face_clusters SET label = ?, updated_at = ? WHERE id = ?",
            (label, now_iso(), cluster_id),
        )
        return cur.rowcount > 0

    def items_in_face_cluster(self, cluster_id: str, limit: int = 200) -> list[sqlite3.Row]:
        """这个人物出现在哪些内容里——"搜所有出现过这张脸的照片"就是查这个。"""
        conn = self.db.connect()
        return conn.execute(
            """
            SELECT DISTINCT i.* FROM items i
            JOIN faces f ON f.item_id = i.id
            WHERE f.cluster_id = ?
            ORDER BY i.content_time DESC
            LIMIT ?
            """,
            (cluster_id, limit),
        ).fetchall()

    def faces_for_item(self, item_id: str) -> list[dict[str, Any]]:
        """一张图里检测到的所有脸，含各自所属的人物（供界面画框+标名字）。"""
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT f.id, f.bbox_json, f.det_score, f.cluster_id, c.label
            FROM faces f LEFT JOIN face_clusters c ON c.id = f.cluster_id
            WHERE f.item_id = ?
            """,
            (item_id,),
        ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "bbox": json.loads(r["bbox_json"]),
                "detScore": r["det_score"],
                "clusterId": r["cluster_id"],
                "label": r["label"],
            }
            for r in rows
        ]

    def write_scenes(
        self, item_id: str, scenes: list[tuple[int, float, float, str | None, str]]
    ) -> int:
        """写视频场景。scenes: [(index, start, end, keyframe_path, transcript), ...]"""
        if not scenes:
            return 0
        conn = self.db.connect()
        conn.execute("DELETE FROM video_scenes WHERE item_id = ?", (item_id,))
        conn.executemany(
            "INSERT INTO video_scenes (item_id, scene_index, start_sec, end_sec, "
            "keyframe_path, transcript) VALUES (?,?,?,?,?,?)",
            [(item_id, i, a, b, kf, tx) for i, a, b, kf, tx in scenes],
        )
        return len(scenes)

    def write_scene_vectors(self, item_id: str, vectors: list[tuple[int, Any]]) -> int:
        """
        关键帧向量。存在 vec_scenes 里，rowid 映射到 video_scenes 的 rowid，
        这样向量命中之后能直接拿到"第几分几秒"和那一帧的缩略图。
        """
        if not vectors:
            return 0
        conn = self.db.connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS scene_vec_map ("
            "  vec_rowid INTEGER PRIMARY KEY, item_id TEXT NOT NULL, scene_index INTEGER NOT NULL,"
            "  UNIQUE (item_id, scene_index))"
        )
        dim = len(list(vectors[0][1]))
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_scenes USING vec0("
            f"  vec_rowid INTEGER PRIMARY KEY, embedding FLOAT[{dim}])"
        )

        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT vec_rowid FROM scene_vec_map WHERE item_id = ?", (item_id,)
            ).fetchall()
            for r in old:
                conn.execute("DELETE FROM vec_scenes WHERE vec_rowid = ?", (r["vec_rowid"],))
            conn.execute("DELETE FROM scene_vec_map WHERE item_id = ?", (item_id,))

            for idx, vec in vectors:
                cur = conn.execute(
                    "INSERT INTO scene_vec_map (item_id, scene_index) VALUES (?,?)",
                    (item_id, idx),
                )
                conn.execute(
                    "INSERT INTO vec_scenes (vec_rowid, embedding) VALUES (?,?)",
                    (cur.lastrowid, sqlite_vec.serialize_float32(list(vec))),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return len(vectors)

    def attach_transcript_to_scenes(
        self, item_id: str, segments: list[tuple[float, float, str]]
    ) -> None:
        """把转写句按时间落到对应场景上 —— 一段场景既有画面也有台词。"""
        conn = self.db.connect()
        scenes = conn.execute(
            "SELECT scene_index, start_sec, end_sec FROM video_scenes WHERE item_id = ? "
            "ORDER BY scene_index",
            (item_id,),
        ).fetchall()
        if not scenes:
            return
        buckets: dict[int, list[str]] = {}
        for a, b, text in segments:
            mid = (a + b) / 2
            for s in scenes:
                if float(s["start_sec"]) <= mid < float(s["end_sec"]):
                    buckets.setdefault(int(s["scene_index"]), []).append(text)
                    break
        for idx, texts in buckets.items():
            conn.execute(
                "UPDATE video_scenes SET transcript = ? WHERE item_id = ? AND scene_index = ?",
                (" ".join(texts), item_id, idx),
            )

    def scenes_of(self, item_id: str) -> list[dict[str, Any]]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT scene_index, start_sec, end_sec, keyframe_path, transcript "
            "FROM video_scenes WHERE item_id = ? ORDER BY scene_index",
            (item_id,),
        ).fetchall()
        return [
            {
                "index": int(r["scene_index"]),
                "startSec": float(r["start_sec"]),
                "endSec": float(r["end_sec"]),
                "keyframePath": r["keyframe_path"],
                "transcript": r["transcript"] or "",
            }
            for r in rows
        ]

    def pending_transcribe_items(self, limit: int = 20) -> list[tuple[str, str]]:
        """还没转写的视频/音频，新的排前面。"""
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT i.id, i.locator FROM items i
            LEFT JOIN item_stages s ON s.item_id = i.id AND s.stage = 'transcribe'
            WHERE i.modality IN ('video','audio')
              AND (s.status IS NULL OR s.status = 'pending')
            ORDER BY i.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(str(r["id"]), str(r["locator"])) for r in rows]

    def file_backed_items(self, limit: int = 5000, offset: int = 0) -> list[sqlite3.Row]:
        """
        磁盘上有实体文件的条目（4.22b H2 源文件完整性校验用）。

        排除 `source='link'/'clipboard'` 这类没有本地文件的 —— 对它们做
        "文件还在不在"的检查毫无意义，只会在报告里制造一堆假的"文件丢失"。
        """
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT id, locator, fingerprint, title, size_bytes, modality
            FROM items
            WHERE locator IS NOT NULL AND locator <> ''
              AND source NOT IN ('link', 'clipboard')
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return list(rows)

    def pending_ocr_items(self, limit: int = 200) -> list[tuple[str, str]]:
        """还没跑 OCR 的图片，按新到旧排 —— 用户最近加的图最可能马上要搜。"""
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT i.id, i.locator FROM items i
            LEFT JOIN item_stages s ON s.item_id = i.id AND s.stage = 'ocr'
            WHERE i.modality = 'image'
              AND (s.status IS NULL OR s.status = 'pending')
            ORDER BY i.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(str(r["id"]), str(r["locator"])) for r in rows]

    def pending_description_items(self, limit: int = 200) -> list[tuple[str, str]]:
        """
        C4：还没生成描述的图片。**"description" 阶段一开始不存在**——
        不像 OCR 那样在图片摄取时就统一标 pending（OCR 一直是默认要做的事，
        只是延后跑；图片描述是默认关闭的可选功能，大部分用户永远不会打开它，
        没道理给每张图片都预先写一行"pending"占着表）。所以这里判"待处理"
        用的是"从来没有这个阶段的记录"，跟 OCR 那条"记录是 pending"的判据不同。
        """
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT i.id, i.locator FROM items i
            LEFT JOIN item_stages s ON s.item_id = i.id AND s.stage = 'description'
            WHERE i.modality = 'image' AND s.stage IS NULL
            ORDER BY i.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [(str(r["id"]), str(r["locator"])) for r in rows]

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
        """
        删一条内容，连带清掉它的全部索引 —— 少清一处就会留下幽灵结果。

        🔴 **这里发现并修了一个真实存在的 bug**：`vec_chunks`/`vec_items`
        是延迟建表（见 db.py 的 `ensure_vector_table`/`ensure_image_vector_table`）
        —— `vec_items` 专管图像向量，一个从没索引过图片的库压根不会建这张表。
        之前这里无条件 `DELETE FROM vec_items`，任何这样的库只要删一条内容
        就会 `sqlite3.OperationalError: no such table: vec_items`，整个请求
        500——不是"删除功能有点小问题"，是"删除功能在很常见的一类库里
        直接不能用"。现在删之前先确认表存在。
        """
        conn = self.db.connect()
        existing_tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name IN ('vec_chunks', 'vec_items')"
            ).fetchall()
        }
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
                if "vec_chunks" in existing_tables:
                    conn.execute("DELETE FROM vec_chunks WHERE chunk_rowid = ?", (r["rowid"],))
            conn.execute("DELETE FROM items_fts WHERE rowid = ?", (row["rowid"],))
            conn.execute("DELETE FROM items_tri WHERE rowid = ?", (row["rowid"],))
            if "vec_items" in existing_tables:
                conn.execute("DELETE FROM vec_items WHERE item_rowid = ?", (row["rowid"],))
            # items 上有外键级联，chunks / item_tags / stages 会自动清
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    #: 回收站保留期。到期前用户随时能按 locator 重新投喂一次找回来
    TRASH_RETENTION_DAYS = 30

    def soft_delete_item(self, item_id: str) -> str | None:
        """
        先把这条内容的关键信息记进回收站，再照常执行 `delete_item()`
        （索引照常立刻清干净，搜不到、不留幽灵结果）。

        🔴 **不保留 chunks/向量**——恢复不是"瞬间撤销"，是"把这个 locator
        重新投喂一次"（re-ingest）。权衡过的取舍：保留向量能让恢复更快，
        但代价是要么让已删除内容继续占着索引表、要么给全库每一条搜索
        查询都加一层"跳过回收站里的"过滤——后者几处查询路径漏掉一处
        就会让"删了"的内容又搜得到，风险比"恢复慢一点"大得多。

        返回新建的回收站条目 id；item 本来就不存在则返回 None，不记录。
        """
        conn = self.db.connect()
        row = conn.execute(
            "SELECT title, locator, modality, source, size_bytes FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return None

        trash_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        deleted_at = now.isoformat()
        purge_at = (now + timedelta(days=self.TRASH_RETENTION_DAYS)).isoformat()

        conn.execute(
            """
            INSERT INTO trash (id, item_id, title, locator, modality, source,
                                size_bytes, deleted_at, purge_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trash_id, item_id, row["title"] or "", row["locator"],
                row["modality"], row["source"], row["size_bytes"],
                deleted_at, purge_at,
            ),
        )
        self.delete_item(item_id)
        return trash_id

    def list_trash(self) -> list[sqlite3.Row]:
        conn = self.db.connect()
        return conn.execute("SELECT * FROM trash ORDER BY deleted_at DESC").fetchall()

    def get_trash_entry(self, trash_id: str) -> sqlite3.Row | None:
        conn = self.db.connect()
        return conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id,)).fetchone()

    def remove_trash_entry(self, trash_id: str) -> None:
        """从回收站表里拿掉一条——不管是恢复完了还是用户手动"彻底删除"。"""
        conn = self.db.connect()
        conn.execute("DELETE FROM trash WHERE id = ?", (trash_id,))

    def purge_expired_trash(self) -> int:
        """清掉过期的回收站记录（不碰硬盘原文件——那从来不是这个功能的范围）。"""
        conn = self.db.connect()
        now_iso = datetime.now(UTC).isoformat()
        cur = conn.execute("DELETE FROM trash WHERE purge_at <= ?", (now_iso,))
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

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

    def item_chunks(self, item_id: str, *, limit: int = 400) -> list[sqlite3.Row]:
        """
        一篇文档的所有块，按原文顺序。N6「这篇能回答哪些问题」要用。

        `limit` 卡在 400 是因为再多也读不完 —— 一篇 400 块的文档
        约等于 12 万字，问题清单从前 400 块里读出来已经足够代表全篇，
        而不设上限的话，索引了一整本书的用户会一次拉出几万行。
        """
        conn = self.db.connect()
        # 只取正文块（body）：OCR 和语音转写那些块混进来会让问题清单
        # 出现「图里那行字讲了什么」这种没意义的条目
        return conn.execute(
            "SELECT rowid, * FROM chunks WHERE item_id = ? AND channel = 'body' "
            "ORDER BY chunk_index LIMIT ?",
            (item_id, max(1, limit)),
        ).fetchall()

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
