"""
引擎运行时上下文 —— 全局单例，装着所有子系统的引用
====================================================================
不用全局变量满天飞，也不用依赖注入框架（那对这个规模是过度设计）。
一个 Runtime 对象串起来，FastAPI 的 app.state 里挂一份。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .store.db import Database


@dataclass
class EngineConfig:
    host: str = "127.0.0.1"
    port: int = 0
    data_dir: Path = field(default_factory=lambda: Path.cwd() / "data")
    model_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "models")
    concurrency: int = 7
    """是否允许把内容送到云端（受隐私围栏二次约束）"""
    allow_cloud: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "synorive.db"

    @property
    def thumb_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"


class EventBus:
    """
    引擎 → 界面的实时事件广播。

    用有界队列：界面卡住或断线时事件会堆积，无界队列会把内存吃光。
    满了就丢最老的 —— 状态类事件丢了无所谓，下一条就是最新状态。
    """

    MAX_QUEUE = 512

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self.dropped = 0

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.MAX_QUEUE)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    def publish(self, event_type: str, payload: Any) -> None:
        """线程安全的发布：分析流水线在工作线程里也能调。"""
        msg = {"type": event_type, "payload": payload}
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # 丢最老的一条给新的腾位置
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                    self.dropped += 1
                except Exception:
                    self.dropped += 1

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


class Runtime:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.started_at = time.time()
        self.db = Database(config.db_path)
        self.events = EventBus()
        self._proc_handle: Any | None = None

    def initialize(self) -> None:
        for d in (
            self.config.data_dir,
            self.config.model_dir,
            self.config.thumb_dir,
            self.config.archive_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        self.db.initialize()

    @property
    def uptime_sec(self) -> float:
        return time.time() - self.started_at

    # ── 自身资源占用（状态栏要显示）────────────────────────

    def resource_usage(self) -> tuple[float, float]:
        """返回 (cpu_percent, memory_mb)。拿不到就返回 0，不让它把请求搞挂。"""
        try:
            import psutil  # 可选依赖，没装就降级

            if self._proc_handle is None:
                self._proc_handle = psutil.Process(os.getpid())
            p = self._proc_handle
            return float(p.cpu_percent(interval=None)), p.memory_info().rss / 1024 / 1024
        except Exception:
            return 0.0, 0.0
