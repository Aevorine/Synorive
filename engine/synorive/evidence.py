"""
证据链导出（提案 33）
====================================================================
你从库里摘了一段话放进报告。半年后有人问"这句话哪来的、是不是你改过的" ——
现在能给出的只有一个文件路径，而路径证明不了任何事：文件可能已经被改过，
也可能压根不是当初那一份。

这个模块出的是一份**能复核的清单**：每条来源的定位符、当初入库时的内容指纹、
以及**导出这一刻重新算的指纹**，两者一并写进去。

🔴 **重算指纹是这个功能的全部价值所在。**
   只把库里存的指纹抄一遍，那是自己证明自己 —— 库被改了就一起被改了。
   在导出这一刻重新读一遍磁盘上的文件、重新算一遍哈希，才能回答
   "从入库到现在，这份东西动过没有"。

🔴 **对不上的时候要显眼地写出来，而不是悄悄跳过。**
   "源文件已经变了"恰恰是这份清单最有价值的一行 —— 它告诉你这段引用不能再用了。
   一份只列出"全部正常"的证据清单是没有用的，因为它无法出错，也就无法证明什么。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("synorive.evidence")

#: 超过这个大小就只哈希首尾各 4 MB + 文件长度。
#: 对一个 8 GB 的视频完整哈希要几分钟，用户点了"导出"之后界面在那儿干等 ——
#: 而首尾抽样已经足以发现"这文件被换过/被重新编码过"这类实际会发生的改动。
FULL_HASH_LIMIT = 64 * 1024 * 1024
SAMPLE_SPAN = 4 * 1024 * 1024

#: 入库端 `ingest.pipeline.file_fingerprint` 的抽样长度。**必须和它一样**，
#: 见下面 `ingest_fingerprint` 的说明。
INGEST_SAMPLE = 1 << 20

#: 状态取值。界面按这个上色，别改字面量。
OK = "unchanged"
CHANGED = "changed"
MISSING = "missing"
UNVERIFIABLE = "unverifiable"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_digest(path: Path) -> tuple[str, bool]:
    """
    返回 (十六进制摘要, 是不是整份文件都算了)。

    抽样模式会把文件长度也拌进去 —— 只哈希首尾的话，中间被替换掉一段是发现不了的；
    加上长度至少能挡住"整段替换成不等长内容"这一类。
    """
    h = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as f:
        if size <= FULL_HASH_LIMIT:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
            return h.hexdigest(), True
        h.update(str(size).encode())
        h.update(f.read(SAMPLE_SPAN))
        f.seek(max(0, size - SAMPLE_SPAN))
        h.update(f.read(SAMPLE_SPAN))
    return h.hexdigest(), False


def ingest_fingerprint(path: Path) -> str:
    """
    按**入库当时那套算法**重算指纹，用来和 `items.fingerprint` 比对。

    算法逐字节抄自 `ingest.pipeline.file_fingerprint`：
    `sha256(文件长度 + 头 1MB + 尾 1MB)`，取十六进制的前 32 个字符。

    🔴 **比对必须用同一套算法，这不是可以"差不多"的地方。**
       原来这里拿 `file_digest()`（整份文件的 SHA-256，64 字符）去和入库存的
       抽样指纹（32 字符）比前缀，两边算的根本不是一个东西，**永远对不上** ——
       于是每一条来源都被判成「已改动」，而接口照样 200、清单照样生成、
       Markdown 照样导出。一份把所有来源都标红的清单和一份全绿的一样没用，
       它还会让用户去重新核对根本没动过的文件。
       `file_digest()` 保留下来只作为"导出这一刻的强摘要"记录在案，不参与判定。
    """
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(INGEST_SAMPLE))
        if size > INGEST_SAMPLE * 2:
            f.seek(-INGEST_SAMPLE, 2)
            h.update(f.read(INGEST_SAMPLE))
    return h.hexdigest()[:32]


def _verify_one(row: Any) -> dict[str, Any]:
    """核对一条来源。任何一步失败都返回一个说得清原因的结果，不抛异常。"""
    locator = str(row["locator"] or "")
    stored = str(row["fingerprint"] or "")
    out: dict[str, Any] = {
        "itemId": str(row["id"]),
        "title": str(row["title"] or "") or locator,
        "locator": locator,
        "modality": str(row["modality"] or ""),
        "contentTime": row["content_time"],
        "ingestedAt": row["created_at"],
        "storedFingerprint": stored,
    }

    # 链接类没有本地文件可以重算 —— 说清楚它没法核对，而不是假装通过
    if not locator or "://" in locator[:8]:
        out["status"] = UNVERIFIABLE
        out["note"] = "网页/链接类来源，本地没有可重算的文件"
        return out

    p = Path(locator)
    try:
        if not p.is_file():
            out["status"] = MISSING
            out["note"] = "源文件已经不在这个位置了，这段引用无法再复核"
            return out
        digest, full = file_digest(p)
        recomputed = ingest_fingerprint(p)
    except OSError as e:
        out["status"] = UNVERIFIABLE
        out["note"] = f"读不了源文件：{e.__class__.__name__}"
        return out

    out["recheckedFingerprint"] = recomputed
    out["strongDigest"] = digest
    out["fullFileHashed"] = full
    out["sizeBytes"] = p.stat().st_size
    # 两边都是入库那套算法算出来的 32 位十六进制，直接整串比
    if stored and recomputed == stored:
        out["status"] = OK
    elif not stored:
        out["status"] = UNVERIFIABLE
        out["note"] = "入库时没有留下指纹，没有可比对的基准"
    else:
        out["status"] = CHANGED
        out["note"] = "源文件的内容和入库时不一样了，引用前请重新核对"
    return out


def build_chain(repo: Any, item_ids: list[str], note: str = "") -> dict[str, Any]:
    """
    给一组资料出一份证据清单。

    item_ids 里查不到的 id 也会**列出来**并标成 missing ——
    静默丢掉的话，导出的清单会比你实际引用的少几条，而你不会发现。
    """
    ids = [i for i in dict.fromkeys(item_ids) if i]
    if not ids:
        return {"generatedAt": _now(), "note": note, "sources": [], "summary": _summary([])}

    conn = repo.db.connect()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, title, locator, modality, fingerprint, content_time, created_at "
        f"FROM items WHERE id IN ({marks})",
        tuple(ids),
    ).fetchall()
    by_id = {str(r["id"]): r for r in rows}

    sources: list[dict[str, Any]] = []
    for iid in ids:
        row = by_id.get(iid)
        if row is None:
            sources.append(
                {
                    "itemId": iid,
                    "title": "(已从库中删除)",
                    "locator": "",
                    "status": MISSING,
                    "note": "这条资料已经不在库里了，无法复核",
                }
            )
            continue
        sources.append(_verify_one(row))

    return {
        "generatedAt": _now(),
        "note": note,
        "sources": sources,
        "summary": _summary(sources),
    }


def _summary(sources: list[dict[str, Any]]) -> dict[str, int]:
    s = {OK: 0, CHANGED: 0, MISSING: 0, UNVERIFIABLE: 0}
    for x in sources:
        st = str(x.get("status", UNVERIFIABLE))
        s[st] = s.get(st, 0) + 1
    s["total"] = len(sources)
    return s


_LABEL = {
    OK: "未改动",
    CHANGED: "已改动",
    MISSING: "已丢失",
    UNVERIFIABLE: "无法核对",
}


def to_markdown(chain: dict[str, Any]) -> str:
    """
    转成一份可以直接贴进报告附录的 Markdown。

    🔴 **结论行放最前面。** 一份 40 条的表格，没人会逐行看；
       "40 条里 3 条源文件已改动"这句话才是读者真正要的。
    """
    s = chain.get("summary", {})
    total = int(s.get("total", 0))
    bad = int(s.get(CHANGED, 0)) + int(s.get(MISSING, 0))
    lines = [
        "# 来源核对清单",
        "",
        f"- 生成时间：{chain.get('generatedAt', '')}",
        f"- 共 {total} 条来源，其中 **{bad} 条需要注意**"
        f"（已改动 {s.get(CHANGED, 0)}、已丢失 {s.get(MISSING, 0)}、"
        f"无法核对 {s.get(UNVERIFIABLE, 0)}）",
    ]
    if chain.get("note"):
        lines.append(f"- 备注：{chain['note']}")
    lines += [
        "",
        "核对方式：读取每份来源文件、重新计算 SHA-256，与入库当时留下的指纹比对。",
        "",
        "| # | 状态 | 标题 | 位置 | 入库时间 |",
        "| --: | --- | --- | --- | --- |",
    ]
    for i, x in enumerate(chain.get("sources", []), start=1):
        label = _LABEL.get(str(x.get("status")), "?")
        title = str(x.get("title", "")).replace("|", "\\|")
        loc = str(x.get("locator", "")).replace("|", "\\|")
        lines.append(f"| {i} | {label} | {title} | `{loc}` | {x.get('ingestedAt', '') or ''} |")

    notes = [x for x in chain.get("sources", []) if x.get("note")]
    if notes:
        lines += ["", "## 需要注意的几条", ""]
        for x in notes:
            lines.append(f"- **{x.get('title', '')}** —— {x['note']}")
    return "\n".join(lines) + "\n"
