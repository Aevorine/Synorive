"""
内容增强 —— C9 摘要与关键词、C10 实体抽取
====================================================================
两条都用**纯本地、零额外模型**的做法：

  C9 关键词：jieba 自带的 TF-IDF 与 TextRank
  C9 摘要：  抽取式（挑原文里最有代表性的句子），不是生成式
  C10 实体： jieba 词性标注（nr 人名 / ns 地名 / nt 机构 / nz 专名）+ 正则补时间、
             邮箱、URL、金额、编号

为什么不上模型：这两件事在检索场景里的作用是「让列表一眼看清是什么」和
「能顺藤摸瓜」，不需要生成质量。上个 NER 模型要多下 400MB、每条多花几十毫秒，
换来的提升对检索排序几乎没有影响。想要更好的效果时，B3 混合模式下
可以按需调云端做深度理解 —— 那条路已经预留好了。

抽取式摘要 vs 生成式：抽取式**永远不会编造原文没有的内容**。
在一个"帮你找回自己东西"的工具里，摘要编错比摘要平淡糟糕得多。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

#: 摘要最多取几句
SUMMARY_SENTENCES = 3
#: 摘要总长上限（字符）
SUMMARY_MAX_CHARS = 220
#: 关键词个数
TOP_KEYWORDS = 12

# 句子切分：中文句末标点 + 换行
_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")

# ── 正则抽取的实体 ──────────────────────────────────────────
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("time", re.compile(r"\b(?:19|20)\d{2}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?")),
    ("time", re.compile(r"\b(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月")),
    ("contact", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("link", re.compile(r"https?://[^\s<>\"'）】]+")),
    ("money", re.compile(r"(?:[¥$€£]\s?[\d,]+(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*(?:元|万元|亿元|美元))")),
    # 版本号：带 v 前缀的，或者恰好三段数字的。
    #
    # ⚠️ 前后**都要**加边界断言。只加负向前瞻 `(?!\.\d)` 是不够的：
    #    对 127.0.0.1，引擎会退而从第二段开始，匹配出子串「0.0.1」。
    #    实测这么错过一次 —— 加上 `(?<![\d.])` 之后 IP 才真的匹配不到。
    (
        "version",
        re.compile(
            r"\bv\d+(?:\.\d+){1,3}(?:-[\w.]+)?\b"
            r"|(?<![\d.])\d+\.\d+\.\d+(?:-[\w.]+)?(?![\d.])"
        ),
    ),
)

#: jieba 词性 → 我们的实体类型
_POS_KIND = {
    "nr": "person",
    "nrt": "person",
    "nrfg": "person",
    "ns": "place",
    "nt": "org",
    "nz": "product",
    "t": "time",
}

_MIN_ENTITY_LEN = {"person": 2, "place": 2, "org": 3, "product": 2, "time": 2}

# ═══════════════════════════════════════════════════════════
# 关于中文人名 / 地名 / 机构名的抽取：**默认关掉，这是实测后的决定**
# ═══════════════════════════════════════════════════════════
#
# jieba 的词性标注在这个任务上不可靠。我试了三个判据，**全部失败**：
#
#   ① 出现次数 ≥2 才收
#      → 真名张伟/李娜/王强各出现 1 次，全被滤掉；
#        误判的「索引」出现 2 次，反倒留下了。两头都错。
#
#   ② 首字必须是常见姓氏
#      → 挡住了「索引」，但挡不住「宋体」（宋是姓）、「余弦」（余是姓）。
#
#   ③ 孤立词性（脱离上下文单独标注，走词典）
#      → 也没用。实测孤立标注：宋体→nr、余弦→nr、维度→ns、索引→nr。
#        **jieba 词典本身就把这些词标错了**，换个调用方式救不回来。
#
#   附：词频同样分不开 —— 真名「李娜」438，假阳性「宋体」170，真的比假的还高。
#
# 在真实语料上跑出来的图谱长这样：
#   [place] 维度 x11  [person] 宋体 x11  [place] 东西 x9  [person] 余弦 x4
# 这种图没人会点第二次。按「宁可漏也不要脏」，默认只留高精度的那几类
# （邮箱 / 链接 / 金额 / 日期 / 版本号 / 带后缀的机构名），
# 人名地名要么等用户在设置里开（知道会有噪声），要么装个真正的 NER 模型。
POS_NER_DEFAULT = False

#: 机构后缀。jieba 的词典盖不全公司名（实测「字节跳动」直接被切成「字节+跳动」，
#: 词性 n + vn，压根没被识别成机构），靠后缀正则补一刀。
_ORG_TAIL = re.compile(
    r"(?:有限公司|股份公司|科技公司|公司|集团|大学|学院|中学|小学|医院|银行|"
    r"研究院|研究所|实验室|工作室|事务所|协会|基金会|委员会|中心)"
)

#: 从后缀往前扫时，遇到这些字就停 —— 它们不可能是机构名的一部分。
#:
#: 为什么不用「前缀正则 + 非贪婪」：实测 `[一-龥]{2,12}?(?:公司)` 会从
#: 「伟和李娜在北京的字节跳动公司」这句话的最左边开始匹配，一路吃到「公司」，
#: 抽出整整半句话。非贪婪只影响长度优先级，管不住起始位置。
#: 从后缀反向扫、遇到虚词就停，才是对的做法。
_ORG_STOP_CHARS = set(
    "的地得在和与及或是了也就都很不我你他她它这那有为以对从向被把等着过"
    "上下里外前后中间时候因为所以但是而且如果虽然然后并且以及由于关于"
    "请按照可能需要仅靠要让各种大型一个两个三个多个若干"
    "，。、；：！？（）【】「」《》\"'“”‘’ \t\n\r0123456789"
)

#: 机构名里允许出现的字符。**只有汉字、字母、数字**。
#:
#: 只靠停用字集是拦不住的 —— 实测在自己的文档上抽出过
#: 「检仅靠正则识别密码/银行」「.要让公司」这种东西：
#: 斜杠、点号、括号这些字符不在停用集里，反向扫描就一路吃过去了。
#: 改成白名单（只允许构成名字的字符），比黑名单可靠得多。
_ORG_NAME_CHAR = re.compile(r"[一-龥A-Za-z0-9]")

#: 抽出来的机构名如果以这些字开头，多半是从半句话里截出来的碎片
_ORG_BAD_PREFIX = (
    "和", "与", "及", "或", "等", "为", "把", "让", "使", "有", "无", "各", "该", "其",
)

#: 通用技术词混进 nz（其他专名）的重灾区。词频过滤盖不住这些
#: （它们在 jieba 词典里频次不高，但确确实实是通用词）。
_GENERIC_TECH_WORDS = {
    "中文", "英文", "日文", "数据", "系统", "网络", "技术", "信息", "内容",
    "文件", "项目", "服务", "平台", "模式", "功能", "性能", "版本", "接口",
    "参数", "配置", "文档", "代码", "程序", "软件", "硬件", "用户", "客户",
    "产品", "方案", "方法", "流程", "结果", "问题", "环境", "工具", "组件",
}

#: nz（其他专名）里常混进通用词。这一类**可以**用词频过滤 ——
#: 实测腾讯 98、阿里巴巴 82，而通用词都在几千以上。
_NZ_MAX_FREQ = 2000

#: 常见误判成人名/地名的词，直接拉黑
_STOP_ENTITIES = {
    "本文", "上述", "如下", "以下", "其中", "同时", "目前", "现在", "已经", "可以",
    "需要", "使用", "进行", "通过", "对于", "关于", "根据", "由于", "因为", "所以",
    "这里", "那里", "什么", "怎么", "为了", "并且", "而且", "但是", "如果", "或者",
    "我们", "你们", "他们", "自己", "这个", "那个", "一个", "没有", "就是", "还是",
}


@dataclass
class Entity:
    kind: str
    name: str
    count: int = 1


@dataclass
class Enrichment:
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    language: str = "zh"


# ── 关键词 ──────────────────────────────────────────────────


def extract_keywords(text: str, topk: int = TOP_KEYWORDS) -> list[str]:
    """
    TF-IDF 和 TextRank 各取一半再合并去重。

    两者的偏好不一样：TF-IDF 偏「这篇独有的词」，TextRank 偏「篇内中心词」。
    只用一个的话，TF-IDF 会挑出一堆生僻的专名，TextRank 会挑出一堆通用词。
    合起来互补。
    """
    if not text or len(text) < 20:
        return []

    import jieba.analyse

    half = max(3, topk // 2)
    try:
        tfidf = jieba.analyse.extract_tags(text, topK=half, allowPOS=("n", "nr", "ns", "nt", "nz", "vn", "eng"))
    except Exception:  # noqa: BLE001
        tfidf = []
    try:
        rank = jieba.analyse.textrank(text, topK=half, allowPOS=("n", "nr", "ns", "nt", "nz", "vn"))
    except Exception:  # noqa: BLE001
        rank = []

    out: list[str] = []
    seen: set[str] = set()
    # 交错合并，两边都有的词自然排到前面
    for a, b in zip(tfidf + [""] * len(rank), rank + [""] * len(tfidf)):
        for w in (a, b):
            w = (w or "").strip()
            if len(w) >= 2 and w not in seen and w not in _STOP_ENTITIES:
                seen.add(w)
                out.append(w)
    return out[:topk]


# ── 摘要 ────────────────────────────────────────────────────


def extract_summary(text: str, keywords: list[str] | None = None) -> str:
    """
    抽取式摘要：按「关键词密度 + 位置 + 长度」给句子打分，取前几句按原顺序拼。

    位置加权是有道理的：中文技术文档和邮件的第一句往往就是主旨。
    但只取开头会漏掉真正的重点，所以关键词密度才是主分。
    """
    if not text:
        return ""

    sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    if not sentences:
        return text[:SUMMARY_MAX_CHARS]
    if len(sentences) <= SUMMARY_SENTENCES:
        return _clip(" ".join(sentences))

    kw = set(keywords or extract_keywords(text))
    scored: list[tuple[float, int, str]] = []

    for i, s in enumerate(sentences[:200]):  # 只看前 200 句，长文档没必要全扫
        n = len(s)
        if n < 8:
            continue
        # 太长的句子（往往是代码行、表格行、路径）不适合当摘要
        if n > 160:
            continue

        hits = sum(1 for k in kw if k in s)
        density = hits / (n**0.5)          # 开方是为了不让长句只靠字多取胜
        position = 1.0 / (1 + i * 0.25)     # 越靠前越有可能是主旨
        # 带数字的句子往往含具体信息，略微加权
        concrete = 0.15 if re.search(r"\d", s) else 0.0
        scored.append((density * 2.0 + position + concrete, i, s))

    if not scored:
        return _clip("".join(sentences[:SUMMARY_SENTENCES]))

    scored.sort(key=lambda x: -x[0])
    picked = sorted(scored[:SUMMARY_SENTENCES], key=lambda x: x[1])  # 恢复原文顺序

    # 挑中的句子在原文里往往不相邻。直接拼起来会造出原文没有的邻接
    # （实测拼出过「分词选型实测 SQLite」这种病句）—— 每个字都来自原文，
    # 但读起来是错的。中间跳过了内容就用省略号明示，这是抽取式摘要的本分。
    parts: list[str] = []
    prev_idx: int | None = None
    for _, idx, s in picked:
        if prev_idx is not None:
            if idx != prev_idx + 1:
                parts.append("……")
            elif parts and parts[-1][-1] not in "。！？；，、":
                # 原文里这两句是换行分隔的，句末没有标点。
                # 直接拼会读成「分词选型实测 SQLite」这种病句，补个空格。
                # 空白不是内容，补空格不算编造；补标点就算了。
                parts.append(" ")
        parts.append(s)
        prev_idx = idx
    return _clip("".join(parts))


def _clip(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= SUMMARY_MAX_CHARS:
        return s
    cut = s[:SUMMARY_MAX_CHARS]
    # 尽量断在标点处，别把句子切成半截
    m = re.search(r"[。！？；，、,.][^。！？；，、,.]*$", cut)
    return (cut[: m.start() + 1] if m else cut) + "…"


# ── 实体 ────────────────────────────────────────────────────


def _common_word_freq(word: str) -> int:
    """这个词在 jieba 通用词典里的频次。"""
    try:
        import jieba

        return int(jieba.dt.FREQ.get(word, 0))
    except Exception:  # noqa: BLE001
        return 0


def extract_entities(
    text: str, max_entities: int = 40, *, pos_ner: bool = POS_NER_DEFAULT
) -> list[Entity]:
    """
    抽实体。

    **默认只跑高精度的那几类**：邮箱、链接、金额、日期、版本号，
    以及带明确后缀的机构名（X公司 / X大学 / X研究院…）。
    这几类要么是正则能死死咬住的格式，要么有强后缀信号，几乎不会误判。

    pos_ner=True 才会额外跑 jieba 词性标注抽人名/地名/专名。
    那一路噪声很大（详见文件上方那段实测记录），只在用户明确打开时用。
    """
    if not text or len(text) < 30:
        return []

    counters: dict[str, Counter[str]] = {}
    head = text[:200_000]

    # ① 正则那批：格式咬得死，准确率最高
    for kind, pat in _PATTERNS:
        for m in pat.finditer(head):
            v = m.group(0).strip()
            if len(v) >= 4:
                counters.setdefault(kind, Counter())[v] += 1

    # ② 机构后缀 —— 从后缀**反向**扫，双重约束：
    #    · 白名单：只能是汉字/字母/数字（拦掉 `/`、`.`、括号这类，
    #      黑名单拦不住，实测抽出过「检仅靠正则识别密码/银行」「.要让公司」）
    #    · 停用字：遇到虚词就停（拦掉「在…的…公司」这类跨句截取）
    for m in _ORG_TAIL.finditer(head[:100_000]):
        start = m.start()
        i = start
        while i > 0 and start - i < 10:
            ch = head[i - 1]
            if ch in _ORG_STOP_CHARS or not _ORG_NAME_CHAR.match(ch):
                break
            i -= 1
        v = head[i : m.end()].strip()

        if not (3 <= len(v) <= 16):
            continue
        if v in _STOP_ENTITIES or len(v) <= len(m.group(0)):
            continue
        # 只剩后缀本身、或者以虚词开头 = 从半句话里截出来的碎片
        if v.startswith(_ORG_BAD_PREFIX):
            continue
        # 名字里不能有非名字字符（双保险：反向扫万一漏了）
        if not all(_ORG_NAME_CHAR.match(ch) for ch in v):
            continue
        counters.setdefault("org", Counter())[v] += 1

    # ③ 词性标注那批 —— 默认不跑，噪声太大
    if pos_ner:
        try:
            import jieba.posseg as pseg

            for word, flag in pseg.cut(head[:100_000]):
                kind = _POS_KIND.get(flag)
                if not kind:
                    continue
                w = word.strip()
                if w in _STOP_ENTITIES or w in _GENERIC_TECH_WORDS:
                    continue
                if len(w) < _MIN_ENTITY_LEN.get(kind, 2) or len(w) > 12:
                    continue
                if re.fullmatch(r"[\W\d_]+", w):
                    continue
                # 打开这一路时至少卡掉高频通用词，能少一半噪声
                if _common_word_freq(w) > _NZ_MAX_FREQ:
                    continue
                counters.setdefault(kind, Counter())[w] += 1
        except Exception:  # noqa: BLE001
            pass

    # 机构名去重：「字节跳动公司」和「字节跳动」都抽到时，留长的那个
    if "org" in counters:
        names = sorted(counters["org"], key=len, reverse=True)
        for i, long in enumerate(names):
            for short in names[i + 1 :]:
                if short in long and short in counters["org"]:
                    counters["org"][long] += counters["org"].pop(short)

    out: list[Entity] = []
    for kind, c in counters.items():
        for name, n in c.most_common(max_entities):
            out.append(Entity(kind=kind, name=name, count=n))

    out.sort(key=lambda e: (-e.count, e.kind, e.name))
    return out[:max_entities]


# ── 语言判定 ────────────────────────────────────────────────


def detect_language(text: str) -> str:
    """粗判中英，只用于筛选和显示，不需要精确。"""
    sample = text[:4000]
    if not sample:
        return "unknown"
    cjk = sum(1 for ch in sample if "一" <= ch <= "鿿")
    latin = sum(1 for ch in sample if ch.isascii() and ch.isalpha())
    if cjk == 0 and latin == 0:
        return "unknown"
    return "zh" if cjk * 3 > latin else "en"


# ── 代码文件专用 ────────────────────────────────────────────
#
# 拿中文散文的那套去处理源代码，产出的东西没法看。实测：
#     关键词 → ['len', 'return', 'str', 'text', '公司', '句子']
#     摘要   → '内容增强 —— C9 摘要与关键词……return []……return ""'
# TF-IDF 挑出的是 return/str/len 这类语法词，摘要挑出的是 `return []` 这种行。
#
# 代码要的是别的东西：**这个文件是干嘛的**（文档字符串 / 开头注释块）
# 和**它定义了什么**（类名 / 函数名）。这两样对"找回自己写过的代码"直接有用。

_DEF_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", re.M),          # Python
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)", re.M),                      # Python / TS / Java
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$]\w*)", re.M),  # JS/TS
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$]\w*)\s*=\s*(?:async\s*)?\(", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$]\w*)", re.M),
    re.compile(r"^\s*(?:public|private|protected|internal)?\s*fun\s+([A-Za-z_]\w*)", re.M),  # Kotlin
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M),      # Go
    re.compile(r"^\s*CREATE\s+(?:VIRTUAL\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(\w+)", re.M | re.I),
)

#: 开头的文档字符串或注释块
_LEADING_DOC = re.compile(
    r'\A\s*(?:"""(?P<py>.*?)"""|\'\'\'(?P<py2>.*?)\'\'\'|/\*\*?(?P<c>.*?)\*/)',
    re.S,
)
_COMMENT_LINE = re.compile(r"^\s*(?://+|#+|--+|\*)\s?(.*)$")


def code_summary(text: str) -> str:
    """取代码文件开头的文档字符串或连续注释块，那才是"这个文件是干嘛的"。"""
    m = _LEADING_DOC.match(text)
    if m:
        doc = (m.group("py") or m.group("py2") or m.group("c") or "").strip()
        if doc:
            return _clip(_strip_rules(doc))

    # 没有文档字符串就收集开头连续的注释行
    lines: list[str] = []
    for raw in text.splitlines()[:40]:
        s = raw.strip()
        if not s:
            if lines:
                break
            continue
        cm = _COMMENT_LINE.match(raw)
        if cm:
            body = cm.group(1).strip()
            if body:
                lines.append(body)
        elif lines:
            break
        elif len(lines) == 0 and len(s) > 0:
            # 第一行就是代码，说明没有文件头注释
            break
    if lines:
        return _clip(_strip_rules(" ".join(lines)))
    return ""


def _strip_rules(s: str) -> str:
    """去掉注释里的分隔线（====、----、────），它们不是内容。"""
    s = re.sub(r"[=\-─━_]{4,}", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def code_symbols(text: str, topk: int = 12) -> list[str]:
    """抽出这个文件定义的类名 / 函数名 / 表名。"""
    seen: dict[str, int] = {}
    for pat in _DEF_PATTERNS:
        for m in pat.finditer(text[:200_000]):
            name = m.group(1)
            # 私有和 dunder 不算对外特征
            if name.startswith("_") or len(name) < 3:
                continue
            seen[name] = seen.get(name, 0) + 1
    return sorted(seen, key=lambda k: (-seen[k], k))[:topk]


def enrich(text: str, *, is_code: bool = False, pos_ner: bool = POS_NER_DEFAULT) -> Enrichment:
    """
    一次算完摘要、关键词、实体、语言。

    is_code=True 走代码专用路径：摘要取文件头注释，关键词取定义的符号名。
    对源代码用散文那套只会产出 return/str/len 这种垃圾。
    """
    if not text or not text.strip():
        return Enrichment()

    if is_code:
        summary = code_summary(text)
        symbols = code_symbols(text)
        # 文件头注释里如果有中文，再补几个中文关键词 —— 很多代码的注释才是主要内容
        zh_kws: list[str] = []
        if summary and detect_language(summary) == "zh":
            zh_kws = [k for k in extract_keywords(summary, topk=5) if not k.isascii()]
        return Enrichment(
            summary=summary or _clip(text[:200]),
            keywords=(symbols + zh_kws)[:TOP_KEYWORDS],
            # 代码文件**完全不抽实体**。
            # 试过只留正则那几类，结果在自己的源码上抽出了这些：
            #   [org] |协会|基金会|委员会      ← 抽的是正则字面量本身
            #   [link] http://(127\.0\.0\.1|localhost)...  ← 抽的是 CORS 配置
            # 代码里的"实体"就是变量名和模式串，没有任何分析价值，
            # 塞进图谱只会让它变成垃圾场。符号名已经进关键词了，那才是代码该有的索引。
            entities=[],
            language=detect_language(summary or text),
        )

    kws = extract_keywords(text)
    return Enrichment(
        summary=extract_summary(text, kws),
        keywords=kws,
        entities=extract_entities(text, pos_ner=pos_ner),
        language=detect_language(text),
    )
