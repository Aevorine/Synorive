"""
提案 33-38 的 HTTP 接口层
====================================================================
上一个文件验的是"模块自己算得对"。这个验的是**它有没有真的挂到路由上、
返回的字段名和界面取的是不是同一个**。

这两件事必须分开测，因为"模块跑得通但没挂上路由"和"挂上了但字段名对不上"
都属于最典型的静默失败：进程正常、日志干净、界面上那一块永远是空的。

这里不启动完整引擎（那要加载模型，几十秒），只挂一个最小 app +
一个只有 repo/config 的假 runtime —— 路由层要验的东西全在这个范围内。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from synorive.api.routes import router
from synorive.store.db import Database
from synorive.store.repository import Repository


@dataclass
class _Config:
    data_dir: Path
    model_dir: Path


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "t.db")
        db.initialize()
        self.repo = Repository(db)
        self.config = _Config(tmp_path, tmp_path / "models")


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = _Runtime(tmp_path)
    with TestClient(app) as c:
        c.tmp_path = tmp_path  # type: ignore[attr-defined]
        c.rt = app.state.runtime  # type: ignore[attr-defined]
        yield c


def _add(client: Any, item_id: str, locator: str, title: str = "标题") -> None:
    client.rt.repo.db.connect().execute(
        """INSERT INTO items (id, fingerprint, modality, source, status, title, locator,
                              created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (item_id, f"fp-{item_id}", "text", "file", "done", title, locator,
         "2026-08-01T00:00:00", "2026-08-01T00:00:00"),
    )


# ── 33 证据链 ────────────────────────────────────────────────


def test_证据链接口_能出markdown(client: Any) -> None:
    f = client.tmp_path / "a.txt"
    f.write_bytes(b"content")
    _add(client, "a", str(f))
    r = client.post("/evidence/chain", json={"itemIds": ["a"], "format": "markdown"})
    assert r.status_code == 200
    body = r.json()
    assert "markdown" in body and "来源核对清单" in body["markdown"]
    assert body["summary"]["total"] == 1


def test_证据链接口_一次太多条要挡住(client: Any) -> None:
    """不挡的话，用户全选一万条点导出，引擎会去读几十 GB 的盘。"""
    r = client.post("/evidence/chain", json={"itemIds": [f"x{i}" for i in range(501)]})
    assert r.status_code == 400


# ── 34 快照 ──────────────────────────────────────────────────


def test_快照接口_拍列删对比一条龙(client: Any) -> None:
    _add(client, "a", "D:/a.txt")
    s = client.post("/snapshots", json={"label": "手动"}).json()
    assert s["itemCount"] == 1

    assert [x["id"] for x in client.get("/snapshots").json()] == [s["id"]]

    _add(client, "b", "D:/b.txt")
    d = client.get(f"/snapshots/{s['id']}/diff").json()
    assert d["counts"]["added"] == 1 and d["other"] == "现在"

    assert client.delete(f"/snapshots/{s['id']}").status_code == 200
    assert client.delete(f"/snapshots/{s['id']}").status_code == 404


def test_快照接口_对不存在的快照报404不是500(client: Any) -> None:
    assert client.get("/snapshots/根本没有/diff").status_code == 404


# ── 35 关系时间线 ────────────────────────────────────────────


def test_关系接口_找人和出时间线(client: Any) -> None:
    conn = client.rt.repo.db.connect()
    conn.execute("INSERT INTO entities (id,kind,name,mention_count) VALUES ('me','person','老王',2)")
    conn.execute("INSERT INTO entities (id,kind,name,mention_count) VALUES ('a','person','小张',1)")
    _add(client, "i1", "D:/1.md")
    conn.execute("UPDATE items SET content_time='2026-03-01T00:00:00' WHERE id='i1'")
    for e in ("me", "a"):
        conn.execute(
            "INSERT INTO entity_mentions (entity_id,item_id,chunk_id) VALUES (?,?,NULL)", (e, "i1")
        )

    found = client.get("/relations/entities", params={"q": "老"}).json()
    assert [e["name"] for e in found] == ["老王"]

    tl = client.get("/relations/me/timeline", params={"bucket": "month"}).json()
    assert tl["entity"]["name"] == "老王"
    assert tl["buckets"][0]["at"] == "2026-03"
    assert [p["name"] for p in tl["buckets"][0]["peers"]] == ["小张"]


def test_关系接口_错误码分得清404和400(client: Any) -> None:
    assert client.get("/relations/查无此人/timeline").status_code == 404
    conn = client.rt.repo.db.connect()
    conn.execute("INSERT INTO entities (id,kind,name,mention_count) VALUES ('me','person','老王',0)")
    assert client.get("/relations/me/timeline", params={"bucket": "十年"}).status_code == 400


# ── 36 简报 ──────────────────────────────────────────────────


def test_简报接口_六块齐全并能出纯文本(client: Any) -> None:
    r = client.get("/briefing", params={"format": "text"}).json()
    keys = [s["key"] for s in r["sections"]]
    assert keys == ["fresh", "rising", "stuck", "asleep", "revisit", "thin"]
    assert "text" in r


def test_简报接口_窗口天数越界要挡(client: Any) -> None:
    assert client.get("/briefing", params={"windowDays": 0}).status_code == 400
    assert client.get("/briefing", params={"windowDays": 999}).status_code == 400


# ── 37 联邦检索 ──────────────────────────────────────────────


def test_联邦接口_加了才搜得到删了就没了(client: Any) -> None:
    side = client.tmp_path / "side.db"
    db = Database(side)
    db.initialize()
    db.close_all()

    r = client.post("/federation/libs", json={"dbPath": str(side), "label": "去年"})
    assert r.status_code == 200

    libs = client.get("/federation/libs").json()
    assert libs[0]["label"] == "去年" and libs[0]["reachable"] is True

    out = client.post("/federation/search", json={"query": "预算"}).json()
    # 🔴 界面要靠这个字段提示"副库只有关键词"，丢了它用户会以为能力一样
    assert out["keywordOnly"] is True

    assert client.post(f"/federation/libs/{libs[0]['id']}/enabled", params={"on": False}).json()[
        "enabled"
    ] is False
    assert client.delete(f"/federation/libs/{libs[0]['id']}").status_code == 200
    assert client.delete(f"/federation/libs/{libs[0]['id']}").status_code == 404


def test_联邦接口_加一个打不开的当场400(client: Any) -> None:
    junk = client.tmp_path / "junk.db"
    junk.write_bytes(b"not a database")
    assert client.post("/federation/libs", json={"dbPath": str(junk)}).status_code == 400
    assert client.post("/federation/libs", json={"dbPath": "D:/没有这个.db"}).status_code == 400


# ── 38 语音提问 ──────────────────────────────────────────────


def test_语音接口_模型没装时说清楚而不是假装能用(client: Any) -> None:
    """🔴 最坏的做法是悄悄退回云端识别 —— 那等于把用户说的话传出去了。"""
    st = client.get("/voice/status").json()
    assert st["available"] is False and st["local"] is True
    assert st["reason"]
    r = client.post("/voice/transcribe", files={"file": ("q.wav", b"x" * 2048, "audio/wav")})
    assert r.status_code == 503


def test_语音接口_太长和太短都挡在前面(client: Any, monkeypatch: Any) -> None:
    import synorive.analyze.transcribe as T

    monkeypatch.setattr(T.Transcriber, "available", lambda self: True)
    big = client.post("/voice/transcribe", files={"file": ("q.wav", b"0" * (4 * 1024 * 1024 + 1))})
    assert big.status_code == 413
    tiny = client.post("/voice/transcribe", files={"file": ("q.wav", b"0" * 10)})
    assert tiny.status_code == 400


def test_语音接口_转完要把临时录音删掉(client: Any, monkeypatch: Any) -> None:
    """🔴 留在磁盘上的录音是这个功能唯一会造成的隐私增量。"""
    import synorive.analyze.transcribe as T

    monkeypatch.setattr(T.Transcriber, "available", lambda self: True)
    monkeypatch.setattr(
        T.Transcriber, "transcribe", lambda self, p: [T.Utterance(text="预算表在哪", start_sec=0.0, end_sec=1.0)]
    )
    r = client.post("/voice/transcribe", files={"file": ("q.wav", b"0" * 4096, "audio/wav")})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "预算表在哪"
    # 识别结果不自动去搜 —— 认错了的话用户会以为是搜索坏了
    assert body["autoSearch"] is False
    assert list((client.tmp_path / "tmp").glob("voice-*.wav")) == []
