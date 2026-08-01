"""
语义分块 —— C8「按语义边界切，不是按字数硬切」
====================================================================
为什么不能按字数硬切：切在句子中间的块，两半都表达不了完整意思，
向量化出来的语义是残的。检索时它既匹配不上前半句的意思，也匹配不上后半句，
症状是"明明文档里有这句话就是搜不到"。

切分优先级（从高到低）：
    段落空行  >  中文句号问号叹号分号  >  英文句号  >  逗号顿号  >  硬切

**块之间有重叠**：一个概念正好横跨两块时，没有重叠的话两块都只有半个概念。
重叠 15% 是检索质量和索引体积的折中。

块的大小：目标 300 字左右（约 200~250 token）。
BGE 上限 512 token，但块越大语义越"糊"——一块里塞五个主题，
向量就是五个主题的平均，什么都匹配一点，什么都匹配不准。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parsers import TextSegment

#: 目标块大小（字符数）。中文一个字≈1 token，英文约 4 字符≈1 token。
TARGET_CHARS = 300
#: 硬上限：超过这个必须切，哪怕切在难看的地方
MAX_CHARS = 480
#: 低于这个就别单独成块，并进相邻块 —— 一个字的块毫无检索价值
MIN_CHARS = 40
#: 重叠比例
OVERLAP_RATIO = 0.15

# 断句符号，按"切在这里有多合理"排序
_PARA_BREAK = re.compile(r"\n\s*\n")
_SENT_END_CJK = re.compile(r"(?<=[。！？；…])")
_SENT_END_LATIN = re.compile(r"(?<=[.!?;])\s+")
_CLAUSE = re.compile(r"(?<=[，、,])")


@dataclass
class Chunk:
    index: int
    text: str
    channel: str
    page: int | None = None
    section: str | None = None
    #: 估算的 token 数，用于控制批大小
    token_estimate: int = 0


def chunk_segments(segments: list[TextSegment]) -> list[Chunk]:
    """把解析出来的片段切成检索块，位置信息一路带下去。"""
    chunks: list[Chunk] = []
    idx = 0

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue

        # 标题类片段永远整条保留：标题本来就短，切开毫无意义
        if seg.channel == "title" or len(text) <= MAX_CHARS:
            if len(text) >= MIN_CHARS or seg.channel == "title":
                chunks.append(
                    Chunk(
                        index=idx,
                        text=text,
                        channel=seg.channel,
                        page=seg.page,
                        section=seg.section,
                        token_estimate=estimate_tokens(text),
                    )
                )
                idx += 1
                continue

        for piece in _split_text(text):
            chunks.append(
                Chunk(
                    index=idx,
                    text=piece,
                    channel=seg.channel,
                    page=seg.page,
                    section=seg.section,
                    token_estimate=estimate_tokens(piece),
                )
            )
            idx += 1

    return _merge_tiny(chunks)


def _split_text(text: str) -> list[str]:
    """逐级降低标准地切：段落 → 句子 → 分句 → 硬切。"""
    units = _split_into_units(text)

    out: list[str] = []
    buf = ""
    for u in units:
        if not u.strip():
            continue
        # 加上这个单元还不超标 → 继续攒
        if len(buf) + len(u) <= TARGET_CHARS or not buf:
            buf += u
            # 单个单元本身就超长（比如一整段没有标点的代码）→ 硬切
            while len(buf) > MAX_CHARS:
                out.append(buf[:MAX_CHARS])
                buf = buf[MAX_CHARS:]
            continue

        out.append(buf)
        # 带上一点重叠：上一块的尾巴接到下一块开头
        overlap = _tail(buf, int(TARGET_CHARS * OVERLAP_RATIO))
        buf = overlap + u

    if buf.strip():
        out.append(buf)

    return [c.strip() for c in out if c.strip()]


def _split_into_units(text: str) -> list[str]:
    """
    切成最小可拼装单元。注意保留分隔符本身 —— 把句号丢掉的话，
    块拼回去就没有标点了，OCR/摘要之类的下游会读不懂。
    """
    # 一级：段落
    paras = _PARA_BREAK.split(text)
    units: list[str] = []
    for p in paras:
        p = p.strip()
        if not p:
            continue
        if len(p) <= TARGET_CHARS:
            units.append(p + "\n\n")
            continue

        # 二级：中文句末
        sents = [s for s in _SENT_END_CJK.split(p) if s]
        if len(sents) == 1:
            # 三级：英文句末
            sents = [s for s in _SENT_END_LATIN.split(p) if s]
        if len(sents) == 1:
            # 四级：分句
            sents = [s for s in _CLAUSE.split(p) if s]
        if len(sents) == 1:
            # 五级：换行
            sents = [s + "\n" for s in p.split("\n") if s.strip()]

        units.extend(sents)
    return units


def _tail(s: str, n: int) -> str:
    """取末尾 n 个字符，但尽量从一个句子边界开始，别从半个词切。"""
    if n <= 0 or len(s) <= n:
        return ""
    tail = s[-n:]
    m = re.search(r"[。！？；.!?;，、,]\s*", tail)
    return tail[m.end() :] if m else tail


def _merge_tiny(chunks: list[Chunk]) -> list[Chunk]:
    """
    把过短的块并进后一块。

    为什么要做：切分末尾常留一个几个字的碎块，
    它自己没有检索价值，还会在结果里占一行、稀释排序。
    标题块不并 —— 标题本来就短，而且要单独加权。
    """
    if not chunks:
        return []

    out: list[Chunk] = []
    for c in chunks:
        if (
            out
            and c.channel != "title"
            and out[-1].channel == c.channel
            and out[-1].page == c.page
            and len(c.text) < MIN_CHARS
            and len(out[-1].text) + len(c.text) <= MAX_CHARS
        ):
            out[-1].text = out[-1].text + "\n" + c.text
            out[-1].token_estimate = estimate_tokens(out[-1].text)
            continue
        out.append(c)

    # 合并之后 index 会有洞，重排一遍 —— chunks 表上 (item_id, index, channel) 是唯一键
    for i, c in enumerate(out):
        c.index = i
    return out


def estimate_tokens(text: str) -> int:
    """
    粗估 token 数，不调 tokenizer（那太慢，分块阶段要处理海量文本）。
    中日韩字符约 1 字 1 token，其它约 4 字符 1 token。
    只用来控制批大小和截断，误差 ±20% 完全够用。
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ")
    rest = len(text) - cjk
    return cjk + rest // 4
