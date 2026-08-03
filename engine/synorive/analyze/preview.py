"""
A2 —— 视频「先看后搜」秒开预览
============================================================

**要治的病**：拖一个陌生视频进来，用户的第一个问题不是"搜什么"，
而是"**这里面到底是什么**"。而完整分析（场景检测 + 关键帧 + CLIP 向量
+ 转写）要几十秒到几分钟 —— 那几十秒里界面上什么都没有，
用户只能盯着一个转圈。

所以这里走一条**完全独立于摄取管线**的快速通道，目标 1 秒内出两样东西：
  ① 一条等距缩略带（先给"大概长什么样"）
  ② 一条语音波形（先给"哪里有人说话、哪里是空的"）

🔴 **这条通道不入库、不算向量、不写任何数据库记录。**
它是"看一眼"，不是"分析"。混进摄取管线会让一次随手预览
在库里留下半成品记录，而半成品记录在搜索结果里和真记录长得一模一样。

🔴 **缩略图不是场景切分的结果，是等距抽的。**
真正的场景切分要完整解码一遍（10 分钟视频约 20 秒），一秒内做不到。
等距抽帧靠关键帧 seek，每帧几十毫秒。**这个差别必须告诉用户** ——
否则他会以为这 12 格就是这个视频的 12 个镜头，然后奇怪为什么
正式分析出来的镜头数对不上。`note` 字段就是干这个的。
"""

from __future__ import annotations

import base64
import subprocess
import time
from pathlib import Path
from typing import Any

from .video import _parse_probe, find_tool, is_audio, is_video, probe

#: 缩略带抽几格。12 格在一屏内看得清，再多每格就成邮票了
THUMB_COUNT = 12
#: 每格宽度。160px 是"看得出是什么"和"12 张加起来还够小"的折中
THUMB_WIDTH = 160
#: 波形取多少个采样点。屏幕上一条带子撑死几百像素，取再多是浪费
WAVE_POINTS = 240

#: 单帧抽取超时。正常几十毫秒，超过 3 秒说明这个文件有问题（损坏 / 网络盘）
_FRAME_TIMEOUT = 3.0
#: 波形解码超时。**超了就放弃波形但保留缩略带** ——
#: 半个功能好过转圈到天荒地老
_WAVE_TIMEOUT = 6.0


def preview_media(path: Path) -> dict[str, Any]:
    """
    给一个视频/音频文件出秒开预览。**不入库。**

    返回里永远带 `note`：说清这一条给的是什么、不是什么。
    """
    t0 = time.perf_counter()
    if not path.exists():
        return _fail(f"文件不在：{path}")
    if not (is_video(path) or is_audio(path)):
        return _fail(f"这不是视频也不是音频：{path.suffix or '（没有扩展名）'}")
    if find_tool("ffmpeg") is None:
        return _fail("没找到 ffmpeg —— 预览、场景切分、转写全都要它")

    # 🔴 `probe()` 回的是 **ffprobe 的原始 JSON**，不是解析好的字段字典。
    # 直接 `info["duration"]` 永远拿到 0 —— 表现是"每个视频都读不出时长"
    duration, width, height, _fps, has_audio, _codec = _parse_probe(probe(path))
    if duration <= 0:
        return _fail("读不出时长（文件可能损坏，或者只有一个不完整的头）")

    audio_only = is_audio(path) and not is_video(path)
    thumbs, dropped = ([], 0) if audio_only else _grab_thumbs(path, duration)
    wave, wave_note = _waveform(path, duration) if has_audio else ([], "这个文件没有音轨")

    notes: list[str] = []
    if thumbs:
        notes.append(
            f"缩略带是**按时间等距抽的 {len(thumbs)} 格，不是场景切分结果** —— "
            "真正的镜头分割要完整解码一遍，一秒内做不到。正式分析出来的镜头数会和这里不一样"
        )
    if thumbs and dropped:
        notes.append(
            f"有 {dropped} 格没抽出来（超时或解码失败）—— 缩略带比预期稀，不是这个视频镜头少"
        )
    if not thumbs and not audio_only:
        # 🔴 **一格都没抽到不能悄悄回一个空数组。** 界面拿到空列表只会
        # 什么都不画，用户看到的是「点了预览，视频那块是空的」——
        # 分不清是没有画面、还是 ffmpeg 挂了、还是文件坏了
        notes.append(
            "一格缩略图都没抽出来。可能是：视频流损坏、编码 ffmpeg 不认、"
            "或者文件正被别的程序占用（Windows 上很常见）"
        )
    if wave_note:
        notes.append(wave_note)
    elif wave:
        notes.append("波形只反映音量大小，**分不出人声和背景音乐** —— 高的地方不一定有人在说话")
    if audio_only:
        notes.append("纯音频文件，没有画面可抽")

    return {
        "ok": True,
        "path": str(path),
        "durationSec": round(duration, 2),
        "width": width,
        "height": height,
        "hasAudio": has_audio,
        "thumbs": thumbs,
        "waveform": wave,
        "elapsedMs": int((time.perf_counter() - t0) * 1000),
        "note": "；".join(notes) if notes else "预览就绪",
    }


def _grab_thumbs(path: Path, duration: float) -> tuple[list[dict[str, Any]], int]:
    """
    等距抽 THUMB_COUNT 帧，直接回 base64 data URI。

    🔴 **`-ss` 必须放在 `-i` 前面。** 放后面是"解码到那一秒"（精确但慢，
    一个 1 小时的视频抽最后一帧要几十秒）；放前面是"跳到最近的关键帧"
    （最多差个把秒，但恒定几十毫秒）。预览要的是快，差一秒无所谓。

    返回 `(格子列表, 抽失败的帧数)`。

    🔴 **回 data URI 而不是存文件。** 这个文件根本没入库，
    没有 itemId，也就没有任何合法的静态路径能发给界面；
    为它临时造一套文件服务，等于为"看一眼"引入一个要清理的目录。
    """
    exe = find_tool("ffmpeg")
    if exe is None:
        return [], 0
    # 首尾各让开一点：0 秒常是黑场或台标，最后一秒常是渐黑
    span = max(0.0, duration - 1.0)

    # 🔴 **格数要跟着时长缩。** 固定抽 12 格的话，一个 2 秒的短视频
    # 12 个时间点会全挤在同一秒内 —— 抽出 12 张**一模一样**的图，
    # 而且 `round(sec, 2)` 之后 `sec` 值重复，界面拿它当 React key
    # 会直接漏渲染。不报错、不崩溃，就是一条看不懂的缩略带。
    # 至少隔 0.4 秒才算两格，最少 1 格最多 THUMB_COUNT 格。
    count = max(1, min(THUMB_COUNT, int(span / 0.4) + 1))
    step = span / count if count else 0.0

    out: list[dict[str, Any]] = []
    seen_sec: set[float] = set()
    #: 🔴 抽失败的帧数要**数出来**。静默 `continue` 的话，12 格里丢了 11 格
    #: 和"这个视频本来只有 1 个镜头"在界面上长得一模一样 ——
    #: 而前者说明文件有问题、后者完全正常。两件事必须分得开
    failed = 0
    for i in range(count):
        sec = round(min(span, 0.5 + step * i), 2)
        # 再兜一道：浮点算出来仍可能撞到同一个两位小数
        if sec in seen_sec:
            continue
        seen_sec.add(sec)
        try:
            r = subprocess.run(  # noqa: S603
                [
                    exe, "-hide_banner", "-nostats", "-loglevel", "error",
                    "-ss", f"{sec:.2f}", "-i", str(path),
                    "-frames:v", "1",
                    "-vf", f"scale={THUMB_WIDTH}:-2",
                    "-q:v", "6",
                    "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                ],
                capture_output=True,
                timeout=_FRAME_TIMEOUT,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            failed += 1
            continue
        # 🔴 只看 returncode 不够：ffmpeg 抽不到帧时**照样回 0 而输出为空**
        # （典型是 seek 越过了最后一个关键帧）。不查长度的话，
        # 界面上会出现一格永远加载不出来的破图，而日志干干净净
        if r.returncode != 0 or not r.stdout:
            failed += 1
            continue
        out.append({
            "sec": sec,
            "dataUrl": "data:image/jpeg;base64," + base64.b64encode(r.stdout).decode("ascii"),
        })
    # 🔴 失败数**作为第二个返回值**给出去，不要往结果列表里塞一个假条目。
    # 塞假条目的话，界面会拿到一个没有 dataUrl 的"格子"去渲染 <img> ——
    # 一个永远加载不出来的破图，而这正是我们想避免的那种症状
    return out, failed


def _waveform(path: Path, duration: float) -> tuple[list[float], str]:
    """
    出一条 0~1 的音量包络。

    做法：让 ffmpeg 把音轨降成**单声道 1kHz 的 16 位 PCM** 吐到管道，
    然后自己分桶取峰值。1kHz 已经远低于任何听觉需求，但画一条
    几百像素宽的包络绰绰有余 —— 而降采样正是把解码量压下来的关键。

    🔴 **超时就返回空波形 + 一句实话，不抛异常。**
    一个 3 小时的视频这一步会超预算，那时候正确的行为是
    「缩略带照给，波形这块说清楚为什么没有」，而不是整个预览失败。
    """
    exe = find_tool("ffmpeg")
    if exe is None:
        return [], "没有 ffmpeg，波形画不出来"
    try:
        r = subprocess.run(  # noqa: S603
            [
                exe, "-hide_banner", "-nostats", "-loglevel", "error",
                "-i", str(path),
                "-vn", "-ac", "1", "-ar", "1000",
                "-f", "s16le", "-acodec", "pcm_s16le", "-",
            ],
            capture_output=True,
            timeout=_WAVE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        mins = int(duration // 60)
        return [], f"波形没画出来：这个文件太长（约 {mins} 分钟），{_WAVE_TIMEOUT:g} 秒内解不完音轨"
    except OSError as e:
        return [], f"波形没画出来：{e}"

    raw = r.stdout
    if r.returncode != 0 or len(raw) < 4:
        return [], "波形没画出来：这个文件的音轨解不出来（可能没有音轨，或编码不受支持）"

    import array

    samples = array.array("h")
    # 奇数字节会让 frombytes 抛 ValueError —— 管道被截断时真的会发生
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if not samples:
        return [], "波形没画出来：音轨解出来是空的"

    bucket = max(1, len(samples) // WAVE_POINTS)
    peaks: list[float] = []
    for i in range(0, len(samples), bucket):
        chunk = samples[i : i + bucket]
        if not chunk:
            continue
        peaks.append(max(abs(min(chunk)), abs(max(chunk))) / 32768.0)
        if len(peaks) >= WAVE_POINTS:
            break
    # 归一化到峰值 1：绝对音量没有意义（同一段话录进不同设备差十几 dB），
    # 用户要看的是"这一段相对别处是响还是静"
    top = max(peaks) if peaks else 0.0
    if top > 0:
        peaks = [round(p / top, 3) for p in peaks]
    return peaks, ""


def _fail(note: str) -> dict[str, Any]:
    return {
        "ok": False,
        "durationSec": 0.0,
        "thumbs": [],
        "waveform": [],
        "elapsedMs": 0,
        "note": note,
    }
