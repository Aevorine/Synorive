"""
每日选题简报（提案 36）
====================================================================
库越大越有一个毛病：**只有你想得起来的东西才找得到。** 存进去两年没再打开过的
资料，等于不存在。这个模块每天给一屏，主动把"你可能忘了的东西"端上来。

六块，各回答一个具体问题：
  1. 昨天/今天进来了什么     —— 别让新进的东西沉底
  2. 最近冒头的话题          —— 近 N 天出现次数明显高于之前的实体
  3. 卡住的                  —— 入库失败/分析没跑完的，不修就永远搜不到
  4. 沉睡的                  —— 存进来很久、一次都没打开过的
  5. 该复看的                —— 打开过很多次、但最近很久没碰
  6. 没头没尾的              —— 没标题或没正文的，搜索几乎命中不了

🔴 **每一块都要能点进去，不能只是数字。** 一个只报"有 12 条卡住了"的简报，
   用户看完还得自己去翻，等于没帮上忙 —— 所以每块都带上具体条目和 id。

🔴 **"冒头"必须跟自己的过去比，不能只看绝对次数。**
   只看次数的话，榜首永远是那几个从头到尾都很常见的词，每天都一样，
   看三天就没人再看了。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("synorive.briefing")

SECTION_LIMIT = 8


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _rows(conn: Any, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(r["id"]),
            "title": str(r["title"] or "") or str(r["locator"] or ""),
            "locator": str(r["locator"] or ""),
            "modality": str(r["modality"] or ""),
            "at": r["at"] if "at" in r.keys() else None,
        }
        for r in conn.execute(sql, args).fetchall()
    ]


def build(repo: Any, now: datetime | None = None, window_days: int = 7) -> dict[str, Any]:
    """
    出一份简报。`now` 可注入，测试里不用等真的过一天。

    🔴 时间一律用 UTC 的 ISO 串比对，因为库里存的就是这个格式。
       混用本地时间会让"昨天"整体偏移几个小时，症状是简报时准时不准。
    """
    conn = repo.db.connect()
    now = now or datetime.now(timezone.utc)
    recent = _iso(now - timedelta(days=1))
    win = _iso(now - timedelta(days=window_days))
    prev_win = _iso(now - timedelta(days=window_days * 2))

    fresh = _rows(
        conn,
        "SELECT id, title, locator, modality, created_at AS at FROM items "
        "WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
        (recent, SECTION_LIMIT),
    )
    fresh_total = int(
        conn.execute("SELECT COUNT(*) c FROM items WHERE created_at >= ?", (recent,)).fetchone()["c"]
    )

    stuck = _rows(
        conn,
        "SELECT id, title, locator, modality, updated_at AS at FROM items "
        "WHERE status IN ('failed','error') OR (error IS NOT NULL AND error <> '') "
        "ORDER BY updated_at DESC LIMIT ?",
        (SECTION_LIMIT,),
    )
    stuck_total = int(
        conn.execute(
            "SELECT COUNT(*) c FROM items WHERE status IN ('failed','error') "
            "OR (error IS NOT NULL AND error <> '')"
        ).fetchone()["c"]
    )

    # 沉睡：进来超过一个窗口期、从没打开过
    asleep = _rows(
        conn,
        "SELECT id, title, locator, modality, created_at AS at FROM items "
        "WHERE open_count = 0 AND created_at < ? "
        "ORDER BY RANDOM() LIMIT ?",  # 随机取，否则每天端上来的永远是同几条
        (win, SECTION_LIMIT),
    )
    asleep_total = int(
        conn.execute(
            "SELECT COUNT(*) c FROM items WHERE open_count = 0 AND created_at < ?", (win,)
        ).fetchone()["c"]
    )

    revisit = _rows(
        conn,
        "SELECT id, title, locator, modality, last_opened_at AS at FROM items "
        "WHERE open_count >= 3 AND (last_opened_at IS NULL OR last_opened_at < ?) "
        "ORDER BY open_count DESC LIMIT ?",
        (win, SECTION_LIMIT),
    )

    thin = _rows(
        conn,
        "SELECT id, title, locator, modality, created_at AS at FROM items "
        "WHERE (title IS NULL OR TRIM(title) = '') OR (snippet IS NULL OR TRIM(snippet) = '') "
        "ORDER BY created_at DESC LIMIT ?",
        (SECTION_LIMIT,),
    )
    thin_total = int(
        conn.execute(
            "SELECT COUNT(*) c FROM items WHERE (title IS NULL OR TRIM(title) = '') "
            "OR (snippet IS NULL OR TRIM(snippet) = '')"
        ).fetchone()["c"]
    )

    return {
        "generatedAt": _iso(now),
        "windowDays": window_days,
        "sections": [
            {
                "key": "fresh",
                "title": "刚进来的",
                "why": "新收进来的东西最容易看一眼就沉底",
                "total": fresh_total,
                "items": fresh,
            },
            {
                "key": "rising",
                "title": "最近冒头的话题",
                "why": f"近 {window_days} 天出现得比之前明显多的名字",
                "total": 0,
                "items": [],
                "entities": rising(repo, win, prev_win),
            },
            {
                "key": "stuck",
                "title": "卡住的",
                "why": "入库或分析没成功，不处理的话搜索永远搜不到它们",
                "total": stuck_total,
                "items": stuck,
            },
            {
                "key": "asleep",
                "title": "存了很久没打开过的",
                "why": "每天随机翻几条出来，否则它们等于不存在",
                "total": asleep_total,
                "items": asleep,
            },
            {
                "key": "revisit",
                "title": "以前常看、最近没碰的",
                "why": "打开过 3 次以上，说明当时重要",
                "total": len(revisit),
                "items": revisit,
            },
            {
                "key": "thin",
                "title": "没标题或没正文的",
                "why": "缺这两样的资料几乎不可能被搜到",
                "total": thin_total,
                "items": thin,
            },
        ],
    }


def rising(repo: Any, since: str, prev_since: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    近期 vs 上一个同长度周期，按增幅排。

    加 1 平滑：不加的话，一个之前 0 次、现在 1 次的词会算出无穷大的增幅，
    榜单会被只出现过一次的噪音塞满。
    """
    conn = repo.db.connect()
    rows = conn.execute(
        """
        SELECT e.id, e.kind, e.name,
               SUM(CASE WHEN COALESCE(i.content_time, i.created_at) >= ? THEN 1 ELSE 0 END) AS now_n,
               SUM(CASE WHEN COALESCE(i.content_time, i.created_at) >= ?
                        AND COALESCE(i.content_time, i.created_at) <  ? THEN 1 ELSE 0 END) AS prev_n
        FROM entity_mentions m
        JOIN entities e ON e.id = m.entity_id
        JOIN items i    ON i.id = m.item_id
        WHERE COALESCE(i.content_time, i.created_at) >= ?
        GROUP BY e.id
        HAVING now_n > 0
        """,
        (since, prev_since, since, prev_since),
    ).fetchall()

    scored = []
    for r in rows:
        now_n, prev_n = int(r["now_n"]), int(r["prev_n"])
        if now_n < 2:  # 只出现一次的不算趋势，只算噪音
            continue
        scored.append(
            {
                "id": str(r["id"]),
                "kind": str(r["kind"]),
                "name": str(r["name"]),
                "recent": now_n,
                "previous": prev_n,
                "lift": round(now_n / (prev_n + 1), 2),
            }
        )
    scored.sort(key=lambda x: (-float(x["lift"]), -int(x["recent"])))
    return scored[:limit]


def to_text(brief: dict[str, Any]) -> str:
    """给通知/剪贴板用的纯文本版。只列有内容的块 —— 空块占位纯属噪音。"""
    lines = [f"Synorive 简报 · {brief.get('generatedAt', '')}", ""]
    for s in brief.get("sections", []):
        items = s.get("items") or s.get("entities") or []
        if not items:
            continue
        total = int(s.get("total") or len(items))
        head = f"【{s['title']}】{total} 条" if total > len(items) else f"【{s['title']}】"
        lines.append(head)
        for x in items[:5]:
            lines.append(f"  · {x.get('title') or x.get('name') or ''}")
        lines.append("")
    if len(lines) <= 2:
        lines.append("今天没有需要处理的东西。")
    return "\n".join(lines).rstrip() + "\n"
