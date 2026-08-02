"""
语音转写 —— VAD 断句 + SenseVoice 识别，出带时间戳的句子
====================================================================
「搜一句台词跳到那一秒」需要两样东西：**说了什么** 和 **什么时候说的**。

SenseVoice 是非自回归模型（一次前向出全部结果），在 CPU 上比 Whisper 快
好几倍，中文准确率也更高 —— 但代价是它**不输出时间戳**。
所以配一个 2.3MB 的 Silero VAD 先把音频切成一句一句：
时间由 VAD 给，内容由 SenseVoice 给，两者拼起来就是带时间的转写。

这个组合比"用 Whisper 一把梭"划算得多：Whisper 自带时间戳但慢，
而 VAD 只有 2.3MB、耗时可以忽略。
"""

from __future__ import annotations

import logging
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("synorive.asr")

SAMPLE_RATE = 16_000

#: VAD 判定为语音的概率阈值。太低会把空调声当成人说话，
#: 太高会漏掉小声的部分。0.5 是 Silero 的推荐值。
VAD_THRESHOLD = 0.5
#: 一段语音至少这么长才算（秒）—— 短于这个多半是咳嗽、键盘声
VAD_MIN_SPEECH = 0.25
#: 静音超过这么久就断句
VAD_MIN_SILENCE = 0.5
#: 单段最长（秒）。太长的话 SenseVoice 一次要处理的音频过大，
#: 而且一整段几十秒的文字定位精度也没意义。
VAD_MAX_SPEECH = 20.0


@dataclass
class Utterance:
    start_sec: float
    end_sec: float
    text: str
    language: str = ""


def read_wav_mono16k(path: Path) -> np.ndarray:
    """
    读 16kHz 单声道 wav 成 float32 [-1, 1]。

    只支持这一种格式是有意的 —— 抽音轨那一步已经统一转好了。
    在这里再做格式适配等于把同一件事做两遍，而且容易漏掉某种格式。
    """
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1:
            raise ValueError(
                f"音频格式不对：{w.getframerate()}Hz {w.getnchannels()}声道，"
                f"需要 {SAMPLE_RATE}Hz 单声道（抽音轨那一步应该已经转好了）"
            )
        if w.getsampwidth() != 2:
            raise ValueError(f"位深 {w.getsampwidth() * 8} 不是 16 位")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


class Transcriber:
    """
    线程安全的转写器。每个线程一套模型 —— 但实践中转写是单线程后台跑的
    （和 OCR 一样，多线程收益极小而内存翻倍）。
    """

    model_id = "sense-voice"

    def __init__(self, model_dir: Path, vad_dir: Path, threads: int | None = None) -> None:
        self.model_dir = model_dir
        self.vad_dir = vad_dir
        self.threads = threads
        self._local = threading.local()
        self._lock = threading.Lock()

    def available(self) -> bool:
        return (self.model_dir / "model.int8.onnx").exists() and (
            self.model_dir / "tokens.txt"
        ).exists()

    def vad_available(self) -> bool:
        return (self.vad_dir / "silero_vad.onnx").exists()

    # ── 加载 ────────────────────────────────────────────────

    def _recognizer(self) -> Any:
        r = getattr(self._local, "recognizer", None)
        if r is not None:
            return r
        import sherpa_onnx

        from .embedder import physical_cores

        r = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(self.model_dir / "model.int8.onnx"),
            tokens=str(self.model_dir / "tokens.txt"),
            num_threads=self.threads or physical_cores(),
            # 自动判语言 —— 中英混说的视频很常见，写死中文会把英文部分识别成谐音汉字
            language="auto",
            # 加标点。不加的话转写出来是一长串没有断句的字，
            # 分块和阅读都很难受
            use_itn=True,
        )
        self._local.recognizer = r
        return r

    def _vad(self) -> Any:
        v = getattr(self._local, "vad", None)
        if v is not None:
            return v
        import sherpa_onnx

        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(self.vad_dir / "silero_vad.onnx")
        cfg.silero_vad.threshold = VAD_THRESHOLD
        cfg.silero_vad.min_silence_duration = VAD_MIN_SILENCE
        cfg.silero_vad.min_speech_duration = VAD_MIN_SPEECH
        cfg.silero_vad.max_speech_duration = VAD_MAX_SPEECH
        cfg.sample_rate = SAMPLE_RATE
        # 缓冲区按最长语音段算，太小会在长句中间强行截断
        v = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=VAD_MAX_SPEECH + 10)
        self._local.vad = v
        return v

    # ── 转写 ────────────────────────────────────────────────

    def transcribe(self, wav_path: Path) -> list[Utterance]:
        """转写整个音频，返回带时间戳的句子列表。"""
        if not self.available():
            raise FileNotFoundError(f"语音模型缺失：{self.model_dir}（依赖医生里装 asr-zh）")

        samples = read_wav_mono16k(wav_path)
        if samples.size < SAMPLE_RATE // 2:
            return []

        if self.vad_available():
            return self._transcribe_with_vad(samples)

        # 没有 VAD 就整段转写 —— 拿得到文字但没有时间戳，
        # 只能定位到"这个视频里有这句话"，定位不到秒
        log.warning("没有 VAD 模型，整段转写（结果没有时间戳）")
        text = self._recognize(samples)
        if not text:
            return []
        return [Utterance(0.0, len(samples) / SAMPLE_RATE, text)]

    def _transcribe_with_vad(self, samples: np.ndarray) -> list[Utterance]:
        vad = self._vad()
        vad.reset()

        out: list[Utterance] = []
        window = 512  # Silero VAD 的固定窗口
        offset = 0

        def drain() -> None:
            while not vad.empty():
                seg = vad.front
                start = seg.start / SAMPLE_RATE
                dur = len(seg.samples) / SAMPLE_RATE
                text = self._recognize(np.asarray(seg.samples, dtype=np.float32))
                if text:
                    out.append(Utterance(round(start, 2), round(start + dur, 2), text))
                vad.pop()

        while offset < samples.size:
            chunk = samples[offset : offset + window]
            if chunk.size < window:
                chunk = np.pad(chunk, (0, window - chunk.size))
            vad.accept_waveform(chunk)
            offset += window
            drain()

        vad.flush()
        drain()
        return out

    def _recognize(self, samples: np.ndarray) -> str:
        if samples.size < SAMPLE_RATE // 8:  # 短于 0.125 秒，不可能是完整的话
            return ""
        rec = self._recognizer()
        stream = rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        rec.decode_stream(stream)
        text = str(stream.result.text or "").strip()
        # SenseVoice 会在结果里带 <|zh|><|NEUTRAL|> 这类标记，得剥掉
        while "<|" in text and "|>" in text:
            a = text.index("<|")
            b = text.index("|>", a) + 2
            text = text[:a] + text[b:]
        return text.strip()
