"""
D2 时间线冲突检测 ＋ D6 争议度指数
====================================================================
**D2 要治的是什么**：同一件事，A 站说发生在 3 月 5 日，B 站说 3 月 12 日，
C 站根本没写日期。现有的「转载爆发」判据只看**发布时间**扎不扎堆，
看不到**内容里说的事件时间**互相打架 —— 而后者才是谣言最典型的破绽：
复制链在传播过程中日期会漂，原始事件的时间反而是最先失真的那一项。

所以这里分开两条时间轴，**永远不混用**：
  · `published` = 这篇文章什么时候**发出来**的（引擎给的元数据）
  · `eventDate` = 文章正文里说这件事**什么时候发生**的（正则从正文抽）

一篇 2026 年的文章说「2019 年那次事故」，`published` 和 `eventDate`
差七年是完全正常的。**只比同类的**，跨着比会满屏假警报。

**D6 争议度指数**：把 `verify.py` 已经算出来的支持/反驳分布压成一个
0–100 的数，好让结果能按"争议大小"排序。

🔴 **争议度高 ≠ 假**。学界正在讨论的前沿问题争议度天然就高，
那恰恰是最值得读的。所以这个数只用于**排序和提示**，
绝不参与"要不要显示"的决策。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: 正文里的日期写法。顺序有讲究：**先匹配最具体的**，
#: 否则「2026年8月3日」会被只认年份的那条先吃掉，退化成 2026 年
_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(20\d{2})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?"), "ymd"),
    (re.compile(r"(20\d{2})\s*[年/\-.]\s*(\d{1,2})\s*月?(?!\d)"), "ym"),
    (re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})\b"), "dmy"),
    (re.compile(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
        r"(\d{1,2}),?\s+(20\d{2})\b", re.I), "mdy"),
    (re.compile(r"\b(20\d{2})\b"), "y"),
]

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

#: 事件时间与发布时间差超过这个天数就不当成"这篇在讲刚发生的事"，
#: 从冲突检测里排除。三年是拍的：再往上就把「回顾类文章」全卷进来了
_EVENT_WINDOW_DAYS = 1095


@dataclass
class EventDate:
    """从一篇正文里抽出来的一个事件日期。"""

    date: str = ""                  # ISO yyyy-mm-dd（精度不足时补 01）
    precision: str = "day"          # day / month / year
    raw: str = ""
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date, "precision": self.precision,
                "raw": self.raw, "context": self.context}


def _iso(y: int, m: int = 1, d: int = 1) -> str:
    try:
        return datetime(y, max(1, min(12, m)), max(1, min(28, d))).strftime("%Y-%m-%d")
    except ValueError:
        return f"{y:04d}-01-01"


def extract_dates(text: str, *, limit: int = 6) -> list[EventDate]:
    """
    从正文里抽事件日期。**只取前几个** —— 一篇长文里可能有几十个日期，
    但真正描述"这件事发生在什么时候"的几乎总在开头几段。
    往后抽只会把参考文献里的年份也卷进来。
    """
    s = str(text or "")[:4000]      # 只看前 4000 字，理由同上
    out: list[EventDate] = []
    seen: set[str] = set()
    for pat, kind in _DATE_PATTERNS:
        for m in pat.finditer(s):
            try:
                if kind == "ymd":
                    iso, prec = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3))), "day"
                elif kind == "ym":
                    iso, prec = _iso(int(m.group(1)), int(m.group(2))), "month"
                elif kind == "dmy":
                    iso, prec = _iso(int(m.group(3)), int(m.group(2)), int(m.group(1))), "day"
                elif kind == "mdy":
                    mon = _MONTHS.get(m.group(1)[:3].lower(), 1)
                    iso, prec = _iso(int(m.group(3)), mon, int(m.group(2))), "day"
                else:
                    iso, prec = _iso(int(m.group(1))), "year"
            except (ValueError, IndexError):
                continue
            if iso in seen:
                continue
            seen.add(iso)
            a, b = max(0, m.start() - 24), min(len(s), m.end() + 24)
            out.append(EventDate(date=iso, precision=prec, raw=m.group(0),
                                 context=s[a:b].replace("\n", " ").strip()))
            if len(out) >= limit:
                return out
    return out


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d.replace(tzinfo=None) if d.tzinfo else d
    except (ValueError, TypeError):
        return None


@dataclass
class TimelineConflict:
    """两个来源对同一件事给出的时间对不上。"""

    a_url: str = ""
    b_url: str = ""
    a_site: str = ""
    b_site: str = ""
    a_date: str = ""
    b_date: str = ""
    gap_days: int = 0
    a_context: str = ""
    b_context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "aUrl": self.a_url, "bUrl": self.b_url,
            "aSite": self.a_site, "bSite": self.b_site,
            "aDate": self.a_date, "bDate": self.b_date,
            "gapDays": self.gap_days,
            "aContext": self.a_context, "bContext": self.b_context,
        }


def detect_conflicts(
    entries: list[dict[str, Any]],
    texts: dict[str, str],
    *,
    min_gap_days: int = 2,
    max_conflicts: int = 12,
) -> dict[str, Any]:
    """
    D2 主入口 —— 找同一话题下互相打架的事件时间。

    `min_gap_days=2` 是刻意的：**差一天几乎总是时区问题**
    （UTC 与东八区跨日），把那个也报出来会让这个功能的第一印象
    就是"它老是在报没用的东西"。
    """
    dated: list[tuple[dict[str, Any], EventDate]] = []
    undated: list[dict[str, Any]] = []

    for e in entries:
        url = str(e.get("url") or "")
        body = texts.get(url) or f"{e.get('title', '')} {e.get('snippet', '')}"
        pub = _parse_iso(e.get("published"))
        picked: EventDate | None = None
        for d in extract_dates(body):
            dt = _parse_iso(d.date)
            if dt is None:
                continue
            # 只留和发布时间在同一个时间尺度上的 —— 见模块头注释
            if pub is not None and abs((pub - dt).days) > _EVENT_WINDOW_DAYS:
                continue
            picked = d
            break
        if picked is not None:
            dated.append((e, picked))
        else:
            undated.append(e)

    conflicts: list[TimelineConflict] = []
    for i in range(len(dated)):
        for j in range(i + 1, len(dated)):
            ea, da = dated[i]
            eb, db = dated[j]
            # 精度不同就按粗的那个比：月精度对上日精度，同月就不算冲突
            if "year" in (da.precision, db.precision):
                if da.date[:4] == db.date[:4]:
                    continue
            elif "month" in (da.precision, db.precision):
                if da.date[:7] == db.date[:7]:
                    continue
            ta, tb = _parse_iso(da.date), _parse_iso(db.date)
            if ta is None or tb is None:
                continue
            gap = abs((ta - tb).days)
            if gap < min_gap_days:
                continue
            sa = str(ea.get("site") or "")
            sb = str(eb.get("site") or "")
            if sa and sa == sb:
                continue        # 同一个站自己前后不一致是另一回事，不在这里报
            conflicts.append(TimelineConflict(
                a_url=str(ea.get("url") or ""), b_url=str(eb.get("url") or ""),
                a_site=sa, b_site=sb, a_date=da.date, b_date=db.date,
                gap_days=gap, a_context=da.context, b_context=db.context,
            ))
            if len(conflicts) >= max_conflicts:
                break
        if len(conflicts) >= max_conflicts:
            break

    conflicts.sort(key=lambda c: -c.gap_days)
    return {
        "conflicts": [c.to_dict() for c in conflicts],
        "datedCount": len(dated),
        "undatedCount": len(undated),
        "verdict": (
            f"{len(conflicts)} 组来源对事件时间说法不一致"
            if conflicts else "各来源给出的事件时间没有明显冲突"
        ),
        "note": "时间对不上**不代表哪一方在造假** —— 也可能是在说同一件事的"
                "不同阶段，或者其中一方引用了不准确的二手信息。这里只把差异摆出来",
    }


# ────────────────────────────────────────────────────────────────
# D6 争议度指数
# ────────────────────────────────────────────────────────────────
@dataclass
class Controversy:
    """一个说法的争议度。0 = 众口一词，100 = 完全对立。"""

    score: int = 0
    support: int = 0
    refute: int = 0
    neutral: int = 0
    independent_sites: int = 0
    level: str = "unknown"
    note: str = ""
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "support": self.support, "refute": self.refute,
            "neutral": self.neutral, "independentSites": self.independent_sites,
            "level": self.level, "note": self.note, "signals": self.signals,
        }


def score_controversy(verdict: dict[str, Any]) -> Controversy:
    """
    把 `verify.verify_claims()` 出来的一条判定压成 0–100。

    公式故意做得简单可解释（用户点开要能看懂为什么是 62 分）：

        平衡度 = 1 - |支持 - 反驳| / (支持 + 反驳)      ← 势均力敌 = 1
        规模系数 = min(1, (支持 + 反驳) / 6)            ← 证据太少就压低
        争议度 = 平衡度 × 规模系数 × 100

    **规模系数是关键的一环**：1 支持 1 反驳的平衡度是满分，
    但那只是两条零散的结果，报 100 分会严重误导。乘上规模系数以后，
    这种情况只有 33 分，符合直觉。
    """
    sup = len(verdict.get("support") or [])
    ref = len(verdict.get("refute") or [])
    neu = int(verdict.get("neutralCount") or verdict.get("neutral_count") or 0)
    c = Controversy(support=sup, refute=ref, neutral=neu)

    total = sup + ref
    if total == 0:
        c.level = "unknown"
        c.note = "没有找到明确表态的来源，无法评估争议度"
        return c

    balance = 1.0 - abs(sup - ref) / total
    scale = min(1.0, total / 6.0)
    c.score = int(round(balance * scale * 100))

    sites = set()
    for s in (verdict.get("support") or []) + (verdict.get("refute") or []):
        if isinstance(s, dict) and s.get("site"):
            sites.add(str(s["site"]))
    c.independent_sites = len(sites)

    if c.score >= 60:
        c.level = "high"
        c.note = "支持与反驳的来源数量接近，这是个**正在被争论**的说法"
        c.signals.append("势均力敌")
    elif c.score >= 30:
        c.level = "medium"
        c.note = "存在不同声音，但一边明显占多数"
    elif ref == 0:
        c.level = "low"
        c.note = "没找到反驳材料。**这不等于它是对的** —— 也可能只是没人反驳过"
        c.signals.append("无反驳")
    else:
        c.level = "low"
        c.note = "绝大多数来源口径一致"

    if c.independent_sites <= 2 and total >= 2:
        c.signals.append("表态的来源集中在极少数站点，样本不足")
    return c


def annotate_controversy(verification: dict[str, Any]) -> dict[str, Any]:
    """
    给 `verify.py` 的整份核查结果补上争议度，就地写回并返回。
    没有 `claims` 字段时安静返回原对象 —— 核查是可选功能，
    没开的时候这里不该报错。
    """
    claims = verification.get("claims")
    if not isinstance(claims, list):
        return verification
    scores: list[int] = []
    for cl in claims:
        if not isinstance(cl, dict):
            continue
        c = score_controversy(cl)
        cl["controversy"] = c.to_dict()
        if c.level != "unknown":
            scores.append(c.score)
    if scores:
        verification["controversyAvg"] = int(round(sum(scores) / len(scores)))
        verification["controversyMax"] = max(scores)
    return verification
