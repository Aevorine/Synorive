"""
整库加密
====================================================================
「电脑丢了、硬盘被拆走，别人拿到的是一堆乱码」——这句话必须是真的。

所以这组测试盯的不是"函数返回了什么"，而是**磁盘上那个文件里到底有没有明文**。
一个只在界面上显示"已加密"、而文件里明文照旧的实现，能通过任何形式的
接口测试，也能骗过所有人 —— 只有直接去读那个文件才拆得穿。

三条红线：
  ① 加密之后，原文**一个字节都不能**出现在文件里。
  ② 错口令、不带口令都必须打不开，**不能降级成明文读**。
  ③ 没装 sqlcipher 时**整个功能不可用**，不是"退回明文继续跑" ——
     后者会让用户以为自己的库加密了，而实际是裸的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synorive.store.db import Database, cipher_available, looks_encrypted
from synorive.store.text import to_index_text

pytestmark = pytest.mark.skipif(
    not cipher_available(), reason="没装 sqlcipher3-wheels 时整库加密本来就不可用"
)

SECRET = "第三季度预算绝密内容XYZ"
PW = "我的口令-a'b;--"  # 故意带引号和分号：PRAGMA key 是拼字符串的，这是注入面


def _seed(db: Database) -> None:
    c = db.connect()
    c.execute(
        """INSERT INTO items (id,fingerprint,modality,source,status,title,locator,
                              created_at,updated_at)
           VALUES ('a','fp','text','file','done',?,'D:/x.md','t','t')""",
        (SECRET,),
    )
    c.execute("INSERT INTO chunks (id,item_id,chunk_index,text) VALUES ('c','a',0,?)", (SECRET,))
    # FTS 表里存的是**分词后**的序列，不是原文（见 store/text.to_index_text）。
    # 塞原文进去的话 MATCH 一个词永远命中不了 —— 那是测试自己的错，不是迁移的错
    c.execute("INSERT INTO chunks_fts (rowid,text) VALUES (1,?)", (to_index_text(SECRET),))


class TestEncryptedFromScratch:
    def test_文件里没有明文(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "e.db", key=PW)
        db.initialize()
        _seed(db)
        db.close_all()
        raw = (tmp_path / "e.db").read_bytes()
        assert SECRET.encode("utf-8") not in raw, "原文出现在了加密库文件里"
        assert not raw.startswith(b"SQLite format 3"), "连文件头都该是密文"

    def test_对口令读得回来(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "e.db", key=PW)
        db.initialize()
        _seed(db)
        db.close_all()
        db2 = Database(tmp_path / "e.db", key=PW)
        assert db2.connect().execute("SELECT title FROM items").fetchone()[0] == SECRET
        db2.close_all()

    def test_错口令打不开(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "e.db", key=PW)
        db.initialize()
        _seed(db)
        db.close_all()
        with pytest.raises(Exception):
            Database(tmp_path / "e.db", key="错口令").connect().execute(
                "SELECT 1 FROM items"
            ).fetchone()

    def test_不带口令打不开(self, tmp_path: Path) -> None:
        """🔴 这条最要紧：不能因为没给口令就"退回明文读"。"""
        db = Database(tmp_path / "e.db", key=PW)
        db.initialize()
        _seed(db)
        db.close_all()
        with pytest.raises(Exception):
            Database(tmp_path / "e.db").connect().execute("SELECT 1 FROM items").fetchone()

    def test_口令里带引号分号也正常(self, tmp_path: Path) -> None:
        """PRAGMA key 不支持绑定参数，是拼字符串的 —— 所以派生成十六进制再传。"""
        db = Database(tmp_path / "q.db", key="'; DROP TABLE items; --")
        db.initialize()
        _seed(db)
        db.close_all()
        assert looks_encrypted(tmp_path / "q.db")
        db2 = Database(tmp_path / "q.db", key="'; DROP TABLE items; --")
        assert db2.connect().execute("SELECT count(*) FROM items").fetchone()[0] == 1
        db2.close_all()

    def test_不给口令时仍然建明文库(self, tmp_path: Path) -> None:
        """不开加密的人不该被牵连 —— 行为要和以前完全一样。"""
        db = Database(tmp_path / "p.db")
        db.initialize()
        _seed(db)
        db.close_all()
        assert not looks_encrypted(tmp_path / "p.db")


class TestMigration:
    def test_明文库转加密后明文消失且数据都在(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "lib.db")
        db.initialize()
        _seed(db)
        db.close_all()
        assert SECRET.encode() in (tmp_path / "lib.db").read_bytes()

        db.encrypt_in_place(PW)

        raw = (tmp_path / "lib.db").read_bytes()
        assert SECRET.encode() not in raw, "转换之后明文还在文件里"
        assert looks_encrypted(tmp_path / "lib.db")

        db2 = Database(tmp_path / "lib.db", key=PW)
        db2.initialize()
        c = db2.connect()
        assert c.execute("SELECT title FROM items").fetchone()[0] == SECRET
        assert c.execute("SELECT text FROM chunks").fetchone()[0] == SECRET
        # 🔴 FTS 的影子表最容易在"自己逐表搬运"的实现里被漏掉，
        #    而漏掉不报错，只是从此少一路召回
        assert c.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '预算'"
        ).fetchall(), "FTS 影子表没跟着迁移过来（搜不到词）"
        # 影子表本身也要在 —— contentless FTS 的数据全在那几张 _data/_idx 表里
        shadows = {
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'chunks_fts%'"
            ).fetchall()
        }
        assert "chunks_fts_data" in shadows and "chunks_fts_idx" in shadows, (
            f"FTS 的影子表没全迁过来：{sorted(shadows)}"
        )
        db2.close_all()

    def test_加密库能解回明文(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "lib.db")
        db.initialize()
        _seed(db)
        db.close_all()
        db.encrypt_in_place(PW)
        db.decrypt_in_place(PW)
        assert not looks_encrypted(tmp_path / "lib.db")
        db2 = Database(tmp_path / "lib.db")
        assert db2.connect().execute("SELECT title FROM items").fetchone()[0] == SECRET
        db2.close_all()

    def test_已经加密的库不给重复加密(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "lib.db")
        db.initialize()
        _seed(db)
        db.close_all()
        db.encrypt_in_place(PW)
        with pytest.raises(RuntimeError, match="已经是加密"):
            db.encrypt_in_place(PW)

    def test_明文库不给解密(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "lib.db")
        db.initialize()
        db.close_all()
        with pytest.raises(RuntimeError, match="本来就是明文"):
            db.decrypt_in_place(PW)

    def test_解密用错口令时原库不动(self, tmp_path: Path) -> None:
        """🔴 迁移失败绝不能把用户的库弄坏 —— 先写临时文件，成了才换名。"""
        db = Database(tmp_path / "lib.db")
        db.initialize()
        _seed(db)
        db.close_all()
        db.encrypt_in_place(PW)
        before = (tmp_path / "lib.db").read_bytes()
        with pytest.raises(Exception):
            db.decrypt_in_place("错口令")
        assert (tmp_path / "lib.db").read_bytes() == before, "失败之后原库被动过了"


class TestSalt:
    def test_同一个口令在不同库上派生出不同密钥(self, tmp_path: Path) -> None:
        """盐的作用就在这儿：防彩虹表。"""
        a = Database(tmp_path / "a.db", key=PW)
        a.initialize()
        b = Database(tmp_path / "b.db", key=PW)
        b.initialize()
        ka, kb = a._raw_key_hex(), b._raw_key_hex()
        a.close_all()
        b.close_all()
        assert ka != kb

    def test_盐跟着库走(self, tmp_path: Path) -> None:
        """盐丢了库就打不开，所以它必须和 .db 放在一起、跟着一起备份。"""
        db = Database(tmp_path / "e.db", key=PW)
        db.initialize()
        db.close_all()
        assert (tmp_path / "e.db.salt").exists()
