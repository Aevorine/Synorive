"""
W5 图片反查 —— 以图搜图找出处、找更高清版
====================================================================
🔴 **这条协议没有官方文档，是逆向出来的，随时可能失效。**

Bing 的"上传图片搜索"走的是三步暗地流程（浏览器点"以图搜图"按钮时
背后真正发生的事）：
  ① POST 一张 base64 编码的图片，换回一个 bcid（Bing Correlation ID）
     和一段"图片签名"（一个要用固定密钥做 XOR 解密才能用的加密串）
  ② 用 bcid + 解密后的签名，去查 insights 接口，拿到结构化的 JSON 结果
  ③ 结果按 `tags[].actions[]` 摊开，不同 `actionType` 装着不同种类的信息
     （"这张图出现在哪些网页里" / "视觉相似的图片" / 最佳猜测关键词……）

这套流程和字段名（`imageBin`/`cbir`/`skey`/`imageSignature` 的 XOR 密钥
和偏移量、`knowledgeRequest` 这个字段名）**全部来自实测核实**——
逐行核对了一个活跃维护的开源项目（PicImageSearch）当前的源码，
不是凭记忆编的。即便如此，Bing 这类没有文档的内部接口历史上
确实改过参数名，这里做不到"保证一直能用"，只能做到"现在能用、
坏了会明确报出来"。

**Yandex / Google Lens 没有实现**：
  Yandex 的以图搜图效果公认最好，但直接请求会被验证码拦下（W1 已经
  实测过它对自动化流量的态度），要走真的浏览器上传文件+点按钮才行，
  而现有的渲染代理（render.ts）只支持"加载一个 URL"，不支持
  "在页面里找到文件选择框、注入本地文件、点击搜索"这几步交互——
  这是一块新的、有真实实现风险的功能，与其写一份没法验证对不对的
  猜测代码，不如老实说清楚现在做不到，留给你或者以后单独评估。
  Google Lens 完全没有可用的网页协议或公开 API，同样没有实现。
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("synorive.reverse_image")

BING_BASE = "https://www.bing.com"
TIMEOUT = httpx.Timeout(20.0, connect=8.0)

#: Bing 图片签名的 XOR 解密密钥和偏移量——来自实测核实的开源实现，
#: 这是 Bing 前端自己用的一段固定字符串，不是什么秘密的东西，
#: 纯粹是页面反爬里"稍微增加点自动化门槛"的手段
_SIG_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIGd3gMN2v1KRLBGmotz7jbQYF8PaB+Jpe6iVf2YIeN5b"
_SIG_OFFSET = 3


def _decrypt_signature_segment(encrypted: str) -> str:
    try:
        raw = base64.b64decode(encrypted)
    except Exception:  # noqa: BLE001
        return encrypted
    out = []
    for i, byte in enumerate(raw):
        key_char = ord(_SIG_KEY[i % len(_SIG_KEY)])
        out.append(chr((byte ^ key_char) - _SIG_OFFSET))
    return "".join(out)


def _parse_signature(raw_signature: str) -> str:
    parts = raw_signature.split("|")
    if len(parts) == 3:
        version, encrypted, ts = parts
        return f"{version}|{_decrypt_signature_segment(encrypted)}|{ts}"
    return raw_signature


@dataclass
class ReverseImageHit:
    title: str
    page_url: str
    thumbnail_url: str
    image_url: str
    kind: str  # pages_including / visual_similar

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "pageUrl": self.page_url,
            "thumbnailUrl": self.thumbnail_url, "imageUrl": self.image_url,
            "kind": self.kind,
        }


@dataclass
class ReverseImageResult:
    pages_including: list[ReverseImageHit] = field(default_factory=list)
    visual_similar: list[ReverseImageHit] = field(default_factory=list)
    best_guess: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pagesIncluding": [h.to_dict() for h in self.pages_including],
            "visualSimilar": [h.to_dict() for h in self.visual_similar],
            "bestGuess": self.best_guess,
            "error": self.error,
        }


class BingReverseImage:
    """
    一次搜索一个新实例——`skey`/图片签名是每次上传各自独立的会话状态，
    不该在多次搜索之间复用（复用了也没意义，Bing 那边这些值本来就是
    绑定到这次上传的临时凭证）。
    """

    def __init__(self) -> None:
        self._session_key: str | None = None
        self._image_signature: str | None = None

    async def search_file(self, path: Path, *, limit: int = 20) -> ReverseImageResult:
        try:
            image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as e:
            return ReverseImageResult(error=f"读不了这个文件：{e}")

        async with httpx.AsyncClient(
            base_url=BING_BASE, timeout=TIMEOUT, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            try:
                bcid = await self._upload(client, image_b64)
            except _ProtocolError as e:
                return ReverseImageResult(error=str(e))
            except httpx.HTTPError as e:
                return ReverseImageResult(error=f"上传失败：{type(e).__name__}: {e}")

            try:
                data = await self._insights(client, bcid=bcid)
            except httpx.HTTPError as e:
                return ReverseImageResult(error=f"查结果失败：{type(e).__name__}: {e}")

        return _parse_insights(data, limit=limit)

    async def _upload(self, client: httpx.AsyncClient, image_b64: str) -> str:
        resp = await client.post(
            "/images/search?view=detailv2&iss=sbiupload",
            files={"cbir": (None, "sbi"), "imageBin": (None, image_b64)},
        )
        text = resp.text
        skey_m = re.search(r"skey=([^&\"]+)", text)
        sig_m = re.search(r"imageSignature&quot;:&quot;(.+?)&quot;", text)
        bcid_m = re.search(r"(bcid_[A-Za-z0-9\-.]+)", text)
        if not bcid_m:
            # HTTP 200 但页面结构不认识——多半是 Bing 又改协议了，
            # 这是"协议失效"不是"没有结果"，两者不能混着报
            raise _ProtocolError(
                "Bing 反查协议看起来失效了（拿不到 bcid，可能是页面结构变了）"
            )
        self._session_key = skey_m[1] if skey_m else ""
        self._image_signature = sig_m[1] if sig_m else ""
        return bcid_m[1]

    async def _insights(self, client: httpx.AsyncClient, *, bcid: str) -> dict[str, Any]:
        params = {
            "rshighlight": "true", "textDecorations": "true",
            "internalFeatures": "similarproducts,share", "nbl": "1",
            "skey": self._session_key or "", "safeSearch": "off",
            "mkt": "zh-cn", "setLang": "zh-hans",
            "iss": "SBIUPLOADGET", "IID": "idpins", "SFX": "1",
            "insightsToken": bcid,
        }
        headers = {"Referer": f"{BING_BASE}/images/search?insightsToken={bcid}"}
        if self._image_signature:
            headers["X-Image-Knowledge-Signature"] = _parse_signature(self._image_signature)

        image_info = {"imageInfo": {"imageInsightsToken": bcid, "source": "Gallery"}}
        # 这次请求要用一份干净的会话——上一步上传留下的 cookie 不该带过来，
        # 这一点也是照抄验证过的实现，不是我自己的猜测
        client.cookies.clear()
        resp = await client.post(
            "/images/api/custom/knowledge",
            params=params, headers=headers,
            files={"knowledgeRequest": (None, json.dumps(image_info), "application/json")},
        )
        try:
            return resp.json()
        except ValueError as e:
            raise _ProtocolError(f"insights 接口返回的不是 JSON（协议多半又变了）：{e}") from e


class _ProtocolError(Exception):
    """协议失效——页面结构/接口形状变了，区别于普通的网络错误，方便上层单独提示。"""


def _parse_insights(data: dict[str, Any], *, limit: int) -> ReverseImageResult:
    result = ReverseImageResult()
    tags = data.get("tags") or []
    for tag in tags:
        for action in tag.get("actions") or []:
            action_type = action.get("actionType") or ""
            payload = action.get("data") or {}

            if action_type == "PagesIncluding":
                for item in (payload.get("value") or [])[:limit]:
                    hit = _hit_from_value(item, "pages_including")
                    if hit:
                        result.pages_including.append(hit)

            elif action_type == "VisualSearch":
                for item in (payload.get("value") or [])[:limit]:
                    hit = _hit_from_value(item, "visual_similar")
                    if hit:
                        result.visual_similar.append(hit)

            elif action_type == "BestRepresentativeQuery":
                guess = action.get("displayName")
                if guess:
                    result.best_guess = str(guess)

    return result


#: 一个视频可能有几十个关键帧，全部拿去反查既慢又没必要——
#: 挑几帧分散在整段视频里就够了，一段视频的"出处"通常整段都是同一个源
MAX_VIDEO_KEYFRAMES = 5


@dataclass
class VideoSourceCandidate:
    page_url: str
    title: str
    #: 命中了这个来源的关键帧数——命中越多次，越可能是真的出处
    #: （单一关键帧撞对一个视觉相似的图不算什么，好几帧都指向同一个站才说明问题）
    matched_keyframes: int
    thumbnail_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pageUrl": self.page_url, "title": self.title,
            "matchedKeyframes": self.matched_keyframes, "thumbnailUrl": self.thumbnail_url,
        }


async def reverse_video_search(
    keyframe_paths: list[Path], *, max_frames: int = MAX_VIDEO_KEYFRAMES,
) -> dict[str, Any]:
    """
    W6 视频反查：均匀抽几帧关键帧分别做以图搜图，按"被几帧命中"聚合排序。

    抽帧不是取前几帧——视频开头常是片头/水印，不代表内容本身。
    均匀间隔取样才能代表"这段视频实际在讲什么"。
    """
    if not keyframe_paths:
        return {"candidates": [], "error": "这个视频没有可用的关键帧（还没跑完场景检测？）"}

    if len(keyframe_paths) > max_frames:
        step = len(keyframe_paths) / max_frames
        sampled = [keyframe_paths[int(i * step)] for i in range(max_frames)]
    else:
        sampled = keyframe_paths

    by_url: dict[str, VideoSourceCandidate] = {}
    errors: list[str] = []
    for p in sampled:
        if not p.exists():
            continue
        result = await BingReverseImage().search_file(p, limit=10)
        if result.error:
            errors.append(result.error)
            continue
        # pages_including 是"这张图出现在哪个网页"，这才是找视频出处要的信息；
        # visual_similar 是"长得像但不一定是同一个视频"，噪声太多，不参与聚合
        for hit in result.pages_including:
            existing = by_url.get(hit.page_url)
            if existing:
                existing.matched_keyframes += 1
            else:
                by_url[hit.page_url] = VideoSourceCandidate(
                    page_url=hit.page_url, title=hit.title,
                    matched_keyframes=1, thumbnail_url=hit.thumbnail_url,
                )

    candidates = sorted(by_url.values(), key=lambda c: -c.matched_keyframes)
    out: dict[str, Any] = {
        "candidates": [c.to_dict() for c in candidates],
        "framesTried": len(sampled),
    }
    # 抽的帧全部失败才算整体失败；部分失败只是少了几帧的信号，
    # 不该让"抽了 5 帧、1 帧网络抖动"这种情况整体报错
    if errors and len(errors) == len(sampled):
        out["error"] = f"全部 {len(sampled)} 帧反查都失败了：{errors[0]}"
    return out


def _hit_from_value(item: dict[str, Any], kind: str) -> ReverseImageHit | None:
    page_url = str(item.get("hostPageUrl") or "")
    if not page_url:
        return None
    return ReverseImageHit(
        title=str(item.get("name") or ""),
        page_url=page_url,
        thumbnail_url=str(item.get("thumbnailUrl") or ""),
        image_url=str(item.get("contentUrl") or ""),
        kind=kind,
    )
