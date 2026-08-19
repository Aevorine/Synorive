"""
多库联邦检索（提案 37）
====================================================================
一台电脑上不止一个库：工作一个、私人一个、外接硬盘上还躺着去年那个。
现在要找一句话，得挨个切库搜三遍 —— 而"我记得看过"这件事本身
是不分库的，人不会记得那句话当初存在哪个库里。

这里让主库的搜索一次问遍所有登记过的库，结果标明来自哪个库。

🔴 **副库一律只读打开（mode=ro）。** 另一个库可能正被另一个 Synorive 进程写着，
   拿写模式去开会抢锁，症状是那边突然"数据库被锁定"。只读还有第二个好处：
   即使这边代码写错了，也绝对改不坏别人的库。

🔴 **副库只走关键词，不走向量。** 向量得由同一个模型算出来才可比 ——
   拿本库的模型去查另一个库存的向量，算出来的相似度是**看着正常的胡说**，
   排序全乱而且不报错。所以宁可少一路召回，也不给假的排序。
   界面上必须写明这一点，否则用户会以为副库的搜索能力和主库一样。

🔴 **单个副库出错不能拖垮整次搜索。** 外接硬盘拔了、库被加密了、文件损坏了 ——
   都只把那个库标成不可用，其余照常返回。
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .store.text import to_query, to_trigram_query

log = logging.getLogger("synorive.federation")

SCHEMA = """
CREATE TABLE IF NOT EXISTS federated_libs (
    id       TEXT PRIMARY KEY,
    label    TEXT NOT NULL,
    db_path  TEXT NOT NULL UNIQUE,
    enabled  INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

#: 每个副库最多取这么多条。副库没有重排，取太多只是把噪音端上来。
PER_LIB_LIMIT = 20
#: 打开副库的超时。外接硬盘掉线时 sqlite 默认会等很久，界面就一直转圈。
OPEN_TIMEOUT_S = 3.0


def ensure_schema(conn: Any) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def register(repo: Any, db_path: str, label: str = "") -> dict[str, Any]:
    """
    登记一个副库。**登记时就要验一次能不能打开**，
    否则错误要等到用户第一次搜索时才暴露，而那时他早忘了自己填了什么。
    """
    p = Path(db_path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"这个路径上没有文件：{p}")
    probe = _probe(p)
    if not probe["ok"]:
        raise ValueError(probe["reason"])

    conn = repo.db.connect()
    ensure_schema(conn)
    lid = uuid.uuid4().hex
    with conn:
        conn.execute(
            "INSERT INTO federated_libs (id, label, db_path, enabled) VALUES (?,?,?,1) "
            "ON CONFLICT (db_path) DO UPDATE SET label = excluded.label, enabled = 1",
            (lid, label.strip() or p.stem, str(p)),
        )
    return {"id": lid, "label": label.strip() or p.stem, "dbPath": str(p), "itemCount": probe["items"]}


def unregister(repo: Any, lib_id: str) -> bool:
    conn = repo.db.connect()
    ensure_schema(conn)
    with conn:
        return conn.execute("DELETE FROM federated_libs WHERE id = ?", (lib_id,)).rowcount > 0


def set_enabled(repo: Any, lib_id: str, enabled: bool) -> bool:
    conn = repo.db.connect()
    ensure_schema(conn)
    with conn:
        n = conn.execute(
            "UPDATE federated_libs SET enabled = ? WHERE id = ?", (1 if enabled else 0, lib_id)
        ).rowcount
    return n > 0


def listing(repo: Any) -> list[dict[str, Any]]:
    """列出所有副库，**顺手探一次活**。列表上直接看得到哪个已经连不上了。"""
    conn = repo.db.connect()
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, label, db_path, enabled FROM federated_libs ORDER BY label"
    ).fetchall()
    out = []
    for r in rows:
        probe = _probe(Path(str(r["db_path"])))
        out.append(
            {
                "id": str(r["id"]),
                "label": str(r["label"]),
                "dbPath": str(r["db_path"]),
                "enabled": bool(r["enabled"]),
                "reachable": probe["ok"],
                "itemCount": probe["items"],
                "problem": None if probe["ok"] else probe["reason"],
            }
        )
    return out


def _open_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=0"
    conn = sqlite3.connect(uri, uri=True, timeout=OPEN_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    return conn


def _probe(path: Path) -> dict[str, Any]:
    """能不能读、有多少条。任何异常都翻译成一句人话，不往上抛。"""
    if not path.is_file():
        return {"ok": False, "items": 0, "reason": "文件不在了（硬盘拔了？路径改了？）"}
    try:
        conn = _open_ro(path)
    except sqlite3.Error as e:
        return {"ok": False, "items": 0, "reason": f"打不开：{e}"}
    try:
        n = int(conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"])
        return {"ok": True, "items": n, "reason": ""}
    except sqlite3.DatabaseError as e:
        # 加密库在没有密钥时报的就是 "file is not a database"
        msg = str(e)
        if "not a database" in msg or "encrypted" in msg:
            return {"ok": False, "items": 0, "reason": "这个库是加密的，联邦检索读不了它"}
        return {"ok": False, "items": 0, "reason": f"库结构对不上：{msg}"}
    finally:
        conn.close()


def _search_one(path: Path, expr: str, query_raw: str, limit: int) -> list[dict[str, Any]]:
    """
    在一个副库里跑关键词召回，三路：块级分词 → 标题/路径分词 → 标题/路径子串。

    三路的顺序就是精度顺序，靠不同的分数区间保证合并后排序不乱。
    """
    conn = _open_ro(path)
    try:
        rows = conn.execute(
            """
            SELECT i.id, i.title, i.locator, i.modality, i.content_time, i.created_at,
                   c.text AS chunk, bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            JOIN items  i ON i.id = c.item_id
            WHERE chunks_fts MATCH ?
            ORDER BY score LIMIT ?
            """,
            (expr, limit),
        ).fetchall()
        hits = {
            str(r["id"]): {
                "itemId": str(r["id"]),
                "title": str(r["title"] or "") or str(r["locator"] or ""),
                "locator": str(r["locator"] or ""),
                "modality": str(r["modality"] or ""),
                "at": r["content_time"] or r["created_at"],
                "snippet": (str(r["chunk"] or "")[:240]) or None,
                "score": -float(r["score"]),  # bm25 越小越好，翻个号方便统一排序
            }
            for r in rows
        }
        # 标题/路径的分词命中
        if len(hits) < limit:
            for r in conn.execute(
                """
                SELECT i.id, i.title, i.locator, i.modality, i.content_time, i.created_at,
                       i.snippet AS chunk, bm25(items_fts) AS score
                FROM items_fts
                JOIN items i ON i.rowid = items_fts.rowid
                WHERE items_fts MATCH ?
                ORDER BY score LIMIT ?
                """,
                (expr, limit),
            ).fetchall():
                hits.setdefault(
                    str(r["id"]),
                    {
                        "itemId": str(r["id"]),
                        "title": str(r["title"] or "") or str(r["locator"] or ""),
                        "locator": str(r["locator"] or ""),
                        "modality": str(r["modality"] or ""),
                        "at": r["content_time"] or r["created_at"],
                        "snippet": (str(r["chunk"] or "")[:240]) or None,
                        "score": -float(r["score"]),
                    },
                )
        # 🔴 **子串兜底这一路不能省。** 中文分词会把「预算表」切成一个词，
        #    于是搜「预算」在分词索引上一条都命中不了 —— 而那个文件就摆在那儿。
        #    主库靠语义那一路把它捞回来，副库没有语义，只剩这条 trigram。
        #    少了它，副库会在最常见的一类查询上表现为"明明有却搜不到"。
        #
        #    ⚠️ 这一路的覆盖面要说准，别夸大：`items_tri` **只索引标题和路径**，
        #    不索引正文；而且 sqlite 的 trigram 要求查询 ≥3 个字符。
        #    所以副库真正的边界是：
        #      · 正文里的词按分词后的整词匹配 —— 搜「预算」找不到正文里的「预算表」
        #      · 标题/路径可以按子串匹配，但查询得有 3 个字以上
        #    主库靠语义那一路补上第一条，副库没有那一路，这就是它的天花板。
        #    界面上照实写了这两句，不写的话用户只会觉得"这个功能时灵时不灵"。
        tri = to_trigram_query(query_raw)
        if tri and len(hits) < limit:
            try:
                for r in conn.execute(
                    """
                    SELECT i.id, i.title, i.locator, i.modality, i.content_time, i.created_at,
                           i.snippet AS chunk, bm25(items_tri) AS score
                    FROM items_tri
                    JOIN items i ON i.rowid = items_tri.rowid
                    WHERE items_tri MATCH ?
                    ORDER BY score LIMIT ?
                    """,
                    (tri, limit),
                ).fetchall():
                    hits.setdefault(
                        str(r["id"]),
                        {
                            "itemId": str(r["id"]),
                            "title": str(r["title"] or "") or str(r["locator"] or ""),
                            "locator": str(r["locator"] or ""),
                            "modality": str(r["modality"] or ""),
                            "at": r["content_time"] or r["created_at"],
                            "snippet": (str(r["chunk"] or "")[:240]) or None,
                            # 子串命中排在分词命中之后：它精度低，
                            # 让它插到前面会把真正相关的结果挤下去
                            "score": -float(r["score"]) - 1000.0,
                        },
                    )
            except sqlite3.OperationalError:
                # 老库可能没有 items_tri 这张表。少一路而已，不该整次搜索失败
                pass

        return sorted(hits.values(), key=lambda h: -float(h["score"]))[:limit]
    finally:
        conn.close()


def search(repo: Any, query: str, limit: int = 30) -> dict[str, Any]:
    """
    问遍所有启用的副库。

    返回里 `libraries` 一栏逐库说明结果数或失败原因 ——
    只给一个混合列表的话，用户没法知道"那个库是没命中还是压根没连上"，
    而这两件事的处理方式完全不同。
    """
    expr = to_query(query)
    conn = repo.db.connect()
    ensure_schema(conn)
    libs = conn.execute(
        "SELECT id, label, db_path FROM federated_libs WHERE enabled = 1 ORDER BY label"
    ).fetchall()

    reports: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    if not expr:
        return {"query": query, "hits": [], "libraries": [], "keywordOnly": True}

    for r in libs:
        label, path = str(r["label"]), Path(str(r["db_path"]))
        try:
            hits = _search_one(path, expr, query, PER_LIB_LIMIT)
        except (sqlite3.Error, OSError) as e:
            log.warning("副库 %s 搜索失败：%s", label, e)
            reports.append({"id": str(r["id"]), "label": label, "ok": False, "count": 0,
                            "problem": _probe(path)["reason"] or str(e)})
            continue
        reports.append({"id": str(r["id"]), "label": label, "ok": True, "count": len(hits),
                        "problem": None})
        for h in hits:
            merged.append({**h, "libraryId": str(r["id"]), "library": label})

    merged.sort(key=lambda h: -float(h["score"]))
    return {
        "query": query,
        "hits": merged[:limit],
        "libraries": reports,
        # 🔴 界面必须把这条显示出来：副库的结果没有语义召回也没有重排
        "keywordOnly": True,
    }
