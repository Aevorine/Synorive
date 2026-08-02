"""
图片分析 —— C1 图像向量 / C2 中文 OCR / C3 EXIF+感知哈希 / C6 截图与色调
====================================================================
每一项都是**独立可失败**的：OCR 挂了不影响向量，向量挂了不影响 EXIF。
一张图缺哪一样就少一条检索通路，不该整张图作废。

关于中文搜图的路线（实测后定的，2026-08-02）：
  Chinese-CLIP 官方没有 ONNX（只有 753MB PyTorch 权重），
  社区导出的中文 CLIP repo 不存在，多语言 CLIP 文本塔也不存在。
  所以：**以图搜图用英文 CLIP 视觉塔**（纯视觉，与语言无关，一样好用），
  **中文搜图主要靠 OCR 文字** —— 库里最需要搜的图（截图、聊天图、文档照片）
  恰恰都有文字，OCR 实测字符覆盖率 100%。
  想要"用中文描述搜风景照"，装可选的 jina-clip-v2 或走云端。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger("synorive.image")

#: 送进 OCR 前把长边压到这个尺寸。
#:
#: 实测扫描（640/960/1280/1600/2000）得到两个结论：
#:   ① **分辨率对速度几乎没影响**（1527~2754ms 无规律）——
#:      RapidOCR 内部会自己缩放，我这一层压缩省不到检测时间。
#:      真正的耗时随**文字行数**增长：6 行 1.5s / 10 行 1.8s / 14 行 2.0s。
#:   ② 准确率在 1280 就到 100%，960 是 99.9%，640 掉到 99.2%（小字丢字）。
#: 所以取 1280：准确率满分，且比 1600 少占内存。原来拍的 1600 是多余的。
OCR_MAX_SIDE = 1280

#: 关掉逐行方向分类（cls）。实测省 16%（1907 → 1608 ms/张）。
#: 相机旋转已经由 EXIF 摆正处理了，cls 管的是单行文字 180° 倒置 —— 极少见。
#: 真遇到倒置的图，设置里可以打开。
OCR_USE_CLS = False

#: CLIP ViT-B/32 的输入
CLIP_SIZE = 224
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

#: 感知哈希用的缩略图边长（DCT 前）
PHASH_SIZE = 32
PHASH_BITS = 8

SUPPORTED_IMAGE_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif", ".heic",
}


@dataclass
class OcrLine:
    text: str
    score: float
    #: 归一化坐标 0~1，用于在结果里高亮命中的那一块
    bbox: tuple[float, float, float, float]


@dataclass
class ImageAnalysis:
    width: int = 0
    height: int = 0
    #: C3 感知哈希，用于 E9 近重复检测
    phash: str | None = None
    exif_time: str | None = None
    camera: str | None = None
    gps: tuple[float, float] | None = None
    #: C2 OCR
    ocr_lines: list[OcrLine] = field(default_factory=list)
    #: C6
    is_screenshot: bool = False
    dominant_colors: list[str] = field(default_factory=list)
    #: C1 图像向量（已 L2 归一化）
    embedding: np.ndarray | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ocr_text(self) -> str:
        return "\n".join(l.text for l in self.ocr_lines)


def is_image(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_IMAGE_EXT


# ── 打开图片 ────────────────────────────────────────────────


def open_image(path: Path) -> Image.Image:
    """
    打开并按 EXIF 方向摆正。

    不摆正的话，手机竖拍的照片在分析时是躺着的 ——
    OCR 一个字都认不出来，缩略图也是横的。而且这事不报错，
    只表现为"某些照片就是搜不到"。
    """
    img = Image.open(path)
    img = ImageOps.exif_transpose(img) or img
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    return img


# ── C3 EXIF ─────────────────────────────────────────────────


def read_exif(img: Image.Image) -> tuple[str | None, str | None, tuple[float, float] | None]:
    """返回 (拍摄时间 ISO, 相机型号, GPS)。任何一项拿不到就是 None。"""
    try:
        exif = img.getexif()
        if not exif:
            return None, None, None
    except Exception:  # noqa: BLE001
        return None, None, None

    shot_time = None
    for tag in (36867, 36868, 306):  # DateTimeOriginal, DateTimeDigitized, DateTime
        v = exif.get(tag)
        if isinstance(v, str) and len(v) >= 19:
            # EXIF 格式是 "2026:08:02 14:30:00"，冒号要换成横杠才是 ISO
            try:
                d, t = v[:19].split(" ")
                shot_time = f"{d.replace(':', '-')}T{t}"
                break
            except ValueError:
                continue

    make = str(exif.get(271, "") or "").strip()
    model = str(exif.get(272, "") or "").strip()
    camera = f"{make} {model}".strip() or None

    gps = None
    try:
        gps_ifd = exif.get_ifd(0x8825)
        if gps_ifd:
            lat = _dms(gps_ifd.get(2), gps_ifd.get(1))
            lon = _dms(gps_ifd.get(4), gps_ifd.get(3))
            if lat is not None and lon is not None:
                gps = (lat, lon)
    except Exception:  # noqa: BLE001
        pass

    return shot_time, camera, gps


def _dms(value: Any, ref: Any) -> float | None:
    """EXIF 的度分秒转十进制。"""
    if not value or len(value) != 3:
        return None
    try:
        d, m, s = (float(x) for x in value)
        dec = d + m / 60 + s / 3600
        if str(ref).upper() in ("S", "W"):
            dec = -dec
        return round(dec, 6)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ── C3 感知哈希 ─────────────────────────────────────────────


def perceptual_hash(img: Image.Image) -> str:
    """
    pHash（DCT 版）。比 aHash 抗缩放和压缩，用于 E9 找近重复图。

    做法：缩到 32×32 灰度 → 二维 DCT → 取左上 8×8 低频 → 与中位数比较 → 64 位。
    用中位数不用均值：均值会被左上角那个巨大的直流分量带偏，
    结果是几乎所有图的哈希都长得差不多。
    """
    small = img.convert("L").resize((PHASH_SIZE, PHASH_SIZE), Image.LANCZOS)
    a = np.asarray(small, dtype=np.float32)

    dct = _dct2(a)
    low = dct[:PHASH_BITS, :PHASH_BITS].flatten()
    med = np.median(low[1:])  # 跳过直流分量
    bits = (low > med).astype(np.uint8)
    return "".join(f"{b:x}" for b in np.packbits(bits))


def _dct2(a: np.ndarray) -> np.ndarray:
    """二维 DCT-II。scipy 有现成的，但只用这一处，自己算省个导入。"""
    n = a.shape[0]
    k = np.arange(n)
    basis = np.cos(np.pi * (2 * k[:, None] + 1) * k[None, :] / (2 * n))
    return basis.T @ a @ basis


def hamming(a: str, b: str) -> int:
    """两个哈希的汉明距离。≤10 基本可以认为是同一张图的不同版本。"""
    if len(a) != len(b):
        return 64
    return sum(bin(int(x, 16) ^ int(y, 16)).count("1") for x, y in zip(a, b))


# ── C6 截图判定与主色 ───────────────────────────────────────


def flat_ratio(img: Image.Image) -> float:
    """
    水平方向上「和右邻居完全相同」的像素占比。

    这是区分截图和照片**最可靠的单一信号**：
      · 界面截图有大片纯色区域（背景、控件填充）→ 相邻像素完全相等的比例很高
      · 照片即使看起来是平滑渐变，传感器噪点也会让相邻像素几乎总是差一点 → 比例极低

    比"数颜色种类"稳得多。实测发现按颜色数判断会把**合成的平滑渐变图**
    误判成截图（渐变的量化颜色数同样很少），而 flat_ratio 能分开。
    """
    g = np.asarray(img.convert("L").resize((256, 256), Image.NEAREST), dtype=np.int16)
    return float((np.diff(g, axis=1) == 0).mean())


def classify(
    img: Image.Image, exif_time: str | None, camera: str | None
) -> tuple[bool, list[str]]:
    """
    判断是不是截图 + 取主色调。

    截图判据以 flat_ratio 为主，EXIF 和分辨率只做辅证：
    「没有 EXIF」这条太弱 —— 下载的图、编辑过的图、微信存的图统统没有 EXIF，
    单靠它会把一大半照片judge成截图。
    """
    w, h = img.size

    small = img.resize((64, 64), Image.NEAREST)
    arr = np.asarray(small.convert("RGB")).reshape(-1, 3)
    quant = (arr // 16).astype(np.uint8)
    packed = quant[:, 0].astype(np.int32) * 256 + quant[:, 1] * 16 + quant[:, 2]

    flat = flat_ratio(img)
    known_res = (w, h) in {
        (1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1440, 900),
        (1280, 720), (1600, 900), (2880, 1800), (1170, 2532), (1080, 2400),
        (1179, 2556), (1284, 2778), (750, 1334), (828, 1792),
    }
    no_exif = not camera and not exif_time

    # 主判据：大片纯色。0.25 是实测的分界 ——
    # 合成渐变照片约 0.02，界面截图约 0.5 以上。
    if flat >= 0.25:
        is_shot = True
    elif flat >= 0.10 and (known_res or no_exif):
        is_shot = True
    else:
        is_shot = False

    # 主色：按量化桶计数取前 5，比真 k-means 快两个数量级，对"找蓝色调的图"够用
    vals, counts = np.unique(packed, return_counts=True)
    top_idx = np.argsort(-counts)[:5]
    colors = []
    for v in vals[top_idx]:
        r, g, b = int(v) // 256, (int(v) // 16) % 16, int(v) % 16
        colors.append(f"#{r * 16 + 8:02X}{g * 16 + 8:02X}{b * 16 + 8:02X}")

    return is_shot, colors


# ── C2 OCR ──────────────────────────────────────────────────


class OcrEngine:
    """
    RapidOCR 包装。线程安全（每线程一个引擎实例）。

    ⚠️ 包名必须用 `rapidocr` 不是 `rapidocr-onnxruntime`：
       后者所有版本都限制 Python <3.13，本项目跑在 3.13 上装不了。
       这个坑在写代码时看不出来，装的时候才炸。
    """

    def __init__(self, min_score: float = 0.5, use_cls: bool = OCR_USE_CLS) -> None:
        self.min_score = min_score
        self.use_cls = use_cls
        self._local = threading.local()
        self.available = _rapidocr_available()

    def _engine(self) -> Any:
        if not self.available:
            return None
        e = getattr(self._local, "engine", None)
        if e is not None:
            return e
        try:
            from rapidocr import RapidOCR

            try:
                e = RapidOCR(params={"Global.use_cls": self.use_cls})
            except Exception:  # noqa: BLE001
                # 参数名在不同版本里可能变，回退到默认构造 ——
                # 慢 16% 总好过整个 OCR 用不了
                e = RapidOCR()
            self._local.engine = e
            return e
        except Exception as ex:  # noqa: BLE001
            log.warning("OCR 引擎加载失败：%s", ex)
            self.available = False
            return None

    def read(self, img: Image.Image) -> list[OcrLine]:
        engine = self._engine()
        if engine is None:
            return []

        w, h = img.size
        # 压到长边 1600：原图 4000px 又慢又不会更准
        scale = min(1.0, OCR_MAX_SIDE / max(w, h))
        work = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS) if scale < 1 else img

        try:
            res = engine(np.asarray(work.convert("RGB")))
        except Exception as ex:  # noqa: BLE001
            log.debug("OCR 识别失败：%s", ex)
            return []

        # ⚠️ 不能写 `getattr(res, "txts", None) or []`：
        #    RapidOCR 返回的是 numpy 数组，`or` 会触发 bool(array)，
        #    直接抛 "The truth value of an array with more than one element is ambiguous"。
        #    这个错被外层 catch 吞掉之后，表现是「OCR 一行都识别不出来」而不是报错。
        def _as_list(v: Any) -> list[Any]:
            if v is None:
                return []
            return list(v)

        texts = _as_list(getattr(res, "txts", None))
        scores = _as_list(getattr(res, "scores", None))
        boxes = _as_list(getattr(res, "boxes", None))

        ww, hh = work.size
        out: list[OcrLine] = []
        for i, t in enumerate(texts):
            t = (t or "").strip()
            if not t:
                continue
            sc = float(scores[i]) if i < len(scores) else 1.0
            if sc < self.min_score:
                continue
            bbox = (0.0, 0.0, 1.0, 1.0)
            if i < len(boxes) and boxes[i] is not None:
                try:
                    pts = np.asarray(boxes[i], dtype=np.float32).reshape(-1, 2)
                    x0, y0 = pts.min(axis=0)
                    x1, y1 = pts.max(axis=0)
                    bbox = (
                        round(float(x0) / ww, 4), round(float(y0) / hh, 4),
                        round(float(x1 - x0) / ww, 4), round(float(y1 - y0) / hh, 4),
                    )
                except Exception:  # noqa: BLE001
                    pass
            out.append(OcrLine(text=t, score=round(sc, 3), bbox=bbox))
        return out


def _rapidocr_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("rapidocr") is not None


# ── C1 图像向量 ─────────────────────────────────────────────


class ImageEmbedder:
    """CLIP ViT-B/32 视觉塔。纯视觉，和语言无关，用于以图搜图与近似镜头。"""

    model_id = "clip-vit-b32"
    dim = 512

    def __init__(self, model_dir: Path, threads: int | None = None) -> None:
        self.model_dir = model_dir
        self.threads = threads
        self._session: Any = None
        self._lock = threading.Lock()
        self._input_name = "pixel_values"

    @property
    def ready(self) -> bool:
        return self._session is not None

    def available(self) -> bool:
        return (self.model_dir / "vision_model.onnx").exists()

    def load(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort

            p = self.model_dir / "vision_model.onnx"
            if not p.exists():
                raise FileNotFoundError(f"图像模型缺失：{p}（依赖医生里装 embed-image）")

            from .embedder import physical_cores

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = self.threads or physical_cores()
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(str(p), opts, providers=["CPUExecutionProvider"])
            self._input_name = self._session.get_inputs()[0].name

            out_shape = self._session.get_outputs()[0].shape
            if isinstance(out_shape[-1], int):
                self.dim = int(out_shape[-1])
            log.info("图像向量模型已加载，维度 %d", self.dim)

    def preprocess(self, img: Image.Image) -> np.ndarray:
        """CLIP 的标准预处理：短边缩放到 224 → 中心裁 → 归一化。"""
        w, h = img.size
        scale = CLIP_SIZE / min(w, h)
        img2 = img.convert("RGB").resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.BICUBIC)
        w2, h2 = img2.size
        left, top = (w2 - CLIP_SIZE) // 2, (h2 - CLIP_SIZE) // 2
        img2 = img2.crop((left, top, left + CLIP_SIZE, top + CLIP_SIZE))

        a = np.asarray(img2, dtype=np.float32) / 255.0
        a = (a - CLIP_MEAN) / CLIP_STD
        return a.transpose(2, 0, 1)[None, ...]  # NCHW

    def encode(self, images: list[Image.Image], batch_size: int = 8) -> np.ndarray:
        if not images:
            return np.zeros((0, self.dim), dtype=np.float32)
        self.load()
        assert self._session is not None

        out: list[np.ndarray] = []
        for i in range(0, len(images), batch_size):
            batch = np.concatenate([self.preprocess(im) for im in images[i : i + batch_size]], axis=0)
            vecs = self._session.run(None, {self._input_name: batch})[0]
            out.append(_l2(np.asarray(vecs, dtype=np.float32)))
        return np.vstack(out)

    def encode_one(self, img: Image.Image) -> np.ndarray:
        return self.encode([img])[0]


def _l2(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-12)


# ── 总入口 ──────────────────────────────────────────────────


def analyze_image(
    path: Path,
    *,
    ocr: OcrEngine | None = None,
    embedder: ImageEmbedder | None = None,
) -> ImageAnalysis:
    """
    分析一张图。每一项独立可失败 —— 缺哪一样就少一条检索通路，
    不该让整张图作废。
    """
    res = ImageAnalysis()
    try:
        img = open_image(path)
    except Exception as e:  # noqa: BLE001
        res.warnings.append(f"打不开：{type(e).__name__}")
        return res

    res.width, res.height = img.size

    try:
        res.exif_time, res.camera, res.gps = read_exif(img)
    except Exception as e:  # noqa: BLE001
        res.warnings.append(f"EXIF 读取失败：{e}")

    try:
        res.phash = perceptual_hash(img)
    except Exception as e:  # noqa: BLE001
        res.warnings.append(f"感知哈希失败：{e}")

    try:
        res.is_screenshot, res.dominant_colors = classify(img, res.exif_time, res.camera)
    except Exception as e:  # noqa: BLE001
        res.warnings.append(f"分类失败：{e}")

    if ocr is not None:
        try:
            res.ocr_lines = ocr.read(img)
        except Exception as e:  # noqa: BLE001
            res.warnings.append(f"OCR 失败：{e}")

    if embedder is not None and embedder.available():
        try:
            res.embedding = embedder.encode_one(img)
        except Exception as e:  # noqa: BLE001
            res.warnings.append(f"图像向量失败：{e}")

    return res
