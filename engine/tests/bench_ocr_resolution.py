"""
OCR 分辨率扫描：压到多大是速度与准确率的甜点？

OCR_MAX_SIDE 原来拍的是 1600，没有依据。这里量出来再定。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synorive.analyze.image import OcrEngine  # noqa: E402

FONT = r"C:\Windows\Fonts\simsun.ttc"
WORK = Path(tempfile.gettempdir()) / "synorive_ocrbench"
WORK.mkdir(parents=True, exist_ok=True)

# 三种典型场景，字号从大到小
SCENES = [
    ("大字截图（网页正文）", 1920, 1080, 34, 6),
    ("中字截图（IDE / 表格）", 1600, 900, 22, 10),
    ("小字截图（聊天记录）", 1280, 720, 15, 14),
]
LINE = "Synorive 中文分词与向量检索 2026 年 8 月 2 日 预算 12000 元"


def make(w: int, h: int, size: int, rows: int, path: Path) -> str:
    im = Image.new("RGB", (w, h), (250, 249, 246))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONT, size)
    for i in range(rows):
        d.text((40, 30 + i * int(size * 2.1)), LINE, font=f, fill=(31, 41, 51))
    im.save(path)
    return "".join((LINE * rows).split())


def coverage(truth: str, got: str) -> float:
    g = "".join(got.split())
    if not truth:
        return 0.0
    # 逐字符看有没有被识别出来（顺序无关，够用）
    from collections import Counter

    tc, gc = Counter(truth), Counter(g)
    hit = sum(min(n, gc.get(ch, 0)) for ch, n in tc.items())
    return hit / sum(tc.values())


def main() -> int:
    import synorive.analyze.image as img_mod

    ocr = OcrEngine()
    if not ocr.available:
        print("OCR 不可用，跳过")
        return 0

    fixtures = []
    for label, w, h, size, rows in SCENES:
        p = WORK / f"{w}x{h}_{size}px.png"
        truth = make(w, h, size, rows, p)
        fixtures.append((label, p, truth))

    # 预热
    ocr.read(Image.open(fixtures[0][1]))

    print(f"{'场景':<24}{'压到长边':>10}{'耗时ms':>9}{'行数':>6}{'字符覆盖率':>11}")
    print("-" * 62)

    best: dict[int, list[float]] = {}
    for label, path, truth in fixtures:
        im = Image.open(path)
        for side in (640, 960, 1280, 1600, 2000):
            img_mod.OCR_MAX_SIDE = side
            t0 = time.perf_counter()
            lines = ocr.read(im)
            dt = (time.perf_counter() - t0) * 1000
            cov = coverage(truth, "".join(x.text for x in lines))
            print(f"{label:<24}{side:>10}{dt:>9.0f}{len(lines):>6}{cov * 100:>10.1f}%")
            best.setdefault(side, []).append(cov)
        print()

    print("-" * 62)
    print(f"{'长边':>8}{'平均覆盖率':>12}")
    for side, covs in sorted(best.items()):
        print(f"{side:>8}{sum(covs) / len(covs) * 100:>11.1f}%")

    # 没文字的图跑 OCR 要多久（决定要不要加"跳过"的闸门）
    rng = np.random.default_rng(3)
    g = np.zeros((900, 1200, 3), dtype=np.float32)
    for y in range(900):
        g[y, :, :] = (40 + y * 0.15, 90 + y * 0.08, 160 - y * 0.05)
    g += rng.normal(0, 9, g.shape)
    photo = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8))
    print()
    print("无文字照片跑 OCR 的开销（决定要不要加跳过闸门）：")
    for side in (640, 960, 1600):
        img_mod.OCR_MAX_SIDE = side
        t0 = time.perf_counter()
        for _ in range(3):
            ocr.read(photo)
        print(f"  长边 {side:>5}：{(time.perf_counter() - t0) / 3 * 1000:6.0f} ms/张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
