#!/usr/bin/env python
"""
生成 GitHub 社交预览图（Open Graph）—— 1280×640
====================================================================

## 治的什么病

仓库设置里没上传 social preview 时，GitHub 会自动拼一张灰底默认图：
一个头像 + 仓库名 + 描述前几十个字。发到 X / Reddit / HN / 微博 时，
别人在信息流里先看到的就是那张图 —— **它决定了点不点进来**，
而默认图和其他几千万个仓库长得一模一样。

这是曝光链路上单项收益最大、又最容易被跳过的一步：
它不影响任何功能，所以永远排不进待办。

## 尺寸为什么是 1280×640

GitHub 官方要求 1280×640（2:1），上限 1 MB。
各家社交平台在信息流里会裁成不同比例，所以：

🔴 **关键信息必须离边缘足够远。** 这里留了 96px 的安全边距，
最窄的那种 1.91:1 裁法也不会把字切掉。踩过的教训是"设计稿上看着挺好，
发出去标题少了半个字" —— 而那时候图已经传播出去了。

## 用法

    engine/.venv/Scripts/python.exe scripts/build_social_preview.py

产物：docs/social-preview.png（要进仓库，只有一两百 KB）
上传：仓库 Settings → General → Social preview → Upload an image
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "social-preview.png"

W, H = 1280, 640
#: 安全边距。见上面那段——不是审美选择，是防裁切
PAD = 96

# 配色直接取自 README 徽章，保持同一套视觉身份
NAVY = (15, 76, 140)        # #0F4C8C
GREEN = (30, 158, 118)      # #1E9E76
GOLD = (200, 135, 27)       # #C8871B
INK = (18, 22, 30)
PAPER = (250, 250, 248)
MUTED = (110, 118, 132)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """
    找一个能用的字体。

    🔴 **必须按顺序试而不是写死一个路径。** 写死 `times.ttf` 的话，
    这个脚本只能在装了 Times New Roman 的 Windows 上跑，
    而在 CI 或别人的机器上会直接抛 OSError —— 一个只在作者机器上
    能跑的构建脚本，等于没有构建脚本。
    找不到任何一个就退回 PIL 自带的位图字体：**丑，但不会失败**。
    """
    candidates = (
        ["timesbd.ttf", "Georgia Bold.ttf", "georgiab.ttf", "arialbd.ttf", "DejaVuSerif-Bold.ttf"]
        if bold
        else ["times.ttf", "georgia.ttf", "arial.ttf", "DejaVuSerif.ttf"]
    )
    roots = [
        Path("C:/Windows/Fonts"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/Library/Fonts"),
    ]
    for name in candidates:
        for r in roots:
            p = r / name
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    continue
    print("[social] ⚠️ 没找到任何 TrueType 字体，退回位图字体（会很糙）", file=sys.stderr)
    return ImageFont.load_default()


def load_cjk_font(size: int) -> ImageFont.FreeTypeFont | None:
    """
    找一个有汉字字形的字体。

    🔴 **不能沿用上面那个 `load_font`。** Times New Roman / Georgia / Arial
    统统没有 CJK 字形，PIL 遇到汉字**不会报错**，而是画一串空心方框（豆腐块）——
    图照样生成、脚本照样退 0，只有肉眼看图才发现底部那行中文全是方块。
    这正是"静默失败"最典型的一种：所有自动检查都会说它成功了。

    找不到就返回 None，调用方据此**跳过中文那一段**而不是画一排方框。
    少一句中文比多一排方框好看得多。
    """
    for name in ("simsun.ttc", "msyh.ttc", "simhei.ttf", "NotoSansCJK-Regular.ttc"):
        for r in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/opentype/noto")):
            p = r / name
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    continue
    return None


def text_w(draw: ImageDraw.ImageDraw, s: str, font: ImageFont.FreeTypeFont) -> int:
    return int(draw.textbbox((0, 0), s, font=font)[2])


def main() -> int:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)

    # 左侧一条竖色带：让缩略图在信息流里一眼能认出来，
    # 比整张图铺满颜色更耐看，也不会把文字压得没对比度
    d.rectangle([0, 0, 18, H], fill=NAVY)
    d.rectangle([0, 0, 18, H // 3], fill=GREEN)

    f_title = load_font(112, bold=True)
    f_tag = load_font(40)
    f_sub = load_font(29)
    f_chip = load_font(25, bold=True)
    f_foot = load_font(24)

    y = PAD - 8
    d.text((PAD, y), "Synorive", font=f_title, fill=INK)
    y += 132

    d.text((PAD, y), "Search everything you own — by meaning.", font=f_tag, fill=NAVY)
    y += 62

    for line in (
        "Local-first semantic search over documents, code, PDFs,",
        "images (OCR) and video — down to the second. Fully offline.",
    ):
        d.text((PAD, y), line, font=f_sub, fill=MUTED)
        y += 40

    y += 26

    # 能力标签。**只放四个** —— 信息流里的缩略图很小，
    # 放八个的结果是一个都读不清
    chips = [
        ("Multimodal RAG", NAVY),
        ("Verbatim citations", GREEN),
        ("24 MCP tools", GOLD),
        ("Windows + Android", NAVY),
    ]
    x = PAD
    for label, color in chips:
        tw = text_w(d, label, f_chip)
        box_w = tw + 40
        if x + box_w > W - PAD:          # 放不下就换行，别让它糊出安全区
            x = PAD
            y += 62
        d.rounded_rectangle([x, y, x + box_w, y + 48], radius=24, fill=color)
        d.text((x + 20, y + 11), label, font=f_chip, fill=PAPER)
        x += box_w + 16

    # 页脚分两段画：西文用衬线体，中文用 CJK 字体。
    # 一次性把整行交给 Times New Roman 的话，后半截会变成一排豆腐块
    foot_y = H - PAD + 18
    latin = "github.com/Aevorine/Synorive   ·   AGPL-3.0"
    d.text((PAD, foot_y), latin, font=f_foot, fill=MUTED)

    f_cjk = load_cjk_font(24)
    if f_cjk is not None:
        d.text(
            (PAD + text_w(d, latin, f_foot) + 26, foot_y),
            "·   本地优先的多模态语义检索",
            font=f_cjk,
            fill=MUTED,
        )
    else:
        print("[social] ⚠️ 没找到 CJK 字体，页脚的中文这次跳过了（不画方框）", file=sys.stderr)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)

    size = OUT.stat().st_size
    # 🔴 GitHub 的上限是 1 MB。超了它**不会告诉你为什么**，只是上传失败
    if size > 1_000_000:
        print(f"✗ {OUT} 有 {size/1024:.0f} KB，超过 GitHub 的 1 MB 上限", file=sys.stderr)
        return 1
    print(f"✓ {OUT.relative_to(ROOT)}  {W}×{H}  {size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
