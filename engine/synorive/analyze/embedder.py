"""
文本向量化 —— BGE-small-zh-v1.5（ONNX，INT8 量化）
====================================================================
把文字变成 512 维向量，让「用描述搜内容」成为可能。

几个不显然的点：

① **执行器按 [DirectML, CPU] 顺序请求，装了就用、没装自动回落。**
   同一套代码在两种环境都能跑，不用分支判断。

② **BGE 的查询侧要加指令前缀**，文档侧不加。这是 BGE 系列的约定，
   不加的话检索质量掉一大截，而且不会报任何错 —— 典型的静默退化。

③ **必须 L2 归一化。** sqlite-vec 用的是 L2 距离，归一化之后
   L2 距离和余弦相似度是单调等价的，这样才能用向量索引做语义排序。
   忘了归一化的症状是"排序看着有点道理但就是不太对"，极难定位。

④ **批处理不是可选优化。** ONNX Runtime 单条推理的固定开销占大头，
   批量 32 条的吞吐是逐条的 5~8 倍。分析流水线永远走批。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("synorive.embed")

#: BGE 官方约定的查询前缀。文档侧不加，只有查询侧加。
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

#: 模型上限 512 token，留一点余量给特殊 token
MAX_TOKENS = 512

#: 默认批大小。实测批 8 最快，批越大 padding 浪费越多（一批里最长的那条决定
#: 所有条的计算量）。16 是"够快 + 少一半调用开销"的折中。
DEFAULT_BATCH = 16


def physical_cores() -> int:
    """
    物理核数，不是逻辑核数。

    实测这颗 i5-1155G7（4 物理核 / 8 逻辑核）：
      intra=1 → 110 段/秒　intra=2 → 196　**intra=4 → 247**　intra=8 → 134
    开到逻辑核数反而掉 45% —— 这类矩阵运算超线程只会互相抢 SIMD 单元。
    """
    try:
        import psutil

        n = psutil.cpu_count(logical=False)
        if n:
            return int(n)
    except Exception:  # noqa: BLE001
        pass
    import os

    return max(1, (os.cpu_count() or 4) // 2)


class TextEmbedder:
    """
    线程安全的文本向量化器。

    ⚠️ threads 这个参数传错不会报错，只会慢一倍，所以说清楚怎么传：

      · **摄取流水线**：N 个 worker，每个 worker 一个 threads=1 的实例。
        实测 7 worker × 1 线程 = **317 段/秒**（本机最优）。
      · **查询路径**：单个实例，threads=物理核数（默认值）。
        实测 **247 段/秒**，单条查询延迟最低。

    默认值给的是查询路径的配置 —— 因为流水线那边是显式构造的，
    忘了传参数至少不会掉到最差的那一档。
    """

    model_id = "bge-small-zh-v1.5"
    dim = 512

    def __init__(
        self,
        model_dir: Path,
        prefer_gpu: bool = False,
        threads: int | None = None,
    ) -> None:
        self.model_dir = model_dir
        self.prefer_gpu = prefer_gpu
        self.threads = threads if threads is not None else physical_cores()
        self._session: Any = None
        self._tokenizer: Any = None
        self._lock = threading.Lock()
        self._provider = "未加载"

    # ── 加载 ────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._session is not None

    @property
    def provider(self) -> str:
        return self._provider

    def load(self) -> None:
        """幂等加载。首次约 1~2 秒，之后直接返回。"""
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return

            import onnxruntime as ort
            from tokenizers import Tokenizer

            onnx_path = self.model_dir / "model.onnx"
            tok_path = self.model_dir / "tokenizer.json"
            for p in (onnx_path, tok_path):
                if not p.exists():
                    raise FileNotFoundError(f"模型文件缺失：{p}（跑一次依赖医生就能补上）")

            tokenizer = Tokenizer.from_file(str(tok_path))
            tokenizer.enable_truncation(max_length=MAX_TOKENS)
            tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            self._tokenizer = tokenizer

            available = ort.get_available_providers()
            wanted: list[str] = []
            if self.prefer_gpu and "DmlExecutionProvider" in available:
                wanted.append("DmlExecutionProvider")
            wanted.append("CPUExecutionProvider")

            opts = ort.SessionOptions()
            # 线程数见 __init__ 的注释：流水线传 1（多 worker 并行），
            # 查询路径用默认的物理核数。写死成 1 会让单会话吞吐掉一半以上。
            opts.intra_op_num_threads = self.threads
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self._session = ort.InferenceSession(str(onnx_path), opts, providers=wanted)
            self._provider = self._session.get_providers()[0]
            log.info(
                "文本向量模型已加载，执行器 %s，线程 %d", self._provider, self.threads
            )

            # 拿真实输出维度核对，不信文档写的 512
            out_shape = self._session.get_outputs()[0].shape
            if isinstance(out_shape[-1], int) and out_shape[-1] != self.dim:
                log.warning("模型实际维度 %s 与预期 %d 不符，以实际为准", out_shape[-1], self.dim)
                self.dim = int(out_shape[-1])

    # ── 推理 ────────────────────────────────────────────────

    def encode(
        self,
        texts: list[str],
        *,
        is_query: bool = False,
        batch_size: int = DEFAULT_BATCH,
    ) -> np.ndarray:
        """
        返回 (n, dim) 的 float32 数组，已 L2 归一化。

        is_query=True 时自动加 BGE 的查询前缀 —— 索引侧和查询侧
        必须一致地处理这件事，不然检索质量会莫名其妙地差。
        """
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        self.load()
        assert self._tokenizer is not None and self._session is not None

        prepared = [QUERY_PREFIX + t if is_query else t for t in texts]
        out: list[np.ndarray] = []

        for i in range(0, len(prepared), batch_size):
            chunk = prepared[i : i + batch_size]
            out.append(self._encode_batch(chunk))

        return np.vstack(out)

    def _encode_batch(self, texts: list[str]) -> np.ndarray:
        assert self._tokenizer is not None and self._session is not None

        encodings = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        feed: dict[str, np.ndarray] = {"input_ids": ids, "attention_mask": mask}
        # 有的导出带 token_type_ids，有的不带 —— 按会话实际要什么给什么，
        # 多给一个没用的输入 ONNX Runtime 会直接报错
        names = {inp.name for inp in self._session.get_inputs()}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.zeros_like(ids)
        feed = {k: v for k, v in feed.items() if k in names}

        outputs = self._session.run(None, feed)
        hidden = outputs[0]  # (batch, seq, dim)

        # BGE 用 CLS 池化（取第一个 token），不是均值池化。
        # 用错池化方式不会报错，只会让检索质量下降 ——
        # 这类"错了也不报错"的地方必须写死并注释清楚。
        if hidden.ndim == 3:
            vec = hidden[:, 0, :]
        else:
            vec = hidden

        return _l2_normalize(vec.astype(np.float32))

    def encode_one(self, text: str, *, is_query: bool = False) -> np.ndarray:
        return self.encode([text], is_query=is_query)[0]


def _l2_normalize(x: np.ndarray) -> np.ndarray:
    """
    L2 归一化。sqlite-vec 用 L2 距离，归一化后 L2 距离与余弦相似度单调等价，
    向量索引出来的顺序才等于语义相似度顺序。

    零向量除零会得到 nan，nan 进了向量库之后所有距离计算都变 nan，
    症状是"某些查询突然一条结果都没有"，所以这里必须兜底。
    """
    norm = np.linalg.norm(x, axis=-1, keepdims=True)
    norm = np.maximum(norm, 1e-12)
    return x / norm


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """两个已归一化向量的余弦相似度，就是点积。"""
    return float(np.dot(a, b))
