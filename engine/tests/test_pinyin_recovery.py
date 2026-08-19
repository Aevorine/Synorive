"""
拼音纠错：打 `jiqixuexi` 也能找到「机器学习」
====================================================================
治的是一种很常见、而现在**一条结果都没有**的输入：中文输入法没切换。
用户只会以为库里没这东西。

编辑距离那条路（`_did_you_mean`）对这个完全无能为力 ——
拉丁串和汉字词之间的编辑距离就是词长本身。所以必须单独一条。

两条不能破的规矩：
  ① **只从库里真有的词里挑候选。** 建议一个库里没有的"正确写法"，
     用户点进去还是零结果，等于骗他点了一下。
  ② **英文查询不能误伤。** `transformer` 算出来的拼音串匹配不上任何汉字词，
     应该什么都不建议，而不是硬凑一个。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from synorive.search.recovery import RecoveryPlanner
from synorive.store.db import Database
from synorive.store.text import to_index_text

pytest.importorskip("pypinyin", reason="没装 pypinyin 时这条补救路本来就该整条跳过")


def _planner(tmp_path: Path, words: list[str], hits: dict[str, int]) -> RecoveryPlanner:
    """建一个只有词表的最小库；`hits` 决定某个查询能搜到几条。"""
    db = Database(tmp_path / "t.db")
    db.initialize()
    conn = db.connect()
    for i, w in enumerate(words):
        conn.execute(
            """INSERT INTO items (id, fingerprint, modality, source, status, title, locator,
                                  created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (f"i{i}", f"fp{i}", "text", "file", "done", w, f"D:/x/{i}.md",
             "2026-08-01T00:00:00", "2026-08-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO items_fts (rowid, title, snippet, locator) VALUES (?,?,?,?)",
            (i + 1, to_index_text(w), "", ""),
        )
        # 🔴 词表读的是 **chunks_fts**（见 recovery._vocabulary），不是 items_fts。
        #    只塞 items_fts 的话词表恒为空，纠错静默不工作 —— 这个测试第一版就踩了。
        conn.execute(
            "INSERT INTO chunks (id, item_id, chunk_index, text) VALUES (?,?,?,?)",
            (f"c{i}", f"i{i}", 0, w),
        )
        conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?,?)", (i + 1, to_index_text(w)))
    conn.commit()
    return RecoveryPlanner(lambda: db.connect(), lambda q, f: hits.get(q.strip(), 0))


def test_整串拼音命中库里的词(tmp_path: Path) -> None:
    p = _planner(tmp_path, ["机器学习", "向量检索"], {"机器 学习": 7})
    plan = p.plan("jiqixuexi", {}, total_items=2)
    labels = [s["label"] for s in plan["suggestions"]]
    # 词表是分好词的，所以切出来是「机器 学习」两个词，不是整串
    assert any("机器" in x and "学习" in x and "拼音" in x for x in labels), labels


def test_点进去要真的有结果(tmp_path: Path) -> None:
    """建议一个搜出来还是 0 条的词，等于骗用户点一下。"""
    p = _planner(tmp_path, ["机器学习"], {})  # 任何查询都是 0 条
    plan = p.plan("jiqixuexi", {}, total_items=1)
    assert not any("拼音" in s["label"] for s in plan["suggestions"])


def test_只打了一半也能切出来(tmp_path: Path) -> None:
    """`jiqixue` 少打了最后一个音节，前面切得出来的部分照样给建议。"""
    p = _planner(tmp_path, ["机器学习"], {"机器": 3})
    plan = p.plan("jiqixue", {}, total_items=1)
    assert any("机器" in s["label"] and "拼音" in s["label"] for s in plan["suggestions"])


def test_英文查询不误伤(tmp_path: Path) -> None:
    p = _planner(tmp_path, ["机器学习", "向量检索"], {"机器学习": 5, "向量检索": 5})
    plan = p.plan("transformer", {}, total_items=2)
    assert not any("拼音" in s["label"] for s in plan["suggestions"])


def test_混着汉字时只纠拉丁那一截(tmp_path: Path) -> None:
    """
    `机器xuexi` 是"打到一半忘了切输入法"。该纠的是 xuexi 这一截，
    前面的汉字原样留着 —— 整句重写会把用户已经打对的部分也换掉。
    """
    # 只有 xuexi 那一截被替换掉，前面的汉字原样保留 → "机器学习"
    p = _planner(tmp_path, ["机器学习"], {"机器学习": 5})
    plan = p.plan("机器xuexi", {}, total_items=1)
    labels = [s["label"] for s in plan["suggestions"]]
    assert any("学习" in x and "拼音" in x for x in labels), labels


def test_太短的拉丁串不触发(tmp_path: Path) -> None:
    """三个字母以内的串歧义太大，建议出来多半是错的。"""
    p = _planner(tmp_path, ["机器学习"], {"机器学习": 5})
    plan = p.plan("ji", {}, total_items=1)
    assert not any("拼音" in s["label"] for s in plan["suggestions"])


def test_拼音表建一次就复用(tmp_path: Path) -> None:
    p = _planner(tmp_path, ["机器学习"], {"机器学习": 5})
    first = p._pinyin_table()
    second = p._pinyin_table()
    assert first is second, "拼音表每次重算的话，搜不到东西时会明显卡一下"


def test_库为空时整条路都不走(tmp_path: Path) -> None:
    p = _planner(tmp_path, [], {})
    plan = p.plan("jiqixuexi", {}, total_items=0)
    assert plan["reason"] == "empty-library"
    assert plan["suggestions"] == []


def test_老库没有词表时不报错(tmp_path: Path) -> None:
    """fts5vocab 表可能不存在。纠错功能没了不影响搜索，绝不能抛。"""
    db = Database(tmp_path / "t.db")
    db.initialize()

    def broken() -> sqlite3.Connection:
        raise sqlite3.OperationalError("no such table: items_fts_v")

    p = RecoveryPlanner(broken, lambda q, f: 0)
    plan = p.plan("jiqixuexi", {}, total_items=5)
    assert isinstance(plan["suggestions"], list)
