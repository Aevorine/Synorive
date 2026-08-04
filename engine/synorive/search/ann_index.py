"""
向量近似索引 —— A17 拍板：上 ANN，把天花板从 15 万块真正抬到百万
====================================================================
🔴 先说清楚这解决的是什么问题，不解决什么问题：

sqlite-vec 0.1.9 的 `k=` 查询是**暴力线性扫描**，没有 ANN 索引，
成本 ≈ 3.1µs × 块数，严格线性（25 万块 828ms，100 万块 3070ms，
实测数字见 `tests/bench_scale.py` 和台账 A17）。按 A3 的 500ms 门槛倒推，
**实际天花板约 15 万块**。这是这个模块要解决的唯一问题：
让语义检索的延迟不再随库的大小线性变差。

**不解决**：召回质量（ANN 是近似的，代价是极小概率漏掉几个真正最近的邻居，
用的是 HNSW 这个业界最常用的方案，在合理的 expansion 参数下召回率损失
通常 <1%，但"通常"不是"零"——这是主动选择的近似换速度，不是 bug）。

**设计决定，为什么这么做**：

① **usearch 不是 hnswlib**。台账里最早写的是"需装 hnswlib"，
   实测这台机器上 hnswlib 在 Python 3.13 + Windows 下**没有预编译 wheel**，
   要装 Visual C++ Build Tools 才能从源码编译——那是个几百 MB、
   要动系统开发环境的大动作，不该为了一个 Python 包默默做。
   usearch 提供同样的 HNSW 算法、API 更简洁，且**有现成的 cp313-win_amd64 wheel**，
   pip 一条命令装完，不用编译环境。换库不换算法，A17 的目标没有打折扣。

② **只在真的需要时才启用，阈值以下行为完全不变**。
   15 万块以内暴力扫描本来就够快（10.2 万块实测 373.5ms，压线 A3 的 500ms 门槛
   都还有余量），这个规模去启用一套近似索引反而是拿"可能漏掉几个结果"
   去换一个不存在的性能问题。ANN 只在 `ANN_THRESHOLD` 之上才接管查询路径。

③ **增量维护，不是每次查询前现建**。索引随着 `write_chunks` 逐条增量更新
   （和 `vec_chunks` 本身维护的时机完全对齐），启动时从磁盘加载一份持久化的索引，
   不用每次重启都重建；只有"模型换了"或"索引文件损坏/缺失但库已经大到该用 ANN 了"
   这两种情况才需要显式 rebuild（`/api/search/ann/rebuild`）。

④ **model_id 兼容性检查复用 `vec_chunks` 那一套**（`meta_kv.embed_model`）。
   两边共享同一个"模型变了就作废"的判据，不用发明第二套。
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("synorive.ann")

#: 库规模超过这个才启用 ANN；以下沿用暴力扫描（更快更简单更精确）。
#: 定在 15 万——A17 实测的暴力扫描天花板就是这个数字（10.2 万块 373.5ms，
#: 按 3.1µs/块线性外推到 15 万约 465ms，逼近 A3 的 500ms 门槛）
ANN_THRESHOLD = 150_000

#: HNSW 图连接度。usearch 默认值，业界最常用的起点，
#: 召回率/内存/速度的折中点，没有实测理由去偏离默认值就不偏离
CONNECTIVITY = 16
EXPANSION_ADD = 128
EXPANSION_SEARCH = 64

INDEX_FILENAME = "ann_index.usearch"

#: 攒够这么多条改动就在后台落一次盘（见 `_maybe_autosave`）。
#:
#: 5000 是这样估出来的：一次落盘的代价随索引规模走（百万级向量约几秒），
#: 而 5000 条大约是"摄取几百个文档"的量级。取这个数意味着
#: **最坏情况下异常退出丢掉的是几百个文档的语义索引**，
#: 而不是"上次干净关闭以来的全部内容"——后者可能是几万条。
#: 调小 = 更安全但摄取更慢；调大 = 反之。没有实测理由就别动它。
AUTOSAVE_EVERY = 5_000


class AnnIndex:
    """
    单例式封装：一个 Repository 持有一个 AnnIndex（如果模型已就绪的话）。

    线程安全：usearch 的 Index 本身在 C++ 层是线程安全的（官方文档写明
    支持并发 add/search），这里额外加一把锁只是为了保护"要不要重建"
    这个判断和文件读写这两个不是原子操作的地方，不是为了保护 add/search 本身。
    """

    def __init__(self, dim: int, model_tag: str, index_path: Path) -> None:
        self.dim = dim
        self.model_tag = model_tag
        self.index_path = index_path
        self._lock = threading.Lock()
        self._index: Any = None
        self._count = 0
        #: 自上次落盘以来新增/删除了多少条。见 `_maybe_autosave`
        self._dirty = 0
        #: 有没有一次后台落盘正在跑。防止摄取高峰期堆起一串落盘线程
        self._saving = False

    # ── 生命周期 ────────────────────────────────────────────
    def _ensure_index(self) -> Any:
        if self._index is None:
            from usearch.index import Index

            self._index = Index(
                ndim=self.dim, metric="cos", dtype="f32",
                connectivity=CONNECTIVITY, expansion_add=EXPANSION_ADD,
                expansion_search=EXPANSION_SEARCH,
            )
        return self._index

    def load(self) -> bool:
        """从磁盘加载已有索引。返回 False 表示没有可用的文件（不是错误）。"""
        meta_path = self.index_path.with_suffix(".meta")
        if not self.index_path.exists() or not meta_path.exists():
            return False
        saved_tag = meta_path.read_text(encoding="utf-8").strip()
        if saved_tag != self.model_tag:
            log.info("磁盘上的 ANN 索引是给 %s 建的，当前模型是 %s，不能用，需要重建",
                      saved_tag, self.model_tag)
            return False
        try:
            idx = self._ensure_index()
            idx.load(str(self.index_path))
            self._count = len(idx)
            log.info("ANN 索引加载完成：%d 个向量", self._count)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("ANN 索引加载失败，视为不存在，等待重建：%s", e)
            self._index = None
            return False

    def save(self) -> None:
        """
        把整个 HNSW 图序列化到磁盘。

        ⚠️ **全程持锁**。百万级索引序列化要几秒，这几秒里并发的 `add_many`
           会被挡住。这是**有意的**：usearch 在写盘的同时被 add，
           落盘的文件可能是撕裂的 —— 而撕裂的索引下次加载时才会暴露，
           那时候数据早就写进去了。宁可摄取卡一下，也不能存出一份坏索引。
        """
        if self._index is None or self._count == 0:
            return
        with self._lock:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self._index.save(str(self.index_path))
            self.index_path.with_suffix(".meta").write_text(self.model_tag, encoding="utf-8")
            # 只有真的写成功了才清账。失败时保留 _dirty，
            # 下一批改动会再触发一次自动落盘 —— 而不是"失败一次就等到关机"
            self._dirty = 0

    # ── 维护 ────────────────────────────────────────────────
    @property
    def size(self) -> int:
        return self._count

    @property
    def active(self) -> bool:
        """规模够大才真正接管查询——见模块开头②。"""
        return self._count >= ANN_THRESHOLD

    def add(self, rowid: int, vector: list[float]) -> None:
        import numpy as np

        idx = self._ensure_index()
        with self._lock:
            # 同样的"C 扩展只认 NumPy 数组"限制，见 search() 的注释
            idx.add(rowid, np.asarray(vector, dtype="float32"))
            self._count = len(idx)
            self._dirty += 1
        self._maybe_autosave()

    def add_many(self, rowids: list[int], vectors: Any) -> None:
        import numpy as np

        if not rowids:
            return
        idx = self._ensure_index()
        with self._lock:
            idx.add(np.asarray(rowids), np.asarray(vectors, dtype="float32"), threads=0)
            self._count = len(idx)
            self._dirty += len(rowids)
        self._maybe_autosave()

    def remove(self, rowid: int) -> None:
        if self._index is None:
            return
        with self._lock:
            try:
                self._index.remove(rowid)
                self._count = len(self._index)
                self._dirty += 1
            except Exception:  # noqa: BLE001
                # rowid 本来就不在索引里（比如库还小、ANN 从没接管过），
                # 删除一个不存在的 key 不该让删除操作本身失败
                pass

    # ── 自动落盘 ────────────────────────────────────────────

    def _maybe_autosave(self) -> None:
        """
        攒够 `AUTOSAVE_EVERY` 条改动就在后台落一次盘。

        🔴 **要治的病**：在这之前，索引**只在引擎干净关闭时才存盘**
           （`main.py` 的 will_quit）。也就是说，只要发生一次异常退出
           —— 断电、任务管理器强杀、装更新时被结束进程 ——
           上次存盘之后新增的全部向量就都丢了。

           丢了之后的表现极其隐蔽：数据库里 `vec_chunks` 一条不少，
           搜索也不报错，只是**那批内容在语义检索里查不到**了。
           兜底的重建逻辑要下次启动检测到落差才触发，
           而在那之前用户完全不知道自己少搜到了东西。

        🔴 **不是每写一条都存**：一次落盘是把整个 HNSW 图序列化到磁盘，
           几十万向量要几百毫秒到几秒。摄取一万个文件时每条都存，
           光落盘就能把整批摄取拖慢一个数量级。

        🔴 **后台线程 + `_saving` 闸**：摄取高峰期 `add_many` 调得很密，
           不设闸会堆起一串落盘线程同时写同一个文件。
           已经有一次在跑就直接跳过 —— 反正下一批改动还会再触发。

        落盘失败**只记日志不抛**：它是一个优化，失败的后果是回到
        "只在关闭时存"这个原来的行为，而不该让正在进行的摄取整批失败。
        """
        with self._lock:
            if self._dirty < AUTOSAVE_EVERY or self._saving or self._index is None:
                return
            # 只置 _saving，**不在这里清 _dirty** —— 清账的事交给 save()，
            # 而它只在真的写成功之后才清。在这里乐观清掉的话，
            # 一次失败的落盘会让计数归零，于是要再攒满 5000 条才会重试，
            # 中间那一大批"以为存过了"的向量其实一直没落盘
            self._saving = True

        def run() -> None:
            try:
                self.save()
                log.debug("ANN 索引自动落盘完成，当前 %d 个向量", self._count)
            except Exception as e:  # noqa: BLE001
                log.warning("ANN 索引自动落盘失败（不影响检索，关闭时会再试）：%s", e)
            finally:
                with self._lock:
                    self._saving = False

        threading.Thread(target=run, daemon=True, name="ann-autosave").start()

    def search(self, query_vector: list[float], k: int) -> list[tuple[int, float]]:
        """返回 `[(chunk_rowid, distance), ...]`，按距离升序。"""
        if self._index is None or self._count == 0:
            return []
        import numpy as np

        # usearch 认 NumPy 数组，直接喂 list 会在 C 扩展层断言失败——
        # 调用方（engine.py）手上拿到的查询向量本来就是 numpy 数组转出来的 list，
        # 这里转回去，好让 AnnIndex 的公开接口对调用方来说只需要认"一串数字"
        r = self._index.search(np.asarray(query_vector, dtype="float32"), k)
        # 🔴 usearch 的 cos 距离是 `1 - cos_sim`（实测验证过：cos_sim=0.7071
        # 时它返回 0.2929）。全引擎沿用的是 sqlite-vec vec0 默认度量的约定——
        # 拿真实建表实测过：那是**没开方的** L2 距离 `sqrt(2 - 2·cos_sim)`，
        # 不是很多资料里写的"平方 L2"。换算关系不是简单乘 2，是：
        #   u = 1 - cos_sim  →  cos_sim = 1 - u  →  sqlite_vec_style = sqrt(2 - 2(1-u)) = sqrt(2u)
        # 第一版写的是 `× 2.0`——那是我按"平方 L2"这个（同样没实测过的）假设推的，
        # 后来在 `_similarity()` 那边用真实 sqlite-vec 建表测出它是没开方的 L2 时，
        # 才发现这里也得跟着改，不能是简单的倍数关系。两次错都栽在同一件事上：
        # 公式抄了别处的说法就直接用，没有自己拿真实数据核验一遍。
        return [
            (int(k_), math.sqrt(2.0 * max(0.0, float(d))))
            for k_, d in zip(r.keys.tolist(), r.distances.tolist(), strict=True)
        ]

    def rebuild_from_db(self, conn: Any, batch_size: int = 20_000) -> int:
        """
        扫一遍 `vec_chunks` 整表重建索引。**这是唯一的全量重建路径**——
        给已经攒了几十万块、在这个功能上线之前就存在的老库一条升级路径，
        也是模型换了之后唯一能让 ANN 重新可用的办法。

        用 sqlite-vec 自己存的向量重建，而不是重新跑一遍嵌入模型——
        向量内容没变，只是换一种索引结构去组织它们，没道理重新推理一遍。
        """
        import numpy as np
        from usearch.index import Index

        total = conn.execute("SELECT COUNT(*) AS n FROM vec_chunks").fetchone()["n"]
        if total == 0:
            return 0

        new_index = Index(
            ndim=self.dim, metric="cos", dtype="f32",
            connectivity=CONNECTIVITY, expansion_add=EXPANSION_ADD,
            expansion_search=EXPANSION_SEARCH,
        )
        done = 0
        last_rowid = 0
        while True:
            rows = conn.execute(
                "SELECT chunk_rowid, embedding FROM vec_chunks "
                "WHERE chunk_rowid > ? ORDER BY chunk_rowid LIMIT ?",
                (last_rowid, batch_size),
            ).fetchall()
            if not rows:
                break
            ids = np.asarray([int(r["chunk_rowid"]) for r in rows])
            # np.frombuffer 而不是 struct.unpack 逐行拆——重建时是几十万上百万行，
            # Python 层逐个 unpack 会成为这一步真正的瓶颈，向量化转换快一个数量级
            vecs = np.vstack([
                np.frombuffer(r["embedding"], dtype="float32") for r in rows
            ])
            new_index.add(ids, vecs, threads=0)
            last_rowid = int(ids[-1])
            done += len(ids)
            if done % (batch_size * 5) == 0:
                log.info("ANN 重建进度：%d/%d", done, total)

        with self._lock:
            self._index = new_index
            self._count = len(new_index)
        self.save()
        log.info("ANN 重建完成：%d 个向量", self._count)
        return self._count
