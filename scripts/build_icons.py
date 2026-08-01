#!/usr/bin/env python
"""
Synorive 图标流水线
====================================================================
源图：E:\\Pictures\\SoftwarePictures\\SynorivePictures\\ChatGPT Image 2026年8月1日 23_20_07.png
      1254x1254，Format24bppRgb —— **没有透明通道**，圆角方块外面那圈是白色。

直接转 .ico 会在深色任务栏上显示成白方块，所以第一步必须把四角抠成透明。

抠法：几何遮罩，不是颜色抠图。
  颜色抠图（从角落 flood fill）在这张图上会翻车 —— 圆角内部左上角也是近白色
  (#F8F8FA)，和外部纯白只差几个色阶，flood fill 会漏进内部把图案吃掉。
  几何遮罩先量出圆角半径，再用 4 倍超采样画圆角矩形，边缘抗锯齿干净。

── 关于安卓自适应图标的前景层（这里踩过坑，记下来免得下次重来）──────
  本来想自动抠出「S + 放大镜」当前景层。实测两条路都走不通：

  ① 连通域法：S、放大镜、装饰波浪是**同一个连通域**（53.8 万像素横跨整张图），
     "删掉接触边缘的连通域"删了 0 个。
  ② 饱和度法：实测左下装饰蓝浪 sat=0.995，S 本体 sat=0.991 —— 完全一样；
     而 logo 内部的文档线条只有 sat=0.386，比多处波浪还低。红通道也同样接近 0。

  **装饰波浪和 logo 在设计上就是同一套颜色，任何颜色/拓扑启发式都分不开。**

  所以改成：前景层 = 整块圆角方块图标缩到 0.72 覆盖率（正好填满 108dp 画布里
  可见的 72dp 区域），背景层 = 图标自身背景色。圆形遮罩削掉方块角之后露出的
  那一丝背景与图标底色同色，接缝看不出来。

  Material You 单色图标**故意不生成** —— 自动做出来只能是个圆角方块剪影，很难看。
  缺这一层时安卓会回落到普通自适应图标，比硬凑一个强。要做得手绘一个 S 字形。

产物：
  apps/desktop/resources/icons/   Windows .ico (16~256) + PNG (16~1024) + 托盘图标
  apps/mobile/app/src/main/res/   Android 传统图标 + 自适应图标前景/背景 + XML
  assets/icon-master.png          抠好透明四角的主图，其他地方一律引用它
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

# ── 路径 ────────────────────────────────────────────────────────
SRC = Path(r"E:\Pictures\SoftwarePictures\SynorivePictures\ChatGPT Image 2026年8月1日 23_20_07.png")
ROOT = Path(__file__).resolve().parent.parent
OUT_DESKTOP = ROOT / "apps" / "desktop" / "resources" / "icons"
OUT_ASSETS = ROOT / "assets"
OUT_ANDROID = ROOT / "apps" / "mobile" / "app" / "src" / "main" / "res"
OUT_STORE = ROOT / "assets" / "store"

# 超采样倍数：遮罩先画 4 倍大再缩，边缘才不会有锯齿
SS = 4

# 判定"这是背景白"的阈值：RGB 三通道都 ≥ 此值且彼此接近
WHITE_MIN = 248
WHITE_SPREAD = 6

# 自适应图标：108dp 画布里可见区域是中央 72dp
ADAPTIVE_COVERAGE = 72 / 108


def is_background_white(px: tuple[int, ...]) -> bool:
    r, g, b = px[:3]
    return min(r, g, b) >= WHITE_MIN and (max(r, g, b) - min(r, g, b)) <= WHITE_SPREAD


def find_content_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """找到非背景白内容的外接矩形。"""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    assert px is not None

    def row_has_content(y: int) -> bool:
        return any(not is_background_white(px[x, y]) for x in range(0, w, 2))

    def col_has_content(x: int) -> bool:
        return any(not is_background_white(px[x, y]) for y in range(0, h, 2))

    top = next((y for y in range(h) if row_has_content(y)), 0)
    bottom = next((y for y in range(h - 1, -1, -1) if row_has_content(y)), h - 1)
    left = next((x for x in range(w) if col_has_content(x)), 0)
    right = next((x for x in range(w - 1, -1, -1) if col_has_content(x)), w - 1)
    return left, top, right + 1, bottom + 1


def estimate_corner_radius(img: Image.Image, bbox: tuple[int, int, int, int]) -> int:
    """
    量圆角半径：在内容外接框顶部往下几行，找最左边的非白像素，
    它离左边框的距离 ≈ 圆角半径。取中位数抗噪。
    """
    left, top, right, bottom = bbox
    rgb = img.convert("RGB")
    px = rgb.load()
    assert px is not None
    width = right - left

    samples: list[int] = []
    for dy in (1, 2, 3, 4):
        y = top + dy
        if y >= bottom:
            continue
        for x in range(left, right):
            if not is_background_white(px[x, y]):
                samples.append(x - left)
                break
    if not samples:
        return int(width * 0.22)

    samples.sort()
    r = samples[len(samples) // 2]
    # 兜底：半径落在 8%~35% 之间才可信，否则用 iOS 常见的 22%
    if not (width * 0.08 <= r <= width * 0.35):
        r = int(width * 0.22)
    return int(r)


def make_rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """4 倍超采样画圆角矩形遮罩，缩回来边缘就是抗锯齿的。"""
    w, h = size
    big = Image.new("L", (w * SS, h * SS), 0)
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=radius * SS, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def build_master(src_path: Path) -> Image.Image:
    """读源图 → 裁到内容 → 补成正方形 → 打透明圆角 → 返回 RGBA 主图。"""
    if not src_path.exists():
        raise FileNotFoundError(f"源图不存在：{src_path}")

    img = Image.open(src_path)
    print(f"[icons] 源图 {img.size[0]}x{img.size[1]} {img.mode}")

    bbox = find_content_bbox(img)
    print(f"[icons] 内容外接框 {bbox}  (裁掉外圈纯白边)")
    cropped = img.convert("RGBA").crop(bbox)

    # 外接框不一定是正方形（这张图量出来 1237x1245）。
    # 不补正就直接 resize 到 NxN 会有约 0.6% 的拉伸 —— 肉眼看不出，但没必要留着。
    cw, ch = cropped.size
    side = max(cw, ch)
    if cw != ch:
        squared = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        squared.paste(cropped, ((side - cw) // 2, (side - ch) // 2))
        print(f"[icons] 外接框非正方 {cw}x{ch} → 补正为 {side}x{side}（居中，不拉伸）")
        cropped = squared

    radius = estimate_corner_radius(img, bbox)
    print(f"[icons] 量得圆角半径 {radius}px  ({radius / side * 100:.1f}% of width)")

    mask = make_rounded_mask(cropped.size, radius)
    master = Image.new("RGBA", cropped.size, (0, 0, 0, 0))
    master.paste(cropped, (0, 0), mask)
    return master


def sample_background_color(master: Image.Image) -> tuple[int, int, int]:
    """
    取主图左上区域的平均色，作为安卓自适应图标背景层的底色。
    选左上是因为那一块是纯背景渐变，没有 logo 也没有装饰波浪。
    """
    w, h = master.size
    patch = master.convert("RGB").crop((int(w * 0.06), int(h * 0.06), int(w * 0.22), int(h * 0.22)))
    small = patch.resize((1, 1), Image.LANCZOS)
    px = small.getpixel((0, 0))
    assert isinstance(px, tuple)
    return (px[0], px[1], px[2])


def fit_into(canvas_size: int, art: Image.Image, coverage: float) -> Image.Image:
    """把图案等比缩放并居中放进方形画布，coverage 是图案边长占画布的比例。"""
    target = int(canvas_size * coverage)
    aw, ah = art.size
    scale = target / max(aw, ah)
    new = art.resize((max(1, int(aw * scale)), max(1, int(ah * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    canvas.paste(new, ((canvas_size - new.size[0]) // 2, (canvas_size - new.size[1]) // 2), new)
    return canvas


def square(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.LANCZOS)


def circle_crop(img: Image.Image) -> Image.Image:
    """圆形裁切，给安卓传统圆形图标用。"""
    size = img.size[0]
    mask = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * SS - 1, size * SS - 1], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def write_desktop(master: Image.Image) -> list[Path]:
    OUT_DESKTOP.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for s in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        p = OUT_DESKTOP / f"icon-{s}.png"
        square(master, s).save(p, "PNG")
        written.append(p)

    # electron-builder 认这个名字（要求 ≥256，给 512）
    p512 = OUT_DESKTOP / "icon.png"
    square(master, 512).save(p512, "PNG")
    written.append(p512)

    # .ico 多尺寸：Windows 任务栏 / 资源管理器 / Alt-Tab 按 DPI 自己挑
    ico_sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    pico = OUT_DESKTOP / "icon.ico"
    square(master, 256).save(pico, format="ICO", sizes=ico_sizes)
    written.append(pico)

    # 托盘图标：Windows 托盘按 DPI 取 16/20/24/32
    for s in (16, 20, 24, 32):
        p = OUT_DESKTOP / f"tray-{s}.png"
        square(master, s).save(p, "PNG")
        written.append(p)

    return written


def write_android(master: Image.Image, bg: tuple[int, int, int]) -> list[Path]:
    written: list[Path] = []

    # ① 传统图标（Android 7 及以下、部分启动器仍在用）
    legacy = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for dpi, size in legacy.items():
        d = OUT_ANDROID / f"mipmap-{dpi}"
        d.mkdir(parents=True, exist_ok=True)

        sq = square(master, size)
        p = d / "ic_launcher.png"
        sq.save(p, "PNG")
        written.append(p)

        pr = d / "ic_launcher_round.png"
        circle_crop(sq).save(pr, "PNG")
        written.append(pr)

    # ② 自适应图标（Android 8+）：画布 108dp，可见区域中央 72dp
    adaptive = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
    for dpi, size in adaptive.items():
        d = OUT_ANDROID / f"mipmap-{dpi}"
        d.mkdir(parents=True, exist_ok=True)

        # 前景：整块圆角方块图标，缩到正好填满可见的 72dp 区域
        fg = fit_into(size, master, coverage=ADAPTIVE_COVERAGE)
        pf = d / "ic_launcher_foreground.png"
        fg.save(pf, "PNG")
        written.append(pf)

        # 背景：纯色，取自图标自身底色 —— 圆形遮罩削角后露出的缝隙才看不出来
        # （自适应图标的背景层不允许透明）
        pb = d / "ic_launcher_background.png"
        Image.new("RGBA", (size, size), (*bg, 255)).save(pb, "PNG")
        written.append(pb)

    # ③ anydpi-v26 的 XML 声明（不含 monochrome，理由见文件头注释）
    xml_dir = OUT_ANDROID / "mipmap-anydpi-v26"
    xml_dir.mkdir(parents=True, exist_ok=True)
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@mipmap/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        "</adaptive-icon>\n"
    )
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        p = xml_dir / name
        p.write_text(xml, encoding="utf-8")
        written.append(p)

    # ④ 应用商店用图
    OUT_STORE.mkdir(parents=True, exist_ok=True)
    p = OUT_STORE / "play-icon-512.png"
    square(master, 512).save(p, "PNG")
    written.append(p)

    return written


def verify(paths: list[Path], bg: tuple[int, int, int]) -> int:
    """
    静默失败自查。这一段是有意写得啰嗦的 —— 图标生成最典型的失败是
    "脚本退出码 0、文件也在，但内容是空的或者角没抠干净"，不主动查就发现不了。
    """
    problems = 0

    for p in paths:
        if not p.exists():
            print(f"  ✗ 不存在 {p}")
            problems += 1
            continue
        if p.stat().st_size == 0:
            print(f"  ✗ 空文件 {p}")
            problems += 1

    # ① .ico 必须真的含多尺寸，只有一个 256 的话小尺寸下会糊成一团
    ico = OUT_DESKTOP / "icon.ico"
    if ico.exists():
        with Image.open(ico) as im:
            sizes = sorted(im.ico.sizes()) if hasattr(im, "ico") else [im.size]
        print(f"  · icon.ico 内含尺寸 {sizes}")
        if len(sizes) < 5:
            print("  ✗ icon.ico 尺寸不足 5 种，Windows 各 DPI 下会糊")
            problems += 1

    # ② 主图四角必须全透明，否则深色任务栏上是个白方块
    master_path = OUT_ASSETS / "icon-master.png"
    if master_path.exists():
        with Image.open(master_path) as im:
            rgba = im.convert("RGBA")
            w, h = rgba.size
            alphas = [rgba.getpixel(c)[3] for c in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]  # type: ignore[index]
        print(f"  · 主图四角 alpha = {alphas}")
        if any(a > 8 for a in alphas):
            print("  ✗ 四角没抠干净，深色任务栏上会显示白方块")
            problems += 1

    # ③ 小尺寸图标不能是纯色块（缩得太狠会糊成一片）
    icon16 = OUT_DESKTOP / "icon-16.png"
    if icon16.exists():
        with Image.open(icon16) as im:
            colors = im.convert("RGB").getcolors(maxcolors=4096) or []
        print(f"  · icon-16.png 含 {len(colors)} 种颜色")
        if len(colors) < 8:
            print("  ✗ 16px 图标颜色过少，八成糊成一团了")
            problems += 1

    # ④ 安卓前景层必须有透明边（否则圆形遮罩里会出现方块）
    fg = OUT_ANDROID / "mipmap-xxxhdpi" / "ic_launcher_foreground.png"
    if fg.exists():
        with Image.open(fg) as im:
            rgba = im.convert("RGBA")
            corner_alpha = rgba.getpixel((2, 2))[3]  # type: ignore[index]
            center_alpha = rgba.getpixel((rgba.size[0] // 2, rgba.size[1] // 2))[3]  # type: ignore[index]
        print(f"  · 安卓前景层 角alpha={corner_alpha} 中心alpha={center_alpha}")
        if corner_alpha > 8:
            print("  ✗ 前景层四角不透明，圆形遮罩里会露出方块")
            problems += 1
        if center_alpha < 200:
            print("  ✗ 前景层中心是透明的 —— 图案没放进去（典型静默失败）")
            problems += 1

    # ⑤ 安卓背景层必须不透明且是采样到的底色
    bgp = OUT_ANDROID / "mipmap-xxxhdpi" / "ic_launcher_background.png"
    if bgp.exists():
        with Image.open(bgp) as im:
            px = im.convert("RGBA").getpixel((10, 10))
        assert isinstance(px, tuple)
        print(f"  · 安卓背景层像素 = {px}")
        if px[3] != 255:
            print("  ✗ 背景层带透明，安卓自适应图标不允许")
            problems += 1
        if px[:3] != bg:
            print(f"  ✗ 背景层颜色 {px[:3]} != 采样色 {bg}")
            problems += 1

    return problems


def main() -> int:
    print("=" * 64)
    print("Synorive 图标流水线")
    print("=" * 64)

    master = build_master(SRC)
    OUT_ASSETS.mkdir(parents=True, exist_ok=True)
    master_path = OUT_ASSETS / "icon-master.png"
    master.save(master_path, "PNG")
    print(f"[icons] 主图（已抠透明四角）→ {master_path}  {master.size[0]}x{master.size[1]}")

    bg = sample_background_color(master)
    print(f"[icons] 安卓背景层采样色 = rgb{bg}  #{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}")

    written = [master_path]
    n0 = len(written)
    written += write_desktop(master)
    print(f"[icons] 桌面端产物 {len(written) - n0} 个 → {OUT_DESKTOP}")

    n1 = len(written)
    written += write_android(master, bg)
    print(f"[icons] 安卓端产物 {len(written) - n1} 个 → {OUT_ANDROID}")

    print("-" * 64)
    print("自查：")
    problems = verify(written, bg)
    if problems:
        print(f"✗ 发现 {problems} 个问题")
        return 1
    print(f"✓ 全部 {len(written)} 个产物通过检查")
    return 0


if __name__ == "__main__":
    sys.exit(main())
