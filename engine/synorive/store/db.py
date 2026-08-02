"""
SQLite 连接管理
====================================================================
一个库文件装下全部：内容、分块、关键词索引（FTS5）、向量索引（sqlite-vec）、
图谱、任务、审计。拷走这个文件就是完整备份。

几个不显然但重要的决定：

① **每个线程一条连接。** sqlite3 的连接不是线程安全的，而分析流水线是多线程/
   多进程的。用 threading.local 给每个线程发一条，比加锁串行化快得多。

② **WAL 模式。** 读不阻塞写、写不阻塞读，且崩溃时靠 WAL 回放恢复，
   验收标准 A14「分析中强杀进程库文件不损坏」靠它。

③ **向量表延迟建。** 维度取决于用哪个嵌入模型，模型还没下载时建不了。
   所以建库时不建，第一次要写向量时按实际维度建，并把模型 id 记进 meta_kv
   —— 换模型时靠它判断要不要重建（E15 模型热插拔）。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import sqlite_vec

SCHEMA_VERSION = 1
_SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False
        self.capabilities: dict[str, Any] = {}

    # ── 连接 ────────────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """拿本线程的连接，没有就现开一条。"""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        conn = sqlite3.connect(
            str(self.path),
            # 分析流水线里等锁是常态，30 秒够了；再久就是真死锁，该报错
            timeout=30.0,
            isolation_level=None,  # 自己管事务，不要 Python 层的隐式 BEGIN
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row

        # 加载 sqlite-vec 扩展
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
        finally:
            # 用完立刻关掉，别给后续 SQL 留下加载任意扩展的口子
            try:
                conn.enable_load_extension(False)
            except (AttributeError, sqlite3.OperationalError):
                pass

        # 每条连接都要设的 PRAGMA（WAL 是库级的，只需设一次，但重设无害）
        for pragma in (
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA foreign_keys = ON",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA busy_timeout = 30000",
            "PRAGMA cache_size = -65536",
        ):
            conn.execute(pragma)

        self._local.conn = conn
        return conn

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ── 建库 ────────────────────────────────────────────────

    def initialize(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            conn = self.connect()
            self.capabilities = probe_capabilities(conn)

            # 硬性必需：少了这三样检索根本没法工作
            required = ("fts5", "unicode61", "sqlite_vec")
            missing = [k for k in required if not self.capabilities.get(k)]
            if missing:
                raise RuntimeError(
                    f"SQLite 缺少必需能力：{missing}。实测到的能力：{self.capabilities}"
                )
            # trigram 只影响「标题子串兜底」这一路，缺了降级不中断
            if not self.capabilities.get("trigram"):
                self.capabilities["degraded"] = "trigram 不可用，标题子串搜索关闭"

            conn.executescript(_SCHEMA_SQL)
            cur = conn.execute("SELECT value FROM meta_kv WHERE key = 'schema_version'")
            row = cur.fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta_kv (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            self._initialized = True

    # ── 向量表：维度已知时才建 ────────────────────────────────

    def ensure_text_vector_table(self, dim: int, model_id: str) -> bool:
        """兼容旧名。文本向量表现在只管 vec_chunks，图像的走 ensure_image_vector_table。"""
        return self.ensure_vector_tables(dim, model_id)

    def ensure_vector_tables(self, dim: int, model_id: str) -> bool:
        """
        按实际嵌入维度建向量表。返回 True 表示这次真的建了（或重建了）。

        换模型时维度可能变（BGE-small 512 维 vs BGE-base 768 维），
        维度不一致的向量放一起是纯粹的垃圾，所以维度变了必须重建，
        重建就意味着全库要重新算向量 —— E15 模型热插拔会做成后台渐进迁移。
        """
        conn = self.connect()
        cur = conn.execute("SELECT value FROM meta_kv WHERE key = 'embed_model'")
        row = cur.fetchone()
        current = row["value"] if row else None
        want = f"{model_id}:{dim}"

        if current == want:
            return False

        conn.execute("BEGIN")
        try:
            if current is not None:
                # 维度或模型变了，旧向量作废
                conn.execute("DROP TABLE IF EXISTS vec_chunks")

            # ⚠️ 这里**只建 vec_chunks**。vec_items 归图像模型管
            #    （ensure_image_vector_table）。两个模型的维度和语义空间都不一样，
            #    早期版本在这儿一起建了 vec_items，结果图像向量被文本模型的维度定义，
            #    写进去直接维度不匹配报错。
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
                f"  chunk_rowid INTEGER PRIMARY KEY,"
                f"  embedding FLOAT[{dim}]"
                f")"
            )
            conn.execute(
                "INSERT INTO meta_kv (key, value) VALUES ('embed_model', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (want,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return True

    def ensure_image_vector_table(self, dim: int, model_id: str) -> bool:
        """
        图像向量表。和文本向量表分开建，因为**维度和语义空间都不一样** ——
        CLIP 的 512 维和 BGE 的 512 维虽然数字一样，但根本不在一个空间里，
        混在一张表里查出来的"最近邻"毫无意义。
        """
        conn = self.connect()
        row = conn.execute("SELECT value FROM meta_kv WHERE key = 'image_model'").fetchone()
        current = row["value"] if row else None
        want = f"{model_id}:{dim}"
        if current == want:
            return False

        conn.execute("BEGIN")
        try:
            if current is not None:
                conn.execute("DROP TABLE IF EXISTS vec_items")
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0("
                f"  item_rowid INTEGER PRIMARY KEY,"
                f"  embedding FLOAT[{dim}]"
                f")"
            )
            conn.execute(
                "INSERT INTO meta_kv (key, value) VALUES ('image_model', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (want,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return True

    # ── 统计 ────────────────────────────────────────────────

    def size_mb(self) -> float:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total / 1024 / 1024

    def count_items(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
        return int(row["n"]) if row else 0


def probe_capabilities(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    实测 SQLite 到底有哪些能力 —— 不看版本号猜，直接建个临时表试。

    这一段是有意写成"真的去建表"而不是"查 compile_options"的：
    编译选项写着有、实际用起来报错的情况真实存在，而这种错要是留到
    第一次索引时才炸，用户已经等了十分钟了。
    """
    caps: dict[str, Any] = {"sqlite_version": sqlite3.sqlite_version}

    # FTS5
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._cap_fts USING fts5(x)")
        conn.execute("DROP TABLE temp._cap_fts")
        caps["fts5"] = True
    except sqlite3.OperationalError as e:
        caps["fts5"] = False
        caps["fts5_error"] = str(e)

    # trigram 分词器 —— 只用于标题子串兜底，不是主索引
    #
    # ⚠️ 测试用例必须用 **≥3 个字符** 的查询串。trigram 就是三字组索引，
    #    2 字查询命中 0 是它的正常行为，不是它坏了。
    #    （这里踩过一次：拿「分词」两个字测，判定 trigram 不可用，引擎直接起不来。）
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._cap_tri USING fts5(x, tokenize='trigram')")
        conn.execute("INSERT INTO temp._cap_tri(x) VALUES ('中文分词测试')")
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM temp._cap_tri WHERE x MATCH '\"中文分\"'"
        ).fetchone()
        conn.execute("DROP TABLE temp._cap_tri")
        caps["trigram"] = bool(row and row["n"] == 1)
        if not caps["trigram"]:
            caps["trigram_error"] = "建表成功但三字查询也匹配不到，分词器可能是残的"
    except sqlite3.OperationalError as e:
        caps["trigram"] = False
        caps["trigram_error"] = str(e)

    # unicode61 —— 中文检索的**主索引**靠它（配合入库前 jieba 分词）
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE temp._cap_u61 USING fts5(x, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        conn.execute("INSERT INTO temp._cap_u61(x) VALUES ('中文 分词 测试 文档')")
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM temp._cap_u61 WHERE x MATCH '\"分词\"'"
        ).fetchone()
        conn.execute("DROP TABLE temp._cap_u61")
        # 两字中文词必须能命中 —— 这才是「搜索」「视频」这类查询的生死线
        caps["unicode61"] = bool(row and row["n"] == 1)
        if not caps["unicode61"]:
            caps["unicode61_error"] = "预分词后两字词仍匹配不到，主索引不可用"
    except sqlite3.OperationalError as e:
        caps["unicode61"] = False
        caps["unicode61_error"] = str(e)

    # sqlite-vec
    try:
        row = conn.execute("SELECT vec_version() AS v").fetchone()
        caps["sqlite_vec"] = True
        caps["sqlite_vec_version"] = row["v"] if row else None
    except sqlite3.OperationalError as e:
        caps["sqlite_vec"] = False
        caps["sqlite_vec_error"] = str(e)

    return caps
