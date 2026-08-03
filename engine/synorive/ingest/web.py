"""
网页抓取与存档 —— C11
====================================================================
用户选了「网页链接与收藏」作为数据源。这里做的事：

  抓 → 提正文 → **存档一份快照** → 索引

存档那一步是关键，也是收藏夹做不到的事：原网页删了、改了、要登录了，
你这儿还有当时那一份。技术博客一年后 404 是常态，
「我收藏过一篇讲 X 的文章」变成一个死链是最让人恼火的情况之一。

正文提取用 trafilatura：它会去掉导航、广告、页脚、相关推荐。
不去的话每个网页都会因为共同的导航文字而互相"相似"，
语义检索直接废掉 —— 搜什么都返回同一批站内页面。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("synorive.web")

#: 假装成普通浏览器。不少站点对没有 UA 的请求直接返回 403。
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: 单页最多读多少字节。有些站点会返回几十 MB 的页面（内嵌 base64 图片），
#: 全读进来毫无意义还会撑爆内存。
MAX_BYTES = 8 << 20

TIMEOUT = httpx.Timeout(20.0, connect=10.0, read=20.0)

#: 这些主机不允许抓 —— 防止把内网地址当成链接抓进来（SSRF）。
#: 用户自己粘的链接理论上安全，但剪贴板哨兵是自动抓的，
#: 复制到一个 http://192.168.1.1/admin 就麻烦了。
_BLOCKED_HOSTS = re.compile(
    r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|\[?::1\]?$)",
    re.I,
)


@dataclass
class FetchedPage:
    url: str
    final_url: str
    status: int
    title: str
    text: str
    html: str = ""
    published: str | None = None
    author: str | None = None
    site: str = ""
    lang: str | None = None
    archive_path: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def domain(self) -> str:
        try:
            return urlparse(self.final_url or self.url).netloc
        except ValueError:
            return ""


def is_url(s: str) -> bool:
    s = s.strip()
    if not s or len(s) > 2048 or " " in s:
        return False
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except ValueError:
        return False


def is_safe_url(s: str) -> tuple[bool, str]:
    """
    不许抓内网地址。

    用户手动粘的链接基本安全，但剪贴板哨兵是**自动**抓的 ——
    复制一个 http://192.168.1.1/admin 就会让这个工具替你去访问路由器后台。
    """
    try:
        u = urlparse(s)
    except ValueError:
        return False, "URL 格式不对"
    if u.scheme not in ("http", "https"):
        return False, f"只支持 http/https，收到 {u.scheme}"
    host = (u.hostname or "").strip("[]")
    if not host:
        return False, "没有主机名"
    if _BLOCKED_HOSTS.match(host):
        return False, f"不抓内网地址（{host}）—— 防止自动抓取访问到内网服务"
    return True, ""


def url_fingerprint(url: str) -> str:
    """URL 的指纹。去掉常见的追踪参数，同一篇文章不同来源不会重复入库。"""
    try:
        u = urlparse(url)
    except ValueError:
        return hashlib.sha256(url.encode()).hexdigest()[:32]

    drop = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "from", "spm", "share_token", "share_source", "fbclid", "gclid", "ref",
    }
    query = "&".join(
        p for p in (u.query or "").split("&")
        if p and p.split("=", 1)[0].lower() not in drop
    )
    canon = f"{u.scheme}://{u.netloc.lower()}{u.path.rstrip('/')}"
    if query:
        canon += f"?{query}"
    return hashlib.sha256(canon.encode()).hexdigest()[:32]


def fetch(
    url: str,
    *,
    archive_dir: Path | None = None,
    save_html: bool = True,
    keep_html: bool = False,
) -> FetchedPage:
    """
    抓一个网页并（可选）存档。任何失败都返回带 warnings 的对象，不抛异常。

    `keep_html` 把原始 HTML 一起带回来（N4 顺藤摸瓜要靠它抠出链）。
    **默认关**是刻意的：正文提取那一步会把链接和导航全部扔掉，
    那对语义检索是对的（不扔的话每个网页都因共同的导航文字而互相"相似"），
    但代价就是拿不到链接。默认带回一份完整 HTML 会让每次抓取
    多占几百 KB 内存，而绝大多数调用方根本不需要它。
    """
    ok, why = is_safe_url(url)
    if not ok:
        return FetchedPage(url=url, final_url=url, status=0, title="", text="", warnings=[why])

    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": UA,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
        ) as client:
            with client.stream("GET", url) as r:
                status = r.status_code
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                    return FetchedPage(
                        url=url, final_url=str(r.url), status=status, title="", text="",
                        warnings=[f"不是网页（Content-Type: {ctype}）"],
                    )
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        break
                final_url = str(r.url)
                encoding = r.encoding or "utf-8"
    except httpx.HTTPError as e:
        return FetchedPage(
            url=url, final_url=url, status=0, title="", text="",
            warnings=[f"抓取失败：{type(e).__name__}: {e}"],
        )

    html = bytes(buf).decode(encoding, errors="replace")
    page = _extract(html, url=final_url)
    page.url = url
    page.final_url = final_url
    page.status = status
    if keep_html:
        page.html = html
    if status >= 400:
        page.warnings.append(f"HTTP {status}")

    if archive_dir is not None and save_html and html:
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            name = f"{url_fingerprint(final_url)}.html"
            (archive_dir / name).write_text(html, encoding="utf-8", errors="replace")
            page.archive_path = name
        except OSError as e:
            page.warnings.append(f"存档失败：{e}")

    return page


def _extract(html: str, *, url: str) -> FetchedPage:
    """从 HTML 抽正文与元数据。"""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        from html import unescape

        title = unescape(re.sub(r"\s+", " ", m.group(1))).strip()

    text = ""
    published = author = lang = None
    site = ""

    try:
        import trafilatura

        # with_metadata 一次拿正文和元数据，比抽两遍快
        doc = trafilatura.bare_extraction(
            html, url=url, include_comments=False, include_tables=True,
            favor_precision=True, with_metadata=True,
        )
        if doc:
            get = doc.get if isinstance(doc, dict) else lambda k, d=None: getattr(doc, k, d)
            text = (get("text") or "").strip()
            title = (get("title") or title or "").strip()
            published = get("date") or None
            author = get("author") or None
            site = get("sitename") or ""
            lang = get("language") or None
    except Exception as e:  # noqa: BLE001
        log.debug("trafilatura 抽取失败：%s", e)

    warnings: list[str] = []
    if not text:
        # 兜底：粗暴去标签。质量差但总比索引不了强。
        from html import unescape

        body = re.sub(
            r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", " ", html,
            flags=re.S | re.I,
        )
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", body))).strip()
        if text:
            warnings.append("正文提取器没抽出内容，用了粗暴去标签，可能含导航噪声")

    if not site:
        try:
            site = urlparse(url).netloc
        except ValueError:
            site = ""

    return FetchedPage(
        url=url, final_url=url, status=200, title=title or site or url,
        text=text[:2_000_000], html=html, published=_norm_date(published),
        author=author, site=site, lang=lang, warnings=warnings,
    )


def _norm_date(s: str | None) -> str | None:
    """把各种日期格式统一成 ISO。抽不出来就返回 None，不猜。"""
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, tzinfo=UTC).isoformat(timespec="seconds")
        except ValueError:
            return None
    if re.match(r"^\d{4}$", s):
        return f"{s}-01-01T00:00:00+00:00"
    return None


def extract_urls(text: str, limit: int = 50) -> list[str]:
    """从一段文字里抽出链接 —— 剪贴板哨兵和聊天记录导入都要用。"""
    found = re.findall(r"https?://[^\s<>\"'）】，。；]+", text)
    seen: set[str] = set()
    out: list[str] = []
    for u in found:
        u = u.rstrip(".,;:!?)")
        if u in seen or not is_url(u):
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out
