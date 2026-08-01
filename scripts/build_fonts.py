#!/usr/bin/env python
"""
Synorive 字体流水线 —— 思源宋体（Noto Serif SC）子集打包
====================================================================
字体方案 F1-b（用户选的）：
    正文 16px / 18.67px  →  系统自带 SimSun 宋体（不打包）
    标题 ≥24px           →  思源宋体（打包进应用，不依赖系统装没装）
    西文字母/数字/西文标点 →  Times New Roman（系统自带）

为什么不直接 import '@fontsource/noto-serif-sc/400.css'：
  ① 那个 CSS 每条 @font-face 都带 .woff 回退 —— Electron 只认 woff2 就够了，
     带上等于让 Vite 多打一倍的字体资源进产物。
  ② 它一次引入 8 个字重，我们只要 400 和 600。
  ③ 路径写死在 node_modules 里，包结构一变就断。

所以自己生成：只留 woff2、只留两个字重、文件复制到自己的目录、路径自己控。

覆盖率实测（2026-08-02，@fontsource/noto-serif-sc 5.2.8）：
  编号分片版 97 个文件 / 13556 个码位 / 3.0 MB per weight
  合集版 chinese-simplified 只有 7946 码位，抽查 30 字缺 3 个 —— 所以用分片版。
  分片版按 unicode-range 加载，界面上实际只会命中 2~4 片（约 100 KB）。
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTSOURCE = ROOT / "node_modules" / "@fontsource" / "noto-serif-sc"
OUT_DIR = ROOT / "apps" / "desktop" / "src" / "styles" / "fonts"
OUT_CSS = ROOT / "apps" / "desktop" / "src" / "styles" / "fonts.css"

# 只要这两个字重：400 正文标题、600 强调标题
WEIGHTS = (400, 600)

# 匹配一整条 @font-face
FACE_RE = re.compile(r"@font-face\s*\{[^}]*\}", re.S)
# 匹配 src 里的 woff2 url
WOFF2_RE = re.compile(r"url\(\./files/([^)]+\.woff2)\)")


def extract_faces(css_path: Path) -> list[tuple[str, str]]:
    """从 fontsource 的 CSS 里抽出 (字体文件名, 重写后的 @font-face) 列表。"""
    raw = css_path.read_text(encoding="utf-8")
    out: list[tuple[str, str]] = []

    for block in FACE_RE.findall(raw):
        m = WOFF2_RE.search(block)
        if not m:
            continue
        filename = m.group(1)

        # 只保留 woff2：把整条 src 换成单一 woff2 引用
        block = re.sub(
            r"src:\s*[^;]+;",
            f"src: url('./fonts/{filename}') format('woff2');",
            block,
            flags=re.S,
        )
        # 字体名统一成 Source Han Serif SC，和设计令牌里写的一致
        block = block.replace("'Noto Serif SC'", "'Source Han Serif SC'")
        # swap 会让标题先用宋体渲染再跳变，很难看；改成 block，最多等 3s
        block = block.replace("font-display: swap;", "font-display: block;")
        out.append((filename, block.strip()))

    return out


def main() -> int:
    print("=" * 64)
    print("Synorive 字体流水线 · 思源宋体子集")
    print("=" * 64)

    if not FONTSOURCE.exists():
        print(f"✗ 没找到 {FONTSOURCE}")
        print("  跑一次：npm install --workspace=@synorive/desktop -D @fontsource/noto-serif-sc")
        return 1

    version = "unknown"
    pkg = FONTSOURCE / "package.json"
    if pkg.exists():
        import json

        version = json.loads(pkg.read_text(encoding="utf-8")).get("version", "unknown")
    print(f"[fonts] @fontsource/noto-serif-sc {version}")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_blocks: list[str] = []
    copied = 0
    total_bytes = 0

    for w in WEIGHTS:
        css = FONTSOURCE / f"{w}.css"
        if not css.exists():
            print(f"✗ 缺 {css}")
            return 1

        faces = extract_faces(css)
        print(f"[fonts] 字重 {w}: {len(faces)} 个 unicode-range 分片")

        for filename, block in faces:
            src = FONTSOURCE / "files" / filename
            if not src.exists():
                print(f"  ✗ 字体文件不存在 {src}")
                return 1
            dst = OUT_DIR / filename
            shutil.copy2(src, dst)
            copied += 1
            total_bytes += dst.stat().st_size
            all_blocks.append(block)

    header = f"""/* ============================================================
 * Synorive 字体 —— 自动生成，请勿手改
 * 源：@fontsource/noto-serif-sc {version}（SIL OFL 1.1 开源许可，可商用）
 * 生成：python scripts/build_fonts.py
 *
 * 字体方案 F1-b：
 *   正文（16px 小四 / 18.67px 四号）→ 系统 SimSun 宋体
 *   标题（≥24px）→ 这里打包的思源宋体
 *   西文字母 / 数字 / 西文标点 → Times New Roman（在字族里排第一位，
 *   浏览器逐字符回退，汉字自动落到宋体，一行 CSS 就分开了）
 *
 * 只含 woff2、只含 400/600 两个字重。
 * 按 unicode-range 分片，界面上实际只加载命中的那几片。
 * ============================================================ */

"""

    OUT_CSS.write_text(header + "\n\n".join(all_blocks) + "\n", encoding="utf-8")

    print("-" * 64)
    print(f"[fonts] 复制 {copied} 个 woff2，共 {total_bytes / 1024 / 1024:.2f} MB → {OUT_DIR}")
    print(f"[fonts] 生成 {OUT_CSS}")

    # ── 静默失败自查 ────────────────────────────────────────────
    problems = 0
    css_text = OUT_CSS.read_text(encoding="utf-8")

    face_count = css_text.count("@font-face")
    print(f"  · @font-face 条数 = {face_count}")
    if face_count < 100:
        print(f"  ✗ @font-face 只有 {face_count} 条，期望 ≥100（两个字重各约 97 片）")
        problems += 1

    if "format('woff2')" not in css_text:
        print("  ✗ CSS 里没有 woff2 引用")
        problems += 1
    if ".woff)" in css_text or "format('woff')" in css_text:
        print("  ✗ 还残留 .woff 回退，没剥干净")
        problems += 1
    if "Noto Serif SC" in css_text:
        print("  ✗ 字体名没统一成 Source Han Serif SC")
        problems += 1

    # 引用的每个文件都必须真的躺在目录里 —— 少一个就是运行时字体缺字
    referenced = set(re.findall(r"url\('\./fonts/([^']+)'\)", css_text))
    on_disk = {p.name for p in OUT_DIR.glob("*.woff2")}
    missing = referenced - on_disk
    orphan = on_disk - referenced
    print(f"  · CSS 引用 {len(referenced)} 个文件，目录里有 {len(on_disk)} 个")
    if missing:
        print(f"  ✗ CSS 引用了但文件不存在：{sorted(missing)[:5]}")
        problems += 1
    if orphan:
        print(f"  ✗ 目录里有 {len(orphan)} 个没被引用的孤儿文件（白占体积）")
        problems += 1

    if problems:
        print(f"✗ 发现 {problems} 个问题")
        return 1
    print("✓ 字体子集打包完成，自查全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
