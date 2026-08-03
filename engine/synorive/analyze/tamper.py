"""
D4 —— 图片篡改初筛
====================================================================
四条**廉价**判据，全部本地跑，不上模型、不联网（反查那一路除外）：

  ① **EXIF 缺失或异常** —— 原始拍摄的照片带一整套相机参数；
     被编辑软件保存过的通常只剩下软件名，被平台压过的往往什么都不剩
  ② **重压缩痕迹** —— JPEG 量化表非标准、或多轮压缩留下的块效应
  ③ **尺寸与元数据打架** —— EXIF 里写着 4000×3000 而实际是 800×600
  ④ **最早出现时间** —— 拿反查接口找这张图最早出现在哪（这条要联网）

🔴 **「初筛」两个字是这个模块的全部定位。** 真正的图像取证要做 ELA、
噪声一致性分析、光照方向估计，那些都要专业工具和人来判读。这里做的是
**把「有必要多看两眼」的挑出来**，不是「判定这张是 P 的」。

所以输出里：
  · 没有 `tampered` 字段，只有 `signals` 和 `suspicion`（0~1）
  · 每条信号都写清**它也可能是正常原因**（微信发一次 EXIF 就没了，
    那不代表这张图被改过）
  · `suspicion` 高只在界面上显示一行提示，不影响这张图能不能被搜到
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 常见编辑软件在 EXIF Software 字段里留下的名字
_EDITORS = (
    "photoshop", "lightroom", "gimp", "snapseed", "picsart", "meitu",
    "美图", "affinity", "pixlr", "canva", "figma", "paint.net",
)

#: 各信号的权重，总和封顶 0.8 —— **永远达不到 1.0**，
#: 因为这套判据在设计上就不足以支撑"确定"这个程度
_WEIGHTS = {
    "no_exif": 0.10,
    "editor_software": 0.25,
    "size_mismatch": 0.30,
    "recompressed": 0.15,
    "no_camera": 0.08,
    "reverse_older": 0.20,
}


@dataclass
class TamperReport:
    """一张图的初筛结果。"""

    path: str = ""
    suspicion: float = 0.0
    signals: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    exif: dict[str, Any] = field(default_factory=dict)
    width: int = 0
    height: int = 0
    format: str = ""
    earliest_seen: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path, "suspicion": round(self.suspicion, 3),
            "signals": self.signals, "reasons": self.reasons,
            "exif": self.exif, "width": self.width, "height": self.height,
            "format": self.format, "earliestSeen": self.earliest_seen,
            "note": self.note,
        }


def _read_exif(path: Path) -> tuple[dict[str, Any], int, int, str]:
    """读 EXIF 和基本信息。缺 Pillow 或读不出时返回空 dict，不抛。"""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
    except ImportError:
        return {}, 0, 0, ""
    try:
        with Image.open(path) as im:
            w, h = im.size
            fmt = im.format or ""
            raw = getattr(im, "_getexif", lambda: None)() or {}
            exif = {}
            for k, v in raw.items():
                name = TAGS.get(k, str(k))
                if isinstance(v, bytes):
                    v = v.decode("utf-8", "ignore")[:120]
                if isinstance(v, (str, int, float)):
                    exif[name] = v
            return exif, w, h, fmt
    except (OSError, ValueError, AttributeError):
        return {}, 0, 0, ""


def _quant_tables(path: Path) -> list[int] | None:
    """
    取 JPEG 的量化表。非标准量化表说明这张图**不是相机直出**，
    而是被某个软件重新编码过。返回 None 表示不是 JPEG 或读不出。
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            if (im.format or "").upper() != "JPEG":
                return None
            q = getattr(im, "quantization", None)
            if not q:
                return None
            out: list[int] = []
            for table in q.values():
                out += list(table)[:8]
            return out
    except (OSError, ValueError, AttributeError):
        return None


def screen(path: str | Path, *, earliest_seen: str = "") -> TamperReport:
    """
    D4 主入口 —— 对一张图跑四条判据。

    `earliest_seen` 由调用方从反查结果里传进来（那一路要联网，
    不在这个函数里做 —— 一个"初筛"函数不该偷偷发网络请求）。
    """
    p = Path(path)
    rep = TamperReport(path=str(p), earliest_seen=earliest_seen)
    if not p.exists():
        rep.note = "文件不存在"
        return rep

    exif, w, h, fmt = _read_exif(p)
    rep.exif = {k: exif[k] for k in list(exif)[:24]}
    rep.width, rep.height, rep.format = w, h, fmt

    # ① EXIF 缺失 -----------------------------------------------
    if not exif:
        rep.signals.append("no_exif")
        rep.reasons.append(
            "完全没有 EXIF 信息。**这非常常见** —— 微信、微博、"
            "多数网站都会在压缩时把 EXIF 全部剥掉，所以它单独出现几乎说明不了什么"
        )
    else:
        if not any(k in exif for k in ("Make", "Model")):
            rep.signals.append("no_camera")
            rep.reasons.append("有 EXIF 但没有相机品牌/型号，多半经过软件保存")
        sw = str(exif.get("Software") or "").lower()
        if any(e in sw for e in _EDITORS):
            rep.signals.append("editor_software")
            rep.reasons.append(
                f"EXIF 里写着用 {exif.get('Software')} 处理过。"
                "**这不代表内容被改了** —— 调个色、裁个边也会留下同样的记录"
            )
        # ③ 尺寸打架 --------------------------------------------
        ex_w = int(exif.get("ExifImageWidth") or 0)
        ex_h = int(exif.get("ExifImageHeight") or 0)
        if ex_w and ex_h and w and h:
            if abs(ex_w - w) > 2 or abs(ex_h - h) > 2:
                rep.signals.append("size_mismatch")
                rep.reasons.append(
                    f"EXIF 里记的尺寸是 {ex_w}×{ex_h}，实际是 {w}×{h} —— "
                    "被裁剪或缩放过，且处理它的软件没同步更新元数据。"
                    "这一条是四条里最值得多看两眼的"
                )

    # ② 重压缩痕迹 ----------------------------------------------
    q = _quant_tables(p)
    if q:
        # 相机和标准编码器的量化表首项通常很小（高质量）。
        # 首项偏大 + 表内数值跨度大 = 被重新编码过且质量下降
        head = q[0] if q else 0
        spread = (max(q) - min(q)) if q else 0
        if head >= 8 and spread >= 40:
            rep.signals.append("recompressed")
            rep.reasons.append(
                "JPEG 量化表显示这张图被重新编码过（可能不止一次）。"
                "**存一次就会这样** —— 转发、截图、导出都算"
            )

    # ④ 反查时间 ------------------------------------------------
    if earliest_seen:
        rep.signals.append("reverse_older")
        rep.reasons.append(
            f"网上最早出现在 {earliest_seen}。如果这个时间早于它声称的拍摄时间，"
            "那才是真正值得追的线索"
        )

    rep.suspicion = min(0.8, sum(_WEIGHTS.get(s, 0.0) for s in rep.signals))

    if rep.suspicion >= 0.45:
        rep.note = ("有几处值得多看两眼的地方。**这不是「这张图是假的」的结论** —— "
                    "这套判据只做初筛，真正的图像取证要用专业工具由人判读")
    elif rep.signals:
        rep.note = "有一些常见的处理痕迹，大多数正常的转发和保存都会产生同样的痕迹"
    else:
        rep.note = "没有发现明显的处理痕迹（这也不等于它没被改过）"
    return rep


def screen_batch(paths: list[str | Path]) -> dict[str, Any]:
    """批量初筛。按可疑度倒序，方便用户从最该看的那张开始。"""
    reports = [screen(p) for p in paths[:200]]
    reports.sort(key=lambda r: -r.suspicion)
    flagged = [r for r in reports if r.suspicion >= 0.45]
    return {
        "reports": [r.to_dict() for r in reports],
        "total": len(reports),
        "flagged": len(flagged),
        "note": (
            f"{len(reports)} 张里有 {len(flagged)} 张有若干处理痕迹。"
            "**排序靠前不代表更可能是假的**，只代表更值得你自己看一眼"
        ),
    }
