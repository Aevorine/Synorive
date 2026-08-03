"""
A6 —— 长视频自动章节化
====================================================================
把一个小时的视频/播客切成十几个**有标题的章节**，每章一句摘要，
搜索命中直接跳到那一章。

**为什么不是场景切分就够了**：场景切分给的是 200 多个几秒钟的镜头
（`MAX_SCENES = 240`），那是给"以图搜镜头"用的粒度。人要的是
「第 3 章 · 环境搭建 · 12:30–19:05」这种能直接跳的粒度。

**怎么切**：两个信号取交集
  ① **语音停顿**：转写句之间的间隔超过阈值 → 大概率在换话题
  ② **画面切换密度**：一段时间内场景切换突然变密或变疏 → 换场景了

🔴 **两个信号都没有时退回等分**，并且**明说是等分的**。
一个把等分结果伪装成"智能分章"的功能，会让用户点进第 5 章发现
讲的是第 3 章的内容，然后再也不信这个功能 —— 说清楚反而还能用。

🔴 **章节标题从转写原文里挑，不生成**。理由同 `search/questions.py`：
生成的标题会写出这一段根本没讲的东西。挑不出来就用时间码当标题，
难看但不骗人。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 语音停顿超过这么久就认为可能在换话题。3 秒是拍的 ——
#: 讲课/播客里句间停顿通常 0.3~1.5 秒，超过 3 秒往往是"下面我们看……"
_PAUSE_GAP_S = 3.0

#: 一章至少多长。太短的章节没有导航价值，用户点进去还没读完就到下一章了
_MIN_CHAPTER_S = 45.0

#: 一章最长多久。超过就硬切 —— 一个 20 分钟的"章节"等于没分章
_MAX_CHAPTER_S = 600.0

#: 章节数上限。超过 30 章的目录本身就没法用了
_MAX_CHAPTERS = 30

#: 能当标题的句式：以这些开头的句子往往是段落主题句
_TITLE_HINTS = [
    re.compile(r"^(接下来|下面|现在|首先|其次|然后|最后|第[一二三四五六七八九十\d]+[章节部分步])"),
    re.compile(r"^(我们(来|先)?(看|讲|说|聊|介绍|讨论))"),
    re.compile(r"^(let'?s|now|next|first|second|finally|in this (section|part|chapter))", re.I),
]

#: 这些词出现在句子里，说明它在点题
_TOPIC_WORDS = re.compile(
    r"(是什么|怎么|如何|为什么|原理|方法|步骤|安装|配置|区别|对比|总结|注意)"
)


@dataclass
class Chapter:
    """一个章节。`title_source` 说明标题是怎么来的 —— 用户有权知道。"""

    index: int = 0
    start_sec: float = 0.0
    end_sec: float = 0.0
    title: str = ""
    summary: str = ""
    title_source: str = "timecode"      # transcript / hint / timecode
    scene_count: int = 0
    keyframe: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "startSec": round(self.start_sec, 2),
            "endSec": round(self.end_sec, 2),
            "durationSec": round(self.duration, 2),
            "title": self.title,
            "summary": self.summary,
            "titleSource": self.title_source,
            "sceneCount": self.scene_count,
            "keyframe": self.keyframe,
            "timecode": _tc(self.start_sec),
        }


def _tc(sec: float) -> str:
    s = int(max(0, sec))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m}:{ss:02d}"


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _pick_title(sentences: list[str]) -> tuple[str, str]:
    """
    从一章的转写句里挑一句当标题。返回 `(标题, 来源)`。

    优先级：**点题句式 > 含主题词的句子 > 最长的那句**。
    挑出来还要截断到 28 字 —— 一整句台词当标题在目录里会换行三次。
    """
    cands = [_clean(s) for s in sentences if 6 <= len(_clean(s)) <= 120]
    if not cands:
        return "", "timecode"

    for s in cands[:6]:
        for pat in _TITLE_HINTS:
            m = pat.match(s)
            if m:
                # 把"接下来我们看"这个引子去掉，留后半句真正的主题
                rest = _clean(s[m.end():]).lstrip("，,、：: ")
                if len(rest) >= 4:
                    return rest[:28], "hint"
                return s[:28], "hint"

    for s in cands[:8]:
        if _TOPIC_WORDS.search(s):
            return s[:28], "transcript"

    return max(cands[:8], key=len)[:28], "transcript"


def build_chapters(
    scenes: list[Any],
    transcript: list[Any] | None = None,
    *,
    duration_sec: float = 0.0,
    max_chapters: int = _MAX_CHAPTERS,
) -> dict[str, Any]:
    """
    A6 主入口。

    `scenes` 是 `video.Scene` 列表（或同形状的 dict）；
    `transcript` 是 `video.TranscriptSegment`（有 `start_sec` / `end_sec` / `text`）。
    两者都可以为空 —— 都空时退回等分并**在 `method` 里标明**。

    返回 `{chapters, method, note}`。
    """
    def g(o: Any, name: str, default: Any = None) -> Any:
        if isinstance(o, dict):
            return o.get(name, o.get(_camel(name), default))
        return getattr(o, name, default)

    total = float(duration_sec or 0.0)
    if not total and scenes:
        total = max(float(g(s, "end_sec", 0.0) or 0.0) for s in scenes)
    if total <= 0:
        return {"chapters": [], "method": "none",
                "note": "读不出时长，没法分章"}

    segs = list(transcript or [])
    cut_points: list[float] = []
    method = "equal"

    # ① 语音停顿 -------------------------------------------------
    if len(segs) >= 4:
        method = "pause"
        prev_end = float(g(segs[0], "end_sec", 0.0) or 0.0)
        for seg in segs[1:]:
            st = float(g(seg, "start_sec", 0.0) or 0.0)
            if st - prev_end >= _PAUSE_GAP_S:
                cut_points.append(st)
            prev_end = float(g(seg, "end_sec", st) or st)

    # ② 画面切换密度 ---------------------------------------------
    if scenes and len(scenes) >= 8:
        starts = sorted(float(g(s, "start_sec", 0.0) or 0.0) for s in scenes)
        gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
        if gaps:
            avg = sum(gaps) / len(gaps)
            # 间隔突然拉长到平均的三倍 = 前面那段画面很稳定，多半是新一节的开场
            for i, gp in enumerate(gaps):
                if gp >= avg * 3 and gp >= 8.0:
                    cut_points.append(starts[i + 1])
            if method == "equal":
                method = "scene"
            elif method == "pause":
                method = "pause+scene"

    # ③ 合并切点：太近的丢掉，太长的补切 --------------------------
    cut_points = sorted(set([0.0] + [c for c in cut_points if 0 < c < total]))
    merged: list[float] = [0.0]
    for c in cut_points[1:]:
        if c - merged[-1] >= _MIN_CHAPTER_S:
            merged.append(c)
    # 太长的章节硬切
    filled: list[float] = []
    for i, c in enumerate(merged):
        filled.append(c)
        nxt = merged[i + 1] if i + 1 < len(merged) else total
        while nxt - filled[-1] > _MAX_CHAPTER_S:
            filled.append(filled[-1] + _MAX_CHAPTER_S)

    if len(filled) <= 1:
        # 两个信号都没给出有用的切点 → 等分，**并且如实标 method="equal"**
        n = max(2, min(max_chapters, int(total // 300) + 1))
        filled = [total * i / n for i in range(n)]
        method = "equal"

    if len(filled) > max_chapters:
        # 超上限就按等间隔抽稀，保留首尾
        step = len(filled) / max_chapters
        filled = [filled[int(i * step)] for i in range(max_chapters)]

    # ④ 组装 -----------------------------------------------------
    chapters: list[Chapter] = []
    for i, start in enumerate(filled):
        end = filled[i + 1] if i + 1 < len(filled) else total
        ch = Chapter(index=i + 1, start_sec=start, end_sec=end)

        lines = [
            _clean(str(g(s, "text", "") or ""))
            for s in segs
            if start <= float(g(s, "start_sec", -1) or -1) < end
        ]
        lines = [x for x in lines if x]
        if lines:
            ch.title, ch.title_source = _pick_title(lines)
            ch.summary = _clean(" ".join(lines))[:160]
        if not ch.title:
            ch.title = f"{_tc(start)} – {_tc(end)}"
            ch.title_source = "timecode"

        in_scenes = [
            s for s in (scenes or [])
            if start <= float(g(s, "start_sec", -1) or -1) < end
        ]
        ch.scene_count = len(in_scenes)
        if in_scenes:
            ch.keyframe = str(g(in_scenes[0], "keyframe_path", "") or "")
        chapters.append(ch)

    method_note = {
        "pause+scene": "按语音停顿和画面切换两个信号一起切的",
        "pause": "按语音停顿切的",
        "scene": "按画面切换密度切的",
        "equal": "**这是等分的** —— 没有转写也没有足够的场景数据，"
                 "章节边界不代表内容真的在这里换了话题",
    }.get(method, method)

    return {
        "chapters": [c.to_dict() for c in chapters],
        "method": method,
        "count": len(chapters),
        "note": (
            f"{len(chapters)} 章，{method_note}。"
            "标题是从转写原文里**挑**出来的，不是生成的 —— "
            "挑不出来的用时间码，难看但不会写出这一段没讲过的东西"
        ),
    }


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)
