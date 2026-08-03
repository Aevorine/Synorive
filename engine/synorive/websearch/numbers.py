"""
D5 —— 数字与单位回原文逐个校对
====================================================================
简报里出现的每一个数字，都回到它挂的那条出处原文里找一遍。找不到就标红。

**为什么单独把数字拎出来做**：这是整条链路上**错得最多、又最没人查**的
一环。文字写错了读者一眼能看出别扭，数字写错了看起来完全正常 ——
「同比增长 23%」和「同比增长 32%」在版面上长得一模一样，
而其中一个是把原文的数字记反了。

**判据必须容得下同义写法，否则全是假警报**：
  `1,234` = `1234` = `1 234`
  `3.5万` = `35000` = `3.5 万`
  `23%` = `23 %` = `百分之二十三`
  `1.2 亿` = `120000000` = `1.2亿`
一个只做字符串包含判断的版本，第一天就会把 90% 的正确数字标成错的，
然后用户再也不看这个功能了 —— **假警报比不做更糟**。

🔴 **对不上 ≠ 错**。可能是原文用了别的表述、可能是引擎给的摘要被截断了。
所以结论只有三档：`ok`（原文里找到了）/ `unverified`（这条出处的正文
没拿到，没法查）/ `mismatch`（正文拿到了但里面没有这个数）。
**只有 mismatch 才标红**，`unverified` 单独一档灰色显示。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 中文数量单位 → 倍数。**万亿放在万和亿前面** —— 否则「万亿」会先被
#: 「万」匹配掉，1.5 万亿变成 1.5 万，差了八个数量级
_CN_UNITS: list[tuple[str, float]] = [
    ("万亿", 1e12), ("兆", 1e12),
    ("亿", 1e8), ("千万", 1e7), ("百万", 1e6),
    ("万", 1e4), ("千", 1e3), ("百", 1e2),
]

#: 英文数量单位
_EN_UNITS: list[tuple[str, float]] = [
    ("trillion", 1e12), ("billion", 1e9), ("million", 1e6),
    ("thousand", 1e3), ("k", 1e3), ("m", 1e6), ("bn", 1e9),
]

#: 抓数字：可带千分位、小数、正负号，后面可跟中英文单位和百分号
_NUM_RE = re.compile(
    r"(?<![\w.])"
    r"([+-]?\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?)"
    r"\s*"
    r"(万亿|兆|亿|千万|百万|万|千|百|%|％|"
    r"trillion|billion|million|thousand|bn|k|m)?",
    re.I,
)

#: 中文数字，用来把「百分之二十三」这种也认出来
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

#: 纯序号/年份/编号不参与校对。它们数量巨大且几乎不会被写错，
#: 全放进来只会把真正要看的那几条淹掉
_SKIP_RE = re.compile(r"^(19|20)\d{2}$")

#: 相对误差容忍度。原文写「约 3.5 万」简报写「35000」应该算对得上；
#: 但 1e-6 那种严格相等会把所有四舍五入过的数全判成错
_REL_TOL = 0.005


@dataclass
class NumberCheck:
    """一个数字的校对结果。"""

    raw: str = ""
    value: float | None = None
    unit: str = ""
    is_percent: bool = False
    status: str = "unverified"      # ok / mismatch / unverified
    source_url: str = ""
    context: str = ""
    near: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "value": self.value,
            "unit": self.unit,
            "isPercent": self.is_percent,
            "status": self.status,
            "sourceUrl": self.source_url,
            "context": self.context,
            "near": self.near,
            "note": self.note,
        }


def _cn_number(s: str) -> float | None:
    """把「二十三」「一百五十」这种转成数字。只处理万以下，够用了。"""
    s = s.strip()
    if not s or any(c not in "零〇一二两三四五六七八九十百千" for c in s):
        return None
    total, section, digit = 0, 0, 0
    for c in s:
        if c in _CN_DIGITS:
            digit = _CN_DIGITS[c]
        elif c == "十":
            section += (digit or 1) * 10
            digit = 0
        elif c == "百":
            section += (digit or 1) * 100
            digit = 0
        elif c == "千":
            section += (digit or 1) * 1000
            digit = 0
    total += section + digit
    return float(total) if total else None


def _unit_mult(unit: str) -> float:
    u = (unit or "").strip().lower()
    if not u:
        return 1.0
    for name, mult in _CN_UNITS:
        if u == name:
            return mult
    for name, mult in _EN_UNITS:
        if u == name:
            return mult
    return 1.0


def extract(text: str, *, limit: int = 40) -> list[NumberCheck]:
    """
    从一段文字里抽出所有值得校对的数字。

    抽出来的是**归一化后的数值**而不是原字符串 —— 后面比对时要用数值比，
    比字符串比能少掉一大半假警报。
    """
    out: list[NumberCheck] = []
    seen: set[tuple[float, str]] = set()
    s = str(text or "")

    for m in _NUM_RE.finditer(s):
        raw_num, unit = m.group(1), (m.group(2) or "")
        if _SKIP_RE.match(raw_num.replace(",", "").replace(" ", "")) and not unit:
            continue
        try:
            base = float(raw_num.replace(",", "").replace(" ", ""))
        except ValueError:
            continue
        is_pct = unit in ("%", "％")
        value = base if is_pct else base * _unit_mult(unit)
        key = (round(value, 6), "%" if is_pct else "")
        if key in seen:
            continue
        seen.add(key)
        start, end = max(0, m.start() - 28), min(len(s), m.end() + 28)
        out.append(NumberCheck(
            raw=m.group(0).strip(),
            value=value,
            unit=unit,
            is_percent=is_pct,
            context=s[start:end].replace("\n", " ").strip(),
        ))
        if len(out) >= limit:
            break

    # 「百分之二十三」这种中文写法单独扫一遍
    for m in re.finditer(r"百分之([零〇一二两三四五六七八九十百]{1,6})", s):
        v = _cn_number(m.group(1))
        if v is None:
            continue
        key = (round(v, 6), "%")
        if key in seen:
            continue
        seen.add(key)
        start, end = max(0, m.start() - 28), min(len(s), m.end() + 28)
        out.append(NumberCheck(
            raw=m.group(0), value=v, unit="%", is_percent=True,
            context=s[start:end].replace("\n", " ").strip(),
        ))
    return out[:limit]


def _present_in(value: float, is_pct: bool, source_text: str) -> tuple[bool, list[str]]:
    """
    这个数值在原文里出现过吗？返回 `(找到没, 附近出现的相似数字)`。

    「相似数字」是这个函数最有价值的产出：告诉用户「原文里写的是 32%
    不是 23%」，比只说一句"对不上"有用得多 —— 前者能直接改，
    后者还要自己回去翻。
    """
    cands = extract(source_text, limit=400)
    near: list[str] = []
    for c in cands:
        if c.value is None or c.is_percent != is_pct:
            continue
        if value == 0:
            if c.value == 0:
                return True, []
            continue
        rel = abs(c.value - value) / max(abs(value), 1e-9)
        if rel <= _REL_TOL:
            return True, []
        # 数量级相同的记下来，作为"你是不是想写这个"的候选
        if rel <= 0.5 and len(near) < 4:
            near.append(c.raw)
    return False, near


def verify_briefing(
    briefing: dict[str, Any],
    sources: dict[str, str],
    *,
    max_numbers: int = 30,
) -> dict[str, Any]:
    """
    D5 主入口 —— 校对一份简报里的所有数字。

    `briefing` 用 `research.build_briefing()` 的形状；
    `sources` 是 `{url: 正文全文}`，由 `read_url` 抓回来的那份。
    **拿不到正文的出处一律标 `unverified` 而不是跳过** ——
    跳过等于悄悄地把没查的说成查过的，那正是这个功能要防的事。
    """
    checks: list[NumberCheck] = []

    def _walk(node: Any, url_hint: str = "") -> None:
        if len(checks) >= max_numbers:
            return
        if isinstance(node, dict):
            url = str(node.get("url") or node.get("sourceUrl") or url_hint)
            for k, v in node.items():
                if k in ("text", "quote", "snippet", "summary", "line"):
                    for c in extract(str(v), limit=max_numbers - len(checks)):
                        c.source_url = url
                        checks.append(c)
                        if len(checks) >= max_numbers:
                            return
                else:
                    _walk(v, url)
        elif isinstance(node, list):
            for v in node:
                _walk(v, url_hint)

    _walk(briefing)

    for c in checks:
        text = sources.get(c.source_url) or ""
        if not text:
            c.status = "unverified"
            c.note = "这条出处的正文没抓到，数字没法核对"
            continue
        if c.value is None:
            c.status = "unverified"
            c.note = "数字没解析出来"
            continue
        ok, near = _present_in(c.value, c.is_percent, text)
        if ok:
            c.status = "ok"
        else:
            c.status = "mismatch"
            c.near = near
            c.note = (
                f"原文里没找到这个数；最接近的是 {'、'.join(near)}"
                if near else "原文里没找到这个数"
            )

    mismatched = [c for c in checks if c.status == "mismatch"]
    unverified = [c for c in checks if c.status == "unverified"]
    return {
        "checks": [c.to_dict() for c in checks],
        "total": len(checks),
        "ok": len(checks) - len(mismatched) - len(unverified),
        "mismatch": len(mismatched),
        "unverified": len(unverified),
        "verdict": (
            "有对不上的数字，建议逐条点开看" if mismatched
            else ("全部数字都在原文里找到了" if checks and not unverified
                  else "部分出处没抓到正文，这些数字没能核对")
        ),
        "note": "『对不上』只表示这个数没在正文里原样出现，"
                "可能是原文换了单位或表述，**不等于写错了**",
    }
