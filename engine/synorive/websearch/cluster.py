"""
C8 —— 几百篇一次进来，先按内容聚成 N 堆再读
====================================================================
一次文献检索返回两三百篇是常态。按相关度排成一列长表，用户读到第 30 条
就放弃了 —— 而真正有用的信息往往是**「这批文献分成了几个流派」**，
那在一列长表里完全看不出来。

**做法**：标题 + 摘要 → 词袋 → 余弦相似 → 层次凝聚聚类（average linkage）。

🔴 **为什么不用向量模型**：本机文本向量化实测 47 块/秒（`task-progress.md`
A7），300 篇摘要要 6 秒以上，全顶在用户等结果的路径上。
而聚类的用途是**分堆给人看**，不是精确检索 —— 词袋在这个用途上够用，
且是毫秒级。要更准可以后续把已入库文献的现成向量接进来（那些是
入库时就算好的，不额外花时间）。

🔴 **簇标签只从原文里挑词，不生成**：生成的标签会出现这批文献里
根本没有的概念，用户点进去发现名不副实。所以标签就是簇内**最高频且
最有区分度**的那几个词，哪怕读起来不够漂亮。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

#: 停用词。中英各一份，只收**在学术摘要里几乎每篇都有**的那批 ——
#: 收窄一点没关系，收宽了会把领域术语误杀（比如把 "network" 当停用词，
#: 那神经网络那一簇就没有标签了）
_STOP_CN = {
    "研究", "方法", "分析", "结果", "问题", "提出", "基于", "进行", "使用",
    "本文", "我们", "可以", "通过", "以及", "或者", "但是", "因此", "这些",
    "一个", "该", "其", "对于", "并且", "同时", "此外", "如下", "表明",
    "实验", "数据", "模型", "系统", "应用", "技术", "影响", "作用", "发展",
}
_STOP_EN = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "have", "has", "been", "not", "but", "which", "such", "these", "those",
    "our", "their", "its", "can", "may", "using", "used", "use", "based",
    "paper", "study", "results", "method", "methods", "propose", "proposed",
    "show", "shows", "shown", "we", "in", "of", "to", "on", "by", "as", "is",
    "an", "at", "it", "be", "or", "also", "however", "more", "than", "between",
}

#: 相似度低于这个值就不合并。0.18 是个偏低的门槛 —— 摘要词袋的相似度
#: 天然偏低（不同作者用词差异大），门槛定高会导致每篇自成一簇
_MERGE_THRESHOLD = 0.18


def _terms(text: str) -> list[str]:
    """
    切词。中文用二字滑窗（理由同 `aidetect.py`：不引 jieba，
    这条路径要快），英文按空白切。
    """
    s = str(text or "").lower()
    en = [w for w in re.findall(r"[a-z][a-z\-]{2,}", s) if w not in _STOP_EN]
    cn_runs = re.findall(r"[一-鿿]{2,}", s)
    cn: list[str] = []
    for run in cn_runs:
        for i in range(len(run) - 1):
            g = run[i:i + 2]
            if g not in _STOP_CN:
                cn.append(g)
    return en + cn


def _vec(terms: list[str], idf: dict[str, float]) -> dict[str, float]:
    """TF-IDF 向量，L2 归一化后返回稀疏 dict。"""
    if not terms:
        return {}
    tf = Counter(terms)
    n = len(terms)
    v = {t: (c / n) * idf.get(t, 1.0) for t, c in tf.items()}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


@dataclass
class Cluster:
    """一堆内容相近的文献/网页。"""

    id: int = 0
    label: str = ""
    keywords: list[str] = field(default_factory=list)
    members: list[int] = field(default_factory=list)
    size: int = 0
    representative: dict[str, Any] | None = None
    year_span: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "keywords": self.keywords,
            "members": self.members, "size": self.size,
            "representative": self.representative, "yearSpan": self.year_span,
        }


def cluster_entries(
    entries: list[dict[str, Any]],
    *,
    max_clusters: int = 8,
    min_size: int = 2,
) -> dict[str, Any]:
    """
    C8 主入口。`entries` 用 `merge_scholar()` 或 `WebResult.to_dict()` 的形状。

    返回 `{clusters: [...], outliers: [...], note}`。
    **散落的（自成一簇且只有一篇的）单独放 `outliers`，不硬塞进某个簇** ——
    硬塞会污染那个簇的标签，让用户点进去看到一篇完全不相干的。
    """
    n = len(entries)
    if n < 3:
        return {
            "clusters": [], "outliers": list(range(n)), "note":
            "结果太少，不做聚类 —— 三五篇直接看列表比分堆更快",
        }

    docs = [
        _terms(f"{e.get('title', '')} {e.get('snippet', '')}")
        for e in entries
    ]
    df: dict[str, int] = defaultdict(int)
    for d in docs:
        for t in set(d):
            df[t] += 1
    idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
    vecs = [_vec(d, idf) for d in docs]

    # 层次凝聚（average linkage）。n 最多几百，O(n²) 完全能接受，
    # 换成 KMeans 反而要先定 k —— 而"该分几堆"恰恰是不知道的那个量
    groups: list[list[int]] = [[i] for i in range(n)]
    sims: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            s = _cos(vecs[i], vecs[j])
            if s > 0:
                sims[(i, j)] = s

    def group_sim(g1: list[int], g2: list[int]) -> float:
        tot = 0.0
        for a in g1:
            for b in g2:
                tot += sims.get((a, b) if a < b else (b, a), 0.0)
        return tot / (len(g1) * len(g2))

    while len(groups) > max_clusters:
        best: tuple[float, int, int] | None = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                s = group_sim(groups[i], groups[j])
                if best is None or s > best[0]:
                    best = (s, i, j)
        if best is None or best[0] < _MERGE_THRESHOLD:
            break        # 已经没有够像的了，停下来而不是硬凑到 max_clusters
        _s, i, j = best
        groups[i] = groups[i] + groups[j]
        groups.pop(j)

    clusters: list[Cluster] = []
    outliers: list[int] = []
    for g in groups:
        if len(g) < min_size:
            outliers += g
            continue
        # 标签：簇内高频 且 簇外低频（这才是这一堆区别于其他堆的地方）
        inside = Counter()
        for i in g:
            inside.update(set(docs[i]))
        picked: list[tuple[float, str]] = []
        for t, c in inside.items():
            if len(t) < 2:
                continue
            inside_ratio = c / len(g)
            outside_ratio = (df[t] - c) / max(1, n - len(g))
            if inside_ratio < 0.4:
                continue
            picked.append((inside_ratio - outside_ratio, t))
        picked.sort(reverse=True)
        kws = [t for _s, t in picked[:6]]

        rep_idx = max(g, key=lambda i: int(
            ((entries[i].get("meta") or {}).get("citations")) or 0
        ))
        years = [
            str((entries[i].get("meta") or {}).get("year") or "")[:4] for i in g
        ]
        years = sorted(y for y in years if y.isdigit())

        clusters.append(Cluster(
            id=len(clusters) + 1,
            label="、".join(kws[:3]) if kws else f"第 {len(clusters) + 1} 组",
            keywords=kws,
            members=sorted(g),
            size=len(g),
            representative=entries[rep_idx],
            year_span=(f"{years[0]}–{years[-1]}" if years else ""),
        ))

    clusters.sort(key=lambda c: -c.size)
    for i, c in enumerate(clusters, 1):
        c.id = i

    return {
        "clusters": [c.to_dict() for c in clusters],
        "outliers": sorted(outliers),
        "note": (
            f"{len(entries)} 条分成 {len(clusters)} 堆，"
            f"另有 {len(outliers)} 条没有相近的同伴（单独列出，没有硬塞进某一堆）。"
            "分堆用的是标题和摘要的词面相似度，**不是语义模型** —— "
            "换了说法但讲同一件事的，可能会被分到两堆里"
        ),
    }
