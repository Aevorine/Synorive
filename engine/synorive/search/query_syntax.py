"""
搜索语法 D10 —— 把查询串里的筛选指令拆出来
====================================================================
支持（大小写不敏感，中英文冒号都认）：

    type:pdf,docx        只搜这些类型（扩展名 或 modality 名）
    date:>2026-01        这个时间之后
    date:<2026-08-01     这个时间之前
    date:2026-01..2026-03  区间
    date:今天 / 本周 / 本月 / 今年 / 最近7天
    size:>10mb           大于这个体积
    size:<500kb          小于这个体积
    in:D:\\项目          只在这些目录里搜
    tag:重要             带这些标签
    src:link             来源（file/link/clipboard/chat-export/mail/mobile/api）
    section:方法         只搜论文的这些章节（L3-plus，见下）
    -草稿                排除含这个词的
    "精确短语"           整段精确匹配

设计上刻意做成**宽容解析**：
写错的指令（`date:去年夏天`）不报错、不吞掉整条查询，而是当普通查询词处理。
搜索框是高频输入位置，一个语法错误就返回"语法错误"是很糟的体验 ——
用户会觉得"这破搜索连字都不让我随便打"。

## L3-plus `section:` —— 只搜论文的某几个章节

在 `chunks.section` 上过滤（分节信息在 L3 就已经存进去了，
之前只用来做排序浮现和结果标注，没有显式过滤入口）。

🔴 **按子串匹配，不按精确相等。** 真实论文的章节标题长这样：
`3.2 Experimental Method`、`4. Results and Discussion`、`材料与方法`。
要求精确相等的话，`section:method` 一条都匹配不到 ——
而那种失败是**静默**的：返回空结果，看起来就像"这个库里没有相关内容"。

🔴 **认不出来的词当原样子串用，不报错。** `section:第三章` 匹配不上任何
预设别名，但用户很可能真的就想找标题里带"第三章"的块。宽容优先。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

#: `key:value` —— 冒号支持中文全角，值里允许引号包起来的空格
_DIRECTIVE = re.compile(
    r"""(?<![\w一-鿿])          # 🔴 指令必须**独立成词**，见下
        (?P<key>type|date|size|in|tag|src|section|sec|类型|时间|大小|目录|标签|来源|章节)
        [:：]
        (?P<value>"[^"]*"|'[^']*'|[^\s]+)""",
    re.I | re.X,
)
# 🔴 那个逆向断言不是装饰。少了它，`in` / `sec` 这种短 key 会在**单词中间**命中：
#    搜 `domain:example.com` → 从第 4 个字符起匹配到 `in:example.com`，
#    于是这条查询被悄悄变成"只在 example.com 这个目录里搜"，
#    剩下 `doma` 当关键词。**不报错、不提示，就是搜不到东西。**
#    同类的还有 `margin:0`、`begin:x`、`spec:v2`（`sec` 差一点就中）。

_KEY_ALIAS = {
    "类型": "type",
    "时间": "date",
    "大小": "size",
    "目录": "in",
    "标签": "tag",
    "来源": "src",
    "章节": "section",
    "sec": "section",
}

#: L3-plus：用户敲的章节词 → 一组**子串**匹配模式。
#: 中英文都收，因为同一个库里中英文论文都有，用户不该被迫记住"这篇是英文的"。
#:
#: 🔴 值是子串不是完整标题：真实论文的标题是 `3.2 Experimental Method`、
#: `4. Results and Discussion`、`材料与方法`，精确匹配一条都命中不了。
_SECTION_ALIAS: dict[str, tuple[str, ...]] = {
    "abstract": ("abstract", "摘要"),
    "摘要": ("abstract", "摘要"),
    "intro": ("introduction", "引言", "前言"),
    "introduction": ("introduction", "引言", "前言"),
    "引言": ("introduction", "引言", "前言"),
    "前言": ("introduction", "引言", "前言"),
    "related": ("related work", "related studies", "相关工作", "文献综述", "研究现状"),
    "相关工作": ("related work", "related studies", "相关工作", "文献综述", "研究现状"),
    "background": ("background", "背景"),
    "背景": ("background", "背景"),
    "method": ("method", "approach", "materials and", "方法", "材料与方法", "实验设计"),
    "methods": ("method", "approach", "materials and", "方法", "材料与方法", "实验设计"),
    "方法": ("method", "approach", "materials and", "方法", "材料与方法", "实验设计"),
    "result": ("result", "finding", "结果", "实验结果"),
    "results": ("result", "finding", "结果", "实验结果"),
    "结果": ("result", "finding", "结果", "实验结果"),
    "experiment": ("experiment", "evaluation", "实验", "评估"),
    "实验": ("experiment", "evaluation", "实验", "评估"),
    "discussion": ("discussion", "讨论", "分析"),
    "讨论": ("discussion", "讨论", "分析"),
    "conclusion": ("conclusion", "结论", "总结"),
    "结论": ("conclusion", "结论", "总结"),
    "limitation": ("limitation", "threats to validity", "局限", "不足"),
    "局限": ("limitation", "threats to validity", "局限", "不足"),
    "reference": ("reference", "bibliograph", "参考文献", "引用文献"),
    "参考文献": ("reference", "bibliograph", "参考文献", "引用文献"),
    "appendix": ("appendix", "supplement", "附录", "补充材料"),
    "附录": ("appendix", "supplement", "附录", "补充材料"),
}

#: 扩展名 → modality。用户敲 type:pdf 想要的是"PDF 文件"，
#: 而库里 modality 存的是 text —— 所以扩展名要单独走 locator 过滤。
_EXT_GROUPS: dict[str, tuple[str, ...]] = {
    "doc": (".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt"),
    "文档": (".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt"),
    "sheet": (".xlsx", ".xls", ".csv", ".tsv"),
    "表格": (".xlsx", ".xls", ".csv", ".tsv"),
    "slide": (".pptx", ".ppt"),
    "幻灯片": (".pptx", ".ppt"),
    "code": (
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".c", ".cpp", ".h",
        ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".sql", ".vue",
    ),
    "代码": (
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".c", ".cpp", ".h",
        ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".sql", ".vue",
    ),
}

_MODALITY_ALIAS = {
    "image": "image", "img": "image", "图": "image", "图片": "image",
    "video": "video", "视频": "video",
    "audio": "audio", "音频": "audio",
    "link": "link", "url": "link", "链接": "link", "网页": "link",
    "message": "message", "消息": "message", "聊天": "message",
    "text": "text", "文本": "text",
}

_SOURCE_ALIAS = {
    "file": "file", "文件": "file", "本机": "file",
    "link": "link", "网页": "link",
    "clipboard": "clipboard", "剪贴板": "clipboard",
    "chat": "chat-export", "chat-export": "chat-export", "聊天": "chat-export",
    "mail": "mail", "邮件": "mail",
    "mobile": "mobile", "手机": "mobile",
    "api": "api",
}

_SIZE_UNITS = {"b": 1, "k": 1024, "kb": 1024, "m": 1024**2, "mb": 1024**2, "g": 1024**3, "gb": 1024**3}


@dataclass
class ParsedQuery:
    """拆完之后：干净的查询词 + 结构化筛选。"""

    text: str
    filters: dict[str, Any] = field(default_factory=dict)
    #: 没看懂的指令，原样退回查询词里的同时记下来，界面可以提示一下
    unknown: list[str] = field(default_factory=list)

    @property
    def has_filters(self) -> bool:
        return bool(self.filters)


def parse_query(raw: str) -> ParsedQuery:
    if not raw or not raw.strip():
        return ParsedQuery(text="")

    filters: dict[str, Any] = {}
    unknown: list[str] = []
    consumed: list[tuple[int, int]] = []

    for m in _DIRECTIVE.finditer(raw):
        key = _KEY_ALIAS.get(m.group("key").lower(), m.group("key").lower())
        value = m.group("value").strip("\"'")
        if not value:
            continue

        ok = _apply(key, value, filters)
        if ok:
            consumed.append(m.span())
        else:
            unknown.append(m.group(0))
            # 解析不了就不吃掉它，让它继续当普通查询词 —— 宽容优先

    # 把已消费的片段从查询串里挖掉（从后往前删，避免下标偏移）
    text = raw
    for start, end in sorted(consumed, reverse=True):
        text = text[:start] + " " + text[end:]

    return ParsedQuery(text=re.sub(r"\s+", " ", text).strip(), filters=filters, unknown=unknown)


def _apply(key: str, value: str, filters: dict[str, Any]) -> bool:
    if key == "type":
        return _apply_type(value, filters)
    if key == "date":
        return _apply_date(value, filters)
    if key == "size":
        return _apply_size(value, filters)
    if key == "in":
        filters.setdefault("scopes", []).append(value)
        return True
    if key == "tag":
        filters.setdefault("tags", []).extend(v for v in value.split(",") if v)
        return True
    if key == "src":
        vals = [_SOURCE_ALIAS.get(v.lower()) for v in value.split(",")]
        good = [v for v in vals if v]
        if not good:
            return False
        filters.setdefault("sources", []).extend(good)
        return True
    if key == "section":
        return _apply_section(value, filters)
    return False


def _apply_section(value: str, filters: dict[str, Any]) -> bool:
    """
    L3-plus。`section:方法,结果` → 一堆子串模式，OR 关系。

    🔴 **认不出来的词也照收**（当原样子串用）。`section:第三章` 匹配不上
    任何预设别名，但用户很可能就想找标题里带"第三章"的块 ——
    这时候返回 False 会让整个指令退化成普通查询词，
    表现是"我明明写了 section: 它却当成关键词去搜了"，更让人困惑。
    """
    pats: list[str] = []
    for part in value.split(","):
        p = part.strip().lower()
        if not p:
            continue
        pats.extend(_SECTION_ALIAS.get(p, (p,)))
    if not pats:
        return False
    # 去重但保序 —— 顺序影响 describe() 里显示的第一个词，那个词是用户敲的
    seen: set[str] = set()
    uniq = [x for x in pats if not (x in seen or seen.add(x))]
    filters.setdefault("sections", []).extend(uniq)
    return True


def _apply_type(value: str, filters: dict[str, Any]) -> bool:
    hit = False
    for part in value.split(","):
        p = part.strip().lower().lstrip(".")
        if not p:
            continue
        if p in _MODALITY_ALIAS:
            filters.setdefault("modalities", []).append(_MODALITY_ALIAS[p])
            hit = True
        elif p in _EXT_GROUPS:
            filters.setdefault("extensions", []).extend(_EXT_GROUPS[p])
            hit = True
        else:
            # 当成单个扩展名
            filters.setdefault("extensions", []).append(f".{p}")
            hit = True
    return hit


def _apply_date(value: str, filters: dict[str, Any]) -> bool:
    v = value.strip()
    now = datetime.now(UTC)

    # 相对时间词
    rel = {
        "今天": timedelta(days=1), "today": timedelta(days=1),
        "昨天": timedelta(days=2), "yesterday": timedelta(days=2),
        "本周": timedelta(days=7), "这周": timedelta(days=7), "week": timedelta(days=7),
        "本月": timedelta(days=31), "这个月": timedelta(days=31), "month": timedelta(days=31),
        "今年": timedelta(days=366), "year": timedelta(days=366),
    }
    if v.lower() in rel:
        filters["timeFrom"] = (now - rel[v.lower()]).isoformat(timespec="seconds")
        return True

    m = re.fullmatch(r"最近(\d+)(天|周|月|年)", v)
    if m:
        n = int(m.group(1))
        mult = {"天": 1, "周": 7, "月": 31, "年": 366}[m.group(2)]
        filters["timeFrom"] = (now - timedelta(days=n * mult)).isoformat(timespec="seconds")
        return True

    # 区间 2026-01..2026-03
    if ".." in v:
        a, b = v.split("..", 1)
        fa, fb = _to_iso(a, floor=True), _to_iso(b, floor=False)
        if fa:
            filters["timeFrom"] = fa
        if fb:
            filters["timeTo"] = fb
        return bool(fa or fb)

    # >日期 / <日期 / >=、<=
    m = re.fullmatch(r"(>=|<=|>|<)\s*(.+)", v)
    if m:
        op, d = m.group(1), m.group(2)
        iso = _to_iso(d, floor=op.startswith(">"))
        if not iso:
            return False
        filters["timeFrom" if op.startswith(">") else "timeTo"] = iso
        return True

    # 光一个日期 = 那一天整天
    fa = _to_iso(v, floor=True)
    fb = _to_iso(v, floor=False)
    if fa and fb:
        filters["timeFrom"], filters["timeTo"] = fa, fb
        return True
    return False


def _to_iso(s: str, *, floor: bool) -> str | None:
    """
    '2026'、'2026-08'、'2026-08-02'、'2026/8/2' 都认。

    floor=True 取该粒度的开头（2026 → 2026-01-01T00:00:00），
    floor=False 取结尾（2026 → 2026-12-31T23:59:59）。
    不区分的话 `date:<2026` 会把 2026 全年都排除掉，用户完全想不到。
    """
    s = s.strip().replace("/", "-").replace(".", "-")
    parts = [p for p in s.split("-") if p]
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if not nums or not (1900 <= nums[0] <= 2200):
        return None

    y = nums[0]
    if len(nums) == 1:
        dt = datetime(y, 1, 1, tzinfo=UTC) if floor else datetime(y, 12, 31, 23, 59, 59, tzinfo=UTC)
    elif len(nums) == 2:
        mo = min(max(nums[1], 1), 12)
        if floor:
            dt = datetime(y, mo, 1, tzinfo=UTC)
        else:
            nxt = datetime(y + (mo == 12), (mo % 12) + 1, 1, tzinfo=UTC)
            dt = nxt - timedelta(seconds=1)
    else:
        mo = min(max(nums[1], 1), 12)
        d = min(max(nums[2], 1), 28 if mo == 2 else 31)
        try:
            base = datetime(y, mo, d, tzinfo=UTC)
        except ValueError:
            return None
        dt = base if floor else base + timedelta(hours=23, minutes=59, seconds=59)
    return dt.isoformat(timespec="seconds")


def _apply_size(value: str, filters: dict[str, Any]) -> bool:
    m = re.fullmatch(r"(>=|<=|>|<)?\s*([\d.]+)\s*([kmgb]b?)?", value.strip(), re.I)
    if not m:
        return False
    op = m.group(1) or ">"
    try:
        num = float(m.group(2))
    except ValueError:
        return False
    unit = (m.group(3) or "b").lower()
    n = int(num * _SIZE_UNITS.get(unit, 1))
    filters["sizeMinBytes" if op.startswith(">") else "sizeMaxBytes"] = n
    return True


def describe(parsed: ParsedQuery) -> list[str]:
    """把解析结果说成人话，界面上显示成一排可点掉的小标签。"""
    out: list[str] = []
    f = parsed.filters
    if f.get("modalities"):
        out.append("类型：" + "、".join(f["modalities"]))
    if f.get("extensions"):
        exts = f["extensions"]
        shown = "、".join(e.lstrip(".") for e in exts[:4])
        out.append(f"扩展名：{shown}" + (f" 等 {len(exts)} 种" if len(exts) > 4 else ""))
    if f.get("sources"):
        out.append("来源：" + "、".join(f["sources"]))
    if f.get("tags"):
        out.append("标签：" + "、".join(f["tags"]))
    if f.get("sections"):
        # 只显示第一个（别名表里排头的是规范名），别把 6 条同义写法全糊上去 ——
        # 那是实现细节，用户敲的是「方法」不是「method/approach/材料与方法/…」
        secs = f["sections"]
        tail = f"（连同 {len(secs) - 1} 种同义写法一起匹配）" if len(secs) > 1 else ""
        out.append(f"章节：{secs[0]}{tail}")
    if f.get("scopes"):
        out.append("目录：" + "、".join(f["scopes"]))
    if f.get("timeFrom") or f.get("timeTo"):
        a = (f.get("timeFrom") or "")[:10]
        b = (f.get("timeTo") or "")[:10]
        out.append(f"时间：{a or '不限'} ~ {b or '至今'}")
    if f.get("sizeMinBytes"):
        out.append(f"大于 {_fmt_size(f['sizeMinBytes'])}")
    if f.get("sizeMaxBytes"):
        out.append(f"小于 {_fmt_size(f['sizeMaxBytes'])}")
    return out


def _fmt_size(n: int) -> str:
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f}{unit}".replace(".0", "")
    return f"{n}B"
