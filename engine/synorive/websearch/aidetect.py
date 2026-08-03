"""
D3 —— AI 生成文本判据（困惑度代理 + 句长方差 + 套话密度）
====================================================================
判一段中文/英文正文**像不像模型写的**。

🔴 **这个模块的输出只标注，永不下结论。** 这条不是免责声明，是设计约束：
现有的所有 AI 文本检测方法（包括商用的那些）在真实分布上都有相当高的
误判率，而误判的代价是**把一个认真写东西的人打成机器**。所以：

  · 对外只给 `signals`（观察到了什么）和 `score`（0~1 的可疑度）
  · **没有 `is_ai` 这个字段**，界面上也只显示「文风偏机械」这类描述
  · score 高只影响排序里很小的一档权重，不参与"要不要显示"的决策

**三条判据各自看什么**：

① **困惑度代理**（不上模型）：真正的困惑度要跑语言模型，那在本地引擎里
   太贵。这里用两个廉价代理 —— **词汇重复率**和**高频虚词占比**。
   模型生成的文本倾向于把同一批安全词反复用，人写的东西用词更散。

② **句长方差**：人写句子长短起伏很大（一句 8 字接一句 45 字很常见）；
   模型输出的句长分布明显更集中。这是所有判据里**最稳的一条**，
   因为它不依赖具体词表，换个话题也不失效。

③ **套话密度**：「值得注意的是」「综上所述」「在当今社会」这类过渡短语，
   人也用，但模型用得**特别密**。

三条都只是相关性，任何一条单独都不足以说明什么 —— 所以最终 score 要求
**至少两条同时异常**才会超过 0.5。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

#: 中文套话。列表刻意只收**过渡与总结**类，不收专业表达 ——
#: 「因此」「所以」这种正常写作也高频，收进来只会全员命中
_CN_CLICHE = (
    "值得注意的是", "综上所述", "总而言之", "总的来说", "首先其次最后",
    "在当今社会", "随着科技的发展", "众所周知", "不难看出", "由此可见",
    "在一定程度上", "具有重要意义", "起到了重要作用", "有着广泛的应用",
    "极大地提高了", "为我们提供了", "不仅如此", "与此同时", "换句话说",
    "需要指出的是", "这不仅仅是", "更是一种", "在这个基础上",
)

_EN_CLICHE = (
    "it is worth noting", "in conclusion", "in today's world",
    "plays a crucial role", "it is important to note", "delve into",
    "in the realm of", "a testament to", "navigating the",
    "furthermore", "moreover", "additionally", "on the other hand",
    "leverage", "seamless", "robust solution", "unlock the potential",
)

#: 中文高频虚词。占比过高说明句子结构被"填充"了
_CN_FUNCTION_WORDS = (
    "的", "了", "和", "是", "在", "有", "与", "及", "或", "而", "对",
    "为", "以", "等", "之", "所", "被", "把", "从", "到", "上", "中",
)

_SENT_SPLIT = re.compile(r"[。！？；\n]+|[.!?;]+\s")


@dataclass
class AiSignals:
    """一段文本的 AI 味观察结果。**没有布尔判定字段，这是刻意的。**"""

    score: float = 0.0
    signals: list[str] = field(default_factory=list)
    sentence_count: int = 0
    length_cv: float = 0.0          # 句长变异系数（标准差/均值）
    repeat_ratio: float = 0.0       # 词汇重复率
    function_ratio: float = 0.0     # 虚词占比
    cliche_per_100: float = 0.0     # 每百字套话次数
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "signals": self.signals,
            "sentenceCount": self.sentence_count,
            "lengthCv": round(self.length_cv, 3),
            "repeatRatio": round(self.repeat_ratio, 3),
            "functionRatio": round(self.function_ratio, 3),
            "clichePer100": round(self.cliche_per_100, 2),
            "note": self.note,
        }


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(str(text or "")) if len(s.strip()) >= 4]


def _tokens(text: str) -> list[str]:
    """
    粗分词。**不引 jieba** —— 这个函数会在一波搜索结果上跑几十次，
    jieba 首次加载词典要几百毫秒，为了一个"只标注不下结论"的功能
    去顶 X2 的延迟预算不划算。二字滑窗对重复率统计够用了。
    """
    s = str(text or "")
    cn = re.findall(r"[一-鿿]", s)
    grams = ["".join(cn[i:i + 2]) for i in range(len(cn) - 1)]
    en = re.findall(r"[a-zA-Z]{2,}", s.lower())
    return grams + en


def analyze(text: str) -> AiSignals:
    """
    跑三条判据。文本太短（<120 字）直接返回空结果 ——
    短文本上这三条统计量全是噪声，硬给一个分数只会误导。
    """
    s = str(text or "").strip()
    out = AiSignals()
    if len(s) < 120:
        out.note = "文本太短，统计判据在这个长度上没有意义，不做判断"
        return out

    sents = _sentences(s)
    out.sentence_count = len(sents)
    if len(sents) < 5:
        out.note = "句子太少，不做判断"
        return out

    # ② 句长方差 ------------------------------------------------
    lens = [len(x) for x in sents]
    mean = sum(lens) / len(lens)
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    out.length_cv = (math.sqrt(var) / mean) if mean else 0.0

    # ① 困惑度代理 ----------------------------------------------
    toks = _tokens(s)
    if toks:
        uniq = len(set(toks))
        out.repeat_ratio = 1.0 - (uniq / len(toks))
        cn_chars = re.findall(r"[一-鿿]", s)
        if cn_chars:
            fn = sum(cn_chars.count(w) for w in _CN_FUNCTION_WORDS)
            out.function_ratio = fn / len(cn_chars)

    # ③ 套话密度 ------------------------------------------------
    low = s.lower()
    hits = sum(s.count(c) for c in _CN_CLICHE) + sum(low.count(c) for c in _EN_CLICHE)
    out.cliche_per_100 = hits / (len(s) / 100.0)

    # 判定：每条异常算一票，**要两票以上才让 score 过半** -------
    votes = 0
    if out.length_cv < 0.35:
        votes += 1
        out.signals.append(f"句子长短过于均匀（变异系数 {out.length_cv:.2f}，人写的通常 >0.45）")
    if out.repeat_ratio > 0.62:
        votes += 1
        out.signals.append(f"用词反复度偏高（{out.repeat_ratio:.0%}）")
    if out.function_ratio > 0.30:
        votes += 1
        out.signals.append(f"虚词占比偏高（{out.function_ratio:.0%}），句子被填充词撑长了")
    if out.cliche_per_100 >= 1.2:
        votes += 1
        out.signals.append(f"过渡套话密集（每百字 {out.cliche_per_100:.1f} 处）")

    # 一票 = 0.3，两票 = 0.55，三票 = 0.75，四票 = 0.9
    out.score = {0: 0.0, 1: 0.30, 2: 0.55, 3: 0.75, 4: 0.90}[min(votes, 4)]
    if votes == 0:
        out.note = "没有观察到明显的机械文风特征"
    elif votes == 1:
        out.note = "只有一项偏离常见范围，**不足以说明任何事**，仅作记录"
    else:
        out.note = ("多项文风指标偏离人类写作的常见范围。这是**文风观察**，"
                    "不是「这段是 AI 写的」的结论 —— 人也会这样写，"
                    "尤其是公文、软文和翻译稿")
    return out


def annotate(entries: list[dict[str, Any]], texts: dict[str, str]) -> list[dict[str, Any]]:
    """
    批量给搜索结果标注。`texts` 是 `{url: 正文}`。

    **只对真的抓到正文的条目跑** —— 拿摘要（一两百字，还常被引擎截断）
    去算这三个统计量，得到的全是噪声。宁可少标一批，不给假信号。
    """
    for e in entries:
        url = str(e.get("url") or "")
        body = texts.get(url) or ""
        if len(body) >= 120:
            e["aiStyle"] = analyze(body).to_dict()
    return entries
