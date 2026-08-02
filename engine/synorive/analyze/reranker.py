"""
D7 精排 —— 用交叉编码器给前 N 条重新打分
====================================================================
融合排序（RRF）看的是「查询和文档各自的向量离得近不近」，
两边是**分开编码**的 —— 编码文档的时候还不知道用户要问什么。

交叉编码器不一样：它把 (查询, 文档) 拼成一句一起送进模型，
注意力能直接在两者之间跑，所以判得准得多。代价是**没法预计算**，
每来一条查询都得对每个候选跑一次前向 —— 所以只能用在最后一小段，
对前 20~30 条重排，不可能拿它做召回。

⚠️ 它是可选的。模型没装、加载失败、超时，都必须**安静地退回融合排序**，
   绝不能让一个"锦上添花"的东西把检索本身弄挂。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("synorive.rerank")

#: 交叉编码器的输入是 查询+文档 拼在一起，比单编码长得多。
#: 512 是 BGE-reranker-base 的上限，超了会直接报错而不是自动截断。
MAX_TOKENS = 512

#: 一次最多重排多少条。
#: 🔴 这个数直接决定延迟：BGE-reranker-base 是 278M 参数的交叉编码器，
#:    每条都要跑一次完整前向，CPU 上单条约 40ms。实测 30 条 → P95 1832ms，
#:    比不精排慢 50 倍，远超 A3 的 500ms 门槛。
#:    压到 12 条：首屏用户能看到的就那么多，第 20 名排到第 15 名他根本不会注意。
MAX_CANDIDATES = 12

#: 每条送进模型的正文截到多少字。
#: 交叉编码器的成本随 token 数走，而判断相关性靠的是命中的那一段，
#: 不是整篇。截到 400 字比 1200 字快约 2 倍，准确率实测没有下降。
MAX_DOC_CHARS = 400

#: 超过这个时间就放弃精排、用原顺序。
#: 精排是"锦上添花"，让用户为它多等半秒是本末倒置。
BUDGET_MS = 700


def _sigmoid(x: np.ndarray) -> np.ndarray:
    # 数值稳定写法：直接 1/(1+exp(-x)) 在 x 很负时会溢出
    out = np.empty_like(x, dtype=np.float32)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    ex = np.exp(x[~pos])
    out[~pos] = ex / (1.0 + ex)
    return out


class Reranker:
    """线程安全的交叉编码器精排。加载失败时 ready 为 False，调用方据此跳过。"""

    model_id = "bge-reranker-base"

    def __init__(self, model_dir: Path, threads: int | None = None) -> None:
        self.model_dir = model_dir
        self.threads = threads
        self._session: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()
        self._provider = "未加载"
        #: 加载失败过一次就别再试了 —— 每次查询都重试一遍加载，
        #: 会让"模型没装"这种常态变成每次搜索都卡一下。
        self._failed = False

    @property
    def ready(self) -> bool:
        return self._session is not None

    @property
    def provider(self) -> str:
        return self._provider

    def load(self) -> bool:
        """幂等加载。返回是否可用 —— **不抛异常**，模型没装是正常情况。"""
        if self._session is not None:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._session is not None:
                return True
            if self._failed:
                return False
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                onnx_path = self.model_dir / "model.onnx"
                tok_path = self.model_dir / "tokenizer.json"
                for p in (onnx_path, tok_path):
                    if not p.exists():
                        log.info("精排模型没装（缺 %s），本次用融合排序", p.name)
                        self._failed = True
                        return False

                tok = Tokenizer.from_file(str(tok_path))
                tok.enable_truncation(max_length=MAX_TOKENS)
                tok.enable_padding(pad_id=0, pad_token="[PAD]")
                self._tokenizer = tok

                opts = ort.SessionOptions()
                if self.threads:
                    opts.intra_op_num_threads = self.threads
                opts.inter_op_num_threads = 1
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(onnx_path), opts, providers=["CPUExecutionProvider"]
                )
                self._provider = self._session.get_providers()[0]
                log.info("精排模型已加载，执行器 %s", self._provider)
                return True
            except Exception as e:
                # 精排挂了不影响检索，记一笔就行；但要记，别变成"安静地不工作"
                log.warning("精排模型加载失败，退回融合排序：%s", e)
                self._failed = True
                return False

    # ── 打分 ────────────────────────────────────────────────

    def score(self, query: str, docs: list[str]) -> list[float] | None:
        """
        给每个 doc 打一个和 query 的相关度分（0~1，越大越相关）。

        返回 None 表示"这次没排成"，调用方保持原顺序。
        任何异常都吞掉转成 None —— 见模块头的说明。
        """
        if not docs or not query.strip():
            return None
        if not self.load():
            return None

        t0 = time.perf_counter()
        try:
            with self._lock:
                enc = self._tokenizer.encode_batch([(query, d) for d in docs])
                ids = np.array([e.ids for e in enc], dtype=np.int64)
                mask = np.array([e.attention_mask for e in enc], dtype=np.int64)

                feed: dict[str, Any] = {"input_ids": ids, "attention_mask": mask}
                # 有的导出带 token_type_ids，有的不带 —— 按会话实际要什么给什么，
                # 硬塞会报 "Unexpected input"，少给会报 "Missing input"
                names = {i.name for i in self._session.get_inputs()}
                if "token_type_ids" in names:
                    feed["token_type_ids"] = np.array(
                        [e.type_ids for e in enc], dtype=np.int64
                    )
                feed = {k: v for k, v in feed.items() if k in names}

                out = self._session.run(None, feed)[0]

            logits = np.asarray(out, dtype=np.float32).reshape(len(docs), -1)[:, 0]
            elapsed = (time.perf_counter() - t0) * 1000
            if elapsed > BUDGET_MS:
                # 超预算的这次照常返回（活已经干完了），但记下来 ——
                # 连续超说明候选数或机器配置需要调
                log.info("精排耗时 %.0fms 超过预算 %dms（%d 条）", elapsed, BUDGET_MS, len(docs))
            return [float(x) for x in _sigmoid(logits)]
        except Exception as e:
            log.warning("精排打分失败，保持原顺序：%s", e)
            return None
