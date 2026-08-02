"""
视频分析实测：场景切分 / 关键帧 / 语音转写 / 时间戳精度 / 速度。

素材由 tests/make_video_fixture.ps1 生成（已知切换点 + 已知台词），
所以每一项都有标准答案可对，不是"看着差不多"。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)

from synorive.analyze.transcribe import Transcriber  # noqa: E402
from synorive.analyze.video import (  # noqa: E402
    analyze_video,
    build_scenes,
    detect_scenes,
    find_tool,
    probe,
    _parse_probe,
)
from synorive.doctor.service import Doctor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_DIR = ROOT / "data" / "models"
WORK = Path(tempfile.gettempdir()) / "synorive_videotest"
VIDEO = WORK / "test_video.mp4"
TRUTH_CUTS = [5.0, 10.0, 15.0, 20.0]

failures: list[str] = []


def main() -> int:
    if not VIDEO.exists():
        print(f"素材不存在：{VIDEO}")
        print("先跑：pwsh -File tests/make_video_fixture.ps1")
        return 2

    truth_lines = (WORK / "truth.txt").read_text(encoding="utf-8").strip().splitlines()

    print("=" * 76)
    print("① ffprobe 基本信息")
    print("=" * 76)
    d, w, h, fps, has_audio, codec = _parse_probe(probe(VIDEO))
    print(f"  {w}x{h}  {fps}fps  {d:.1f}s  编码 {codec}  有音轨 {has_audio}")
    if d <= 0:
        failures.append("读不出时长")
    if not has_audio:
        failures.append("测试素材没有音轨，ASR 测不了")

    print()
    print("=" * 76)
    print("② 场景切分 —— 真实切换点在 5 / 10 / 15 / 20 秒")
    print("=" * 76)
    t0 = time.perf_counter()
    cuts = detect_scenes(VIDEO, d)
    dt = time.perf_counter() - t0
    print(f"  检测耗时 {dt:.2f}s（{d / max(dt, 1e-6):.1f} 倍速）")
    print(f"  检出切换点：{[round(c, 2) for c in cuts]}")

    # 每个真实切换点附近 0.5 秒内要有检出
    for truth in TRUTH_CUTS:
        near = [c for c in cuts if abs(c - truth) <= 0.5]
        mark = "✓" if near else "✗"
        print(f"    {mark} {truth}s → {[round(x, 2) for x in near] or '漏检'}")
        if not near:
            failures.append(f"漏检切换点 {truth}s")
    extra = [c for c in cuts if all(abs(c - t) > 0.5 for t in TRUTH_CUTS)]
    if extra:
        print(f"    多检出 {len(extra)} 个：{[round(x, 2) for x in extra]}")
        if len(extra) > 2:
            failures.append(f"误检 {len(extra)} 个切换点")

    scenes = build_scenes(cuts, d)
    print(f"  → 切成 {len(scenes)} 个场景：" +
          "  ".join(f"[{s.start_sec:.1f}-{s.end_sec:.1f}]" for s in scenes))
    if not (4 <= len(scenes) <= 7):
        failures.append(f"场景数 {len(scenes)}，期望 5 个左右")

    print()
    print("=" * 76)
    print("③ 装语音模型")
    print("=" * 76)
    doc = Doctor(MODEL_DIR, on_status=lambda e: None)
    for dep in ("vad", "asr-zh"):
        t0 = time.perf_counter()
        r = asyncio.run(doc.install(dep))
        print(f"  {dep:8} ok={r.get('ok')}  {time.perf_counter() - t0:.1f}s  "
              f"{r.get('error') or ''}")
        if not r.get("ok"):
            failures.append(f"{dep} 装不上：{r.get('error')}")

    print()
    print("=" * 76)
    print("④ 完整分析（场景 + 关键帧 + 转写）")
    print("=" * 76)
    tr = Transcriber(MODEL_DIR / "sense-voice", MODEL_DIR / "vad")
    print(f"  ASR 可用={tr.available()}　VAD 可用={tr.vad_available()}")

    thumbs = WORK / "thumbs"
    t0 = time.perf_counter()
    res = analyze_video(VIDEO, thumb_dir=thumbs, item_id="testvid", transcriber=tr)
    dt = time.perf_counter() - t0
    speed = res.duration_sec / max(dt, 1e-6)
    print(f"  总耗时 {dt:.1f}s，视频 {res.duration_sec:.1f}s → **{speed:.2f} 倍速**"
          f"（A8 要求 ≥6）")
    print(f"  场景 {len(res.scenes)} 个，关键帧 "
          f"{sum(1 for s in res.scenes if s.keyframe_path)} 张")
    print(f"  转写 {len(res.transcript)} 句")
    if res.warnings:
        print(f"  警告：{res.warnings}")

    kf = sum(1 for s in res.scenes if s.keyframe_path)
    if kf < len(res.scenes):
        failures.append(f"只抽到 {kf}/{len(res.scenes)} 张关键帧")
    for s in res.scenes:
        if s.keyframe_path:
            p = thumbs / s.keyframe_path
            if not p.exists() or p.stat().st_size < 500:
                failures.append(f"关键帧 {s.keyframe_path} 是空的")
                break

    print()
    print("=" * 76)
    print("⑤ 转写内容与时间戳")
    print("=" * 76)
    print("  标准答案：")
    for i, t in enumerate(truth_lines):
        print(f"    {i + 1}. {t}")
    print("  实际转写：")
    for u in res.transcript:
        print(f"    [{u.start_sec:5.1f}-{u.end_sec:5.1f}] {u.text}")

    if not res.transcript:
        failures.append("一句都没转写出来")
    else:
        # 字符覆盖率：标准答案里的字有多少被识别出来了
        got = "".join(u.text for u in res.transcript)
        truth = "".join(truth_lines)
        clean_truth = "".join(ch for ch in truth if "一" <= ch <= "鿿")
        hit = sum(1 for ch in clean_truth if ch in got)
        cov = hit / max(len(clean_truth), 1)
        print(f"\n  汉字覆盖率 {hit}/{len(clean_truth)} = {cov * 100:.1f}%")
        if cov < 0.75:
            failures.append(f"转写汉字覆盖率仅 {cov * 100:.0f}%")

        # 时间戳：每句应该落在它对应的那 4~5 秒窗口里
        print("\n  时间戳合理性（每句该落在自己那一段里）：")
        for i, u in enumerate(res.transcript[: len(truth_lines)]):
            expect_lo = i * (res.duration_sec / len(truth_lines)) - 3
            expect_hi = (i + 1) * (res.duration_sec / len(truth_lines)) + 3
            ok = expect_lo <= u.start_sec <= expect_hi
            print(f"    {'✓' if ok else '✗'} 第{i + 1}句 起于 {u.start_sec:.1f}s "
                  f"（合理区间 {max(0, expect_lo):.1f}~{expect_hi:.1f}s）")
            if not ok:
                failures.append(f"第{i + 1}句时间戳 {u.start_sec:.1f}s 不合理")

    print()
    print("=" * 76)
    print("⑥ 场景与台词的对应（E2 片段级定位的地基）")
    print("=" * 76)
    for s in res.scenes:
        txt = s.transcript[:40] if s.transcript else "（无台词）"
        print(f"  [{s.start_sec:5.1f}-{s.end_sec:5.1f}] {s.keyframe_path or '无帧':24} {txt}")
    with_text = sum(1 for s in res.scenes if s.transcript)
    if with_text == 0:
        failures.append("没有任何场景挂上台词，片段级定位不成立")

    print()
    print("=" * 76)
    if failures:
        for x in failures:
            print(f"✗ {x}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
