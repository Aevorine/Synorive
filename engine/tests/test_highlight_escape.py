#!/usr/bin/env python
"""
搜索高亮转义 —— HTML 注入回归
====================================================================
`_highlight()` 生成的字符串会被前端 `dangerouslySetInnerHTML` 直接渲染成 DOM。
被索引的文档正文可能本来就带 `<img>`/`<style>`/`<script>` 这类标签（网页、HTML
文件、Markdown 里嵌的 HTML 都很常见），如果不转义就原样拼进去，等于把文档内容
变成了可执行的界面注入口。

这里只验证一件事：不管命中词落在哪，输出里除了我们自己加的 `<em>...</em>`，
不能再出现任何未转义的尖括号/引号。

用法：python -m tests.test_highlight_escape
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.search.engine import _highlight  # noqa: E402

_DANGEROUS_SNIPPETS = [
    '<img src=x onerror=alert(1)>',
    '<style>body{display:none}</style>',
    '<script>alert(1)</script>',
    '<svg onload=alert(1)>',
    '<a href="javascript:alert(1)">click</a>',
    '<div style="position:fixed">spoof</div>',
]


def _assert_no_raw_tags_except_em(html_out: str, label: str) -> None:
    # 挖掉我们自己生成的 <em>...</em>，剩下的部分不该再有任何 < 或 >
    stripped = re.sub(r"</?em>", "", html_out)
    assert "<" not in stripped and ">" not in stripped, (
        f"[{label}] 转义后仍残留尖括号，可能是未转义的原文标签：{html_out!r}"
    )


def test_dangerous_html_without_matched_terms() -> None:
    for snippet in _DANGEROUS_SNIPPETS:
        text = f"前面一些正常内容 {snippet} 后面还有内容" * 1
        out = _highlight(text, terms=["不存在的词"])
        _assert_no_raw_tags_except_em(out, snippet)
        assert "onerror" not in out or "&lt;" in out  # 属性文本必须是转义后的样子


def test_dangerous_html_with_matched_term_inside_tag() -> None:
    # 命中词恰好出现在危险标签内部（比如 alert 被当成关键词）
    text = 'safe prefix <img src=x onerror=alert(1)> safe suffix'
    out = _highlight(text, terms=["alert"])
    _assert_no_raw_tags_except_em(out, "term-inside-tag")
    assert "<em>alert</em>" in out


def test_dangerous_html_with_matched_term_outside_tag() -> None:
    text = '找找 script 关键字 <script>alert(1)</script> 结束'
    out = _highlight(text, terms=["script"])
    _assert_no_raw_tags_except_em(out, "term-outside-tag")
    assert out.count("<em>") == out.count("</em>")


def test_plain_text_still_highlights() -> None:
    out = _highlight("这是一段包含关键词的普通文本", terms=["关键词"])
    assert "<em>关键词</em>" in out


def test_empty_and_no_terms() -> None:
    assert _highlight("", terms=["x"]) == ""
    assert _highlight("纯文本没有命中词", terms=[]) == "纯文本没有命中词"


def _run_all() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n全部通过：{len(tests)} 个测试")


if __name__ == "__main__":
    _run_all()
