"""
引擎运行时上下文 —— 全局单例，装着所有子系统的引用
====================================================================
不用全局变量满天飞，也不用依赖注入框架（那对这个规模是过度设计）。
一个 Runtime 对象串起来，FastAPI 的 app.state 里挂一份。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .store.db import Database

log = logging.getLogger("synorive.runtime")


def _short_provider(name: str) -> str:
    """
    ONNX Runtime 的执行器全名太长（CPUExecutionProvider），
    状态栏那一小条塞不下。截成人看得懂的短名。
    """
    return {
        "CPUExecutionProvider": "CPU",
        "DmlExecutionProvider": "核显",
        "CUDAExecutionProvider": "CUDA",
        "AzureExecutionProvider": "Azure",
    }.get(name, name.replace("ExecutionProvider", "") or "CPU")


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

        # 这些在 initialize() 里装配
        self.repo: Any = None
        self.search: Any = None
        self.pipeline: Any = None
        self.doctor: Any = None
        self._embedder: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._jobs: dict[str, dict[str, Any]] = {}

    def initialize(self) -> None:
        for d in (
            self.config.data_dir,
            self.config.model_dir,
            self.config.thumb_dir,
            self.config.archive_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        self.db.initialize()

        # 延迟导入：这几个模块拉起 numpy / onnxruntime，
        # 写在文件顶部会让每次 import synorive.runtime 都慢几百毫秒
        from .doctor.service import Doctor
        from .ingest.pipeline import IngestPipeline
        from .search.engine import SearchEngine
        from .store.repository import Repository

        self.repo = Repository(self.db)
        self.doctor = Doctor(
            self.config.model_dir,
            on_status=lambda ev: self.events.publish("dependency.status", ev),
        )
        self.pipeline = IngestPipeline(
            self.repo,
            self.config.model_dir,
            concurrency=self.config.concurrency,
            on_progress=lambda p: self.events.publish("ingest.job", p),
        )
        self.search = SearchEngine(self.db, self.repo, self._get_query_embedder())

    def _get_query_embedder(self) -> Any:
        """
        查询侧向量化器：单实例、多线程（和摄取流水线的配置相反）。
        查询要的是**单条延迟最低**，实测 P50 2.2ms；
        摄取要的是总吞吐，所以那边是多会话各单线程。

        ⚠️ 这里**不 load()**，只构造。加载 ONNX 会话要 300~400ms，
        在 initialize() 里同步加载会把冷启动从 1.2s 拖到 2.5s，
        直接顶破 A1「≤2.0s 可搜索」。改成后台线程预热（见 warmup_async）：
        引擎立刻可响应，模型在后面自己加载好；万一用户在预热完成前就搜了，
        encode() 内部的 load() 是幂等且带锁的，最多那一次慢 300ms。
        """
        if self._embedder is not None:
            return self._embedder
        from .analyze.embedder import TextEmbedder

        d = self.config.model_dir / "bge-small-zh-v1.5"
        if not (d / "model.onnx").exists():
            return None
        self._embedder = TextEmbedder(d)  # threads 默认取物理核数
        return self._embedder

    def warmup_async(self) -> None:
        """后台预热向量模型，不阻塞启动。"""
        import threading

        def run() -> None:
            emb = self._embedder
            if emb is None:
                return
            try:
                t0 = time.time()
                emb.load()
                emb.encode_one("预热", is_query=True)
                log.info("向量模型预热完成，耗时 %.0fms", (time.time() - t0) * 1000)
            except Exception as e:  # noqa: BLE001
                log.warning("向量模型预热失败，语义检索将不可用：%s", e)

        threading.Thread(target=run, daemon=True, name="warmup").start()

    def status_snapshot(self) -> dict[str, Any]:
        """给状态栏用的实时快照。"""
        cpu, mem = self.resource_usage()
        st = self.repo.stats() if self.repo else {"items": 0, "chunks": 0}
        return {
            "uptimeSec": round(self.uptime_sec, 1),
            "concurrency": self.config.concurrency,
            "cpuPercent": round(cpu, 1),
            "memoryMb": round(mem, 1),
            "queueDepth": 0,
            "activeJobs": sum(1 for j in self._jobs.values() if j.get("status") == "running"),
            "indexedItems": st["items"],
            "chunkCount": st.get("chunks", 0),
            "dbSizeMb": round(self.db.size_mb(), 2),
            "executionProvider": _short_provider(
                self._embedder.provider if self._embedder and self._embedder.ready else "CPU"
            ),
            "cloudReady": self.config.allow_cloud,
        }

    async def status_loop(self) -> None:
        """
        每 2 秒推一次状态。

        为什么要主动推：状态栏原来只显示引擎启动那一刻的快照，
        索引了 19 条还写着「已索引 1 条」—— 用户会以为索引没生效。
        没有订阅者时不算也不推，别白烧 CPU。
        """
        while True:
            try:
                await asyncio.sleep(2.0)
                if self.events.subscriber_count == 0:
                    continue
                self.events.publish("engine.status", self.status_snapshot())
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                log.debug("状态推送出错：%s", e)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """记下事件循环，工作线程要靠它把事件安全地送回来。"""
        self._loop = loop

    # ── 摄取任务 ────────────────────────────────────────────

    def start_ingest(
        self,
        paths: list[Path],
        *,
        recursive: bool = True,
        source: str = "file",
        tags: list[str] | None = None,
    ) -> str:
        """
        起一个后台摄取任务，立刻返回 jobId。

        分析全程在**工作线程**里跑，不占事件循环 ——
        这是「分析时界面不卡」的最后一环：引擎自己也不能被自己的分析卡住，
        否则 /health 和 WebSocket 都会超时，界面会以为引擎挂了。
        """
        import threading
        import uuid

        job_id = uuid.uuid4().hex[:16]
        self._jobs[job_id] = {"status": "running", "total": 0, "done": 0}

        def run() -> None:
            try:
                stats = self.pipeline.ingest_paths(paths, recursive=recursive, source=source, tags=tags)
                self._jobs[job_id] = {
                    "status": "done",
                    "total": stats.total,
                    "done": stats.done,
                    "failed": stats.failed,
                    "skipped": stats.skipped,
                }
                self.events.publish(
                    "ingest.job",
                    {
                        "jobId": job_id,
                        "status": "done",
                        "totalItems": stats.total,
                        "doneItems": stats.done,
                        "failedItems": stats.failed,
                        "skippedItems": stats.skipped,
                        "elapsedSec": round(stats.elapsed, 1),
                    },
                )
            except Exception as e:  # noqa: BLE001
                self._jobs[job_id] = {"status": "failed", "error": str(e)}
                self.events.publish("toast", {"level": "error", "message": f"摄取失败：{e}"})

        threading.Thread(target=run, daemon=True, name=f"ingest-{job_id}").start()
        return job_id

    async def install_dependency(self, dep_id: str) -> None:
        try:
            r = await self.doctor.install(dep_id)
            if r.get("ok"):
                # 装完向量模型要重建检索器，否则语义那一路还是空的
                if dep_id == "embed-text-zh":
                    self._embedder = None
                    self.search.embedder = self._get_query_embedder()
                self.events.publish("toast", {"level": "success", "message": f"{dep_id} 装好了"})
            else:
                self.events.publish(
                    "toast", {"level": "error", "message": f"{dep_id} 安装失败：{r.get('error')}"}
                )
        except Exception as e:  # noqa: BLE001
            self.events.publish("toast", {"level": "error", "message": f"{dep_id} 安装异常：{e}"})

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
