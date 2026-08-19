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

import hashlib
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any

import sqlite_vec

#: 加密后端。装了 sqlcipher3 才有；没装时"整库加密"这个功能整个不可用，
#: 而**不是降级成明文** —— 加密这块唯一不能干的事就是"库没装就用个办法顶一下"。
try:
    import sqlcipher3 as _sqlcipher  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - 取决于运行环境装没装
    _sqlcipher = None

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


def cipher_available() -> bool:
    """能不能做整库加密。界面必须照这个说，不能含糊。"""
    return _sqlcipher is not None


def looks_encrypted(path: Path) -> bool:
    """
    这个文件是加密库吗？

    明文 SQLite 的前 16 字节固定是 `SQLite format 3` + 一个 0 字节；SQLCipher 加密之后
    **连文件头都是密文**（这正是它比"只加密内容"强的地方 —— 连表结构、
    索引名、有多少张表都看不出来）。所以判据就是"开头不是那串魔数"。

    🔴 文件不存在时返回 False，不是抛异常 —— 调用方问的是"要不要用密钥开"，
       而一个还不存在的库当然不用。
    """
    try:
        with path.open("rb") as f:
            return f.read(16) != b"SQLite format 3" + bytes([0])
    except OSError:
        return False


class Database:
    def __init__(self, path: Path, key: str | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        #: 整库加密口令。None = 不加密（明文库，和以前完全一样）。
        #:
        #: 🔴 **口令绝不能走命令行参数。** 进程的 argv 在同一台机器上是
        #:    任何用户都能看到的（任务管理器、tasklist、ps）。桌面端是通过
        #:    环境变量 `SYNORIVE_DB_KEY` 传给引擎子进程的。
        self._key = key
        self._raw_hex: str | None = None
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

    # ── 加密 ────────────────────────────────────────────────

    def _salt_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".salt")

    def _raw_key_hex(self) -> str:
        """
        口令 -> 32 字节原始密钥（十六进制）。只算一次。

        用 stdlib 的 `hashlib.scrypt`，不引额外依赖。

        🔴 **`maxmem` 必须显式给。** OpenSSL 默认上限是 32 MB，而 n=2^15/r=8
           要 32 MB 出头，于是直接抛 `memory limit exceeded` —— 报的是
           "内存超限"，看到的人只会以为是机器内存不够，而实际是这个默认值太小。
           给 64 MB 有余量。

        🔴 **盐单独存一个文件，而且它不是秘密。** 盐的作用是让同一个口令在
           不同的库上派生出不同的密钥（防彩虹表），它本来就可以是公开的。
           把盐也加密起来是个死循环。
        🔴 盐**丢了等于库打不开**，所以它和 .db 放在一起、跟着一起备份。
        """
        if self._raw_hex is not None:
            return self._raw_hex
        assert self._key is not None
        sp = self._salt_path()
        if sp.exists():
            salt = sp.read_bytes()
        else:
            salt = secrets.token_bytes(16)
            sp.write_bytes(salt)
        raw = hashlib.scrypt(
            self._key.encode("utf-8"), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024**2
        )
        self._raw_hex = raw.hex()
        return self._raw_hex

    def encrypt_in_place(self, key: str) -> None:
        """
        把一个**明文**库原地转成加密库。

        走 SQLCipher 的 `sqlcipher_export` —— 它在 SQL 层整库搬运，
        FTS5 的影子表、sqlite-vec 的向量表都跟着走，不用我们自己枚举表。
        自己写"逐表 SELECT/INSERT"的话，一定会漏掉某张影子表，
        而漏掉的表**不报错**，只是搜索从此少一路召回。

        🔴 **先写到临时文件，成了再换名。** 直接就地改写的话，中途断电
           留下的是一个半加密的文件 —— 既打不开也没法回退，用户的库就没了。
        🔴 换名前**必须关掉所有连接**，否则 Windows 上换不了名（文件被占）。
        """
        if _sqlcipher is None:
            raise RuntimeError("没装 sqlcipher3，做不了加密。补上：pip install sqlcipher3-wheels")
        if looks_encrypted(self.path):
            raise RuntimeError("这个库已经是加密的了")

        tmp = self.path.with_suffix(self.path.suffix + ".enc-tmp")
        salt_tmp = tmp.with_suffix(tmp.suffix + ".salt")
        for f in (tmp, salt_tmp):
            if f.exists():
                f.unlink()

        # 目标库的盐和密钥
        target = Database(tmp, key=key)
        raw_hex = target._raw_key_hex()

        self.close_all()
        src = _sqlcipher.connect(str(self.path))
        try:
            src.execute(f"ATTACH DATABASE '{tmp.as_posix()}' AS enc KEY \"x'{raw_hex}'\"")
            src.execute("SELECT sqlcipher_export('enc')")
            src.execute("DETACH DATABASE enc")
        finally:
            src.close()

        if not looks_encrypted(tmp):
            tmp.unlink(missing_ok=True)
            salt_tmp.unlink(missing_ok=True)
            raise RuntimeError("导出的文件看起来不是加密库，已放弃，原库没动")

        # WAL/SHM 是明文库的附属文件，留着会让 SQLite 拿旧数据去回放
        for suf in ("-wal", "-shm"):
            side = Path(str(self.path) + suf)
            side.unlink(missing_ok=True)
        self.path.unlink()
        tmp.replace(self.path)
        salt_tmp.replace(self._salt_path())

        self._key = key
        self._raw_hex = None
        self._initialized = False

    def decrypt_in_place(self, key: str) -> None:
        """加密库转回明文。和上面完全对称，同样先写临时文件再换名。"""
        if _sqlcipher is None:
            raise RuntimeError("没装 sqlcipher3，做不了解密")
        if not looks_encrypted(self.path):
            raise RuntimeError("这个库本来就是明文的")

        tmp = self.path.with_suffix(self.path.suffix + ".plain-tmp")
        tmp.unlink(missing_ok=True)

        probe = Database(self.path, key=key)
        raw_hex = probe._raw_key_hex()
        self.close_all()

        src = _sqlcipher.connect(str(self.path))
        try:
            src.execute(f"PRAGMA key = \"x'{raw_hex}'\"")
            # 口令对不对在这里才知道 —— 先读一下，错了直接抛，不去动原库
            src.execute("SELECT count(*) FROM sqlite_master").fetchone()
            src.execute(f"ATTACH DATABASE '{tmp.as_posix()}' AS plain KEY ''")
            src.execute("SELECT sqlcipher_export('plain')")
            src.execute("DETACH DATABASE plain")
        finally:
            src.close()

        if looks_encrypted(tmp):
            tmp.unlink(missing_ok=True)
            raise RuntimeError("导出的文件仍然是加密的，已放弃，原库没动")

        for suf in ("-wal", "-shm"):
            Path(str(self.path) + suf).unlink(missing_ok=True)
        self.path.unlink()
        tmp.replace(self.path)
        self._salt_path().unlink(missing_ok=True)

        self._key = None
        self._raw_hex = None
        self._initialized = False

    # ── 连接 ────────────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """拿本线程的连接，没有就现开一条。"""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        driver = _sqlcipher if self._key else sqlite3
        if self._key and driver is None:
            # 🔴 **绝不静默退回明文。** 用户开了加密、界面显示"已加密"，
            #    而实际写的是明文库 —— 这是这个项目里最不能出的一种错。
            raise RuntimeError(
                "这个库是加密的，但当前环境没有 sqlcipher3，打不开。"
                "补上：pip install sqlcipher3-wheels"
            )
        conn = driver.connect(
            str(self.path),
            # 分析流水线里等锁是常态，30 秒够了；再久就是真死锁，该报错
            timeout=30.0,
            isolation_level=None,  # 自己管事务，不要 Python 层的隐式 BEGIN
            check_same_thread=False,
        )
        if self._key:
            # PRAGMA key 必须是**第一条**语句，在任何读写之前。
            # 晚一条都会先以明文方式碰到文件头，然后报 "file is not a database"。
            #
            # 🔴 **不能用绑定参数**（PRAGMA 不支持），也**不能直接把口令拼进 SQL** ——
            #    口令里有一个单引号就是一条 SQL 注入。所以走 SQLCipher 的
            #    "原始密钥"形式：自己用 scrypt 把口令派生成 32 字节，
            #    以 `x'<64位十六进制>'` 传进去。十六进制里不可能有引号，
            #    这条路从形状上就注入不了。
            conn.execute(f"PRAGMA key = \"x'{self._raw_key_hex()}'\"")
        # 🔴 row_factory 要用**这个驱动自己的** Row。
        #    混用会抛 `Row() argument 1 must be sqlite3.Cursor, not sqlcipher3...` ——
        #    而且是在第一次 fetchone 时才抛，离真正的原因（连接是另一个驱动开的）很远。
        conn.row_factory = driver.Row

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
