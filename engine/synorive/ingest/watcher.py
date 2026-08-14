"""
目录监控 —— 文件变了自动同步索引，不用每次手动投喂
====================================================================
只监控用户在设置里显式加进"监听的目录"列表的那几个目录——不是一个
全局总开关，是按目录逐个加，这个列表默认是空的，加不加、加哪个都是
用户自己选。

🔴 **去抖是必须的，不是锦上添花。** 复制一个大文件、或者 git checkout
切分支，会在几百毫秒内对同一批文件触发几十个 created/modified 事件。
摄取不是幂等到可以无成本重复调用的操作——重新解析、重新切块、
重新向量化都是真实的 CPU/IO 成本。这里用一个"最后一次变化后 500ms
没再变"的静默窗口，把同一批变化合并成一次提交。

删除事件走 `soft_delete_item`——跟用户在界面上手动删除是同一条路径，
行为要一致（进回收站，不是硬删）。
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .parsers import SKIP_DIR_NAMES

log = logging.getLogger("synorive.watcher")

#: 变化静默多久才提交一次——见模块开头的说明
DEBOUNCE_SECONDS = 0.5

#: 每个监听目录根下可选的忽略规则文件，格式类似 .gitignore 但简化很多：
#: 一行一个 fnmatch 模式（相对目录根的路径），# 开头是注释，空行跳过。
#: 不支持 .gitignore 的否定模式（`!pattern`）和目录专属模式——
#: 需要那些复杂度的话用户大概率也希望这整个目录别被监控，直接从
#: "监听的目录"列表里移除更直接。
IGNORE_FILE_NAME = ".synorive-ignore"


def _read_ignore_patterns(folder: Path) -> list[str]:
    f = folder / IGNORE_FILE_NAME
    if not f.exists():
        return []
    try:
        lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


class _WatchedFolder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.patterns = _read_ignore_patterns(path)
        self.watch: Any = None  # watchdog 的 ObservedWatch 句柄，unschedule 时要用

    def is_ignored(self, changed: Path) -> bool:
        try:
            rel = changed.relative_to(self.path)
        except ValueError:
            return False
        rel_str = str(rel).replace("\\", "/")
        if any(part in SKIP_DIR_NAMES for part in rel.parts[:-1]):
            return True
        return any(fnmatch.fnmatch(rel_str, pat) for pat in self.patterns)


class _Handler(FileSystemEventHandler):
    def __init__(self, owner: "FolderWatcher") -> None:
        self._owner = owner

    def _handle(self, event: FileSystemEvent, *, removed: bool) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        self._owner._note(path, removed=removed)
        # 移动/改名：目的地按"新增"处理，源路径按"删除"处理——
        # 否则改个文件名，库里会同时留着旧路径的幽灵记录和新路径搜不到
        if event.event_type == "moved":
            dest = Path(str(getattr(event, "dest_path", "") or ""))
            if dest and not event.is_directory:
                self._owner._note(dest, removed=False)

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event, removed=False)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(event, removed=False)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(event, removed=True)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._handle(event, removed=True)


class FolderWatcher:
    """
    引擎里只建一个实例。`set_folders()` 是唯一的对外接口——
    传一份完整的目标目录列表进来，内部自己 diff 出该新增监听哪些、
    该撤销监听哪些，调用方不用关心"上次是什么状态"。
    """

    def __init__(
        self,
        on_changed: Callable[[list[Path]], None],
        on_removed: Callable[[list[Path]], None],
        debounce_sec: float = DEBOUNCE_SECONDS,
    ) -> None:
        self._on_changed = on_changed
        self._on_removed = on_removed
        self._debounce_sec = debounce_sec
        self._observer = Observer()
        self._observer.start()
        self._folders: dict[str, _WatchedFolder] = {}
        self._lock = threading.Lock()
        self._pending_changed: set[Path] = set()
        self._pending_removed: set[Path] = set()
        self._timer: threading.Timer | None = None

    def watched_folders(self) -> list[str]:
        return list(self._folders.keys())

    def set_folders(self, folders: list[str]) -> None:
        want = {str(Path(f)) for f in folders if f.strip()}
        with self._lock:
            for key in list(self._folders.keys()):
                if key not in want:
                    wf = self._folders.pop(key)
                    if wf.watch is not None:
                        try:
                            self._observer.unschedule(wf.watch)
                        except Exception as e:  # noqa: BLE001
                            log.debug("撤销监听失败（不影响其它目录）：%s", e)
                    log.info("目录监控：停止监听 %s", key)

            for key in want:
                if key in self._folders:
                    continue
                p = Path(key)
                if not p.is_dir():
                    log.warning("目录监控：%s 不是个目录，跳过", key)
                    continue
                wf = _WatchedFolder(p)
                try:
                    wf.watch = self._observer.schedule(_Handler(self), key, recursive=True)
                    self._folders[key] = wf
                    log.info(
                        "目录监控：开始监听 %s（%d 条忽略规则）", key, len(wf.patterns)
                    )
                except OSError as e:
                    log.warning("目录监控：监听 %s 失败：%s", key, e)

    def _note(self, path: Path, *, removed: bool) -> None:
        # 🔴 必须在同一把锁里读 self._folders 再改 pending 集合。
        # 这个方法跑在 watchdog 的调度线程上，set_folders()（来自
        # POST /api/watch/folders，即用户改"监听的目录"或引擎每次就绪
        # 重推）跑在别的线程改同一个 dict。原来 _owner_folder() 在锁外
        # 遍历 self._folders.values()，撞上 set_folders() 同时在
        # pop()/赋值这个 dict 就是 RuntimeError: dictionary changed
        # size during iteration——而 watchdog 的调度循环只 catch
        # queue.Empty，这个异常会直接把调度线程杀死，从此**所有**
        # 监听目录静默失效，watched_folders() 却还照常报"在监听"。
        with self._lock:
            owner = self._owner_folder(path)
            if owner is not None and owner.is_ignored(path):
                return
            if removed:
                self._pending_removed.add(path)
                self._pending_changed.discard(path)
            else:
                self._pending_changed.add(path)
                self._pending_removed.discard(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_sec, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _owner_folder(self, path: Path) -> _WatchedFolder | None:
        # 目录可能是嵌套关系？—— 不支持，一个变化路径归它路径前缀匹配到的
        # 第一个监听目录管，够用了（正常使用不会把一个监听目录加进另一个里）
        for wf in self._folders.values():
            try:
                path.relative_to(wf.path)
                return wf
            except ValueError:
                continue
        return None

    def _flush(self) -> None:
        with self._lock:
            changed = list(self._pending_changed)
            removed = list(self._pending_removed)
            self._pending_changed.clear()
            self._pending_removed.clear()
            self._timer = None
        if changed:
            try:
                self._on_changed(changed)
            except Exception as e:  # noqa: BLE001
                log.warning("目录监控：处理变化文件时出错：%s", e)
        if removed:
            try:
                self._on_removed(removed)
            except Exception as e:  # noqa: BLE001
                log.warning("目录监控：处理删除文件时出错：%s", e)

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        try:
            self._observer.stop()
            self._observer.join(timeout=5)
        except Exception as e:  # noqa: BLE001
            log.debug("停止目录监控失败（引擎马上也要退出了）：%s", e)
