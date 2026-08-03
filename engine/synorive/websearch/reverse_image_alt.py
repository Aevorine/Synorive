"""
B2 —— Yandex / Google Lens 图片反查（Bing 之外的另外两路）
====================================================================
🔴 **和 Bing 那条一样没有官方接口，是逆向出来的，随时可能失效。**

之前不做这两家的理由写在 `reverse_image.py` 的开头：「要真浏览器上传文件，
而渲染代理只会加载 URL」。那个判断**只对了一半** ——
上传那一步两家其实都收普通的 multipart POST（httpx 就能发，不需要浏览器）；
真正需要浏览器的只有**结果页**，而那正是 `RenderBroker.render()` 干的事。

拆成两步就绕开了原来的障碍：
  ① httpx 直接 multipart 上传 → 换回一个结果页地址
  ② 把那个地址交给渲染代理 → 拿渲染完的 HTML → 解析

🔴 **两家都可能弹验证码。** 那时候拿回来的是一个人机验证页而不是结果页。
所以解析器解不出东西时**必须区分「没有结果」和「被挡住了 / 协议变了」** ——
混成一句"没找到"，用户会以为这张图网上真的没出现过，
而那是这个功能能犯的最严重的错误：它把"我没查到"说成了"事实上没有"。

🔴 **这两家做出来是为了「多一路可试」，不是「稳定可用」。**
Google 对自动化流量最严，被挡是常态。界面上必须照实说，
否则某天它开始只回验证码，用户会以为是我们坏了。

单独成文件而不是塞进 `reverse_image.py`：那个文件装的是 Bing 那套
三步暗地流程（XOR 解密密钥、bcid、insights 协议），和这里的
「上传 + 借浏览器渲染 + 捞外链」是两种完全不同的做法，
混在一起会让两边的注意事项互相污染。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

from .reverse_image import ReverseImageHit, ReverseImageResult

log = logging.getLogger("synorive.reverse_image")

TIMEOUT = httpx.Timeout(20.0, connect=8.0)

YANDEX_UPLOAD = (
    "https://yandex.com/images-apphost/image-download"
    "?cbird=111&images_avatars_size=orig&images_avatars_namespace=images-cbir"
)
LENS_UPLOAD = "https://lens.google.com/v3/upload?hl=zh-CN&re=df&st=0&vpw=1280&vph=800"

#: 反查上传的体积上限。两家都会拒绝过大的图，但**在本地先拦下来**
#: 比发出去等对方拒绝快得多，也少一次把大图传给第三方
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

#: 从渲染后的 HTML 里捞外链。两家的结果页 DOM 变得很勤，
#: 但"结果条目是一个指向站外的 <a>"这一点十年没变过 —— 所以按这个捞，
#: 而不是按某个 class 名（那些是混淆过的随机串，每隔几个月换一批）
_ANCHOR_RE = re.compile(r'<a\b[^>]*?href="(https?://[^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")

#: 结果页自家的域名不算"别处出现过"
_SELF_HOSTS = (
    "yandex.",
    "google.",
    "gstatic.com",
    "googleusercontent.com",
    "lens.google",
    "ya.ru",
    "translate.goog",
    "schema.org",
    "w3.org",
)

#: 认出人机验证页。命中就**不是**"没有结果"
_BLOCKED_HINTS = (
    "showcaptcha",
    "smartcaptcha",
    "are you not a robot",
    "recaptcha",
    "unusual traffic",
    "/sorry/index",
)


def _read_image(path: Path) -> tuple[bytes | None, str | None]:
    try:
        data = path.read_bytes()
    except OSError as e:
        return None, f"读不了这个文件：{e}"
    if not data:
        return None, "这个文件是空的（0 字节）"
    if len(data) > MAX_UPLOAD_BYTES:
        mb = len(data) / 1024 / 1024
        return None, f"图太大了（{mb:.1f} MB），反查上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB"
    return data, None


def _looks_blocked(html: str) -> bool:
    low = html[:20000].lower()
    return any(h in low for h in _BLOCKED_HINTS)


def _hits_from_html(html: str, *, limit: int) -> list[ReverseImageHit]:
    """
    从渲染后的结果页里捞站外链接。

    🔴 **按"是不是站外链接"捞，不按 class 名捞。** 按 class 写的解析器
    寿命以周计；而"结果是一个指向别的站的 <a>"这个结构十年没变。
    代价是会混进少量导航链接 —— 用 `_SELF_HOSTS` 滤掉自家域名，
    再按 URL 去重，剩下的噪音用户一眼能认出来。
    这是个**明确的取舍**：宁可多几条噪音，也不要一个每月都失效的解析器。
    """
    out: list[ReverseImageHit] = []
    seen: set[str] = set()
    for m in _ANCHOR_RE.finditer(html):
        url = m[1]
        if any(h in url for h in _SELF_HOSTS) or url in seen:
            continue
        seen.add(url)
        title = _TAG_RE.sub("", m[2] or "").strip()[:160]
        out.append(
            ReverseImageHit(
                title=title or url,
                page_url=url,
                thumbnail_url="",
                image_url="",
                kind="pages_including",
            )
        )
        if len(out) >= limit:
            break
    return out


class YandexReverseImage:
    """
    Yandex 以图搜图。公认效果最好的一家，尤其是人像和小语种内容。

    两步：multipart 上传换一段查询串，再让渲染代理去打开结果页。
    """

    name = "yandex"
    label = "Yandex"

    def __init__(self, broker: Any = None) -> None:
        #: `RenderBroker`。**没有它这一家就用不了** —— 结果页是纯 JS 渲染的，
        #: 拿原始 HTML 回来只有一个空壳
        self.broker = broker

    async def search_file(self, path: Path, *, limit: int = 20) -> ReverseImageResult:
        data, err = _read_image(path)
        if err or data is None:
            return ReverseImageResult(error=err)
        if self.broker is None or not self.broker.available:
            return ReverseImageResult(
                error="Yandex 反查要借桌面端的浏览器渲染结果页，现在借不到"
                "（桌面端没连上引擎，或者引擎是在纯命令行模式下跑的）"
            )

        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.post(
                    YANDEX_UPLOAD, files={"upfile": ("image.jpg", data, "image/jpeg")}
                )
        except httpx.HTTPError as e:
            return ReverseImageResult(error=f"上传到 Yandex 失败：{type(e).__name__}: {e}")

        if resp.status_code != 200:
            return ReverseImageResult(error=f"Yandex 上传返回 HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            return ReverseImageResult(
                error="Yandex 上传接口回的不是 JSON —— 多半是协议变了，或者被验证码拦了"
            )

        # 正常返回里有一个 `url` 字段，内容是一段**查询串**（不是完整网址），
        # 拼到 /images/search 后面就是结果页
        frag = str(payload.get("url") or "").lstrip("?&")
        if not frag:
            keys = ", ".join(sorted(payload)[:6]) or "（空对象）"
            return ReverseImageResult(
                error=f"Yandex 上传成功但没给出结果页地址（返回里只有：{keys}）—— 协议可能变了"
            )
        page = f"https://yandex.com/images/search?{frag}&rpt=imageview&cbir_page=similar"

        html = await self.broker.render(page, timeout_s=14.0)
        if not html:
            return ReverseImageResult(error="Yandex 结果页渲染失败或超时")
        if _looks_blocked(html):
            return ReverseImageResult(
                error="Yandex 弹了人机验证 —— 这**不代表**这张图没出现过，只是这次没查成"
            )
        hits = _hits_from_html(html, limit=limit)
        if not hits:
            return ReverseImageResult(
                error="Yandex 结果页解析不出条目 —— 可能真的没有结果，也可能页面结构又变了。"
                "两种情况这里分不出来，别把它当成「网上没有这张图」"
            )
        return ReverseImageResult(pages_including=hits)


class LensReverseImage:
    """
    Google Lens 以图搜图。

    🔴 **比 Yandex 更脆**：上传接口回的是一个 302，要跟着跳到结果页；
    而 Google 对自动化流量的态度是三家里最严的，被 `/sorry/` 挡住是常态。
    """

    name = "lens"
    label = "Google Lens"

    def __init__(self, broker: Any = None) -> None:
        self.broker = broker

    async def search_file(self, path: Path, *, limit: int = 20) -> ReverseImageResult:
        data, err = _read_image(path)
        if err or data is None:
            return ReverseImageResult(error=err)
        if self.broker is None or not self.broker.available:
            return ReverseImageResult(
                error="Google Lens 反查要借桌面端的浏览器渲染结果页，现在借不到"
            )

        try:
            # 🔴 **不跟随重定向**是有意的：我们要的就是 Location 头里那个
            # 结果页地址，好把它交给渲染代理。让 httpx 自己跟过去，
            # 只会拿回一个没跑 JS 的空壳
            async with httpx.AsyncClient(
                timeout=TIMEOUT,
                follow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.post(
                    LENS_UPLOAD, files={"encoded_image": ("image.jpg", data, "image/jpeg")}
                )
        except httpx.HTTPError as e:
            return ReverseImageResult(error=f"上传到 Google Lens 失败：{type(e).__name__}: {e}")

        page = resp.headers.get("location") or ""
        if not page:
            if "sorry" in resp.text[:2000].lower():
                return ReverseImageResult(error="Google 直接返回了人机验证页，这次查不成")
            return ReverseImageResult(
                error=f"Google Lens 上传没给出结果页地址（HTTP {resp.status_code}）—— 协议可能变了"
            )
        if page.startswith("/"):
            page = "https://lens.google.com" + page

        html = await self.broker.render(page, timeout_s=14.0)
        if not html:
            return ReverseImageResult(error="Google Lens 结果页渲染失败或超时")
        if _looks_blocked(html):
            return ReverseImageResult(
                error="Google 弹了人机验证 —— 这**不代表**这张图没出现过，只是这次没查成"
            )
        hits = _hits_from_html(html, limit=limit)
        if not hits:
            return ReverseImageResult(
                error="Lens 结果页解析不出条目 —— 可能真没结果，也可能页面结构变了。这里分不出来"
            )
        return ReverseImageResult(pages_including=hits)
