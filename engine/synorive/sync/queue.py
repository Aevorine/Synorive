"""
6.5 —— 离线队列与冲突合并
====================================================================

手机端在地铁里存了三条笔记，桌面端同时改了其中一条的标签。
两边再见面时，**谁赢**？这个文件回答的就是这一个问题。

## 冲突策略：Lamport 时钟 + 设备号兜底（LWW）

🔴 **不用挂钟时间比大小。** 手机和电脑的系统时间差几分钟是常态
（时区没设对、没联网校时、用户手动改过）。按挂钟比的话，
一台时间偏快的设备会**永远赢**，它的旧数据会持续覆盖另一台的新数据 ——
而且完全不报错，用户只会觉得"我明明改了怎么又变回去了"。

Lamport 时钟只保证因果序：我见过你的第 5 号操作，我的下一个操作就是第 6 号。
两个操作**并发**（谁也没见过谁）时 Lamport 值可能相同，这时用设备 id
字典序兜底 —— 兜底规则本身不重要，**重要的是它在两台设备上算出同一个结果**。
不兜底的话两边各留各的，从此永久分叉。

🔴 **删除必须留墓碑（tombstone）。** 直接把行删掉的话，
对端下次同步会把它当成"我这儿有你没有的东西"再推回来 ——
删除会自己复活。墓碑要保留到确认两端都收到为止。

## 队列本身

离线时操作先进队列，联网了再批量推。
🔴 **队列在磁盘上是密文**（用 `crypto.seal`）—— 它装着笔记正文，
明文躺在手机的应用目录里，等于绕过了整套加密同步。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("synorive.sync")

#: 单次推送最多带多少条。太大一次失败就全部重来，太小往返次数多
MAX_BATCH = 200
#: 墓碑保留多久（秒）。30 天足够任何一台设备回来同步一次；
#: 再长下去墓碑表会无限膨胀
TOMBSTONE_TTL_S = 30 * 86400


@dataclass
class Op:
    """一条同步操作。"""

    id: str
    entity: str  # item / tag / note
    entity_id: str
    kind: str  # upsert / delete
    payload: dict[str, Any] = field(default_factory=dict)
    device: str = ""
    lamport: int = 0
    #: 挂钟时间**只用来显示和清理墓碑，绝不参与冲突判定**（见文件头）
    wall_ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "entity": self.entity, "entityId": self.entity_id,
            "kind": self.kind, "payload": self.payload, "device": self.device,
            "lamport": self.lamport, "wallTs": self.wall_ts,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Op:
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            entity=str(d.get("entity") or "item"),
            entity_id=str(d.get("entityId") or ""),
            kind=str(d.get("kind") or "upsert"),
            payload=dict(d.get("payload") or {}),
            device=str(d.get("device") or ""),
            lamport=int(d.get("lamport") or 0),
            wall_ts=float(d.get("wallTs") or 0.0),
        )

    @property
    def order_key(self) -> tuple[int, str]:
        """
        冲突排序键。**两台设备必须算出完全一样的结果**，
        所以只用 lamport 和 device，不掺任何本地状态。
        """
        return (self.lamport, self.device)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_queue (
    id        TEXT PRIMARY KEY,
    entity    TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    kind      TEXT NOT NULL,
    payload   TEXT NOT NULL,
    device    TEXT NOT NULL,
    lamport   INTEGER NOT NULL,
    wall_ts   REAL NOT NULL,
    sent      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_queue_sent ON sync_queue (sent, lamport);
CREATE TABLE IF NOT EXISTS sync_applied (
    entity    TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    lamport   INTEGER NOT NULL,
    device    TEXT NOT NULL,
    deleted   INTEGER NOT NULL DEFAULT 0,
    wall_ts   REAL NOT NULL,
    PRIMARY KEY (entity, entity_id)
);
"""


class SyncQueue:
    """
    离线操作队列 + 已应用状态。

    用独立的 sqlite 文件，**不塞进主库** —— 同步是可以整个丢掉重来的
    （大不了全量重传一次），而主库不能。混在一起会让"重置同步"
    变成一个动到用户资料的危险操作。
    """

    def __init__(self, path: Path, device_id: str | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 🔴 `check_same_thread=False` 只是**关掉了 sqlite3 的线程检查**，
        # 它不提供任何并发保护。FastAPI 的路由跑在线程池里，两个请求
        # 同时写这个连接会撞出 "database is locked" 或者交错的事务 ——
        # 而同步最不能出的就是"写了一半"。所以自己加一把锁把所有访问串起来
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.device_id = device_id or self._get_or_make_device_id()

    # ── 元数据 ──────────────────────────────────────────

    def _meta_get(self, k: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT v FROM sync_meta WHERE k = ?", (k,)).fetchone()
            return str(row["v"]) if row else default

    def _meta_set(self, k: str, v: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sync_meta (k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (k, v),
            )
            self._conn.commit()

    def _get_or_make_device_id(self) -> str:
        """
        本机的设备 id。**生成一次就永久不变** ——
        每次启动换一个的话，Lamport 的兜底比较会在两次会话之间给出
        不同的胜者，同一次冲突这次这边赢下次那边赢，数据来回翻。
        """
        did = self._meta_get("device_id")
        if not did:
            did = uuid.uuid4().hex[:12]
            self._meta_set("device_id", did)
        return did

    @property
    def lamport(self) -> int:
        try:
            return int(self._meta_get("lamport", "0"))
        except ValueError:
            return 0

    def _bump_lamport(self, seen: int = 0) -> int:
        """
        Lamport 递增：`max(本地, 见到的) + 1`。

        🔴 `max` 那一半是关键。只做 `本地 + 1` 的话，一台长期离线的设备
        计数远远落后，它回来之后产生的**新**操作会带着一个小得多的 lamport，
        于是被判定成"旧数据"而被静默丢弃 —— 用户离线写的东西全没了，一句提示都没有。
        """
        nxt = max(self.lamport, int(seen)) + 1
        self._meta_set("lamport", str(nxt))
        return nxt

    # ── 入队 ────────────────────────────────────────────

    def enqueue(
        self, entity: str, entity_id: str, kind: str, payload: dict[str, Any] | None = None
    ) -> Op:
        # 🔴 **整段必须在同一把锁里。** `_bump_lamport()` 是"读-改-写"，
        # 两个线程同时进来会拿到**同一个 lamport** —— 而 lamport 相同的两条
        # 本地操作会靠 device 兜底比较，可它们的 device 也一样，
        # 于是其中一条被判成重复推送而**静默丢弃**。用户写的东西没了，一句提示都没有。
        with self._lock:
            op = Op(
                id=uuid.uuid4().hex,
                entity=entity,
                entity_id=entity_id,
                kind=kind,
                payload=payload or {},
                device=self.device_id,
                lamport=self._bump_lamport(),
                wall_ts=time.time(),
            )
            self._insert_op(op)
            self._record_applied(op)
            self._conn.commit()
        return op

    def _insert_op(self, op: Op) -> None:
        self._conn.execute(
            "INSERT INTO sync_queue (id, entity, entity_id, kind, payload, device, lamport, wall_ts, sent)"
            " VALUES (?,?,?,?,?,?,?,?,0)",
            (op.id, op.entity, op.entity_id, op.kind,
             json.dumps(op.payload, ensure_ascii=False), op.device, op.lamport, op.wall_ts),
        )

    # 🔴 **本地操作也要登记进 `sync_applied`。**
        # 少了这一句，本地这条在冲突表里根本不存在 —— 于是对端推来的
        # **任何**并发操作都会无条件获胜（`row is None` 直接进 else 分支），
        # 兜底比较那套规则完全没机会生效。
    # 症状：你在本机改的东西，一同步就被手机上的旧版本覆盖，
    # 而两边都显示"同步成功"。这是整个冲突合并里最致命的一个洞。
    def _record_applied(self, op: Op) -> None:
        """把一条操作登记成"这个实体现在的状态"。本地和远端共用同一段逻辑。"""
        self._conn.execute(
            "INSERT INTO sync_applied (entity, entity_id, lamport, device, deleted, wall_ts)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(entity, entity_id) DO UPDATE SET"
            "   lamport = excluded.lamport, device = excluded.device,"
            "   deleted = excluded.deleted, wall_ts = excluded.wall_ts",
            (op.entity, op.entity_id, op.lamport, op.device,
             1 if op.kind == "delete" else 0, op.wall_ts),
        )

    def pending(self, limit: int = MAX_BATCH) -> list[Op]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sync_queue WHERE sent = 0 ORDER BY lamport, id LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_op(r) for r in rows]

    def mark_sent(self, ids: list[str]) -> int:
        """
        🔴 **只在对端确认收到之后才标。** 发出去就标的话，
        网络在半路断了这批操作就永远不会重发 —— 数据静默丢失，
        两端各自看起来都很正常。
        """
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE sync_queue SET sent = 1 WHERE id IN ({marks})", ids
            )
            self._conn.commit()
            return cur.rowcount

    def purge_sent(self, keep_recent: int = 500) -> int:
        """清掉已确认的历史，留最近若干条备查。"""
        cur = self._conn.execute(
            "DELETE FROM sync_queue WHERE sent = 1 AND id NOT IN ("
            "  SELECT id FROM sync_queue WHERE sent = 1 ORDER BY lamport DESC LIMIT ?"
            ")",
            (keep_recent,),
        )
        self._conn.commit()
        return cur.rowcount

    # ── 合并对端来的操作 ────────────────────────────────

    def merge(self, ops: list[Op]) -> dict[str, Any]:
        """
        合并对端推来的操作。返回**逐条**的结果，不只给一个总数。

        🔴 「应用了 3 条、忽略了 7 条」里那 7 条必须说清是为什么被忽略。
        只报总数的话，用户看到"同步完成"而他刚写的东西没出现，
        完全无从判断是丢了还是被覆盖了。
        """
        applied: list[str] = []
        skipped: list[dict[str, str]] = []
        max_seen = 0

        # 🔴 整个合并必须在**一把锁里一次做完**。中途放开锁的话，
        # 另一个线程可以在"查到本地更旧"和"写入新值"之间插一条更新进来，
        # 于是那条更新被这一批悄悄盖掉 —— 丢数据且完全不报错
        with self._lock:
            for op in sorted(ops, key=lambda o: o.order_key):
                max_seen = max(max_seen, op.lamport)
                row = self._conn.execute(
                    "SELECT lamport, device, deleted FROM sync_applied"
                    " WHERE entity = ? AND entity_id = ?",
                    (op.entity, op.entity_id),
                ).fetchone()

                if row is not None:
                    mine = (int(row["lamport"]), str(row["device"]))
                    if op.order_key <= mine:
                        # 并发时 `<=` 里的 `=` 很重要：完全相同的键说明是同一条操作
                        # 又推了一遍（重发/重试），跳过是对的，不是冲突
                        skipped.append({
                            "entityId": op.entity_id,
                            "why": "本地这条更新（或就是同一条重复推送）"
                            if op.order_key < mine else "重复推送，已经应用过了",
                        })
                        continue

                self._record_applied(op)
                applied.append(op.entity_id)

            self._conn.commit()
        # 🔴 把对端的 lamport 吸收进本地时钟。不吸收的话，本地下一条操作
        # 的 lamport 会比对端刚推来的还小，于是**自己的新改动被判成旧数据**。
        # 收到时只取 max **不自增**（自增是发送侧的事）—— 每收一批就 +1
        # 会让时钟被同步频率推着虚涨，两台同步频率不同的设备会系统性地
        # 一强一弱，频繁同步的那台在所有并发冲突里都赢
        if max_seen > self.lamport:
            self._meta_set("lamport", str(int(max_seen)))

        return {
            "applied": len(applied),
            "skipped": len(skipped),
            "skippedDetail": skipped[:50],
            "lamport": self.lamport,
            "note": "冲突按 Lamport 时钟判，**不看系统时间** —— "
            "两台设备时钟差几分钟是常态，按时间判会让走得快的那台永远赢",
        }

    def purge_tombstones(self, ttl_s: float = TOMBSTONE_TTL_S) -> int:
        cutoff = time.time() - ttl_s
        cur = self._conn.execute(
            "DELETE FROM sync_applied WHERE deleted = 1 AND wall_ts < ?", (cutoff,)
        )
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> dict[str, Any]:
        q = self._conn.execute(
            "SELECT COUNT(*) c, SUM(sent = 0) p FROM sync_queue"
        ).fetchone()
        a = self._conn.execute(
            "SELECT COUNT(*) c, SUM(deleted) d FROM sync_applied"
        ).fetchone()
        return {
            "deviceId": self.device_id,
            "lamport": self.lamport,
            "queued": int(q["c"] or 0),
            "pending": int(q["p"] or 0),
            "entities": int(a["c"] or 0),
            "tombstones": int(a["d"] or 0),
        }

    def close(self) -> None:
        self._conn.close()

    # ── 内部 ────────────────────────────────────────────

    @staticmethod
    def _row_to_op(r: sqlite3.Row) -> Op:
        try:
            payload = json.loads(str(r["payload"]))
        except json.JSONDecodeError:
            # 单条坏了不该让整批同步失败 —— 空 payload 至少能让删除类操作走完
            payload = {}
        return Op(
            id=str(r["id"]), entity=str(r["entity"]), entity_id=str(r["entity_id"]),
            kind=str(r["kind"]), payload=payload, device=str(r["device"]),
            lamport=int(r["lamport"]), wall_ts=float(r["wall_ts"]),
        )


def atomic_write(path: Path, data: bytes) -> None:
    """
    原子落盘。**同步状态尤其不能写一半** ——
    半个 lamport 计数会让设备在下次启动后产生一批被判成"旧数据"的操作。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
