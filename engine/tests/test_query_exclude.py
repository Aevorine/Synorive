"""
搜索语法：`-排除词` 与 `"精确短语"`
====================================================================
文档里一直写着支持这两个（见 query_syntax.py 头部），但**解析器从来没实现过**。
后果分两层，都不报错：

  ① `-草稿` 原样留在查询词里 → 关键词那一路靠 `store/text.to_query` 认识
     这个减号，还能翻成 FTS 的 NOT；但**向量那一路是把整段文本拿去编码的**，
     嵌入模型只看见"草稿"两个字 —— 于是 `-草稿` 在语义召回里变成了一个
     **正向**信号，用户想排掉草稿，结果反而更容易捞到草稿。方向正好相反。
  ② 排除项不出现在界面的"我把这句话理解成了什么"标签里，
     用户看到结果少了一大截，找不到是哪一步干的。

这组测试盯的就是这两件事，外加一条最容易改坏的：
减号**不能**把 `UTF-8` / `COVID-19` / `2026-08-01` 切开。
"""

from __future__ import annotations

from synorive.search.query_syntax import describe, parse_query


class TestExclude:
    def test_摘出排除词并从正向查询里去掉(self) -> None:
        p = parse_query("注意力 -草稿")
        assert p.excludes == ["草稿"]
        assert p.text == "注意力"
        # 🔴 这一条是核心：喂给嵌入模型的串里不许再有"草稿"
        assert "草稿" not in p.semantic_text()

    def test_关键词那一路仍然拿得到减号(self) -> None:
        p = parse_query("注意力 -草稿")
        assert p.keyword_text() == "注意力 -草稿"

    def test_多个排除词(self) -> None:
        p = parse_query("注意力 -草稿 -笔记")
        assert p.excludes == ["草稿", "笔记"]
        assert p.text == "注意力"

    def test_和其它指令共存(self) -> None:
        p = parse_query("type:pdf 注意力 -草稿")
        assert p.filters["extensions"] == [".pdf"]
        assert p.excludes == ["草稿"]
        assert p.text == "注意力"

    def test_排除词要显示给用户看(self) -> None:
        # 不显示的话，用户只会觉得"怎么少了这么多"，找不到是哪一步干的
        assert "排除：草稿" in describe(parse_query("注意力 -草稿"))
        assert "排除：草稿、笔记" in describe(parse_query("注意力 -草稿 -笔记"))


class TestNegativeDoesNotBreakRealWords:
    """🔴 减号最容易改坏的地方：把带连字符的正常词切开，而且是静默切开。"""

    def test_utf8(self) -> None:
        p = parse_query("UTF-8 编码")
        assert p.excludes == []
        assert p.text == "UTF-8 编码"

    def test_covid19(self) -> None:
        p = parse_query("COVID-19 疫苗")
        assert p.excludes == []
        assert p.text == "COVID-19 疫苗"

    def test_日期(self) -> None:
        p = parse_query("2026-08-01 的会议纪要")
        assert p.excludes == []
        assert "2026-08-01" in p.text

    def test_行首减号仍然认(self) -> None:
        p = parse_query("-草稿 注意力")
        assert p.excludes == ["草稿"]
        assert p.text == "注意力"

    def test_双减号不当成排除(self) -> None:
        # `--flag` 这种是命令行写法，不是排除
        p = parse_query("命令 --verbose")
        assert p.excludes == []


class TestPhrase:
    def test_摘出精确短语但留在正向查询里(self) -> None:
        p = parse_query('"注意力机制" 论文')
        assert p.phrases == ["注意力机制"]
        # 短语是正向内容，关键词那一路要靠引号做精确匹配，所以 text 里得留着
        assert '"注意力机制"' in p.text

    def test_喂给向量的串不带引号(self) -> None:
        # 引号是给 FTS 看的语法符号，嵌入模型看不懂，留着只是掺两个无意义字符
        p = parse_query('"注意力机制" 论文')
        assert '"' not in p.semantic_text()
        assert "注意力机制" in p.semantic_text()

    def test_短语要显示给用户看(self) -> None:
        assert "精确短语：注意力机制" in describe(parse_query('"注意力机制" 论文'))

    def test_短语和排除同时用(self) -> None:
        p = parse_query('"注意力机制" -综述')
        assert p.phrases == ["注意力机制"]
        assert p.excludes == ["综述"]


class TestNoQuery:
    def test_空查询不炸(self) -> None:
        p = parse_query("")
        assert p.text == ""
        assert p.excludes == []
        assert p.phrases == []

    def test_只有排除词时正向查询为空(self) -> None:
        # 这是合法输入，交给上层决定怎么办（当前是走"按时间列内容"那条路）
        p = parse_query("-草稿")
        assert p.excludes == ["草稿"]
        assert p.text == ""
