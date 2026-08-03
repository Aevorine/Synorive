"""
人脸检测与聚类 —— C5（默认关，隐私最敏感的一类）
====================================================================
🔴 **不做身份识别，只做聚类**：库里同一张脸出现在哪些照片里会被归到一起
（"这些是同一个人"），但**从不主动猜这个人是谁**——聚类结果默认叫
"未命名人物 1" "未命名人物 2"，用户自己去改名字才会有名字，
应用本身没有、也不接任何人脸数据库去反查身份。

**检测和特征提取的算法不自己写**：SCRFD（检测）的原始 ONNX 输出是
多个特征图在不同尺度上的分数/框回归/关键点回归，要手工做锚点解码
和 NMS 才能变成"这里有一张脸"——这段逻辑如果凭记忆写错一个下标，
症状可能是"完全检测不到脸"或者"框全错位"，而且不容易排查。
`insightface` 这个包本身就是这两个模型的官方来源（`buffalo_l` 就是
它发布的），它自带的解码/对齐逻辑经过大量项目验证，直接复用比
自己重新实现更可靠——这里只负责"喂图进去、把结果整理成这个项目
自己的数据结构"，检测和特征提取的核心数学都交给 insightface。

⚠️ **License 提醒**：insightface 官方发布的预训练模型
（包括这里用的 buffalo_l 系列）明确写着"仅限非商业研究用途"
（The pretrained models we provided with this library are
available for non-commercial research purposes only）。
如果这个应用有商业发行计划，这一项功能的模型来源需要重新评估，
不能直接商用分发。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("synorive.face")

#: insightface 的模型包名。决定了它去哪个子目录找 det_10g.onnx / w600k_r50.onnx
MODEL_PACK = "buffalo_l"
#: 检测输入边长。官方默认就是这个，人脸检测这个尺度足够，调大只会更慢
DETECT_SIZE = 640
#: ArcFace w600k_r50 的输出维度
EMBED_DIM = 512
#: 检测置信度门槛。低于这个的框大概率是误检（阴影、图案），
#: 宁可漏检也不要往聚类库里塞垃圾——垃圾人脸会污染后续所有聚类结果
MIN_DET_SCORE = 0.5


@dataclass
class DetectedFace:
    #: 归一化坐标框 (x, y, w, h)，0~1，供界面画框和裁切缩略图
    bbox: tuple[float, float, float, float]
    det_score: float
    #: L2 归一化过的人脸特征向量，512 维
    embedding: np.ndarray


class FaceAnalyzer:
    """
    检测 + 特征提取一体，因为 insightface 的 `FaceAnalysis.get()`
    本来就是一次调用把两步都做完（检测完立刻用检测到的关键点做对齐再提特征），
    拆成两个类反而要在中间传关键点、容易传错。
    """

    model_id = "insightface-buffalo_l"
    dim = EMBED_DIM

    def __init__(self, model_dir: Path) -> None:
        #: insightface 期望的目录结构是 `<root>/models/<pack>/*.onnx`，
        #: 所以这里传的 root 是 model_dir/insightface，
        #: 实际文件应该落在 model_dir/insightface/models/buffalo_l/ 下
        #: （对应 doctor/registry.py 里 face-detect / face-embed 两条的 subdir）
        self.root = model_dir / "insightface"
        self._app: Any = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        d = self.root / "models" / MODEL_PACK
        return (d / "det_10g.onnx").exists() and (d / "w600k_r50.onnx").exists()

    @property
    def ready(self) -> bool:
        return self._app is not None

    def load(self) -> None:
        if self._app is not None:
            return
        with self._lock:
            if self._app is not None:
                return
            if not self.available():
                raise FileNotFoundError(
                    f"人脸模型缺失：{self.root / 'models' / MODEL_PACK}"
                    "（依赖医生里装 face-detect 和 face-embed）"
                )
            from insightface.app import FaceAnalysis

            # allowed_modules 必须显式限定成这两项——buffalo_l 官方包里
            # 还有 landmark_3d68 / genderage 两个模型，这里没有下载它们，
            # 不限定的话 FaceAnalysis 会尝试加载全部 5 个文件然后因为
            # 另外两个文件不存在而直接失败，即使我只想用检测和识别
            self._app = FaceAnalysis(
                name=MODEL_PACK,
                root=str(self.root),
                allowed_modules=["detection", "recognition"],
                providers=["CPUExecutionProvider"],
            )
            self._app.prepare(ctx_id=-1, det_size=(DETECT_SIZE, DETECT_SIZE))
            log.info("人脸模型已加载（%s）", MODEL_PACK)

    def analyze(self, img_bgr: np.ndarray) -> list[DetectedFace]:
        """
        `img_bgr` 是 OpenCV 惯例的 BGR 通道顺序（insightface 内部按这个假设写的），
        调用方如果是从 PIL（RGB）转过来的要记得转一次通道。
        """
        self.load()
        assert self._app is not None
        h, w = img_bgr.shape[:2]

        faces = self._app.get(img_bgr)
        out: list[DetectedFace] = []
        for f in faces:
            score = float(getattr(f, "det_score", 0.0))
            if score < MIN_DET_SCORE:
                continue
            x1, y1, x2, y2 = [float(v) for v in f.bbox]
            emb = np.asarray(f.embedding, dtype=np.float32)
            n = float(np.linalg.norm(emb))
            if n > 1e-6:
                emb = emb / n
            out.append(DetectedFace(
                bbox=(
                    max(0.0, x1 / w), max(0.0, y1 / h),
                    min(1.0, (x2 - x1) / w), min(1.0, (y2 - y1) / h),
                ),
                det_score=score,
                embedding=emb,
            ))
        return out


def bgr_from_pil(img: Any) -> np.ndarray:
    """PIL Image（RGB）转 insightface 要的 BGR ndarray。"""
    arr = np.asarray(img.convert("RGB"))
    return arr[:, :, ::-1].copy()
