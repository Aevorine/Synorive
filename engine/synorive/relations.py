"""
人物关系时间线（提案 35）
====================================================================
现在的图谱回答的是"谁和谁有关"，但它是**没有时间的**：一张把三年的共现全揉在
一起的网，看起来谁都跟谁有关，实际什么也说明不了。

这里回答的是另一个问题：**盯住一个人，看他在不同时期分别跟谁一起出现。**
"2024 上半年一直跟 A 一起出现，下半年换成了 B" —— 这种变化才是有信息量的，
而它在一张无时间的网里完全看不出来。

🔴 **时间取 content_time，取不到才退回 created_at，并且要标出来。**
   入库时间是"你什么时候整理的"，不是"事情什么时候发生的"。
   一次性导入三年的聊天记录，全按入库时间算的话所有事都发生在同一天 ——
   图看着很漂亮，结论全是错的。所以每个桶都带上 `estimated`：
   这一桶里有多少条是拿入库时间凑的。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("synorive.relations")

BUCKETS = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
    "quarter": "%Y-%m",  # 先按月取，下面再折成季度
    "year": "%Y",
}

#: 每个时间桶里最多列几个同伴。再多界面上就是一团糊，也没人看得完。
PEERS_PER_BUCKET = 8


def _quarter(month_bucket: str) -> str:
    try:
        y, m = month_bucket.split("-")
        return f"{y}-Q{(int(m) - 1) // 3 + 1}"
    except (ValueError, IndexError):
        return month_bucket


def find_entities(repo: Any, q: str = "", kind: str = "", limit: int = 40) -> list[dict[str, Any]]:
    """按名字找实体，给"先选一个人"那一步用。"""
    conn = repo.db.connect()
    where, args = [], []
    if q.strip():
        where.append("name LIKE ?")
        args.append(f"%{q.strip()}%")
    if kind.strip():
        where.append("kind = ?")
        args.append(kind.strip())
    sql = "SELECT id, kind, name, mention_count FROM entities"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY mention_count DESC LIMIT ?"
    args.append(limit)
    return [
        {
            "id": str(r["id"]),
            "kind": str(r["kind"]),
            "name": str(r["name"]),
            "mentionCount": int(r["mention_count"]),
        }
        for r in conn.execute(sql, tuple(args)).fetchall()
    ]


def timeline(
    repo: Any,
    entity_id: str,
    bucket: str = "month",
    limit: int = 60,
    kinds: list[str] | None = None,
) -> dict[str, Any]:
    """
    某个实体的关系时间线。

    返回按时间正序排的桶，每桶里是"这段时间跟它一起出现过的实体"和证据条数。
    """
    if bucket not in BUCKETS:
        raise ValueError(f"bucket 只能是 {'/'.join(BUCKETS)}，收到 {bucket}")
    conn = repo.db.connect()

    root = conn.execute(
        "SELECT id, kind, name, mention_count FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if root is None:
        raise KeyError(f"找不到实体 {entity_id}")

    fmt = BUCKETS[bucket]
    kind_filter = ""
    kind_args: tuple[Any, ...] = ()
    if kinds:
        kind_filter = f" AND e2.kind IN ({','.join('?' * len(kinds))})"
        kind_args = tuple(kinds)

    # 一条 SQL 拿全：同一条资料里出现过的其它实体，按时间桶聚合。
    # 用 content_time 是否为 NULL 记下"这条的时间是不是估的"。
    rows = conn.execute(
        f"""
        SELECT strftime(?, COALESCE(i.content_time, i.created_at)) AS b,
               e2.id   AS peer_id,
               e2.kind AS peer_kind,
               e2.name AS peer_name,
               COUNT(DISTINCT i.id) AS n,
               SUM(CASE WHEN i.content_time IS NULL THEN 1 ELSE 0 END) AS guessed
        FROM entity_mentions m1
        JOIN items i          ON i.id = m1.item_id
        JOIN entity_mentions m2 ON m2.item_id = m1.item_id AND m2.entity_id <> m1.entity_id
        JOIN entities e2      ON e2.id = m2.entity_id
        WHERE m1.entity_id = ?
          AND COALESCE(i.content_time, i.created_at) IS NOT NULL
          {kind_filter}
        GROUP BY b, e2.id
        ORDER BY b DESC, n DESC
        """,
        (fmt, entity_id, *kind_args),
    ).fetchall()

    # 这个实体自己在每个桶里的出现量（作为背景，"这段时间他活跃吗"）
    self_rows = conn.execute(
        """
        SELECT strftime(?, COALESCE(i.content_time, i.created_at)) AS b,
               COUNT(DISTINCT i.id) AS n,
               SUM(CASE WHEN i.content_time IS NULL THEN 1 ELSE 0 END) AS guessed
        FROM entity_mentions m
        JOIN items i ON i.id = m.item_id
        WHERE m.entity_id = ? AND COALESCE(i.content_time, i.created_at) IS NOT NULL
        GROUP BY b
        """,
        (fmt, entity_id),
    ).fetchall()

    def key(b: str) -> str:
        return _quarter(b) if bucket == "quarter" else b

    buckets: dict[str, dict[str, Any]] = {}
    for r in self_rows:
        b = key(str(r["b"] or ""))
        if not b:
            continue
        slot = buckets.setdefault(b, {"at": b, "count": 0, "estimated": 0, "peers": {}})
        slot["count"] += int(r["n"])
        slot["estimated"] += int(r["guessed"] or 0)

    for r in rows:
        b = key(str(r["b"] or ""))
        if not b:
            continue
        slot = buckets.setdefault(b, {"at": b, "count": 0, "estimated": 0, "peers": {}})
        pid = str(r["peer_id"])
        peer = slot["peers"].setdefault(
            pid, {"id": pid, "kind": str(r["peer_kind"]), "name": str(r["peer_name"]), "count": 0}
        )
        peer["count"] += int(r["n"])

    out = []
    for b in sorted(buckets)[-limit:]:
        slot = buckets[b]
        peers = sorted(slot["peers"].values(), key=lambda p: -int(p["count"]))
        out.append(
            {
                "at": b,
                "count": slot["count"],
                # 有多少条是拿入库时间凑的 —— 界面要据此打问号
                "estimated": slot["estimated"],
                "peers": peers[:PEERS_PER_BUCKET],
                "peerTotal": len(peers),
            }
        )

    return {
        "entity": {
            "id": str(root["id"]),
            "kind": str(root["kind"]),
            "name": str(root["name"]),
            "mentionCount": int(root["mention_count"]),
        },
        "bucket": bucket,
        "buckets": out,
        "changes": _changes(out),
    }


def _changes(buckets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    找出"这一期新出现的同伴"和"上一期还在、这一期没了的同伴"。

    🔴 **这才是这个功能真正要给的东西。** 一堆桶摆在那儿用户还是得自己逐个比对；
       直接把"变化点"挑出来，才省掉了那一步。
    """
    out = []
    prev: set[str] = set()
    prev_names: dict[str, str] = {}
    for i, b in enumerate(buckets):
        now = {str(p["id"]) for p in b["peers"]}
        names = {str(p["id"]): str(p["name"]) for p in b["peers"]}
        if i > 0:
            new = [names[x] for x in now - prev]
            gone = [prev_names[x] for x in prev - now]
            if new or gone:
                out.append({"at": b["at"], "appeared": sorted(new), "disappeared": sorted(gone)})
        prev, prev_names = now, names
    return out
