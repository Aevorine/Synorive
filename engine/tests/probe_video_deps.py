"""探清楚视频分析要用的东西：ffmpeg 能力 + 中文 ASR 模型可得性。"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

MIRROR = "https://hf-mirror.com"
OFFICIAL = "https://huggingface.co"

ASR_CANDIDATES = [
    ("SenseVoice-small（中文最快）", "FunAudioLLM/SenseVoiceSmall",
     ["model_quant.onnx", "model.onnx", "am.mvn", "chn_jpn_yue_eng_ko_spectok.bpe.model"]),
    ("SenseVoice ONNX 社区导出", "lovemefan/SenseVoice-onnx",
     ["sense-voice-encoder.onnx", "sense-voice-encoder-int8.onnx"]),
    ("Whisper tiny（多语言）", "Xenova/whisper-tiny",
     ["onnx/encoder_model_quantized.onnx", "onnx/decoder_model_merged_quantized.onnx",
      "tokenizer.json", "preprocessor_config.json"]),
    ("Whisper base（多语言）", "Xenova/whisper-base",
     ["onnx/encoder_model_quantized.onnx", "onnx/decoder_model_merged_quantized.onnx"]),
    ("Whisper small（更准）", "Xenova/whisper-small",
     ["onnx/encoder_model_quantized.onnx", "onnx/decoder_model_merged_quantized.onnx"]),
    ("Paraformer 中文（sherpa）", "csukuangfj/sherpa-onnx-paraformer-zh-2023-09-14",
     ["model.int8.onnx", "tokens.txt"]),
]


def probe_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if not exe:
        for guess in (
            r"D:\Files\VideoEditing\ffmpeg\bin\ffmpeg.exe",
            r"D:\APPS\ffmpeg\bin\ffmpeg.exe",
        ):
            if Path(guess).exists():
                exe = guess
                break
    if not exe:
        print("  ✗ 找不到 ffmpeg")
        return None

    r = subprocess.run([exe, "-version"], capture_output=True, text=True, errors="replace")
    ver = (r.stdout or "").splitlines()[0] if r.stdout else "?"
    print(f"  ✓ {exe}")
    print(f"    {ver}")

    # 场景切分靠 scdet/select 滤镜，抽帧靠 fps/thumbnail，都得确认真的编进去了
    r2 = subprocess.run([exe, "-filters"], capture_output=True, text=True, errors="replace")
    filters = r2.stdout or ""
    for f in ("scdet", "select", "thumbnail", "fps", "scale", "showinfo"):
        print(f"    滤镜 {f:10} {'✓' if f' {f} ' in filters else '✗'}")

    r3 = subprocess.run([exe, "-hide_banner", "-decoders"], capture_output=True, text=True,
                        errors="replace")
    dec = r3.stdout or ""
    for d in ("h264", "hevc", "vp9", "av1", "aac", "mp3", "opus"):
        print(f"    解码 {d:10} {'✓' if d in dec else '✗'}")
    return exe


async def head(client: httpx.AsyncClient, repo: str, path: str) -> tuple[bool, str]:
    for base in (MIRROR, OFFICIAL):
        try:
            r = await client.get(f"{base}/{repo}/resolve/main/{path}",
                                 headers={"Range": "bytes=0-0"}, timeout=8.0,
                                 follow_redirects=True)
            if r.status_code in (200, 206):
                cr = r.headers.get("content-range", "")
                s = cr.rsplit("/", 1)[-1] if "/" in cr else "?"
                try:
                    return True, f"{int(s) / 1e6:.1f} MB"
                except ValueError:
                    return True, s
        except Exception:  # noqa: BLE001
            continue
    return False, "404 / 不可达"


async def probe_asr() -> None:
    async with httpx.AsyncClient(headers={"User-Agent": "Synorive/0.1"}) as client:
        for label, repo, files in ASR_CANDIDATES:
            print(f"\n  {label}")
            print(f"    {repo}")
            for f in files:
                ok, info = await head(client, repo, f)
                print(f"      {'✓' if ok else '✗'} {f:52} {info}")


def main() -> int:
    print("=" * 76)
    print("① ffmpeg")
    print("=" * 76)
    probe_ffmpeg()

    print()
    print("=" * 76)
    print("② 中文 ASR 模型可得性")
    print("=" * 76)
    asyncio.run(probe_asr())
    return 0


if __name__ == "__main__":
    sys.exit(main())
