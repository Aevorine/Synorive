"""
B6：后台批量任务主动降 CPU/IO/内存优先级，前台搜索永远不被它们卡住。
====================================================================
用的是 `SetThreadPriority(THREAD_MODE_BACKGROUND_BEGIN)`，**不是**
`SetPriorityClass`：后者是整个进程的开关，会连带把接收前台搜索请求的
那些线程也一起降下去——FastAPI 服务和批量摄取现在跑在同一个进程里，
只有线程级别的 API 才能做到"只降后台工作线程，不动接请求的线程"。

官方文档（Microsoft Learn，2026-09 核实）：
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadpriority
原文：调用一次，系统会联动降低该线程的 CPU 调度优先级、I/O 优先级与
内存优先级——不用分别调三次、也不用自己拼 I/O 优先级那套私有 API。

非 Windows 平台：整个模块退化成空操作，不报错、不警告刷屏——降优先级
是"系统繁忙时谁先让路"的锦上添花，不该成为跨平台可用性的绊脚石。
"""

from __future__ import annotations

import contextlib
import logging
import platform

log = logging.getLogger("synorive.winprio")

IS_WINDOWS = platform.system() == "Windows"

_THREAD_MODE_BACKGROUND_BEGIN = 0x00010000
_THREAD_MODE_BACKGROUND_END = 0x00020000

_warned = False


def _kernel32():
    import ctypes

    return ctypes.windll.kernel32  # type: ignore[attr-defined]


def enter_background_mode() -> bool:
    """
    把**调用它的那个线程**标记为后台模式。只对当前线程生效，
    不影响同进程里的其他线程（尤其是处理搜索请求的那些）。

    返回 True 表示确认生效；False 表示尝试过但系统拒绝或平台不支持——
    调用方不应该因此报错，只是这条线程会按普通优先级继续跑。
    """
    global _warned
    if not IS_WINDOWS:
        return False
    try:
        k32 = _kernel32()
        handle = k32.GetCurrentThread()
        ok = bool(k32.SetThreadPriority(handle, _THREAD_MODE_BACKGROUND_BEGIN))
        if not ok and not _warned:
            _warned = True
            log.warning("SetThreadPriority(BACKGROUND_BEGIN) 被系统拒绝，该线程按默认优先级跑")
        return ok
    except OSError:
        if not _warned:
            _warned = True
            log.warning(
                "降后台线程优先级失败，已忽略——只影响系统繁忙时的抢占顺序，不影响功能",
                exc_info=True,
            )
        return False


def exit_background_mode() -> None:
    """线程退出后台模式，恢复默认优先级（线程池复用线程给别的活时收尾用）。"""
    if not IS_WINDOWS:
        return
    with contextlib.suppress(OSError):
        k32 = _kernel32()
        k32.SetThreadPriority(k32.GetCurrentThread(), _THREAD_MODE_BACKGROUND_END)


def get_current_thread_priority() -> int | None:
    """读当前线程的优先级数值，只用于自检/测试——验证上面两个函数真的生效了。"""
    if not IS_WINDOWS:
        return None
    with contextlib.suppress(OSError):
        k32 = _kernel32()
        return int(k32.GetThreadPriority(k32.GetCurrentThread()))
    return None
