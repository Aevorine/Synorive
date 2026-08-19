"""
点击自学习（条件热度）
====================================================================
盯三件事：

  ① **条件热度和全局热度不是一回事。** `items.open_count` 是"你常开哪些文件"，
     它回答不了"搜『预算』时我每次都得往下翻三条才找到那份" ——
     那份报告在别的查询下并不该被提上来。

  ② **学出来的偏好不能压过内容相关性。** 一旦压过，就形成
     "点过的更容易被点、更容易被点又更容易被提上来"的回路，
     库里其余内容被越埋越深，而用户完全看不出发生了什么。

  ③ **清空要立刻生效。** "隐私开关只是把数据藏起来"是最恶劣的一类失败。
"""

from __future__ import annotations

from pathlib import Path

from synorive.store.db import Database
from synorive.store.repository import Repository


def _repo(tmp_path: Path) -> Repository:
    db = Database(tmp_path / "t.db")
    db.initialize()
    return Repository(db)


def _add_item(repo: Repository, item_id: str, title: str) -> None:
    conn = repo.db.connect()
    conn.execute(
        """INSERT INTO items (id, fingerprint, modality, source, status, title, locator,
                              created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (item_id, f"fp-{item_id}", "text", "file", "done", title, f"D:/x/{item_id}.md",
         "2026-08-01T00:00:00", "2026-08-01T00:00:00"),
    )


def test_记一次点击并按词查回来(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add_item(repo, "a", "季度预算报告")
    repo.record_click("预算 报告", "a")
    got = repo.clicks_for_terms(["预算"])
    assert got == {"a": 1}


def test_同一个词点多次会累加(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add_item(repo, "a", "x")
    for _ in range(3):
        repo.record_click("预算", "a")
    assert repo.clicks_for_terms(["预算"])["a"] == 3


def test_不同词互不影响(tmp_path: Path) -> None:
    """条件热度的全部意义就在这里：换个词，这条就不该被提上来。"""
    repo = _repo(tmp_path)
    _add_item(repo, "a", "x")
    _add_item(repo, "b", "y")
    repo.record_click("预算", "a")
    repo.record_click("会议", "b")
    assert repo.clicks_for_terms(["预算"]) == {"a": 1}
    assert repo.clicks_for_terms(["会议"]) == {"b": 1}
    assert repo.clicks_for_terms(["完全无关的词"]) == {}


def test_record_open_带查询词时两个热度都涨(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add_item(repo, "a", "x")
    repo.record_open("a", query="预算 报告")
    conn = repo.db.connect()
    assert int(conn.execute("SELECT open_count FROM items WHERE id='a'").fetchone()[0]) == 1
    assert repo.clicks_for_terms(["预算"])["a"] == 1


def test_record_open_不带查询词时只涨全局热度(tmp_path: Path) -> None:
    """老客户端（安卓端 / MCP / CLI）不会传查询词，不能因此报错。"""
    repo = _repo(tmp_path)
    _add_item(repo, "a", "x")
    repo.record_open("a")
    conn = repo.db.connect()
    assert int(conn.execute("SELECT open_count FROM items WHERE id='a'").fetchone()[0]) == 1
    assert int(conn.execute("SELECT COUNT(*) FROM click_log").fetchone()[0]) == 0


def test_单字词不记(tmp_path: Path) -> None:
    """一个字的词几乎命中一切，记进去只会污染信号。"""
    repo = _repo(tmp_path)
    _add_item(repo, "a", "x")
    repo.record_click("的", "a")
    assert repo.clicks_for_terms(["的"]) == {}


def test_一次点击最多记几个词(tmp_path: Path) -> None:
    """查询很长时全记进去只会摊薄每个词的信号，还让表毫无必要地增长。"""
    repo = _repo(tmp_path)
    _add_item(repo, "a", "x")
    repo.record_click("预算 报告 会议 纪要 附件 表格 补充 说明 修订", "a")
    conn = repo.db.connect()
    n = int(conn.execute("SELECT COUNT(*) FROM click_log WHERE item_id='a'").fetchone()[0])
    assert n <= repo.CLICK_TERMS_MAX


def test_清空立刻生效且报出确切条数(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add_item(repo, "a", "x")
    repo.record_click("预算 报告", "a")
    assert repo.clicks_for_terms(["预算"])
    n = repo.clear_click_log()
    assert n >= 1
    # 🔴 不是"不显示了"，是真的查不到了
    assert repo.clicks_for_terms(["预算"]) == {}


def test_删掉内容时它的点击记录跟着走(tmp_path: Path) -> None:
    """留下孤儿记录的话，同一个 id 被别的内容复用时会把偏好张冠李戴。"""
    repo = _repo(tmp_path)
    _add_item(repo, "a", "x")
    repo.record_click("预算", "a")
    conn = repo.db.connect()
    conn.execute("DELETE FROM items WHERE id='a'")
    assert repo.clicks_for_terms(["预算"]) == {}


class TestWeightSanity:
    def test_默认权重压得住(self) -> None:
        """
        🔴 学出来的偏好一旦压过内容相关性，就会形成正反馈把其余内容越埋越深。
        这里钉住"个性化权重明显小于语义/关键词权重"这条底线。
        """
        from synorive.search.engine import Weights

        w = Weights()
        assert 0 < w.personal < w.semantic
        assert w.personal < w.keyword

    def test_可以完全关掉(self) -> None:
        from synorive.search.engine import Weights

        assert Weights.from_dict({"personal": 0}).personal == 0.0

    def test_老客户端不带这个字段时用默认值不报错(self) -> None:
        from synorive.search.engine import Weights

        w = Weights.from_dict({"semantic": 1.0})
        assert w.personal == 0.25
