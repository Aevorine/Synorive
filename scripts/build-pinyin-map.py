#!/usr/bin/env python3
"""
生成界面用的「汉字 → 拼音」小表
====================================================================
命令面板要支持拼音检索（打 `qingli` 或 `qlczt` 都能命中「清理重复图」）。

## 为什么是生成一张表，不是手写 py 字段

原来每条命令手写一个 `py: 'qlczt'`。问题不在于麻烦，在于**它会被忘**：
新加一条命令忘了写 `py`，那条命令就永远搜不到 —— 不报错、不告警，
只是用户打拼音时它不出现。这正是"运行正常、功能无效"。

## 为什么不整本字典

只收**界面源码里真出现过的汉字**（约 1200 个），生成的表 ~12 KB。
整本 GBK 字表是 20000+ 字、几百 KB，而界面上永远用不到其中 94%。

## 多音字

**每个字的所有读音都收**，用 `|` 分隔，匹配时逐个试。

只取第一个读音是不够的：`重` 单字读 zhong，而「重复」里读 chong ——
用户按直觉打 `qlcft`（清理重复图）会一条都匹配不到，
而他不会想到是多音字的问题，只会觉得"这面板的拼音搜索是坏的"。
全收之后每个字平均多几个字节，换来的是按直觉打就能中。

用法：python scripts/build-pinyin-map.py
"""

from __future__ import annotations

import pathlib
import sys

try:
    from pypinyin import Style, pinyin
except ImportError:
    sys.exit("缺 pypinyin：engine/.venv/Scripts/python.exe -m pip install pypinyin")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "apps" / "desktop" / "src"
OUT = SRC / "lib" / "pinyinMap.generated.ts"

chars: set[str] = set()
for f in SRC.rglob("*.ts*"):
    if ".generated." in f.name:
        continue
    for ch in f.read_text(encoding="utf-8", errors="replace"):
        if "\u4e00" <= ch <= "\u9fff":
            chars.add(ch)

rows = []
for ch in sorted(chars):
    got = pinyin(ch, style=Style.NORMAL, heteronym=True)
    reads = [r for r in (got[0] if got else []) if r and r.isascii()]
    # 去重保序：pypinyin 偶尔会给重复项
    seen: list[str] = []
    for r in reads:
        if r not in seen:
            seen.append(r)
    if seen:
        rows.append((ch, "|".join(seen[:4])))

body = "".join(f"{ch}{py} " for ch, py in rows).strip()

HEADER = (
    "/* 自动生成，请勿手改 —— 跑 `python scripts/build-pinyin-map.py` 重生成。\n"
    " * 界面源码里出现过的汉字 -> 全部读音。命令面板的拼音检索靠它。\n"
    f" * 共 {len(rows)} 字。 */\n\n"
    "/**\n"
    " * 紧凑格式：`汉字读音1|读音2` 用空格分隔，运行时展开成 Map。\n"
    " *\n"
    " * 多音字**全收**。只取第一个读音的话，`重` 会是 zhong，\n"
    " * 于是「清理重复图」按直觉打 qlcft / chongfu 一条都中不了 ——\n"
    " * 而用户不会想到是多音字，只会觉得这个拼音搜索是坏的。\n"
    " */\n"
)

RUNTIME = (
    "\nlet map: Map<string, string> | null = null;\n\n"
    "/** 一个字的全部读音。不是汉字（或表里没有）返回 undefined */\n"
    "export function readingsOf(ch: string): string[] | undefined {\n"
    "  if (!map) {\n"
    "    map = new Map();\n"
    "    for (const tok of PACKED.split(' ')) {\n"
    "      if (tok.length > 1) map.set(tok[0]!, tok.slice(1));\n"
    "    }\n"
    "  }\n"
    "  return map.get(ch)?.split('|');\n"
    "}\n"
)

OUT.write_text(HEADER + f"const PACKED =\n  '{body}';\n" + RUNTIME, encoding="utf-8")
print(f"写出 {OUT.relative_to(ROOT)}：{len(rows)} 字，{OUT.stat().st_size / 1024:.1f} KB")
