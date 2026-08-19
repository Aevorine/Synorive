"""
提案 33-37 五个分析模块
====================================================================
这五个功能的共同特点是：**跑起来永远不报错，错了也只是内容不对**。
证据清单少列一条、快照把"重新入库"报成"删了又加"、时间线全挤在同一天、
简报榜首永远是那几个常见词、副库连不上却静悄悄返回空 ——
全都是"看起来正常"的失败，所以每一条都得单独钉死。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from synorive import briefing, evidence, federation, relations, snapshots
from synorive.store.db import Database
from synorive.store.repository import Repository


def _repo(tmp_path: Path, name: str = "t.db") -> Repository:
    db = Database(tmp_path / name)
    db.initialize()
    return Repository(db)


def _add(
    repo: Repository,
    item_id: str,
    title: str = "",
    locator: str = "",
    fingerprint: str = "",
    content_time: str | None = None,
    created_at: str = "2026-08-01T00:00:00",
    open_count: int = 0,
    last_opened_at: str | None = None,
    status: str = "done",
    error: str | None = None,
    snippet: str | None = "正文",
) -> None:
    repo.db.connect().execute(
        """INSERT INTO items (id, fingerprint, modality, source, status, title, locator,
                              snippet, content_time, created_at, updated_at,
                              open_count, last_opened_at, error)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item_id, fingerprint or f"fp-{item_id}", "text", "file", status,
         title or item_id, locator or f"D:/x/{item_id}.md", snippet,
         content_time, created_at, created_at, open_count, last_opened_at, error),
    )


# ── 33 证据链 ────────────────────────────────────────────────


def test_证据链_文件没动过报未改动_动过报已改动(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    good = tmp_path / "good.txt"
    good.write_bytes(b"hello evidence")
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"original")

    fp_good = hashlib.sha256(b"hello evidence").hexdigest()[:32]
    fp_bad = hashlib.sha256(b"original").hexdigest()[:32]
    _add(repo, "g", locator=str(good), fingerprint=fp_good)
    _add(repo, "b", locator=str(bad), fingerprint=fp_bad)

    bad.write_bytes(b"someone edited this after it was filed")  # 入库之后被改了

    chain = evidence.build_chain(repo, ["g", "b"], note="季报引用")
    by_id = {s["itemId"]: s for s in chain["sources"]}
    assert by_id["g"]["status"] == evidence.OK
    assert by_id["b"]["status"] == evidence.CHANGED
    assert chain["summary"]["total"] == 2
    assert chain["summary"][evidence.CHANGED] == 1


def test_证据链_源文件没了要显式报出来而不是跳过(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, "gone", locator=str(tmp_path / "不存在.txt"))
    chain = evidence.build_chain(repo, ["gone"])
    assert chain["sources"][0]["status"] == evidence.MISSING
    assert chain["sources"][0]["note"]


def test_证据链_库里查不到的id也要列出来(tmp_path: Path) -> None:
    """🔴 静默丢掉的话，清单会比实际引用的少几条，而用户不会发现。"""
    repo = _repo(tmp_path)
    _add(repo, "a", locator=str(tmp_path / "a.txt"))
    (tmp_path / "a.txt").write_bytes(b"x")
    chain = evidence.build_chain(repo, ["a", "从来没有过的id"])
    assert len(chain["sources"]) == 2
    assert chain["sources"][1]["status"] == evidence.MISSING


def test_证据链_链接类标成无法核对不是通过(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, "u", locator="https://example.com/a")
    assert evidence.build_chain(repo, ["u"])["sources"][0]["status"] == evidence.UNVERIFIABLE


def test_证据链_markdown把结论放最前面(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, "gone", locator=str(tmp_path / "没有.txt"))
    md = evidence.to_markdown(evidence.build_chain(repo, ["gone"], note="备注在这"))
    head = md.split("| # |")[0]
    assert "1 条需要注意" in head
    assert "备注在这" in head


def test_证据链_大文件走抽样并标明没算全(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x00" * (evidence.FULL_HASH_LIMIT + 1024))
    _, full = evidence.file_digest(big)
    assert full is False
    small = tmp_path / "small.bin"
    small.write_bytes(b"abc")
    assert evidence.file_digest(small)[1] is True


# ── 34 快照 ──────────────────────────────────────────────────


def test_快照_对比出新增和删除(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, "keep")
    _add(repo, "will-go")
    s1 = snapshots.take(repo, "第一张")
    assert s1["itemCount"] == 2

    repo.db.connect().execute("DELETE FROM items WHERE id = 'will-go'")
    _add(repo, "brand-new")

    d = snapshots.diff(repo, s1["id"])
    assert d["counts"] == {"added": 1, "removed": 1, "changed": 0, "reingested": 0}
    assert d["added"][0]["itemId"] == "brand-new"
    assert d["removed"][0]["itemId"] == "will-go"


def test_快照_同一份内容换id不算删一条加一条(tmp_path: Path) -> None:
    """🔴 只比 id 的话，重新入库会被报成一删一增 —— 纯噪音，会淹掉真正的变化。"""
    repo = _repo(tmp_path)
    _add(repo, "old-id", fingerprint="SAMEFP")
    s1 = snapshots.take(repo)
    conn = repo.db.connect()
    conn.execute("DELETE FROM items WHERE id = 'old-id'")
    _add(repo, "new-id", fingerprint="SAMEFP")

    d = snapshots.diff(repo, s1["id"])
    assert d["counts"]["added"] == 0
    assert d["counts"]["removed"] == 0
    assert d["counts"]["reingested"] == 1
    assert d["reingested"][0]["wasId"] == "old-id"


def test_快照_原地改动单独算一类(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, "x", fingerprint="V1")
    s1 = snapshots.take(repo)
    repo.db.connect().execute("UPDATE items SET fingerprint = 'V2' WHERE id = 'x'")
    d = snapshots.diff(repo, s1["id"])
    assert d["counts"]["changed"] == 1
    assert d["changed"][0]["wasFingerprint"] == "V1"


def test_快照_两张之间对比而不是只能跟现在比(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, "a")
    s1 = snapshots.take(repo)
    _add(repo, "b")
    s2 = snapshots.take(repo)
    repo.db.connect().execute("DELETE FROM items")  # 现在库空了，但 s1↔s2 的差不受影响
    d = snapshots.diff(repo, s1["id"], s2["id"])
    assert d["counts"]["added"] == 1 and d["counts"]["removed"] == 0


def test_快照_删除和找不到(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, "a")
    s = snapshots.take(repo, "要删的")
    assert snapshots.drop(repo, s["id"]) is True
    assert snapshots.drop(repo, s["id"]) is False
    with pytest.raises(KeyError):
        snapshots.diff(repo, s["id"])


def test_快照_自动清理不碰手动拍的(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _add(repo, "a")
    manual = snapshots.take(repo, "手动留的", auto=False)
    for i in range(snapshots.KEEP_MAX + 3):
        snapshots.take(repo, f"auto{i}", auto=True)
    ids = {s["id"] for s in snapshots.listing(repo, limit=200)}
    assert manual["id"] in ids
    autos = [s for s in snapshots.listing(repo, limit=200) if s["auto"]]
    assert len(autos) <= snapshots.KEEP_MAX


# ── 35 关系时间线 ────────────────────────────────────────────


def _entity(repo: Repository, eid: str, name: str, kind: str = "person") -> None:
    repo.db.connect().execute(
        "INSERT INTO entities (id, kind, name, mention_count) VALUES (?,?,?,1)", (eid, kind, name)
    )


def _mention(repo: Repository, eid: str, item_id: str) -> None:
    # chunk_id 有外键指到 chunks，这里不关心块级定位，传 NULL
    repo.db.connect().execute(
        "INSERT INTO entity_mentions (entity_id, item_id, chunk_id) VALUES (?,?,NULL)",
        (eid, item_id),
    )


def test_关系时间线_同伴按时期分开而不是揉成一团(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _entity(repo, "me", "老王")
    _entity(repo, "a", "小张")
    _entity(repo, "b", "小李")
    _add(repo, "i1", content_time="2026-01-10T00:00:00")
    _add(repo, "i2", content_time="2026-06-10T00:00:00")
    for e in ("me", "a"):
        _mention(repo, e, "i1")
    for e in ("me", "b"):
        _mention(repo, e, "i2")

    tl = relations.timeline(repo, "me", bucket="month")
    by_at = {b["at"]: b for b in tl["buckets"]}
    assert [p["name"] for p in by_at["2026-01"]["peers"]] == ["小张"]
    assert [p["name"] for p in by_at["2026-06"]["peers"]] == ["小李"]


def test_关系时间线_直接给出变化点(tmp_path: Path) -> None:
    """🔴 一堆桶摆着用户还得自己比对；把"这期新来的/走了的"挑出来才省了那一步。"""
    repo = _repo(tmp_path)
    _entity(repo, "me", "老王")
    _entity(repo, "a", "小张")
    _entity(repo, "b", "小李")
    _add(repo, "i1", content_time="2026-01-10T00:00:00")
    _add(repo, "i2", content_time="2026-02-10T00:00:00")
    _mention(repo, "me", "i1"); _mention(repo, "a", "i1")
    _mention(repo, "me", "i2"); _mention(repo, "b", "i2")

    ch = relations.timeline(repo, "me", bucket="month")["changes"]
    assert ch and ch[0]["at"] == "2026-02"
    assert ch[0]["appeared"] == ["小李"] and ch[0]["disappeared"] == ["小张"]


def test_关系时间线_没有内容时间的要标成估算(tmp_path: Path) -> None:
    """🔴 一次性导入的历史资料全按入库时间算，图很漂亮但结论全错，必须标出来。"""
    repo = _repo(tmp_path)
    _entity(repo, "me", "老王")
    _add(repo, "i1", content_time=None, created_at="2026-08-01T00:00:00")
    _mention(repo, "me", "i1")
    b = relations.timeline(repo, "me", bucket="month")["buckets"][0]
    assert b["estimated"] == 1 and b["count"] == 1


def test_关系时间线_季度桶和非法桶(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _entity(repo, "me", "老王")
    _add(repo, "i1", content_time="2026-05-10T00:00:00")
    _mention(repo, "me", "i1")
    assert relations.timeline(repo, "me", bucket="quarter")["buckets"][0]["at"] == "2026-Q2"
    with pytest.raises(ValueError):
        relations.timeline(repo, "me", bucket="十年")
    with pytest.raises(KeyError):
        relations.timeline(repo, "查无此人")


def test_关系时间线_按类型过滤同伴(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _entity(repo, "me", "老王")
    _entity(repo, "p", "小张", "person")
    _entity(repo, "o", "某公司", "org")
    _add(repo, "i1", content_time="2026-01-10T00:00:00")
    for e in ("me", "p", "o"):
        _mention(repo, e, "i1")
    only = relations.timeline(repo, "me", kinds=["org"])["buckets"][0]["peers"]
    assert [p["name"] for p in only] == ["某公司"]


# ── 36 简报 ──────────────────────────────────────────────────


def test_简报_六块都在且卡住的能被挑出来(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    _add(repo, "new", created_at=(now - timedelta(hours=2)).isoformat(timespec="seconds"))
    _add(repo, "broken", created_at="2026-01-01T00:00:00", status="failed", error="解不开")
    _add(repo, "sleepy", created_at="2026-01-01T00:00:00", open_count=0)
    _add(repo, "cold", created_at="2026-01-01T00:00:00", open_count=9,
         last_opened_at="2026-02-01T00:00:00")
    _add(repo, "thin", created_at="2026-01-01T00:00:00", title="", snippet=None)

    b = briefing.build(repo, now=now)
    got = {s["key"]: s for s in b["sections"]}
    assert set(got) == {"fresh", "rising", "stuck", "asleep", "revisit", "thin"}
    assert [x["id"] for x in got["fresh"]["items"]] == ["new"]
    assert "broken" in [x["id"] for x in got["stuck"]["items"]]
    assert "cold" in [x["id"] for x in got["revisit"]["items"]]
    assert "thin" in [x["id"] for x in got["thin"]["items"]]


def test_简报_冒头要跟自己的过去比不是只看次数(tmp_path: Path) -> None:
    """🔴 只看绝对次数的话榜首永远是那几个常见词，看三天就没人再看了。"""
    repo = _repo(tmp_path)
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    _entity(repo, "always", "常见词")
    _entity(repo, "spike", "新话题")
    # 常见词：过去 6 条 + 最近 6 条（没变化）；新话题：过去 0 条 + 最近 3 条
    for i in range(6):
        d = (now - timedelta(days=10 + i)).isoformat(timespec="seconds")
        _add(repo, f"old{i}", content_time=d, created_at=d)
        _mention(repo, "always", f"old{i}")
    for i in range(6):
        d = (now - timedelta(days=1)).isoformat(timespec="seconds")
        _add(repo, f"cur{i}", content_time=d, created_at=d)
        _mention(repo, "always", f"cur{i}")
    for i in range(3):
        d = (now - timedelta(days=1)).isoformat(timespec="seconds")
        _add(repo, f"hot{i}", content_time=d, created_at=d)
        _mention(repo, "spike", f"hot{i}")

    names = [e["name"] for e in briefing.build(repo, now=now)["sections"][1]["entities"]]
    assert names[0] == "新话题"


def test_简报_只出现一次的不算趋势(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    _entity(repo, "once", "只出现一次")
    d = (now - timedelta(days=1)).isoformat(timespec="seconds")
    _add(repo, "i1", content_time=d, created_at=d)
    _mention(repo, "once", "i1")
    assert briefing.build(repo, now=now)["sections"][1]["entities"] == []


def test_简报_纯文本版空库时说人话(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    txt = briefing.to_text(briefing.build(repo, now=datetime(2026, 8, 19, tzinfo=timezone.utc)))
    assert "今天没有需要处理的东西" in txt


# ── 37 联邦检索 ──────────────────────────────────────────────


def _seed_fts(repo: Repository, item_id: str, title: str, text: str) -> None:
    """按真实入库路径写 FTS：存的是分词后的空格序列，不是原文。"""
    from synorive.store.text import to_index_text

    conn = repo.db.connect()
    row = conn.execute("SELECT rowid FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.execute(
        "INSERT INTO items_fts (rowid, title, snippet, locator) VALUES (?,?,?,?)",
        (row["rowid"], to_index_text(title), to_index_text(text), ""),
    )
    conn.execute(
        "INSERT INTO chunks (id, item_id, chunk_index, text, channel) VALUES (?,?,?,?,?)",
        (f"c-{item_id}", item_id, 0, text, "text"),
    )
    crow = conn.execute("SELECT rowid FROM chunks WHERE id = ?", (f"c-{item_id}",)).fetchone()
    conn.execute(
        "INSERT INTO chunks_fts (rowid, text) VALUES (?,?)", (crow["rowid"], to_index_text(text))
    )
    conn.commit()


def test_联邦_能从副库里搜到并标明来自哪个库(tmp_path: Path) -> None:
    main = _repo(tmp_path, "main.db")
    side = _repo(tmp_path, "side.db")
    _add(side, "s1", title="副库里的会议纪要")
    _seed_fts(side, "s1", "副库里的会议纪要", "这是一份关于季度预算的会议纪要")
    side.db.close_all()

    federation.register(main, str(tmp_path / "side.db"), "去年的库")
    out = federation.search(main, "季度预算")
    assert out["keywordOnly"] is True
    assert [h["library"] for h in out["hits"]] == ["去年的库"]
    assert out["libraries"][0]["ok"] is True and out["libraries"][0]["count"] == 1


def test_联邦_副库连不上要单独报出来而不是静悄悄返回空(tmp_path: Path) -> None:
    """🔴 "没命中"和"连不上"处理方式完全不同，混成一个空列表用户就卡住了。"""
    main = _repo(tmp_path, "main.db")
    side_path = tmp_path / "side.db"
    _repo(tmp_path, "side.db").db.close_all()
    federation.register(main, str(side_path), "会掉线的库")
    side_path.unlink()  # 模拟硬盘拔了

    libs = federation.listing(main)
    assert libs[0]["reachable"] is False and "文件不在了" in str(libs[0]["problem"])
    out = federation.search(main, "任何词")
    assert out["libraries"][0]["ok"] is False


def test_联邦_登记时就验一次打不开的当场拒绝(tmp_path: Path) -> None:
    main = _repo(tmp_path, "main.db")
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a sqlite file at all")
    with pytest.raises(FileNotFoundError):
        federation.register(main, str(tmp_path / "根本没有.db"))
    with pytest.raises(ValueError):
        federation.register(main, str(junk))


def test_联邦_停用的库不参与搜索(tmp_path: Path) -> None:
    main = _repo(tmp_path, "main.db")
    side = _repo(tmp_path, "side.db")
    _add(side, "s1", title="预算")
    _seed_fts(side, "s1", "预算", "季度预算表")
    side.db.close_all()
    lib = federation.register(main, str(tmp_path / "side.db"), "副库")
    lid = federation.listing(main)[0]["id"]
    assert federation.search(main, "季度预算")["hits"]
    assert federation.set_enabled(main, lid, False) is True
    assert federation.search(main, "季度预算")["hits"] == []
    assert federation.unregister(main, lid) is True
    assert federation.listing(main) == []
    assert lib["label"] == "副库"


def test_联邦_副库是只读打开绝不会被改坏(tmp_path: Path) -> None:
    """🔴 拿写模式开别人的库会抢锁，症状是那边突然"数据库被锁定"。"""
    import sqlite3

    side = _repo(tmp_path, "side.db")
    side.db.close_all()
    conn = federation._open_ro(tmp_path / "side.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO items (id, fingerprint, modality, source, status, "
                         "locator, created_at, updated_at) VALUES ('x','x','text','file',"
                         "'done','x','x','x')")
    finally:
        conn.close()
