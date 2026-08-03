"""
C4 图片详细描述 —— 调云端视觉模型，把"图里有什么"变成能被搜到的文字
====================================================================
本地没有能读图生成中文描述的小模型（CLIP 只会算相似度，不会说话），
所以这一项**天生只能走云端**，且从一开始就设计成默认关闭、
需要用户在设置里同时打开"云端增强"和"图片详细描述"两道开关才生效——
两道开关缺一不可，任何一道关着都不会真的把图片发出去。

生成出来的描述文本会作为一个独立的检索通道（channel="description"）
写进索引，和 OCR、文件名并列——所以这段文字要**克制**：
一两句话说清楚"画面里有什么"就够了，不需要联想、不需要评价，
写多了反而会稀释检索结果里其它更精确的匹配。
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from .adapters import CloudAdapter, CloudAdapterError

#: 提示词故意压得很短——多几句"请注意""你需要"之类的客套话，
#: 换来的往往是模型也用同样啰嗦的语气回话，白白吃掉字数配额
DESCRIBE_PROMPT = (
    "用一到两句中文平实地描述这张图片的画面内容（有什么物体、场景、文字大意），"
    "不要联想、不要评价好不好看，不要用"这张图片展示了"这种套话开头，直接说内容。"
)

#: 描述最多留这么多字——防止模型抽风写成一整段小作文，
#: 挤爆摘要栏也污染检索结果的可读性
MAX_DESCRIPTION_CHARS = 300

#: 送去描述前把图片压到这个边长以内。分析用不着原图分辨率，
#: 而且很多云端 API 是按图片体积/像素数计费的——压缩既省钱又省流量
MAX_IMAGE_SIDE = 768

_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif",
}


def _prepare_image(path: Path) -> tuple[str, str]:
    """压缩到合理尺寸并转成 base64。返回 `(base64, mime)`。"""
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_IMAGE_SIDE:
            scale = MAX_IMAGE_SIDE / max(w, h)
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.BICUBIC)

        import io

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def _sanitize(text: str) -> str:
    """
    模型偶尔会带上 Markdown 标记或客套话开头，这里做最基本的清理——
    不是安全边界（图片描述没有"编造来源"这种风险，纯粹是可读性问题）。
    """
    # 🔴 这里原来写成 `[展示了显示了包含有]*`——那是字符类，会匹配这些字的
    # 任意顺序任意重复（比如"了了了"也算命中），不是我想要的"整词匹配"。
    # 改成非捕获组的词语交替
    text = re.sub(
        r"^(这张图片|这是一张|图片中|画面中)(?:展示了|显示了|包含|有)?[，,：:]?\s*",
        "", text.strip(),
    )
    text = re.sub(r"[*_`#]+", "", text)  # 偶尔会带 Markdown 强调符号
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_DESCRIPTION_CHARS]


async def describe_image(
    path: Path, *, adapter: CloudAdapter, model: str
) -> str:
    """
    描述一张图。**失败必须抛出去，不能安静返回空字符串**——
    调用方（`pipeline.run_deferred_description`）需要知道"这次没成"和
    "这张图片确实什么都没有"是两码事，前者要记 failed 等下次重试，
    后者才是真的没什么好写的（但视觉模型几乎不会真的什么都不说）。
    """
    try:
        image_b64, mime = _prepare_image(path)
    except Exception as e:  # noqa: BLE001
        raise CloudAdapterError(f"图片处理失败（可能是格式不支持或文件损坏）：{e}") from e

    result = await adapter.describe_image(
        image_b64=image_b64, mime=mime, prompt=DESCRIBE_PROMPT, model=model,
    )
    text = _sanitize(result.text)
    if not text:
        raise CloudAdapterError("模型返回了空描述")
    return text
