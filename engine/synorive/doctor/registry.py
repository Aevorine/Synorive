"""
依赖清单 —— E3 依赖医生要管的东西都登记在这里
====================================================================
用户原话：「可以自动配置需要的工具与内容」。

登记原则：
  ① 每条都要写清楚 **缺了它会失去什么**，而不是笼统说"必需"。
     大部分依赖缺了只是少一类能力，不该让整个应用起不来。
  ② 每条都要有 **校验和**。下一半的模型文件加载时报的错千奇百怪，
     和"没下载"完全不像，排查能耗掉一小时。
  ③ 国内镜像和官方源都列出来，下载器自己按可达性挑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DepKind(str, Enum):
    MODEL = "model"
    BINARY = "binary"
    PY_PACKAGE = "python-pkg"
    FONT = "font"


@dataclass(frozen=True)
class RemoteFile:
    """一个要下载的文件。多个源按顺序试，第一个通的就用。"""

    filename: str
    urls: tuple[str, ...]
    sha256: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class Dependency:
    id: str
    kind: DepKind
    name: str
    #: 说人话：这东西是干嘛的
    purpose: str
    #: 哪些功能依赖它
    required_by: tuple[str, ...]
    #: 缺了它降级成什么样 —— 空字符串表示缺了就整体不可用
    degrades_to: str
    optional: bool
    files: tuple[RemoteFile, ...] = field(default_factory=tuple)
    #: 装到 model_dir 下的哪个子目录
    subdir: str = ""

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes or 0 for f in self.files)


# ── 镜像 ────────────────────────────────────────────────────
# hf-mirror.com 是 HuggingFace 的国内镜像，路径结构完全一致。
# 下载器会同时探测两个源的可达性，谁先响应用谁 —— 不写死"国内就用镜像"，
# 因为用户可能挂了代理，那时候官方源反而更快。
HF_OFFICIAL = "https://huggingface.co"
HF_MIRROR = "https://hf-mirror.com"


def _hf(repo: str, path: str) -> tuple[str, ...]:
    """同一个文件的官方源 + 国内镜像两个地址。"""
    return (
        f"{HF_MIRROR}/{repo}/resolve/main/{path}",
        f"{HF_OFFICIAL}/{repo}/resolve/main/{path}",
    )


# ── 依赖清单 ────────────────────────────────────────────────

REGISTRY: tuple[Dependency, ...] = (
    # ═══ 文本向量：二期的核心，没它语义检索整个不成立 ═══
    Dependency(
        id="embed-text-zh",
        kind=DepKind.MODEL,
        name="BGE-small-zh-v1.5（中文文本向量）",
        purpose="把文字变成向量，让你用「描述内容」也能搜到东西，而不只是搜关键词",
        required_by=("语义检索", "跨模态互搜", "相似内容推荐", "秒答卡"),
        degrades_to="只能用关键词精确匹配，搜不了同义和近义",
        optional=False,
        subdir="bge-small-zh-v1.5",
        files=(
            RemoteFile(
                "model.onnx",
                _hf("Xenova/bge-small-zh-v1.5", "onnx/model_quantized.onnx"),
            ),
            RemoteFile("tokenizer.json", _hf("Xenova/bge-small-zh-v1.5", "tokenizer.json")),
            RemoteFile(
                "tokenizer_config.json",
                _hf("Xenova/bge-small-zh-v1.5", "tokenizer_config.json"),
            ),
            RemoteFile("config.json", _hf("Xenova/bge-small-zh-v1.5", "config.json")),
            RemoteFile(
                "special_tokens_map.json",
                _hf("Xenova/bge-small-zh-v1.5", "special_tokens_map.json"),
            ),
        ),
    ),
    # ═══ 图文跨模态：三期 ═══
    Dependency(
        id="embed-clip-zh",
        kind=DepKind.MODEL,
        name="Chinese-CLIP ViT-B/16（中文图文向量）",
        purpose="用中文描述搜图片，或者用一张图找相似的图",
        required_by=("以文搜图", "以图搜图", "视频画面检索"),
        degrades_to="图片只能靠文件名、OCR 文字和 EXIF 搜",
        optional=True,
        subdir="chinese-clip-vit-b16",
        files=(
            RemoteFile(
                "vision_model.onnx",
                _hf("Xenova/clip-vit-base-patch16", "onnx/vision_model_quantized.onnx"),
            ),
            RemoteFile(
                "text_model.onnx",
                _hf("Xenova/clip-vit-base-patch16", "onnx/text_model_quantized.onnx"),
            ),
            RemoteFile("tokenizer.json", _hf("Xenova/clip-vit-base-patch16", "tokenizer.json")),
            RemoteFile(
                "preprocessor_config.json",
                _hf("Xenova/clip-vit-base-patch16", "preprocessor_config.json"),
            ),
        ),
    ),
    # ═══ 重排：四期，可选 ═══
    Dependency(
        id="reranker-zh",
        kind=DepKind.MODEL,
        name="BGE-reranker-base（精排）",
        purpose="对前 20 条结果重新打分排序，准确率明显提升",
        required_by=("D7 精排",),
        degrades_to="只用融合分排序，Top5 准确率略低",
        optional=True,
        subdir="bge-reranker-base",
        files=(
            RemoteFile(
                "model.onnx",
                _hf("Xenova/bge-reranker-base", "onnx/model_quantized.onnx"),
            ),
            RemoteFile("tokenizer.json", _hf("Xenova/bge-reranker-base", "tokenizer.json")),
        ),
    ),
    # ═══ 外部命令行 ═══
    Dependency(
        id="ffmpeg",
        kind=DepKind.BINARY,
        name="FFmpeg",
        purpose="给视频抽关键帧、抽音轨、读时长",
        required_by=("视频分析", "视频片段级定位", "音频转写"),
        degrades_to="视频只能靠文件名和路径搜，不看内容",
        optional=True,
    ),
    # ═══ Python 包（按需装，不一开始全装）═══
    Dependency(
        id="pkg-docs",
        kind=DepKind.PY_PACKAGE,
        name="文档解析套件（PyMuPDF / python-docx / openpyxl / python-pptx）",
        purpose="读 PDF、Word、Excel、PPT、EPUB 里的文字",
        required_by=("文档索引",),
        degrades_to="只能索引纯文本和 Markdown",
        optional=False,
    ),
    Dependency(
        id="pkg-ocr",
        kind=DepKind.PY_PACKAGE,
        name="RapidOCR（中文图片文字识别）",
        purpose="把截图、表格照片、文档扫描件里的字提出来变成可搜内容",
        required_by=("C2 图片 OCR", "扫描件 PDF"),
        degrades_to="图片里的文字搜不到",
        optional=True,
    ),
    Dependency(
        id="pkg-media",
        kind=DepKind.PY_PACKAGE,
        name="PyAV（视频解码）",
        purpose="按场景切分视频、抽关键帧",
        required_by=("C14 视频分析",),
        degrades_to="视频不做内容分析",
        optional=True,
    ),
    Dependency(
        id="pkg-web",
        kind=DepKind.PY_PACKAGE,
        name="Trafilatura（网页正文提取）",
        purpose="从网页里把正文摘出来，去掉导航、广告、页脚",
        required_by=("C11 链接存档",),
        degrades_to="链接只存标题和 URL",
        optional=True,
    ),
    # ═══ 核显加速：可选，和 CPU 版互斥 ═══
    Dependency(
        id="gpu-directml",
        kind=DepKind.PY_PACKAGE,
        name="ONNX Runtime DirectML（核显加速）",
        purpose="用 Intel/AMD 核显跑推理，图片分析可能快 2~3 倍",
        required_by=("图片向量化提速",),
        degrades_to="用 CPU 跑，慢一些但结果完全一样",
        optional=True,
    ),
)

BY_ID: dict[str, Dependency] = {d.id: d for d in REGISTRY}

#: 装 Python 包时用的实际包名（一个依赖项可能对应多个 pip 包）
PIP_PACKAGES: dict[str, tuple[str, ...]] = {
    "pkg-docs": ("pymupdf>=1.25", "python-docx>=1.1", "openpyxl>=3.1", "python-pptx>=1.0"),
    "pkg-ocr": ("rapidocr-onnxruntime>=1.4",),
    "pkg-media": ("av>=13.0",),
    "pkg-web": ("trafilatura>=2.0",),
    "gpu-directml": ("onnxruntime-directml>=1.20",),
}

#: 装完之后 import 这些名字来确认真的能用 —— 只看 pip 退出码是不够的
IMPORT_PROBES: dict[str, tuple[str, ...]] = {
    "pkg-docs": ("fitz", "docx", "openpyxl", "pptx"),
    "pkg-ocr": ("rapidocr_onnxruntime",),
    "pkg-media": ("av",),
    "pkg-web": ("trafilatura",),
    "gpu-directml": ("onnxruntime",),
}

#: 国内 pip 镜像。装包时先试镜像，不通再回官方源。
PIP_MIRRORS: tuple[str, ...] = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.org/simple",
)
