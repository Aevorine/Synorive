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


def test_dangerous_html_with_no_terms_at_all() -> None:
    """
    🔴 **terms 为空不是边界情况，是常态。**

    以前这条分支直接 `return text[:window]`，原文一个字符都没转义。而走到这条
    分支的恰恰是最常用的几条路：文件管理器页的空查询（默认视图，一进去就是它）、
    纯筛选查询 `type:pdf date:last7days`、纯排除查询 `-草稿`。
    也就是说库里只要存进一个含 `<img onerror>` 的网页/HTML/Markdown，
    默认列表一渲染就在 Electron 渲染进程里执行了。

    上面那个 test_empty_and_no_terms 用的是纯文本样本，转不转义结果都一样，
    所以它一直是绿的 —— 用危险样本重测这条分支。
    """
    for snippet in _DANGEROUS_SNIPPETS:
        out = _highlight(f"正常内容 {snippet} 收尾", terms=[])
        _assert_no_raw_tags_except_em(out, f"no-terms::{snippet}")

    # 同一条分支上的截断路径：截断后的那一段也必须是转义过的
    long_text = "<script>alert(1)</script>" + "填充" * 300
    out = _highlight(long_text, terms=[])
    assert out.endswith("…"), "超长文本应带省略号"
    _assert_no_raw_tags_except_em(out, "no-terms::truncated")


def test_all_terms_too_short() -> None:
    """命中词全短于 2 个字符时 escaped_terms 也会空 —— 另一条会退化的路。"""
    out = _highlight('前缀 <svg onload=alert(1)> 后缀', terms=["a", "b"])
    _assert_no_raw_tags_except_em(out, "short-terms")


def _run_all() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n全部通过：{len(tests)} 个测试")


if __name__ == "__main__":
    _run_all()
