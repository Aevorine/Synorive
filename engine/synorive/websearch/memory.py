"""
E2 长期记忆库 ＋ E4 简报差异复读
====================================================================
**E2 要治的病**：每次研究都是从零开始。同一个话题第三次挖的时候，
前两次读过什么、哪些站被判为不可信、哪个说法当时是有争议的 ——
全都不在了。用户只能凭印象说"我记得上次好像看过一篇……"。

记忆库存三样东西，**都是事实不是结论**：
  · `facts`   —— 逐字摘录 + 出处 + 第一次见到的时间
  · `sources` —— 这个站在哪些话题上出现过、当时的可信度评级
  · `topics`  —— 一个话题挖过几次、每次的简报指纹

🔴 **只存摘录，绝不存"我的总结"**。存总结等于把一次可能出错的提炼固化
成"记忆"，以后每次都在这个可能错的基础上继续 —— 错误会累积且再也
追不回源头。存原文摘录则永远可以回去核对。

**E4 差异复读**：同一个话题隔几天再挖一次，**只给「新增了什么」**。
对比的是**事实级**不是文本级 —— 两份简报字面几乎不会重样（引擎排序天天变），
按文本 diff 会显示"全变了"，那毫无用处。所以比的是：
新出现的 URL、新出现的数字、争议度变了没、有没有新的反驳材料。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: 建表语句。**独立于 `schema.sql`，由 `ensure_schema()` 按需建** ——
#: 记忆库是可选功能，没开的用户不该在库里多出三张空表
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_facts (
    id          TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    text        TEXT NOT NULL,          -- 逐字摘录，不是总结
    url         TEXT NOT NULL,
    title       TEXT,
    site        TEXT,
    trust_score REAL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    seen_count  INTEGER NOT NULL DEFAULT 1,
    fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_facts_topic
    ON memory_facts (topic, last_seen DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_facts_fp
    ON memory_facts (fingerprint);

CREATE TABLE IF NOT EXISTS memory_sources (
    url         TEXT PRIMARY KEY,
    site        TEXT,
    title       TEXT,
    tier        TEXT,
    trust_score REAL,
    topics_json TEXT NOT NULL DEFAULT '[]',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    hit_count   INTEGER NOT NULL DEFAULT 1,
    flags_json  TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_memory_sources_site ON memory_sources (site);

CREATE TABLE IF NOT EXISTS memory_topics (
    topic        TEXT PRIMARY KEY,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    run_count    INTEGER NOT NULL DEFAULT 1,
    last_digest  TEXT,                  -- 上一次简报的事实指纹集合（JSON 数组）
    controversy  INTEGER
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fp(*parts: Any) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "ignore"))
        h.update(b"\x1f")
    return h.hexdigest()[:20]


def _norm_topic(q: str) -> str:
    """
    话题归一化。**只做很轻的归一**（去空格、降小写、去标点）——
    做重了会把「向量数据库选型」和「向量数据库」并成一个话题，
    而那是用户心里的两件事。
    """
    return re.sub(r"[\s\W_]+", "", str(q or "").lower())[:80]


@dataclass
class Fact:
    """一条记住的事实。**永远是逐字摘录**。"""

    id: str = ""
    topic: str = ""
    text: str = ""
    url: str = ""
    title: str = ""
    site: str = ""
    trust_score: float = 0.5
    first_seen: str = ""
    last_seen: str = ""
    seen_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "topic": self.topic, "text": self.text,
            "url": self.url, "title": self.title, "site": self.site,
            "trustScore": self.trust_score, "firstSeen": self.first_seen,
            "lastSeen": self.last_seen, "seenCount": self.seen_count,
        }


class MemoryStore:
    """
    长期记忆的读写。只依赖一个有 `.connect()` 的 `Database`，
    理由同 `projects.ResearchStore` —— 这一块和本地条目库没有外键关系。
    """

    def __init__(self, db: Any) -> None:
        self.db = db
        self._ready = False

    def ensure_schema(self) -> None:
        """第一次用到时才建表。重复调用是安全的（全是 IF NOT EXISTS）。"""
        if self._ready:
            return
        self.db.connect().executescript(_SCHEMA)
        self._ready = True

    # ── 写入 ────────────────────────────────────────────────
    def remember(
        self,
        topic: str,
        briefing: dict[str, Any],
        *,
        clusters: list[dict[str, Any]] | None = None,
        controversy: int | None = None,
        max_facts: int = 40,
    ) -> dict[str, Any]:
        """
        把一次研究的产出记进记忆库。

        返回 `{new, repeated, sources}` —— **`repeated` 那个数很有用**：
        它告诉用户"这次挖出来的东西有多少是上次已经见过的"，
        那是判断"还值不值得继续挖"最直接的信号。
        """
        self.ensure_schema()
        t = _norm_topic(topic)
        now = _now()
        conn = self.db.connect()

        facts = _facts_from_briefing(briefing)[:max_facts]
        new_n = rep_n = 0
        digest: list[str] = []

        for f in facts:
            fp = _fp(t, f.text[:180])
            digest.append(fp)
            row = conn.execute(
                "SELECT id, seen_count FROM memory_facts WHERE fingerprint = ?", (fp,)
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE memory_facts SET last_seen = ?, seen_count = seen_count + 1 "
                    "WHERE fingerprint = ?", (now, fp),
                )
                rep_n += 1
                continue
            conn.execute(
                "INSERT INTO memory_facts "
                "(id, topic, text, url, title, site, trust_score, first_seen, "
                " last_seen, seen_count, fingerprint) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                (fp, t, f.text, f.url, f.title, f.site, f.trust_score, now, now, fp),
            )
            new_n += 1

        for c in (clusters or [])[:60]:
            best = c.get("best") or c
            url = str(best.get("url") or "")
            if not url:
                continue
            row = conn.execute(
                "SELECT topics_json, hit_count FROM memory_sources WHERE url = ?", (url,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO memory_sources "
                    "(url, site, title, tier, trust_score, topics_json, "
                    " first_seen, last_seen, hit_count, flags_json) "
                    "VALUES (?,?,?,?,?,?,?,?,1,?)",
                    (url, str(best.get("site") or ""), str(best.get("title") or ""),
                     str((c.get("trust") or {}).get("tier") or ""),
                     float((c.get("trust") or {}).get("score") or 0.5),
                     json.dumps([t], ensure_ascii=False), now, now,
                     json.dumps((c.get("farm") or {}).get("flags") or [], ensure_ascii=False)),
                )
            else:
                topics = set(json.loads(row["topics_json"] or "[]"))
                topics.add(t)
                conn.execute(
                    "UPDATE memory_sources SET last_seen = ?, hit_count = hit_count + 1, "
                    "topics_json = ? WHERE url = ?",
                    (now, json.dumps(sorted(topics), ensure_ascii=False), url),
                )

        row = conn.execute("SELECT run_count FROM memory_topics WHERE topic = ?", (t,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO memory_topics "
                "(topic, first_seen, last_seen, run_count, last_digest, controversy) "
                "VALUES (?,?,?,1,?,?)",
                (t, now, now, json.dumps(digest), controversy),
            )
        else:
            conn.execute(
                "UPDATE memory_topics SET last_seen = ?, run_count = run_count + 1, "
                "last_digest = ?, controversy = ? WHERE topic = ?",
                (now, json.dumps(digest), controversy, t),
            )

        return {
            "topic": t, "new": new_n, "repeated": rep_n,
            "note": (
                f"记住 {new_n} 条新事实，{rep_n} 条是之前见过的。"
                + ("重复占比很高，这个话题可能已经挖得差不多了"
                   if rep_n > new_n * 2 and rep_n > 5 else "")
            ),
        }

    # ── 读取 ────────────────────────────────────────────────
    def recall(self, topic: str, *, limit: int = 30) -> dict[str, Any]:
        """
        开始挖一个话题之前先问一句"我以前看过什么"。

        按 `seen_count` 排序而不是时间 —— **被反复见到的事实更可能是
        这个话题的骨干信息**，而最新看到的往往只是这次搜索的排序噪声。
        """
        self.ensure_schema()
        t = _norm_topic(topic)
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM memory_facts WHERE topic = ? "
            "ORDER BY seen_count DESC, last_seen DESC LIMIT ?", (t, limit),
        ).fetchall()
        meta = conn.execute(
            "SELECT * FROM memory_topics WHERE topic = ?", (t,)
        ).fetchone()
        return {
            "topic": t,
            "known": bool(meta),
            "runCount": int(meta["run_count"]) if meta else 0,
            "lastSeen": meta["last_seen"] if meta else "",
            "controversy": (meta["controversy"] if meta else None),
            "facts": [dict(r) for r in rows],
            "note": (
                f"这个话题以前挖过 {meta['run_count']} 次" if meta
                else "这个话题以前没挖过"
            ),
        }

    def site_history(self, site: str) -> dict[str, Any]:
        """这个站以前在哪些话题上出现过、当时评级多少、挂过什么旗。"""
        self.ensure_schema()
        rows = self.db.connect().execute(
            "SELECT * FROM memory_sources WHERE site = ? ORDER BY hit_count DESC LIMIT 50",
            (site,),
        ).fetchall()
        topics: set[str] = set()
        flags: set[str] = set()
        for r in rows:
            topics |= set(json.loads(r["topics_json"] or "[]"))
            flags |= set(json.loads(r["flags_json"] or "[]"))
        return {
            "site": site, "urlCount": len(rows),
            "topics": sorted(topics)[:20], "flags": sorted(flags),
            "note": f"这个站在 {len(topics)} 个话题里出现过" if rows else "没见过这个站",
        }

    def forget(self, topic: str) -> int:
        """
        删掉一个话题的全部记忆。**这是用户唯一能主动清理的入口，
        必须有** —— 一个只进不出的记忆库，用户迟早会不敢再用它。
        """
        self.ensure_schema()
        t = _norm_topic(topic)
        conn = self.db.connect()
        n = conn.execute("DELETE FROM memory_facts WHERE topic = ?", (t,)).rowcount
        conn.execute("DELETE FROM memory_topics WHERE topic = ?", (t,))
        return int(n or 0)

    def stats(self) -> dict[str, Any]:
        self.ensure_schema()
        conn = self.db.connect()
        return {
            "facts": int(conn.execute("SELECT COUNT(*) c FROM memory_facts").fetchone()["c"]),
            "sources": int(conn.execute("SELECT COUNT(*) c FROM memory_sources").fetchone()["c"]),
            "topics": int(conn.execute("SELECT COUNT(*) c FROM memory_topics").fetchone()["c"]),
        }


def _facts_from_briefing(briefing: dict[str, Any]) -> list[Fact]:
    """
    从简报里抽出逐字摘录。**只认已经带出处的那些字段** ——
    没有 url 的句子不记，因为记了以后就再也无法核对它从哪来。
    """
    out: list[Fact] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            text = str(node.get("text") or node.get("quote") or "").strip()
            url = str(node.get("url") or "").strip()
            if len(text) >= 12 and url.startswith("http"):
                key = text[:120]
                if key not in seen:
                    seen.add(key)
                    out.append(Fact(
                        text=text[:600], url=url,
                        title=str(node.get("title") or ""),
                        site=str(node.get("site") or ""),
                        trust_score=float(node.get("trustScore")
                                          or node.get("trust_score") or 0.5),
                    ))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(briefing)
    return out


# ────────────────────────────────────────────────────────────────
# E4 差异复读
# ────────────────────────────────────────────────────────────────
def diff_runs(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    max_items: int = 20,
) -> dict[str, Any]:
    """
    E4 —— 两次研究之间**变了什么**。

    比四样东西，都是事实级：
      ① 新出现的来源 URL      ② 消失了的来源（上次有这次没有）
      ③ 新出现的数字          ④ 争议度的变化

    🔴 **「消失了」不等于「被删了」**：搜索引擎的排序每天都在变，
    上次第 8 条这次掉到第 30 条就会"消失"。所以这一栏的措辞是
    「这次没再出现」而不是「已下架」，并且排在新增之后 ——
    它的信息量本来就更低。
    """
    def urls(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c in (payload.get("results") or payload.get("clusters") or []):
            best = c.get("best") or c
            u = str(best.get("url") or "")
            if u:
                out[u] = {"url": u, "title": best.get("title") or "",
                          "site": best.get("site") or ""}
        return out

    old_u, new_u = urls(old), urls(new)
    added = [v for k, v in new_u.items() if k not in old_u][:max_items]
    gone = [v for k, v in old_u.items() if k not in new_u][:max_items]

    # 数字：用 numbers.extract 抽，比字面 diff 有意义得多
    from .numbers import extract as _extract_numbers

    def nums(payload: dict[str, Any]) -> dict[str, str]:
        text = json.dumps(payload.get("briefing") or payload, ensure_ascii=False)
        return {f"{n.value}|{n.is_percent}": n.raw for n in _extract_numbers(text, limit=80)}

    old_n, new_n = nums(old), nums(new)
    new_numbers = [v for k, v in new_n.items() if k not in old_n][:max_items]

    def contro(payload: dict[str, Any]) -> int | None:
        v = (payload.get("verification") or {}).get("controversyAvg")
        return int(v) if isinstance(v, (int, float)) else None

    c_old, c_new = contro(old), contro(new)
    c_delta = (c_new - c_old) if (c_old is not None and c_new is not None) else None

    parts: list[str] = []
    if added:
        parts.append(f"新出现 {len(added)} 个来源")
    if new_numbers:
        parts.append(f"新出现 {len(new_numbers)} 个数字")
    if c_delta:
        parts.append(f"争议度从 {c_old} 变成 {c_new}")
    if not parts:
        parts.append("这次没有实质性的新东西")

    return {
        "added": added,
        "gone": gone,
        "newNumbers": new_numbers,
        "controversyBefore": c_old,
        "controversyAfter": c_new,
        "controversyDelta": c_delta,
        "summary": "；".join(parts),
        "note": (
            "「这次没再出现」的那几条**不代表内容被删了** —— "
            "搜索引擎排序每天都在变，上次排第 8 这次掉到第 30 就会落榜"
        ),
        "at": int(time.time()),
    }
