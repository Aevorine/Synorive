"""
下载器 —— 断点续传 + 多源自动择优 + 校验
====================================================================
模型动辄几十上百 MB，国内网络下载到 90% 断掉是常态。所以：

① **断点续传**：下到 .part 文件，重启后带 Range 头接着下，不从头来。
   这直接对应验收标准 A13「断点续跑：已完成的不重做」。

② **多源择优**：每个文件登记了国内镜像和官方源两个地址。
   不写死"国内就走镜像" —— 用户可能挂了代理，那时官方源更快。
   实际做法是并发 HEAD 探测，谁先回来用谁。

③ **下完必校验**：有 sha256 就比对，没有就至少查文件大小和魔数。
   下了一半的 .onnx 加载时报的错和"文件不存在"完全不像，
   能让人排查一小时才发现是下载断了。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("synorive.doctor")

#: 探测每个源的超时。超过这个还没响应就认为这条路不通。
PROBE_TIMEOUT = 6.0
#: 下载时单次读取的块大小
CHUNK = 1 << 16  # 64 KB
#: 多久回报一次进度（秒）—— 太频繁会把事件通道刷爆
PROGRESS_INTERVAL = 0.4


@dataclass
class Progress:
    filename: str
    downloaded: int
    total: int
    speed_bps: float
    resumed_from: int
    source: str

    @property
    def ratio(self) -> float:
        return self.downloaded / self.total if self.total else 0.0


ProgressCb = Callable[[Progress], None]


class DownloadError(RuntimeError):
    pass


async def probe_fastest(client: httpx.AsyncClient, urls: tuple[str, ...]) -> tuple[str, int | None]:
    """
    并发探测所有源，返回 (最先响应的 URL, 文件大小)。

    用 GET + Range: bytes=0-0 而不是 HEAD：有些 CDN 对 HEAD 的响应
    和真实 GET 不一致（尤其是重定向到对象存储时），探测通过但下载 404。
    """

    async def one(url: str) -> tuple[str, int | None] | None:
        try:
            r = await client.get(
                url, headers={"Range": "bytes=0-0"}, timeout=PROBE_TIMEOUT, follow_redirects=True
            )
            if r.status_code not in (200, 206):
                return None
            size: int | None = None
            cr = r.headers.get("content-range")
            if cr and "/" in cr:
                tail = cr.rsplit("/", 1)[-1]
                if tail.isdigit():
                    size = int(tail)
            elif r.status_code == 200:
                cl = r.headers.get("content-length")
                size = int(cl) if cl and cl.isdigit() else None
            return url, size
        except Exception:
            return None

    tasks = [asyncio.create_task(one(u)) for u in urls]
    try:
        for coro in asyncio.as_completed(tasks):
            got = await coro
            if got is not None:
                return got
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

    raise DownloadError(f"所有下载源都不可达：{urls}")


async def download_file(
    client: httpx.AsyncClient,
    urls: tuple[str, ...],
    dest: Path,
    *,
    expected_sha256: str | None = None,
    on_progress: ProgressCb | None = None,
) -> Path:
    """下载单个文件，支持断点续传。已存在且校验通过就直接返回。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    # 已经下好了？校验一下就不用再下
    if dest.exists() and dest.stat().st_size > 0:
        if expected_sha256 is None or _sha256(dest) == expected_sha256:
            log.debug("已存在，跳过：%s", dest.name)
            return dest
        log.warning("%s 校验不通过，重新下载", dest.name)
        dest.unlink()

    url, total = await probe_fastest(client, urls)
    source = url.split("/")[2]
    resume_from = part.stat().st_size if part.exists() else 0

    # 断点比远端文件还大 = 之前下的是另一个版本，作废重来
    if total is not None and resume_from >= total:
        part.unlink(missing_ok=True)
        resume_from = 0

    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    if resume_from:
        log.info("%s 从 %.1f MB 处续传（源：%s）", dest.name, resume_from / 1e6, source)
    else:
        log.info("%s 开始下载（源：%s）", dest.name, source)

    downloaded = resume_from
    t_start = time.monotonic()
    t_last = t_start
    bytes_last = downloaded

    async with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
        if r.status_code == 416:
            # 服务端说范围不对 —— 多半是 .part 已经完整了
            part.replace(dest)
            return dest
        if r.status_code not in (200, 206):
            raise DownloadError(f"{dest.name} 下载失败 HTTP {r.status_code}（源：{source}）")

        if total is None:
            cl = r.headers.get("content-length")
            if cl and cl.isdigit():
                total = int(cl) + resume_from

        mode = "ab" if resume_from and r.status_code == 206 else "wb"
        if mode == "wb":
            downloaded = 0

        with part.open(mode) as f:
            async for chunk in r.aiter_bytes(CHUNK):
                f.write(chunk)
                downloaded += len(chunk)

                now = time.monotonic()
                if on_progress and now - t_last >= PROGRESS_INTERVAL:
                    speed = (downloaded - bytes_last) / max(1e-6, now - t_last)
                    on_progress(
                        Progress(dest.name, downloaded, total or 0, speed, resume_from, source)
                    )
                    t_last, bytes_last = now, downloaded

    # 收尾必须核对大小：流式下载中途断了不会抛异常，只是提前结束 —— 典型静默失败
    if total and downloaded != total:
        raise DownloadError(
            f"{dest.name} 下载不完整：拿到 {downloaded:,} 字节，应为 {total:,} 字节。"
            "连接中途断了，重跑一次会从这里续传。"
        )

    if expected_sha256:
        got = _sha256(part)
        if got != expected_sha256:
            part.unlink(missing_ok=True)
            raise DownloadError(f"{dest.name} 校验和不符：期望 {expected_sha256[:16]}… 实得 {got[:16]}…")

    part.replace(dest)

    elapsed = time.monotonic() - t_start
    mb = downloaded / 1e6
    log.info("%s 完成 %.1f MB，耗时 %.1fs（%.1f MB/s）", dest.name, mb, elapsed, mb / max(elapsed, 1e-6))

    if on_progress:
        on_progress(Progress(dest.name, downloaded, total or downloaded, 0.0, resume_from, source))
    return dest


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def verify_onnx(path: Path) -> tuple[bool, str]:
    """
    确认这是个像样的 ONNX 文件而不是半截下载或一个 HTML 错误页。

    没有校验和时的兜底：HuggingFace 限流/需登录时会返回一个 HTML 页面，
    文件名对、大小也不为零，但内容是 <!DOCTYPE html>。
    这种文件加载时报的是"protobuf 解析失败"，跟"下载出错"看着毫无关系。
    """
    if not path.exists():
        return False, "文件不存在"
    size = path.stat().st_size
    if size < 1024:
        return False, f"只有 {size} 字节，几乎肯定不是模型"

    head = path.read_bytes()[:512]
    if head.lstrip()[:15].lower().startswith(b"<!doctype html") or head.lstrip()[:5] == b"<html":
        return False, "内容是 HTML 页面 —— 多半被限流或需要登录，不是模型文件"
    # ONNX 是 protobuf，第一个字段通常是 ir_version(varint)，字节 0x08
    if head[0] not in (0x08, 0x0A, 0x12):
        return False, f"文件头 0x{head[0]:02X} 不像 ONNX protobuf"
    return True, f"{size / 1e6:.1f} MB"
