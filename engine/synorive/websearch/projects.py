"""
研究项目持久化 —— P4
====================================================================
**要治的病**：深挖一次要十几秒、发几十个请求、抓十几篇正文。
关掉窗口就全没了 —— 想接着挖，这个成本要从头再付一遍。
而研究这件事天然是断续的：今天挖一轮，看两篇，明天想起还有个方向没查。

所以这一层做的是：**把一次研究变成一个能关掉再打开的东西**。

三张表的分工（为什么不塞进一个大 JSON）：
  · `research_projects` —— "这个研究是关于什么的"。最高频的操作是
    「列出我的研究项目」，它必须只读几十字节，不能反序列化几 MB
  · `research_runs` —— 每一次搜索的完整响应，整份 JSON 存着。
    这份东西只会整取整存，拆成结构化表是白费力气
  · `research_sources` —— 项目累计见过的所有来源，**跨轮次去重**。
    单独一张表是因为它要支持"钉住这条""给这条加备注"这类操作，
    埋在 run 的 JSON 里就改不动了

**为什么来源表要 first_seen 而不是 last_seen**：判断一条资料是不是
「这轮新出现的」比「最近又见到一次」有用得多 —— 前者告诉你第二轮
真的挖出了新东西，后者只是重复。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


class ResearchStore:
    """
    研究项目的读写。**只依赖一个 `Database`**（有 `.connect()` 就行），
    不依赖 repository —— 这一块和本地条目库没有任何外键关系，
    硬绑在一起只会让两边都不好改。
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    # ── 项目 ────────────────────────────────────────────────
    def create_project(
        self,
        query: str,
        *,
        title: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pid = _new_id()
        now = _now()
        # 标题默认取查询词，但**截断而不是原样存**——
        # 用户可能粘进来一整段话当查询，那个东西当标题没法看
        t = (title or query or "未命名研究").strip()[:80]
        self.db.connect().execute(
            "INSERT INTO research_projects "
            "(id, title, query, created_at, updated_at, status, settings_json) "
            "VALUES (?,?,?,?,?,'open',?)",
            (pid, t, query, now, now, json.dumps(settings or {}, ensure_ascii=False)),
        )
        return self.get_project(pid) or {}

    def get_project(self, pid: str) -> dict[str, Any] | None:
        r = self.db.connect().execute(
            "SELECT * FROM research_projects WHERE id = ?", (pid,)
        ).fetchone()
        if r is None:
            return None
        d = _row(r)
        d["settings"] = _loads(d.pop("settings_json", None)) or {}
        d["runCount"] = self.db.connect().execute(
            "SELECT COUNT(*) c FROM research_runs WHERE project_id = ?", (pid,)
        ).fetchone()["c"]
        d["sourceCount"] = self.db.connect().execute(
            "SELECT COUNT(*) c FROM research_sources WHERE project_id = ?", (pid,)
        ).fetchone()["c"]
        return d

    def list_projects(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM research_runs r WHERE r.project_id = p.id) run_count, "
            "(SELECT COUNT(*) FROM research_sources s WHERE s.project_id = p.id) source_count "
            "FROM research_projects p "
        )
        args: list[Any] = []
        if status:
            sql += "WHERE p.status = ? "
            args.append(status)
        sql += "ORDER BY p.updated_at DESC LIMIT ?"
        args.append(max(1, limit))
        out: list[dict[str, Any]] = []
        for r in self.db.connect().execute(sql, args):
            d = _row(r)
            d["settings"] = _loads(d.pop("settings_json", None)) or {}
            d["runCount"] = d.pop("run_count", 0)
            d["sourceCount"] = d.pop("source_count", 0)
            out.append(d)
        return out

    def update_project(self, pid: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"title", "status", "notes", "query"}
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        conn = self.db.connect()
        if sets:
            clause = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(
                f"UPDATE research_projects SET {clause}, updated_at = ? WHERE id = ?",
                [*sets.values(), _now(), pid],
            )
        if "settings" in fields and isinstance(fields["settings"], dict):
            conn.execute(
                "UPDATE research_projects SET settings_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(fields["settings"], ensure_ascii=False), _now(), pid),
            )
        return self.get_project(pid)

    def delete_project(self, pid: str) -> bool:
        cur = self.db.connect().execute(
            "DELETE FROM research_projects WHERE id = ?", (pid,)
        )
        # runs 和 sources 靠外键 ON DELETE CASCADE 一起走 ——
        # 手工删三次的话，中间任何一步失败都会留下孤儿数据
        return cur.rowcount > 0

    # ── 运行记录 ────────────────────────────────────────────
    def add_run(
        self, pid: str, *, query: str, mode: str, payload: dict[str, Any]
    ) -> str:
        rid = _new_id()
        conn = self.db.connect()
        conn.execute(
            "INSERT INTO research_runs "
            "(id, project_id, query, mode, created_at, elapsed_ms, payload_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                rid, pid, query, mode, _now(),
                int(payload.get("elapsedMs") or 0),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        conn.execute(
            "UPDATE research_projects SET updated_at = ? WHERE id = ?", (_now(), pid)
        )
        self._absorb_sources(pid, payload)
        return rid

    def list_runs(self, pid: str, *, limit: int = 20, full: bool = False) -> list[dict[str, Any]]:
        """
        列出这个项目的历次运行。

        `full=False` 时**不返回 payload** —— 一次深挖的响应几百 KB，
        列表页要的只是"什么时候搜了什么"，把几十份完整响应一起塞回去
        会让这个接口比搜索本身还慢。
        """
        cols = "*" if full else "id, project_id, query, mode, created_at, elapsed_ms"
        out: list[dict[str, Any]] = []
        for r in self.db.connect().execute(
            f"SELECT {cols} FROM research_runs WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (pid, max(1, limit)),
        ):
            d = _row(r)
            if full:
                d["payload"] = _loads(d.pop("payload_json", None)) or {}
            out.append(d)
        return out

    def get_run(self, rid: str) -> dict[str, Any] | None:
        r = self.db.connect().execute(
            "SELECT * FROM research_runs WHERE id = ?", (rid,)
        ).fetchone()
        if r is None:
            return None
        d = _row(r)
        d["payload"] = _loads(d.pop("payload_json", None)) or {}
        return d

    # ── 来源 ────────────────────────────────────────────────
    def _absorb_sources(self, pid: str, payload: dict[str, Any]) -> None:
        """
        把这一轮的结果并进项目的来源表。**已存在的不覆盖** ——
        first_seen 要保住第一次见到它的时间，那才是"这轮是不是挖出新东西"
        的判据；覆盖掉之后每条都是"刚见到"，这个信号就废了。
        """
        rows = payload.get("results") or []
        if not rows:
            return
        now = _now()
        conn = self.db.connect()
        conn.executemany(
            "INSERT OR IGNORE INTO research_sources "
            "(project_id, url, title, site, tier, trust_score, first_seen) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (
                    pid,
                    str(c.get("url") or ""),
                    str(c.get("title") or "")[:300],
                    str(c.get("site") or ""),
                    str(((c.get("trust") or {}).get("tierLabel")) or ""),
                    float(((c.get("trust") or {}).get("score")) or 0.0),
                    now,
                )
                for c in rows
                if c.get("url")
            ],
        )

    def list_sources(
        self, pid: str, *, pinned_only: bool = False, limit: int = 500
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM research_sources WHERE project_id = ? "
        args: list[Any] = [pid]
        if pinned_only:
            sql += "AND pinned = 1 "
        sql += "ORDER BY pinned DESC, trust_score DESC, first_seen ASC LIMIT ?"
        args.append(max(1, limit))
        return [_row(r) for r in self.db.connect().execute(sql, args)]

    def pin_source(self, pid: str, url: str, *, pinned: bool = True,
                   note: str | None = None) -> bool:
        conn = self.db.connect()
        if note is not None:
            cur = conn.execute(
                "UPDATE research_sources SET pinned = ?, note = ? "
                "WHERE project_id = ? AND url = ?",
                (1 if pinned else 0, note, pid, url),
            )
        else:
            cur = conn.execute(
                "UPDATE research_sources SET pinned = ? WHERE project_id = ? AND url = ?",
                (1 if pinned else 0, pid, url),
            )
        return cur.rowcount > 0

    # ── 续做 ────────────────────────────────────────────────
    def resume_context(self, pid: str) -> dict[str, Any]:
        """
        续做一个项目时要的全部上下文。

        **不返回历次的完整结果**，只返回：项目本身 + 最近一次的简报 +
        钉住的来源 + 已经搜过哪些查询词。最后一项是关键 ——
        续做时最不该发生的事就是**把上次搜过的词再搜一遍**。
        """
        proj = self.get_project(pid)
        if proj is None:
            return {}
        runs = self.list_runs(pid, limit=1, full=True)
        last = runs[0]["payload"] if runs else {}
        asked: list[str] = []
        for r in self.db.connect().execute(
            "SELECT query, payload_json FROM research_runs WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT 20",
            (pid,),
        ):
            asked.append(str(r["query"]))
            for rd in (_loads(r["payload_json"]) or {}).get("rounds") or []:
                for q in rd.get("queries") or []:
                    t = str(q.get("text") or "")
                    if t:
                        asked.append(t)
        return {
            "project": proj,
            "lastBriefing": last.get("briefing"),
            "lastVerification": last.get("verification"),
            "pinnedSources": self.list_sources(pid, pinned_only=True),
            "askedQueries": list(dict.fromkeys(asked))[:40],
            "sourceCount": proj.get("sourceCount", 0),
        }


def _loads(s: Any) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        # 存进去的时候是合法 JSON，读出来坏了说明文件层面出了问题。
        # 让整个项目打不开是最糟的处理 —— 返回 None，其余字段照常可用
        return None
