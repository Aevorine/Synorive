"""
A5 —— 拖两个文件进来，直接告诉我它们哪里不一样
====================================================================
三种比法，按两个文件的类型自动选：

  · **文本 / 代码 / PDF** → 行级 diff（`difflib`），外加相似度百分比
  · **图片** → 感知哈希距离 + 尺寸/格式差异，判「同一张的不同版本」还是「两张不同的图」
  · **视频** → 关键帧序列比对，找出**两段视频里重复的片段及其时间区间**

**为什么这个功能值得做**：这三件事各自都有专门的工具，但都要先把文件
找出来、打开某个软件、配置一下。而用户手上正好有两个文件、正好想知道
差别的那一刻，往往就不值得为它开一个新软件 —— 于是就不比了。

🔴 **不做「哪个更好」的判断**，只报差异。哪个版本对是用户的事。

🔴 **大文件要有上限**。两个 200MB 的文本做行级 diff 会把内存吃穿，
所以超过阈值就只比前 N 行并**明确说明只比了一部分** ——
悄悄截断然后报"基本相同"是最糟的结果。
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 文本比对的行数上限。超过就只比前这么多行并如实说明
_MAX_LINES = 20_000
#: 单行长度上限，超长行截断（压缩过的 js/json 一行能有几兆）
_MAX_LINE_LEN = 2000
#: 返回给界面的差异块上限。几千个差异块画出来没人看，也会拖垮渲染
_MAX_HUNKS = 300

#: 感知哈希距离阈值。dHash 64 位，实测：
#:   ≤5   同一张图的不同压缩/缩放版本
#:   6~12 明显相关（裁剪、加水印、调色）
#:   >12  基本是两张不同的图
_PHASH_SAME = 5
_PHASH_RELATED = 12


@dataclass
class DiffHunk:
    """一段差异。`tag` 用 difflib 的语义：replace / delete / insert。"""

    tag: str = ""
    a_start: int = 0
    a_lines: list[str] = field(default_factory=list)
    b_start: int = 0
    b_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "aStart": self.a_start, "aLines": self.a_lines,
            "bStart": self.b_start, "bLines": self.b_lines,
        }


def _read_lines(path: Path) -> tuple[list[str], bool]:
    """读文本行。返回 `(行, 是否被截断)`。编码失败时退回 latin-1 保证不炸。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        try:
            raw = path.read_bytes().decode("latin-1", errors="replace")
        except OSError:
            return [], False
    lines = raw.splitlines()
    truncated = len(lines) > _MAX_LINES
    return [ln[:_MAX_LINE_LEN] for ln in lines[:_MAX_LINES]], truncated


def compare_text(a: Path, b: Path) -> dict[str, Any]:
    """文本/代码 diff。相似度用 `SequenceMatcher.ratio()`，是行级的。"""
    la, ta = _read_lines(a)
    lb, tb = _read_lines(b)
    if not la and not lb:
        return {"kind": "text", "error": "两个文件都读不出文本内容"}

    sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
    hunks: list[DiffHunk] = []
    same_lines = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            same_lines += i2 - i1
            continue
        if len(hunks) < _MAX_HUNKS:
            hunks.append(DiffHunk(
                tag=tag, a_start=i1 + 1, a_lines=la[i1:i2][:40],
                b_start=j1 + 1, b_lines=lb[j1:j2][:40],
            ))

    added = sum(len(h.b_lines) for h in hunks if h.tag in ("insert", "replace"))
    removed = sum(len(h.a_lines) for h in hunks if h.tag in ("delete", "replace"))
    ratio = sm.ratio()

    notes: list[str] = []
    if ta or tb:
        notes.append(f"文件太长，**只比了前 {_MAX_LINES} 行**，后面的没看")
    if len(hunks) >= _MAX_HUNKS:
        notes.append(f"差异块超过 {_MAX_HUNKS} 处，只列了前面这些")

    return {
        "kind": "text",
        "similarity": round(ratio, 4),
        "aLines": len(la), "bLines": len(lb),
        "sameLines": same_lines, "added": added, "removed": removed,
        "hunks": [h.to_dict() for h in hunks],
        "truncated": bool(ta or tb),
        "verdict": (
            "内容完全相同" if ratio >= 0.9999 else
            f"约 {ratio:.0%} 的行相同，改了 {len(hunks)} 处"
        ),
        "note": "；".join(notes),
    }


def _dhash(path: Path, size: int = 8) -> int | None:
    """
    差值哈希。**不用均值哈希（aHash）** —— aHash 对整体亮度变化极其敏感，
    同一张图调一下曝光就判成两张，而那恰恰是最常见的"不同版本"。
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            g = im.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
            px = list(g.getdata())
    except (OSError, ValueError):
        return None
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def compare_images(a: Path, b: Path) -> dict[str, Any]:
    """图片比对：感知哈希距离 + 尺寸/体积/格式差异。"""
    ha, hb = _dhash(a), _dhash(b)
    info: dict[str, Any] = {"kind": "image"}

    try:
        from PIL import Image
        with Image.open(a) as ia, Image.open(b) as ib:
            info["aSize"] = list(ia.size)
            info["bSize"] = list(ib.size)
            info["aFormat"] = ia.format
            info["bFormat"] = ib.format
    except (ImportError, OSError, ValueError):
        pass

    info["aBytes"] = a.stat().st_size if a.exists() else 0
    info["bBytes"] = b.stat().st_size if b.exists() else 0

    if ha is None or hb is None:
        info["error"] = "读不出图片（缺 Pillow 或文件损坏），只能比文件体积"
        info["verdict"] = "无法比对图像内容"
        return info

    d = _hamming(ha, hb)
    info["distance"] = d
    info["identicalBytes"] = _sha1(a) == _sha1(b)

    if info["identicalBytes"]:
        info["verdict"] = "两个文件字节完全相同，是同一个文件的副本"
    elif d <= _PHASH_SAME:
        same_px = info.get("aSize") == info.get("bSize")
        info["verdict"] = (
            "同一张图的不同版本" +
            ("（尺寸也相同，差别可能只是压缩质量）" if same_px else "（尺寸不同，被缩放过）")
        )
    elif d <= _PHASH_RELATED:
        info["verdict"] = f"明显相关但不完全一样（距离 {d}）—— 可能被裁剪、加了水印或调过色"
    else:
        info["verdict"] = f"是两张不同的图（距离 {d}）"

    info["note"] = ("距离是感知哈希的汉明距离，0 表示视觉上一致。"
                    "**它看的是构图和明暗分布，看不出局部的小改动** —— "
                    "改掉照片里一个小物件，距离可能仍然是 0")
    return info


def _sha1(p: Path) -> str:
    h = hashlib.sha1()
    try:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def compare_videos(
    a_scenes: list[Any], b_scenes: list[Any], *, threshold: int = 8
) -> dict[str, Any]:
    """
    视频比对 —— 找**两段视频里重复的片段及时间区间**。

    输入是两边已经入库的场景列表（带 `keyframe_path`）。
    对每个关键帧算 dHash，然后做序列匹配：连续 ≥2 帧配上就算一个重复段。

    🔴 **要求连续 2 帧不是 1 帧**：单帧撞车在同题材视频里非常常见
    （都是黑屏、都是白底 PPT），按单帧算会报出一堆假的"重复片段"。
    """
    def frames(scenes: list[Any]) -> list[tuple[float, float, int]]:
        out: list[tuple[float, float, int]] = []
        for s in scenes:
            kf = s.get("keyframePath") if isinstance(s, dict) else getattr(s, "keyframe_path", "")
            if not kf:
                continue
            h = _dhash(Path(kf))
            if h is None:
                continue
            st = s.get("startSec") if isinstance(s, dict) else getattr(s, "start_sec", 0.0)
            en = s.get("endSec") if isinstance(s, dict) else getattr(s, "end_sec", 0.0)
            out.append((float(st or 0), float(en or 0), h))
        return out

    fa, fb = frames(a_scenes), frames(b_scenes)
    if not fa or not fb:
        return {"kind": "video", "segments": [], "error":
                "至少有一边没有可用的关键帧（视频还没分析完，或分析时没抽到帧）"}

    matches: list[tuple[int, int]] = []
    for i, (_sa, _ea, ha) in enumerate(fa):
        best: tuple[int, int] | None = None
        for j, (_sb, _eb, hb) in enumerate(fb):
            d = _hamming(ha, hb)
            if d <= threshold and (best is None or d < best[1]):
                best = (j, d)
        if best is not None:
            matches.append((i, best[0]))

    # 把连续的配对聚成段
    segments: list[dict[str, Any]] = []
    run: list[tuple[int, int]] = []

    def flush() -> None:
        if len(run) >= 2:
            ia0, ib0 = run[0]
            ia1, ib1 = run[-1]
            segments.append({
                "aStartSec": round(fa[ia0][0], 2), "aEndSec": round(fa[ia1][1], 2),
                "bStartSec": round(fb[ib0][0], 2), "bEndSec": round(fb[ib1][1], 2),
                "frames": len(run),
                "aTimecode": _tc(fa[ia0][0]), "bTimecode": _tc(fb[ib0][0]),
            })
        run.clear()

    for k, (i, j) in enumerate(matches):
        if run and (i == run[-1][0] + 1 and j == run[-1][1] + 1):
            run.append((i, j))
        else:
            flush()
            run.append((i, j))
    flush()

    dup_a = sum(s["aEndSec"] - s["aStartSec"] for s in segments)
    total_a = fa[-1][1] if fa else 0.0
    return {
        "kind": "video",
        "segments": segments[:60],
        "matchedFrames": len(matches),
        "aFrames": len(fa), "bFrames": len(fb),
        "duplicateSeconds": round(dup_a, 1),
        "duplicateRatio": round(dup_a / total_a, 3) if total_a else 0.0,
        "verdict": (
            f"找到 {len(segments)} 段重复内容，累计约 {int(dup_a)} 秒"
            if segments else "没有找到连续重复的片段"
        ),
        "note": "比的是关键帧的视觉相似度，**同样的画面配不同的旁白也会算重复** —— "
                "它看不出声音",
    }


def _tc(sec: float) -> str:
    s = int(max(0, sec))
    m, ss = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m}:{ss:02d}"


#: 各类型的扩展名，用来自动选比法
_TEXTISH = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml",
            ".yml", ".xml", ".html", ".css", ".c", ".cpp", ".h", ".java", ".go",
            ".rs", ".sh", ".ps1", ".sql", ".csv", ".tsv", ".ini", ".toml", ".log"}
_IMAGEISH = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
_VIDEOISH = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"}


def compare_files(a: str | Path, b: str | Path) -> dict[str, Any]:
    """
    A5 主入口 —— 自动判类型并选比法。

    **两边类型不同时不硬比**，直接说"这两个没法比" ——
    硬把一张图和一个 txt 做行级 diff 会得到一个 0% 相似度，
    那个数字看起来像结论，其实毫无意义。
    """
    pa, pb = Path(a), Path(b)
    if not pa.exists() or not pb.exists():
        return {"error": f"文件不存在：{pa if not pa.exists() else pb}"}

    ea, eb = pa.suffix.lower(), pb.suffix.lower()

    def kind(e: str) -> str:
        if e in _IMAGEISH:
            return "image"
        if e in _VIDEOISH:
            return "video"
        if e in _TEXTISH or e == ".pdf":
            return "text"
        return "binary"

    ka, kb = kind(ea), kind(eb)
    if ka != kb:
        return {
            "error": f"一个是{ka}、一个是{kb}，这两类没法直接比",
            "aKind": ka, "bKind": kb,
        }

    if ka == "image":
        return {**compare_images(pa, pb), "a": str(pa), "b": str(pb)}
    if ka == "video":
        return {
            "kind": "video", "a": str(pa), "b": str(pb),
            "error": "视频比对要先让两个视频都完成场景分析，"
                     "然后调 compare_videos() 传两边的场景列表",
        }
    if ka == "binary":
        same = _sha1(pa) == _sha1(pb)
        return {
            "kind": "binary", "a": str(pa), "b": str(pb),
            "identical": same,
            "aBytes": pa.stat().st_size, "bBytes": pb.stat().st_size,
            "verdict": "字节完全相同" if same else "内容不同（这类文件只能比到字节层面）",
        }
    return {**compare_text(pa, pb), "a": str(pa), "b": str(pb)}
