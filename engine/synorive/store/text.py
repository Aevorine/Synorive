"""
中文分词 —— 索引侧与查询侧必须用同一套规则
====================================================================
为什么需要它：实测 SQLite 3.50.4 上，中文两字词（搜索/视频/文件）
在 trigram 分词器下命中率为 0，在 unicode61 不分词下也是 0。
唯一可用的是「入库前分好词、存空格分隔序列、用 unicode61 建倒排」。

**索引侧和查询侧必须调同一个函数**。分词规则一旦两边不一致，
症状是"某些词就是搜不到"，而且不报任何错 —— 典型的静默失败。
"""

from __future__ import annotations

import re
import threading
from functools import lru_cache

import jieba

# ── 领域词典 ────────────────────────────────────────────────
# jieba 默认词典不认这些词，会切碎（实测「多模态」被切成「多 / 模态」）。
# 切碎之后用户搜「多模态」就搜不到 —— 加进来是最省事的修法。
_DOMAIN_WORDS: tuple[tuple[str, int], ...] = (
    ("多模态", 1000),
    ("跨模态", 1000),
    ("关键帧", 1000),
    ("向量检索", 1000),
    ("语义检索", 1000),
    ("语义搜索", 1000),
    ("全文检索", 1000),
    ("倒排索引", 1000),
    ("知识图谱", 1000),
    ("时间轴", 800),
    ("剪贴板", 800),
    ("缩略图", 800),
    ("嵌入模型", 800),
    ("大模型", 800),
    ("提示词", 800),
    ("上下文", 800),
    ("命令行", 800),
    ("工作流", 800),
    ("显卡", 600),
    ("核显", 600),
    ("并发度", 600),
    ("吞吐量", 600),
    ("断点续传", 600),
    ("增量索引", 600),
)

_init_lock = threading.Lock()
_initialized = False

# 一次连续的西文字母/数字/常见符号算一个 token，别被 jieba 切碎
_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+#\-]*")
# FTS5 查询语法里的保留字符，用户输入里出现要转义掉
_FTS_SPECIAL = re.compile(r'["*():^{}\[\]~]')


def _ensure_init() -> None:
    """首次调用时加载词典（约 1 秒，之后走磁盘缓存）。"""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        jieba.initialize()
        for word, freq in _DOMAIN_WORDS:
            jieba.add_word(word, freq=freq)
        _initialized = True


def segment(text: str) -> list[str]:
    """
    切成词列表。中文走 jieba，连续的西文/数字整体保留。

    西文为什么要单独处理：jieba 对 "electron-builder" 这类会切成
    "electron" "-" "builder"，而用户搜的往往是整串。
    """
    if not text:
        return []
    _ensure_init()

    out: list[str] = []
    pos = 0
    for m in _LATIN_RUN.finditer(text):
        if m.start() > pos:
            out.extend(w for w in jieba.cut(text[pos : m.start()]) if w.strip())
        out.append(m.group(0))
        pos = m.end()
    if pos < len(text):
        out.extend(w for w in jieba.cut(text[pos:]) if w.strip())

    return [w for w in out if w.strip()]


def to_index_text(text: str) -> str:
    """
    入库用：分词后拼成空格分隔的串，写进 FTS5 的列。
    存进去的**不是原文** —— 原文在 items/chunks 表里，FTS 表只是索引。
    """
    return " ".join(segment(text))


@lru_cache(maxsize=2048)
def to_query(text: str) -> str:
    """
    查询用：把用户输入变成 FTS5 的 MATCH 表达式。

    规则：
      · 每个词加引号，避免被当成 FTS5 语法（比如输入里带 AND / NEAR / *）
      · 词之间用 AND，全部命中才算命中（要 OR 语义走向量那一路）
      · 用户显式写的 -排除词 保留成 NOT
      · 双引号包起来的整段当短语精确匹配

    加了 lru_cache：用户敲字时同一个前缀会反复查，缓存能省掉重复分词。
    """
    if not text or not text.strip():
        return ""
    _ensure_init()

    # 先把用户用双引号圈出来的短语抠出来，它们要整体精确匹配
    phrases: list[str] = []

    def _grab(m: re.Match[str]) -> str:
        phrases.append(m.group(1))
        return " "

    rest = re.sub(r'"([^"]+)"', _grab, text)

    parts: list[str] = []
    for ph in phrases:
        seg = to_index_text(ph)
        if seg:
            parts.append(f'"{seg}"')

    for raw in rest.split():
        negate = raw.startswith("-") and len(raw) > 1
        token = raw[1:] if negate else raw
        token = _FTS_SPECIAL.sub(" ", token).strip()
        if not token:
            continue
        words = segment(token)
        if not words:
            continue
        expr = " ".join(f'"{w}"' for w in words)
        if len(words) > 1:
            expr = f"({expr})"
        parts.append(f"NOT {expr}" if negate else expr)

    if not parts:
        return ""

    # NOT 不能打头，前面必须有个正向条件
    positives = [p for p in parts if not p.startswith("NOT ")]
    negatives = [p for p in parts if p.startswith("NOT ")]
    if not positives:
        return ""
    return " AND ".join(positives + negatives)


def to_trigram_query(text: str) -> str:
    """
    子串兜底表的查询式。trigram 要求 ≥3 字符，短于 3 的直接返回空
    （交给主索引和向量那两路去处理）。
    """
    cleaned = _FTS_SPECIAL.sub(" ", text).strip()
    if len(cleaned) < 3:
        return ""
    return f'"{cleaned}"'


def highlight_terms(query: str) -> list[str]:
    """从查询里取出要在结果里高亮的词（D6 可解释用）。"""
    if not query:
        return []
    cleaned = _FTS_SPECIAL.sub(" ", query.replace('"', " "))
    terms: list[str] = []
    for raw in cleaned.split():
        if raw.startswith("-"):
            continue
        terms.extend(w for w in segment(raw) if len(w) >= 1)
    # 去重保序
    seen: set[str] = set()
    return [t for t in terms if not (t in seen or seen.add(t))]
