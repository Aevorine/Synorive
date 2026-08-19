"""
自定义同义词
====================================================================
内置词表不可能知道"小李"指的是谁 —— 每个人的黑话和缩写只有他自己知道。

三条不能破的规矩，破了都不报错、只是结果悄悄变得莫名其妙：
  ① **展开只做一层 OR，不改 AND 结构。** 否则加一对同义词之后，
     整条查询会从"全都要命中"变成"命中一个就算"，结果集暴涨。
  ② **排除词不展开。** 写 `-草稿` 是明确排掉这个词，顺带排掉它的同义词
     是替用户做了一个他没说过的决定。
  ③ **不做传递闭包。** a=b、b=c 时搜 a 不扩到 c —— 加了几对之后
     很容易把两个不相干的概念连起来，表现是"搜什么都出来一大堆无关的"。
"""

from __future__ import annotations

from pathlib import Path

from synorive.store.db import Database
from synorive.store.repository import Repository
from synorive.store.text import to_query, to_query_with_synonyms


def _repo(tmp_path: Path) -> Repository:
    db = Database(tmp_path / "t.db")
    db.initialize()
    return Repository(db)


class TestStore:
    def test_加了能读回来(self, tmp_path: Path) -> None:
        r = _repo(tmp_path)
        assert r.add_synonym("小李", "李明")
        items = r.list_synonyms()
        assert len(items) == 1
        assert items[0]["a"] == "小李" and items[0]["b"] == "李明"

    def test_双向(self, tmp_path: Path) -> None:
        """单向同义在实际使用里几乎总是让人困惑。"""
        r = _repo(tmp_path)
        r.add_synonym("小李", "李明")
        m = r.synonym_map()
        assert m["小李"] == ["李明"]
        assert m["李明"] == ["小李"]

    def test_空词和自己等于自己都不收(self, tmp_path: Path) -> None:
        r = _repo(tmp_path)
        assert not r.add_synonym("", "李明")
        assert not r.add_synonym("小李", "  ")
        assert not r.add_synonym("小李", "小李")
        assert r.list_synonyms() == []

    def test_重复加不会变成两条(self, tmp_path: Path) -> None:
        r = _repo(tmp_path)
        r.add_synonym("小李", "李明")
        r.add_synonym("小李", "李明")
        assert len(r.list_synonyms()) == 1

    def test_删掉之后就不再扩展(self, tmp_path: Path) -> None:
        r = _repo(tmp_path)
        r.add_synonym("小李", "李明")
        r.remove_synonym("小李", "李明")
        assert r.synonym_map() == {}

    def test_不做传递闭包(self, tmp_path: Path) -> None:
        r = _repo(tmp_path)
        r.add_synonym("a", "b")
        r.add_synonym("b", "c")
        m = r.synonym_map()
        assert set(m["a"]) == {"b"}, "a 不该扩到 c —— 传递闭包会把不相干的概念连起来"
        assert set(m["c"]) == {"b"}


class TestQueryExpansion:
    def test_展开成_OR_但词之间仍是_AND(self) -> None:
        q = to_query_with_synonyms("小李 方案", {"小李": ["李明"]})
        assert '"小李" OR "李明"' in q
        assert " AND " in q, "多个词之间必须还是 AND，否则结果集会暴涨"

    def test_排除词不展开(self) -> None:
        q = to_query_with_synonyms("小李 -方案", {"小李": ["李明"], "方案": ["技术方案"]})
        assert "NOT" in q
        # 排除项里不该出现同义词
        neg = q.split("NOT", 1)[1]
        assert "技术" not in neg

    def test_没有同义词表时和原来完全一样(self) -> None:
        assert to_query_with_synonyms("小李 方案", {}) == to_query("小李 方案")
        assert to_query_with_synonyms("小李 方案", None) == to_query("小李 方案")

    def test_表里没有的词原样不动(self) -> None:
        q = to_query_with_synonyms("向量 检索", {"小李": ["李明"]})
        assert q == to_query("向量 检索")

    def test_空查询不炸(self) -> None:
        assert to_query_with_synonyms("", {"a": ["b"]}) == ""

    def test_一个词的同义词有上限(self) -> None:
        """挂十几个同义词的话单条查询会膨胀得离谱，FTS 也会慢下来。"""
        many = [f"别名{i}" for i in range(20)]
        q = to_query_with_synonyms("原词", {"原词": many})
        assert q.count(" OR ") <= 6
