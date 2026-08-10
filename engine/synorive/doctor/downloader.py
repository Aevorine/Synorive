"""
下载器 —— 断点续传 + 多源自动择优 + 校验
====================================================================
模型动辄几十上百 MB，国内网络下载到 90% 断掉是常态。所以：

① **断点续传**：下到 .part 文件，重启后带 Range 头接着下，不从头来。
   这直接对应验收标准 A13「断点续跑：已完成的不重做」。

② **多源择优 + 卡住自动换源**：每个文件登记了国内镜像和官方源两个地址。
   不写死"国内就走镜像" —— 用户可能挂了代理，那时官方源更快。
   实际做法是并发探测谁先响应，按响应快慢排出一个候选顺序。
   🔴 **响应快不等于下得动**（2026-08-10 实测排查）：探测用的是一个 1 字节
   的 Range 请求，测的是"接不接得通"，不是"带宽够不够"——国内镜像常见的
   情况是探测秒回，正式下载却被限速到几十 KB/s 甚至直接卡死不动，
   而原来的代码选完源之后就只认这一家，卡住了也只能干等 httpx 那个
   120 秒的读超时，用户看到的就是进度条一动不动却也不报错。
   所以正式下载时**边下边测速**：连续几秒钟速度掉到一个很低的数就主动
   断开换下一个候选源接着下（从已下的字节数续传，不是重头来），
   而不是靠一个测不出真实带宽的探针一锤定音。

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
#: 单次等一个数据块最多这么久。真卡死的连接（一个字节都不来）
#: 靠这个及时发现，不靠 httpx 客户端那个笼统的读超时
CHUNK_READ_TIMEOUT_S = 20.0
#: 速度低于这个就算"没有在下"——20 KB/s 是一个很宽松的下限，
#: 只用来抓真正卡住/被限流到几乎不动的情况，不是用来挑"最快"的那家
STALL_BPS = 20_000
#: 刚连上、缓冲区还没爬满速度的宽限期，这段时间内测到的低速不算数
STALL_GRACE_S = 3.0
#: 连续低于 STALL_BPS 这么久才判定"这个源不行了"，
#: 避免网络抖一下就误杀一个其实还行的源
STALL_CONFIRM_S = 5.0


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


async def probe_sources(client: httpx.AsyncClient, urls: tuple[str, ...]) -> list[tuple[str, int | None]]:
    """
    并发探测所有源，按**响应快慢**排出一份候选顺序（不是只留最快那个）。

    用 GET + Range: bytes=0-0 而不是 HEAD：有些 CDN 对 HEAD 的响应
    和真实 GET 不一致（尤其是重定向到对象存储时），探测通过但下载 404。

    🔴 这只测得到"接不接得通、回得快不快"，测不出"下载时快不快"——
    见 `download_file` 里的测速换源逻辑，那才是治真正的病根，
    这里的排序只是给第一次尝试一个比瞎选更好的起点，且**留着候选名单**，
    好让正式下载中途卡住时有下一个可换，不用重新探测。
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
    ordered: list[tuple[str, int | None]] = []
    try:
        for coro in asyncio.as_completed(tasks):
            got = await coro
            if got is not None:
                ordered.append(got)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

    if not ordered:
        raise DownloadError(f"所有下载源都不可达：{urls}")
    return ordered


@dataclass
class _StreamResult:
    """一次单源尝试的结果。`completed=False` 时 `reason` 必须说清楚为什么中止。"""

    downloaded: int
    total: int | None
    completed: bool
    reason: str = ""


async def _stream_one(
    client: httpx.AsyncClient,
    url: str,
    part: Path,
    resume_from: int,
    total: int | None,
    on_progress: ProgressCb | None,
    source: str,
    filename: str,
) -> _StreamResult:
    """
    从一个源流式下载到 `part`，**边下边测速**。

    连续 `STALL_CONFIRM_S` 秒速度都低于 `STALL_BPS`，或者单个数据块等超过
    `CHUNK_READ_TIMEOUT_S` 都没等到，就主动断开、把已经下到的字节数原样
    返回——好过靠 httpx 的读超时干等，那期间进度条纹丝不动，用户分不清
    是卡死了还是真的在下。上层 `download_file` 拿到这个"没下完"的结果后
    会换下一个候选源，从这里返回的字节数继续续传。
    """
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    downloaded = resume_from
    t_start = time.monotonic()
    t_last = t_start
    bytes_last = downloaded
    slow_since: float | None = None

    async with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
        if r.status_code == 416:
            # 服务端说范围不对 —— 多半是 .part 已经完整了
            return _StreamResult(downloaded, total, True)
        if r.status_code not in (200, 206):
            return _StreamResult(downloaded, total, False, f"HTTP {r.status_code}")

        if total is None:
            cl = r.headers.get("content-length")
            if cl and cl.isdigit():
                total = int(cl) + resume_from

        mode = "ab" if resume_from and r.status_code == 206 else "wb"
        if mode == "wb":
            downloaded = 0

        with part.open(mode) as f:
            aiter = r.aiter_bytes(CHUNK)
            while True:
                try:
                    chunk = await asyncio.wait_for(aiter.__anext__(), timeout=CHUNK_READ_TIMEOUT_S)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    return _StreamResult(
                        downloaded, total, False, f"{CHUNK_READ_TIMEOUT_S:.0f}s 没收到新数据，像是卡死了"
                    )

                f.write(chunk)
                downloaded += len(chunk)

                now = time.monotonic()
                if now - t_last >= PROGRESS_INTERVAL:
                    speed = (downloaded - bytes_last) / max(1e-6, now - t_last)
                    if on_progress:
                        on_progress(
                            Progress(filename, downloaded, total or 0, speed, resume_from, source)
                        )

                    if now - t_start >= STALL_GRACE_S:
                        if speed < STALL_BPS:
                            slow_since = slow_since or now
                            if now - slow_since >= STALL_CONFIRM_S:
                                return _StreamResult(
                                    downloaded, total, False,
                                    f"连续 {STALL_CONFIRM_S:.0f}s 低于 {STALL_BPS / 1000:.0f} KB/s"
                                    f"（实测约 {speed / 1000:.1f} KB/s）",
                                )
                        else:
                            slow_since = None
                    t_last, bytes_last = now, downloaded

    return _StreamResult(downloaded, total, True)


async def download_file(
    client: httpx.AsyncClient,
    urls: tuple[str, ...],
    dest: Path,
    *,
    expected_sha256: str | None = None,
    on_progress: ProgressCb | None = None,
) -> Path:
    """
    下载单个文件，支持断点续传 + 卡住自动换源。已存在且校验通过就直接返回。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    # 已经下好了？校验一下就不用再下
    if dest.exists() and dest.stat().st_size > 0:
        if expected_sha256 is None or _sha256(dest) == expected_sha256:
            log.debug("已存在，跳过：%s", dest.name)
            return dest
        log.warning("%s 校验不通过，重新下载", dest.name)
        dest.unlink()

    candidates = await probe_sources(client, urls)
    total = next((sz for _, sz in candidates if sz is not None), None)
    resume_from = part.stat().st_size if part.exists() else 0

    # 断点比远端文件还大 = 之前下的是另一个版本，作废重来
    if total is not None and resume_from >= total:
        part.unlink(missing_ok=True)
        resume_from = 0

    downloaded = resume_from
    t_start = time.monotonic()
    tried: list[str] = []
    last_reason = ""
    completed = False
    final_source = ""

    for url, cand_total in candidates:
        source = url.split("/")[2]
        tried.append(source)
        if downloaded:
            log.info("%s 从 %.1f MB 处续传（源：%s）", dest.name, downloaded / 1e6, source)
        else:
            log.info("%s 开始下载（源：%s）", dest.name, source)

        try:
            result = await _stream_one(
                client, url, part, downloaded, total or cand_total, on_progress, source, dest.name,
            )
        except httpx.HTTPError as e:
            # 连接被重置/中途断开这类传输层错误——常见于国内访问境外镜像/官方源，
            # 换下一个候选源接着下，而不是让整个安装崩成一个未处理的异常
            result = _StreamResult(downloaded, total, False, f"{type(e).__name__}：{e}")

        downloaded = result.downloaded
        total = result.total or total
        final_source = source
        if result.completed:
            completed = True
            break
        last_reason = result.reason
        log.warning("%s 源 %s 中止（%s），换下一个源接着下", dest.name, source, last_reason)

    if not completed:
        raise DownloadError(
            f"{dest.name} 试过 {len(tried)} 个源都下不动（{'、'.join(tried)}）：{last_reason}。"
            f"已下到 {downloaded:,} 字节，留在 .part 里，下次重试会接着续传"
        )

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
        on_progress(Progress(dest.name, downloaded, total or downloaded, 0.0, downloaded, final_source))
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
