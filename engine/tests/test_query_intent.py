#!/usr/bin/env python
"""
D-adaptive 查询意图分类 —— 纯规则单元测试
====================================================================
只测 classify_intent() 这个纯函数，不起引擎。跟 websearch/intent.py
是同一种测试哲学——规则表判得对不对，靠具体例句一条条验证，而不是
相信"看起来应该没问题"。

用法：python -m tests.test_query_intent
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from synorive.search.engine import PRESETS, Weights, _ADAPTIVE_ONLY, classify_intent  # noqa: E402

problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'PASS' if cond else 'FAIL'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


def test_precise() -> None:
    for q in ['"注意力机制"是什么', "main.py 里的 parse_query 函数", "C:\\Users\\a\\report.docx", "fooBarBaz"]:
        intent, w = classify_intent(q)
        check(intent == "precise", f"{q!r} 判成 precise", f"{q!r} 判成了 {intent}，应该是 precise")
        check(w is PRESETS["precise"], "用的是 PRESETS['precise'] 那组数值", "权重对象不是复用 PRESETS 里的")


def test_compare() -> None:
    for q in ["ChatGPT vs Claude 哪个更好", "A和B的区别", "对比一下这两个方案"]:
        intent, _ = classify_intent(q)
        check(intent == "compare", f"{q!r} 判成 compare", f"{q!r} 判成了 {intent}")


def test_factcheck() -> None:
    for q in ["这个说法是不是真的", "求证一下这条新闻", "是否属实"]:
        intent, _ = classify_intent(q)
        check(intent == "factcheck", f"{q!r} 判成 factcheck", f"{q!r} 判成了 {intent}")


def test_explore() -> None:
    for q in ["AI", "关于机器学习"]:
        intent, w = classify_intent(q)
        check(intent == "explore", f"{q!r} 判成 explore", f"{q!r} 判成了 {intent}")
        check(w is PRESETS["semantic"], "explore 复用 PRESETS['semantic']", "explore 权重不对")


def test_fallback_balanced() -> None:
    """判不出来的（长且没有任何信号词的查询）要老实退回 balanced，不能硬猜。"""
    q = "我们公司去年第三季度财务报表里关于研发投入占比的详细说明文档在哪里"
    intent, w = classify_intent(q)
    check(intent == "balanced", f"长查询没命中任何规则，退回 balanced（实际 {intent}）",
          f"应该退回 balanced，判成了 {intent}")
    check(w == Weights(), "balanced 就是默认权重", "balanced 权重不是默认值")


def test_empty_query() -> None:
    intent, w = classify_intent("")
    check(intent == "balanced", "空查询退回 balanced", f"空查询判成了 {intent}")
    intent2, _ = classify_intent("   ")
    check(intent2 == "balanced", "纯空格查询也退回 balanced", f"纯空格判成了 {intent2}")


def test_priority_order() -> None:
    """信号词同时出现时，判定顺序要稳定——不能一次判 compare 一次判 factcheck。"""
    q = "对比一下哪个更好"
    for _ in range(5):
        intent, _ = classify_intent(q)
        check(intent == "compare", "同一句反复判，结果稳定是 compare", f"结果不稳定：{intent}")


def test_adaptive_only_not_in_presets() -> None:
    """factcheck/compare 是自适应专用，不该混进用户手动能选的 PRESETS 表。"""
    check(
        "factcheck" not in PRESETS and "compare" not in PRESETS,
        "factcheck/compare 没有出现在面向用户的 PRESETS 里",
        "自适应专用权重混进了 PRESETS，会被当成手动可选预设暴露出去",
    )
    check(
        set(_ADAPTIVE_ONLY.keys()) == {"factcheck", "compare"},
        "_ADAPTIVE_ONLY 恰好是这两档",
        f"_ADAPTIVE_ONLY 内容不对：{list(_ADAPTIVE_ONLY.keys())}",
    )


def _run_all() -> None:
    test_precise()
    test_compare()
    test_factcheck()
    test_explore()
    test_fallback_balanced()
    test_empty_query()
    test_priority_order()
    test_adaptive_only_not_in_presets()
    if problems:
        print(f"\n{len(problems)} 个问题")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    _run_all()
