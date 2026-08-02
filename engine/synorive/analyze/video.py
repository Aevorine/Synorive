"""
视频分析 —— C14 场景切分 + 关键帧 + 语音转写，支撑 E2 片段级定位
====================================================================
「搜到视频的第 3 分 24 秒」是这个项目最有差异化的能力。别的工具最多
告诉你"在这个视频里"，你还得自己拖进度条找。

做法：
  ① ffmpeg 的 scdet 滤镜找场景切换点（纯计算，不需要模型）
  ② 每个场景抽一张关键帧 → 存缩略图 + 算 CLIP 向量
     → 「用一张图找视频里的相似镜头」直接成立
  ③ 抽音轨 → ASR 转写（带时间戳）→ 每句话是一个带时间的可检索片段
     → 「搜一句台词跳到那一秒」直接成立

为什么用 ffmpeg 而不是 PyAV 做场景检测：
  scdet 滤镜是 ffmpeg 内部实现的，一次解码就能出全部切换点；
  PyAV 要把帧一张张读进 Python 再比较，中间的 Python 层开销是数量级的差距。
  实测一个 10 分钟视频，ffmpeg scdet 约 20 秒，PyAV 逐帧要好几分钟。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("synorive.video")

SUPPORTED_VIDEO_EXT = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mpg", ".mpeg",
}
SUPPORTED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}

#: 场景切换阈值，**直接就是 ffmpeg scdet 的 threshold（百分比 0~100）**，
#: 不要再乘 100 —— 第一版乘了 100 传进去变成 30，一个切换点都检不出来。
#:
#: 取 6 是量出来的。测试素材有 4 个硬切（纯色整帧变化），实测各自得分：
#:     4s → 15.587    8s → 8.957    12s → 19.065    16s → 14.048
#: ffmpeg 默认阈值是 10，**正好漏掉 8.957 那个**。阈值 ≤8 才全检出。
#:
#: 为什么宁可偏低：对**检索**来说过分切分的害处远小于漏切 ——
#: 多切几段只是多几张关键帧（有 MIN_SCENE_SEC 合并和 MAX_SCENES 封顶兜着），
#: 而漏掉一个切换点意味着那一段永远跳不过去。
SCENE_THRESHOLD = 6.0

#: 场景太短就并进相邻的 —— 1 秒的片段做不了检索单元，
#: 缩略图也看不清是什么。
MIN_SCENE_SEC = 2.0

#: 一个视频最多切多少段。超长视频（几小时的录屏）切出上千段
#: 只会让库爆炸，且没人会去看第 800 个片段。
MAX_SCENES = 240

#: 关键帧缩略图的长边
THUMB_SIZE = 320


@dataclass
class Scene:
    index: int
    start_sec: float
    end_sec: float
    keyframe_path: str | None = None
    transcript: str = ""

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec


@dataclass
class TranscriptSegment:
    start_sec: float
    end_sec: float
    text: str


@dataclass
class VideoAnalysis:
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    codec: str = ""
    scenes: list[Scene] = field(default_factory=list)
    transcript: list[TranscriptSegment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def is_video(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_VIDEO_EXT


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_AUDIO_EXT


# ── 找 ffmpeg ───────────────────────────────────────────────

_FFMPEG_CACHE: dict[str, str | None] = {}


def find_tool(name: str) -> str | None:
    """
    找 ffmpeg / ffprobe。

    除了 PATH，还要找这台机器上的自定义安装位置 ——
    实测本机装在 D:\\Files\\VideoEditing\\ffmpeg\\bin，不在 PATH 里，
    只查 PATH 会误判成"没装"。
    """
    if name in _FFMPEG_CACHE:
        return _FFMPEG_CACHE[name]

    exe = shutil.which(name)
    if not exe:
        for base in (
            r"D:\Files\VideoEditing\ffmpeg\bin",
            r"D:\APPS\ffmpeg\bin",
            r"C:\ffmpeg\bin",
        ):
            p = Path(base) / f"{name}.exe"
            if p.exists():
                exe = str(p)
                break
    _FFMPEG_CACHE[name] = exe
    return exe


def _run(cmd: list[str], timeout: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


# ── 探测基本信息 ────────────────────────────────────────────


def probe(path: Path) -> dict[str, Any]:
    """用 ffprobe 拿时长、分辨率、帧率、有没有音轨。"""
    exe = find_tool("ffprobe")
    if not exe:
        return {}
    r = _run([
        exe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ], timeout=60)
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}


def _parse_probe(data: dict[str, Any]) -> tuple[float, int, int, float, bool, str]:
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0) or 0)
    w = h = 0
    fps = 0.0
    codec = ""
    has_audio = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and w == 0:
            w = int(s.get("width", 0) or 0)
            h = int(s.get("height", 0) or 0)
            codec = str(s.get("codec_name", "") or "")
            rate = str(s.get("avg_frame_rate", "0/1") or "0/1")
            if "/" in rate:
                a, b = rate.split("/", 1)
                try:
                    fps = round(float(a) / float(b), 3) if float(b) else 0.0
                except (ValueError, ZeroDivisionError):
                    fps = 0.0
            if duration == 0:
                duration = float(s.get("duration", 0) or 0)
        elif s.get("codec_type") == "audio":
            has_audio = True
    return duration, w, h, fps, has_audio, codec


# ── ① 场景切分 ──────────────────────────────────────────────

#: scdet 打的是 `lavfi.scd.time: 4`，**不是 `pts_time`**。
#: 第一版按 pts_time 匹配（那是 showinfo 滤镜的字段），
#: 结果一个都匹配不到，而且不报错 —— 表现成"这个视频只有一个场景"。
_SCD_TIME = re.compile(r"lavfi\.scd\.time:\s*([0-9.]+)")
#: 回退方案用的是 showinfo 的字段
_PTS_TIME = re.compile(r"pts_time:([0-9.]+)")


def detect_scenes(path: Path, duration: float, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """
    返回场景切换的时间点（秒）。

    用 `-vf scdet` + `-f null` 只解码不输出，切换点从 stderr 的日志里读。
    为了提速把画面缩到 320 宽再检测 —— 实测缩放**不影响检出结果**
    （同一组阈值下检出的时间点完全一样），但解码和比较的开销小一个数量级。
    """
    exe = find_tool("ffmpeg")
    if not exe:
        return []

    r = _run([
        exe, "-hide_banner", "-nostats",
        "-i", str(path),
        "-vf", f"scale=320:-2,scdet=threshold={threshold:g}",
        "-an", "-f", "null", "-",
    ], timeout=1800)

    times = sorted({float(m) for m in _SCD_TIME.findall(r.stderr or "")})
    if times:
        return [t for t in times if 0 < t < duration]

    # 回退：老版本 ffmpeg 可能没有 scdet，用经典的 select+showinfo。
    # scdet 的 threshold 是 0~100，select 里的 scene 是 0~1，换算一下。
    log.debug("scdet 没有输出，回退到 select+showinfo")
    r2 = _run([
        exe, "-hide_banner", "-nostats",
        "-i", str(path),
        "-vf", f"scale=320:-2,select='gt(scene,{threshold / 100:.3f})',showinfo",
        "-an", "-f", "null", "-",
    ], timeout=1800)
    times = sorted({float(m) for m in _PTS_TIME.findall(r2.stderr or "")})
    return [t for t in times if 0 < t < duration]


def build_scenes(cuts: list[float], duration: float) -> list[Scene]:
    """切换点 → 场景区间。过短的并进相邻，过多的按时长均分兜底。"""
    if duration <= 0:
        return []

    bounds = [0.0, *cuts, duration]
    scenes: list[Scene] = []
    start = bounds[0]
    for end in bounds[1:]:
        if end - start < MIN_SCENE_SEC:
            continue  # 太短，并进下一段（start 不动）
        scenes.append(Scene(index=len(scenes), start_sec=round(start, 2), end_sec=round(end, 2)))
        start = end
    if start < duration - 0.5:
        if scenes:
            scenes[-1].end_sec = round(duration, 2)
        else:
            scenes.append(Scene(index=0, start_sec=0.0, end_sec=round(duration, 2)))

    # 一个切换点都没有（静态录屏、单镜头）→ 按固定间隔切，
    # 否则整个视频只有一段，片段级定位就废了
    if len(scenes) <= 1 and duration > 30:
        step = max(MIN_SCENE_SEC, duration / min(MAX_SCENES, max(2, int(duration // 15))))
        scenes = []
        t = 0.0
        while t < duration and len(scenes) < MAX_SCENES:
            scenes.append(
                Scene(index=len(scenes), start_sec=round(t, 2),
                      end_sec=round(min(t + step, duration), 2))
            )
            t += step

    if len(scenes) > MAX_SCENES:
        # 太多就等距抽稀，保留首尾
        keep = max(1, len(scenes) // MAX_SCENES + 1)
        scenes = scenes[::keep][:MAX_SCENES]
        for i, s in enumerate(scenes):
            s.index = i
    return scenes


# ── ② 关键帧 ────────────────────────────────────────────────


def extract_keyframes(path: Path, scenes: list[Scene], out_dir: Path, item_id: str) -> int:
    """
    每个场景抽一张关键帧存成 jpg。取场景**中点**而不是起点：
    起点常常正好是转场的黑帧或叠化的中间态，抽出来一片黑。
    """
    exe = find_tool("ffmpeg")
    if not exe or not scenes:
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    ok = 0
    for s in scenes:
        mid = s.start_sec + s.duration / 2
        dst = out_dir / f"{item_id}_{s.index:04d}.jpg"
        r = _run([
            exe, "-hide_banner", "-nostats", "-loglevel", "error",
            "-ss", f"{mid:.2f}", "-i", str(path),
            "-frames:v", "1",
            "-vf", f"scale={THUMB_SIZE}:-2",
            "-q:v", "4", "-y", str(dst),
        ], timeout=120)
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            s.keyframe_path = dst.name
            ok += 1
    return ok


# ── ③ 音轨 ──────────────────────────────────────────────────


def extract_audio(path: Path, out_wav: Path) -> bool:
    """
    抽成 16kHz 单声道 wav —— 所有主流 ASR 模型都要这个格式。
    不转的话模型内部也要转一次，还不如一次到位。
    """
    exe = find_tool("ffmpeg")
    if not exe:
        return False
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    r = _run([
        exe, "-hide_banner", "-nostats", "-loglevel", "error",
        "-i", str(path), "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-y", str(out_wav),
    ], timeout=1800)
    return r.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 1000


# ── 总入口 ──────────────────────────────────────────────────


def analyze_video(
    path: Path,
    *,
    thumb_dir: Path,
    item_id: str,
    transcriber: Any | None = None,
    want_scenes: bool = True,
) -> VideoAnalysis:
    """
    分析一个视频。各阶段独立可失败。

    transcriber 传 None 就跳过语音转写 —— 转写慢得多，
    实际流水线里它和 OCR 一样走延后补跑。
    """
    res = VideoAnalysis()

    if not find_tool("ffmpeg"):
        res.warnings.append("找不到 ffmpeg，视频只能靠文件名和路径搜")
        return res

    data = probe(path)
    if not data:
        res.warnings.append("ffprobe 读不出信息，文件可能损坏")
        return res
    res.duration_sec, res.width, res.height, res.fps, res.has_audio, res.codec = _parse_probe(data)

    if res.duration_sec <= 0:
        res.warnings.append("时长为 0，不是有效视频")
        return res

    if want_scenes:
        try:
            cuts = detect_scenes(path, res.duration_sec)
            res.scenes = build_scenes(cuts, res.duration_sec)
            log.info("%s：%.1fs → %d 个场景（%d 个切换点）",
                     path.name, res.duration_sec, len(res.scenes), len(cuts))
        except subprocess.TimeoutExpired:
            res.warnings.append("场景检测超时，按固定间隔切分")
            res.scenes = build_scenes([], res.duration_sec)
        except Exception as e:  # noqa: BLE001
            res.warnings.append(f"场景检测失败：{e}")
            res.scenes = build_scenes([], res.duration_sec)

        if res.scenes:
            try:
                n = extract_keyframes(path, res.scenes, thumb_dir, item_id)
                if n < len(res.scenes):
                    res.warnings.append(f"{len(res.scenes) - n} 个场景没抽到关键帧")
            except Exception as e:  # noqa: BLE001
                res.warnings.append(f"抽关键帧失败：{e}")

    if transcriber is not None and res.has_audio:
        with tempfile.TemporaryDirectory(prefix="synorive_av_") as td:
            wav = Path(td) / "audio.wav"
            if extract_audio(path, wav):
                try:
                    res.transcript = transcriber.transcribe(wav)
                    _attach_transcript(res)
                except Exception as e:  # noqa: BLE001
                    res.warnings.append(f"语音转写失败：{e}")
            else:
                res.warnings.append("抽音轨失败")

    return res


def _attach_transcript(res: VideoAnalysis) -> None:
    """把转写句按时间落到对应的场景上，这样一段场景既有画面也有台词。"""
    if not res.scenes or not res.transcript:
        return
    for seg in res.transcript:
        mid = (seg.start_sec + seg.end_sec) / 2
        for s in res.scenes:
            if s.start_sec <= mid < s.end_sec:
                s.transcript = (s.transcript + " " + seg.text).strip()
                break
