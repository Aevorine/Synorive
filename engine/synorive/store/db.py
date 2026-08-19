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

#: 加列迁移表：`(表名, 列名, 列声明)`。
#:
#: 🔴 这条不是可有可无的——`CREATE TABLE IF NOT EXISTS` 只对**全新**数据库
#: 生效，已经建过的库（用户已经索引过内容的那个 .db 文件）不会因为
#: schema.sql 改了就自动多出一列。不补这一步的后果是：新代码写
#: `INSERT INTO chunks (..., section, ...)`，全新安装的用户测不出问题，
#: 而任何一个已经用过这个应用的人一升级就会遇到
#: `sqlite3.OperationalError: table chunks has no column named section`。
#: 这个项目目前没有更完整的迁移框架，加一列就在这张表里加一行，
#: 已经存在的列会被 `_migrate_columns` 自动跳过，天然幂等。
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("chunks", "section", "TEXT"),
    # 摄取任务状态持久化——之前只在内存字典里，引擎重启就没了，
    # `current`/`error`/失败明细列表这些没有专门的列，塞进一个 JSON 列。
    ("jobs", "detail_json", "TEXT"),
)


#: 页缓存与内存映射的档位，按本机物理内存挑。
#:
#: 🔴 **不能写死。** 原来固定 64 MB 页缓存：8 GB 内存的机器上偏保守（十万块以上的库
#: 每次翻页都要重新从磁盘读 B 树内页），4 GB 的上网本上又偏激进。
#:
#: 🔴 **`mmap_size` 对工作线程来说是全新的。** 它原来只写在 `schema.sql` 里，
#: 而那个脚本只在建库时跑一次、只作用于当时那一条连接 —— 引擎是每线程一条连接，
#: 所以真正干活的那些线程上 `mmap_size` 一直是 0（映射关闭），
#: 而 schema.sql 里那句"读放大明显下降"的注释一次都没成立过，也不报错。
#: 现在改成每条连接都设。没有映射时每次读页要走一次 read() 系统调用 + 一次
#: 用户态拷贝；开了之后 SQLite 直接读映射内存。它**不占额外物理内存**——
#: 映射的是文件本身，操作系统按需换页，内存紧张时自己回收。
#:
#: ⚠️ 实测本机 SQLite 3.50.4 接受到 2 GB；有些发行版编译上限更低，
#: 请求超过上限会被**静默截断**（不报错）。截断后仍然远好于 0，所以不做校验。
#:
#: 每条线程一条连接，页缓存是**每连接**的，所以档位按"单连接"算，
#: 别拿总内存直接除。
_TUNING_TIERS: tuple[tuple[int, int, int], ...] = (
    # (物理内存下限 GB, 页缓存 KB, mmap 字节)
    (32, 262144, 8 * 1024**3),   # ≥32G：256 MB 缓存 / 8 GB 映射
    (16, 131072, 4 * 1024**3),   # ≥16G：128 MB / 4 GB
    (8, 65536, 2 * 1024**3),     # ≥8G ：64 MB  / 2 GB（和原来的固定值一致）
    (0, 32768, 512 * 1024**2),   # 更小：32 MB  / 512 MB
)

_tuning_cache: tuple[int, int] | None = None


def _tuning() -> tuple[int, int]:
    """(页缓存 KB, mmap 字节)。只算一次——每开一条连接都去问一遍内存是浪费。"""
    global _tuning_cache
    if _tuning_cache is not None:
        return _tuning_cache
    total_gb = 8.0
    try:
        import psutil

        total_gb = psutil.virtual_memory().total / 1024**3
    except Exception:
        # 问不到就当 8 GB —— 退回原来的固定档位，不比以前差
        pass
    for floor_gb, cache_kb, mmap_bytes in _TUNING_TIERS:
        if total_gb >= floor_gb:
            _tuning_cache = (cache_kb, mmap_bytes)
            return _tuning_cache
    _tuning_cache = (32768, 512 * 1024**2)
    return _tuning_cache


def _migrate_columns(conn: sqlite3.Connection) -> None:
    #: 表不存在时 `PRAGMA table_info` 返回 0 行且**不报错**，于是"列不在里面"
    #: 恒为真，接着 `ALTER TABLE` 抛 `no such table`。老库（建库时还没有 jobs 表）
    #: 升级到新版会在这里直接崩，症状是引擎启动失败而不是迁移失败。
    #: 先问 sqlite_master 拿真实表清单，缺表就跳过——那张表会由
    #: schema.sql 的 CREATE TABLE IF NOT EXISTS 建出来，建出来就自带新列。
    have = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table, column, decl in _COLUMN_MIGRATIONS:
        if table not in have:
            continue
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        #: 所有线程开出来的连接。`close()` 只能关掉**调用它的那个线程**那一条，
        #: 别的线程那几条谁也碰不到 —— 而 SQLite 的文件锁是**进程级**的，
        #: 只要还有一条连接活着，这个 .db 文件在 Windows 上就删不掉、
        #: 也没法安全地拷走做备份。表现是 `shutil.rmtree` 抛
        #: `PermissionError: [WinError 32] 另一个程序正在使用此文件`，
        #: 而它离真正的原因（某个工作线程的连接没关）隔着十万八千里。
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
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
        cache_kb, mmap_bytes = _tuning()
        for pragma in (
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA foreign_keys = ON",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA busy_timeout = 30000",
            f"PRAGMA cache_size = -{cache_kb}",
            f"PRAGMA mmap_size = {mmap_bytes}",
        ):
            conn.execute(pragma)

        self._local.conn = conn
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    def close(self) -> None:
        """只关**当前线程**那一条。别的线程的连接不受影响。"""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            with self._conns_lock:
                if conn in self._all_conns:
                    self._all_conns.remove(conn)
            self._local.conn = None

    def close_all(self) -> int:
        """
        关掉**所有线程**开出来的连接，真正释放这个 .db 文件。返回关掉几条。

        用在"要对库文件本身动手"的场合：删掉它、拷走做备份、换一个库。
        `close()` 做不到这件事 —— 它只管调用者那一条，而 SQLite 的文件锁
        是进程级的，剩下任意一条没关，文件就还锁着。

        🔴 关完之后别的线程再调 `connect()` 会**重新开一条**（这是对的：
        连接是懒建的）。所以调用方要保证这之后不再有人用这个库，
        否则它会静默地又把文件锁上。
        """
        with self._conns_lock:
            conns = list(self._all_conns)
            self._all_conns.clear()
        n = 0
        for c in conns:
            try:
                c.close()
                n += 1
            except sqlite3.Error:
                # 已经关过 / 正在别的线程里用。关不掉就跳过，
                # 这个方法的语义是"尽力释放"，不是"保证全部关掉"
                pass
        self._local.conn = None
        return n

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
            _migrate_columns(conn)
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
