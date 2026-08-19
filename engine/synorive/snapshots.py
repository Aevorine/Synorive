"""
时间机器快照（提案 34）
====================================================================
记下"某一刻库里有哪些东西"，之后可以拿两个时刻对比：这期间多了什么、少了什么、
哪些原地改过。

🔴 **它不是备份，救不回删掉的内容。** 快照存的是清单（id / 指纹 / 标题 / 位置），
   不是文件本身 —— 一份 200 GB 的库，快照只有几 MB。名字叫"时间机器"很容易
   让人以为能回滚，所以界面上必须写清楚它只回答"变了什么"，不负责"变回去"。
   （真要找回删掉的东西走回收站，那是另一条路。）

🔴 **对比要按 id 和指纹两个维度分开算。**
   同一个 id 指纹变了 = 内容原地改过；指纹一样 id 变了 = 同一份东西重新入了一次库。
   只比 id 的话，第二种会被报成"删了一条又加了一条"，纯属噪音。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("synorive.snapshots")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id         TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT '',
    taken_at   TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    auto       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS snapshot_items (
    snapshot_id TEXT NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    item_id     TEXT NOT NULL,
    fingerprint TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    locator     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (snapshot_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_taken ON snapshots (taken_at DESC);
"""

#: 留几份。快照本身很小，但清单表是按条数线性增长的 ——
#: 一个 20 万条的库留 100 份快照就是 2000 万行，删起来比建起来慢得多。
KEEP_MAX = 30


def ensure_schema(conn: Any) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def take(repo: Any, label: str = "", auto: bool = False) -> dict[str, Any]:
    """拍一张。整个过程在一个事务里，中途出错不会留下半张。"""
    conn = repo.db.connect()
    ensure_schema(conn)
    sid = uuid.uuid4().hex
    at = _now()
    with conn:
        conn.execute(
            "INSERT INTO snapshots (id, label, taken_at, item_count, auto) VALUES (?,?,?,?,?)",
            (sid, label.strip(), at, 0, 1 if auto else 0),
        )
        n = conn.execute(
            "INSERT INTO snapshot_items (snapshot_id, item_id, fingerprint, title, locator) "
            "SELECT ?, id, COALESCE(fingerprint,''), COALESCE(title,''), COALESCE(locator,'') "
            "FROM items",
            (sid,),
        ).rowcount
        conn.execute("UPDATE snapshots SET item_count = ? WHERE id = ?", (n, sid))
    _prune(conn)
    return {"id": sid, "label": label.strip(), "takenAt": at, "itemCount": n, "auto": auto}


def _prune(conn: Any) -> None:
    """超量时删最老的**自动**快照。手动拍的是用户特意留的，一律不动。"""
    old = conn.execute(
        "SELECT id FROM snapshots WHERE auto = 1 ORDER BY taken_at DESC LIMIT -1 OFFSET ?",
        (KEEP_MAX,),
    ).fetchall()
    if not old:
        return
    with conn:
        for r in old:
            conn.execute("DELETE FROM snapshot_items WHERE snapshot_id = ?", (str(r["id"]),))
            conn.execute("DELETE FROM snapshots WHERE id = ?", (str(r["id"]),))
    log.info("清掉 %d 张过期的自动快照", len(old))


def listing(repo: Any, limit: int = 50) -> list[dict[str, Any]]:
    conn = repo.db.connect()
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, label, taken_at, item_count, auto FROM snapshots "
        "ORDER BY taken_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "label": str(r["label"] or ""),
            "takenAt": str(r["taken_at"]),
            "itemCount": int(r["item_count"]),
            "auto": bool(r["auto"]),
        }
        for r in rows
    ]


def drop(repo: Any, snapshot_id: str) -> bool:
    conn = repo.db.connect()
    ensure_schema(conn)
    with conn:
        conn.execute("DELETE FROM snapshot_items WHERE snapshot_id = ?", (snapshot_id,))
        n = conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,)).rowcount
    return n > 0


def _load(conn: Any, snapshot_id: str) -> dict[str, dict[str, str]] | None:
    if not conn.execute("SELECT 1 FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone():
        return None
    rows = conn.execute(
        "SELECT item_id, fingerprint, title, locator FROM snapshot_items WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    return {
        str(r["item_id"]): {
            "fingerprint": str(r["fingerprint"] or ""),
            "title": str(r["title"] or ""),
            "locator": str(r["locator"] or ""),
        }
        for r in rows
    }


def _current(conn: Any) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        "SELECT id, COALESCE(fingerprint,'') f, COALESCE(title,'') t, COALESCE(locator,'') l "
        "FROM items"
    ).fetchall()
    return {
        str(r["id"]): {
            "fingerprint": str(r["f"]),
            "title": str(r["t"]),
            "locator": str(r["l"]),
        }
        for r in rows
    }


def diff(repo: Any, base_id: str, other_id: str | None = None, limit: int = 200) -> dict[str, Any]:
    """
    对比两张快照。other_id 传 None 表示"跟现在比"。

    返回四类：新增 / 删除 / 原地改动 / 重新入库（同一份内容换了个 id）。
    """
    conn = repo.db.connect()
    ensure_schema(conn)

    base = _load(conn, base_id)
    if base is None:
        raise KeyError(f"找不到快照 {base_id}")
    if other_id:
        other = _load(conn, other_id)
        if other is None:
            raise KeyError(f"找不到快照 {other_id}")
        other_label = other_id
    else:
        other = _current(conn)
        other_label = "现在"

    base_ids, other_ids = set(base), set(other)
    added_ids = other_ids - base_ids
    removed_ids = base_ids - other_ids

    # 指纹相同但 id 不同 = 同一份东西重新入了一次库，不是"删一条加一条"
    base_fp = {v["fingerprint"]: k for k, v in base.items() if v["fingerprint"]}
    other_fp = {v["fingerprint"]: k for k, v in other.items() if v["fingerprint"]}
    reingested = []
    for fp, oid in other_fp.items():
        bid = base_fp.get(fp)
        if bid and bid != oid and oid in added_ids and bid in removed_ids:
            reingested.append({"fingerprint": fp, "wasId": bid, "nowId": oid, **other[oid]})
            added_ids.discard(oid)
            removed_ids.discard(bid)

    changed = [
        {"itemId": i, **other[i], "wasFingerprint": base[i]["fingerprint"]}
        for i in base_ids & other_ids
        if base[i]["fingerprint"] and other[i]["fingerprint"]
        and base[i]["fingerprint"] != other[i]["fingerprint"]
    ]

    def pack(ids: set[str], src: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
        return [{"itemId": i, **src[i]} for i in sorted(ids)][:limit]

    return {
        "base": base_id,
        "other": other_label,
        "counts": {
            "added": len(added_ids),
            "removed": len(removed_ids),
            "changed": len(changed),
            "reingested": len(reingested),
        },
        "added": pack(added_ids, other),
        "removed": pack(removed_ids, base),
        "changed": changed[:limit],
        "reingested": reingested[:limit],
        # 🔴 明说：清单里只列前 limit 条，别让用户以为"就这么多"
        "truncated": max(len(added_ids), len(removed_ids), len(changed)) > limit,
    }
