"""
引擎运行时上下文 —— 全局单例，装着所有子系统的引用
====================================================================
不用全局变量满天飞，也不用依赖注入框架（那对这个规模是过度设计）。
一个 Runtime 对象串起来，FastAPI 的 app.state 里挂一份。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .render_broker import RenderBroker
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
    """
    是否允许联网搜索出网。

    和 `allow_cloud` **不是一回事，别合并成一个开关**：
    联网搜索是把**查询词**发给搜索引擎，云端推理是把**你的资料原文**发给模型厂商。
    前者泄露的是"我在查什么"，后者泄露的是"我有什么"。
    很多人愿意接受前者而绝不接受后者 —— 合成一个开关就逼他们二选一。
    """
    allow_network: bool = True
    """启用哪几家搜索引擎。None = 用各家的默认开关"""
    web_engines: list[str] | None = None
    """引擎的 Key（Brave/Serper/Tavily/Exa 的 API Key、自建 SearXNG 地址等）"""
    web_keys: dict[str, str] | None = None
    """S1 每轮最多派几家引擎（按最近表现排班 + 一个探索位）。
    0 = 全派，即旧行为。设成 5 之后，一家最近老失败的引擎不会每轮都白等它一次"""
    web_lineup_size: int = 0
    """V5 可信度模型的可调权重（`trust.TrustProfile.from_dict` 的形状）。
    None = 用默认档"""
    trust_profile: dict[str, Any] | None = None
    """V 组核查档位：annotate（只标注）/ counter（反向检索+溯源+撤稿，默认）/
    claim（再加断言级逐句核查，慢很多）"""
    verify_level: str = "counter"
    """C4 图片详细描述：调云端视觉模型给图片生成一段描述并入索引。
    默认关——这是隐私围栏 `allow_cloud` 之外的第二道闸，
    用户可能开了云端简报生成（R8）但不想让"库里的照片"被发出去描述"""
    enable_image_description: bool = False
    """C5 人脸检测与聚类。默认关——人脸数据是最敏感的一类"""
    enable_face_clustering: bool = False
    """A16 安卓配对令牌。非空时，所有从非本机地址发来的 /api 请求
    都必须带 `X-Synorive-Token` 头且和它一致，见 main.py 的 `_pairing_guard`。
    本机（桌面端自己/MCP/CLI，全部走 127.0.0.1）永远不受这道闸影响。"""
    pairing_token: str | None = None
    """4.22b H1：局域网是否走 HTTPS（自签证书 + 手机端指纹固定）。

    **默认 False，而且必须默认 False** —— 现在的明文配对是能用的功能，
    一个没法端到端验证的 TLS 改造默认打开，最坏结果是"更新了一下手机连不上了"。
    证书在 `<data-dir>/lan-cert.pem`，指纹通过免鉴权的 `/status` 报出来
    （手机在配对前就得能读到它，否则没法固定）。"""
    lan_tls: bool = False
    """
    E15 是否优先用核显（DirectML）跑 ONNX 推理。

    🔴 **这个字段以前根本不存在** —— 桌面端设置页有「启用核显加速」开关，
    但引擎侧没有任何地方读它，`TextEmbedder(prefer_gpu=...)` 也从来没人传过。
    结果是那个开关**只换了 onnxruntime 的包，没换实际的执行器**：
    用户打开它、重启引擎、看到一切正常，而推理还在 CPU 上跑。
    不报错、不降级、只是白开心一场。
    """
    prefer_gpu: bool = False
    """
    投喂目录时是否自动跳过看起来像密钥/凭据的文件（.env、id_rsa、
    credentials.json……）。**默认开，跟 allow_network 一样要显式关掉** ——
    这类文件本身就是纯文本/JSON，能被正常解析写进搜索索引甚至发去云端，
    而用户投喂一个项目目录时几百个文件混在一起，肉眼很难逐个排查。
    """
    sensitive_guard_enabled: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "synorive.db"

    @property
    def thumb_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"


@dataclass
class CloudState:
    """
    云端简报生成的运行时状态。**纯内存，不是 EngineConfig 的一部分** ——
    `EngineConfig` 是启动时定死的一份快照，而这个要能在引擎跑着的时候
    随时被桌面端的"设置"页更新（用户改了 Key 或换了通道，不该要求重启引擎）。
    """
    provider: str = "none"  # none / openai-compatible / anthropic
    api_key: str = ""
    base_url: str = ""
    chat_model: str = ""
    #: C4 图片描述用的视觉模型。和 chat_model 分开——很多厂商的文本模型
    #: 不支持读图（或者读图要换一个更贵的型号），逼用户共用一个字段
    #: 会导致"填了聊天模型，结果描述图片时拿它去传图直接 400"
    vision_model: str = ""

    @property
    def configured(self) -> bool:
        return self.provider != "none" and bool(self.api_key) and bool(self.chat_model)

    @property
    def vision_configured(self) -> bool:
        return self.provider != "none" and bool(self.api_key) and bool(self.vision_model)

    def status(self) -> dict[str, Any]:
        """给设置页/MCP 看的状态——**绝不包含 api_key 本身**。"""
        return {
            "provider": self.provider,
            "configured": self.configured,
            "chatModel": self.chat_model,
            "visionModel": self.vision_model,
            "visionConfigured": self.vision_configured,
            "baseUrl": self.base_url,
        }


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
        self.watcher: Any = None
        #: 联网元搜索（W/R/L）。**独立于本地检索** ——
        #: 它是唯一会出网的部件，隐私围栏要关的就是它，所以单独挂一个字段，
        #: 关掉时置 None，接口层据此返回明确的 503 而不是半死不活地跑着
        self.web: Any = None
        #: 浏览器渲染代理（8.5）。永远存在（不出网、不占资源），
        #: 只有桌面端连上并注册了端口才 available=True
        self.render_broker = RenderBroker()
        #: 云端简报生成（R8 右栏）的运行时配置。
        #: 🔴 密钥只活在这个进程的内存里，从不落盘、从不写日志 ——
        #: 桌面端用 Electron `safeStorage` 加密存在本地，引擎重启后
        #: 桌面端会用 `/api/cloud/configure` 重新推一次，不是引擎自己记住的
        self.cloud = CloudState()
        self._embedder: Any = None
        self._reranker: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._jobs: dict[str, dict[str, Any]] = {}
        #: 回收站过期清理上次跑的时间，见 deferred_jobs_loop() 里的节流判断
        self._last_trash_purge = 0.0
        #: 后台补跑循环的轮询间隔。有活干就勤快（3s），没活干就歇着（15s）——
        #: 见 deferred_jobs_loop()
        self._deferred_interval = 3.0

    def initialize(self) -> None:
        for d in (
            self.config.data_dir,
            self.config.model_dir,
            self.config.thumb_dir,
            self.config.archive_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        self.db.initialize()
        self._reconcile_stale_jobs()

        # 延迟导入：这几个模块拉起 numpy / onnxruntime，
        # 写在文件顶部会让每次 import synorive.runtime 都慢几百毫秒
        from .doctor.service import Doctor
        from .ingest.pipeline import IngestPipeline
        from .search.engine import SearchEngine
        from .store.repository import Repository

        self.repo = Repository(self.db)
        # 引擎关着的这段时间里可能已经有回收站条目过期了，起来先清一次，
        # 不用等到下一次 deferred_jobs_loop 的 6 小时节流窗口
        try:
            n = self.repo.purge_expired_trash()
            if n:
                log.info("回收站清掉了 %d 条过期记录", n)
        except Exception as e:  # noqa: BLE001
            log.debug("启动时清理回收站失败（不影响引擎启动）：%s", e)
        self.doctor = Doctor(
            self.config.model_dir,
            on_status=lambda ev: self.events.publish("dependency.status", ev),
        )
        self.pipeline = IngestPipeline(
            self.repo,
            self.config.model_dir,
            concurrency=self.config.concurrency,
            on_progress=lambda p: self.events.publish("ingest.job", p),
            sensitive_guard_enabled=self.config.sensitive_guard_enabled,
        )
        from .ingest.watcher import FolderWatcher

        self.watcher = FolderWatcher(
            on_changed=self._on_watch_changed, on_removed=self._on_watch_removed
        )
        self.search = SearchEngine(
            self.db, self.repo, self._get_query_embedder(), self._get_reranker()
        )

        # 联网层：只建对象不发请求，所以放在 initialize 里不影响冷启动（A1）。
        # 真正出网要等用户在界面上主动搜 —— 断网可用（A18）这条不受影响
        if getattr(self.config, "allow_network", True):
            from .websearch import MetaSearch

            self.web = MetaSearch(
                enabled=getattr(self.config, "web_engines", None),
                keys=getattr(self.config, "web_keys", None),
                renderer=self.render_broker,
                # S1：引擎健康状态落盘，重启后接着用。不落盘的话每次重启
                # 都要重新学一遍，而学习期正好是用户最需要它靠谱的时候
                state_path=self.config.data_dir / "websearch-health.json",
                lineup_size=getattr(self.config, "web_lineup_size", 0),
            )

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
        # 🔴 `prefer_gpu` 必须传。以前这里没传，于是「启用核显加速」开关
        # 对查询路径完全无效 —— 开了也还是 CPU，而且没有任何迹象
        self._embedder = TextEmbedder(d, prefer_gpu=self.config.prefer_gpu)  # threads 默认取物理核数
        return self._embedder

    def _get_reranker(self) -> Any:
        """
        D7 精排器。和向量化器一样**只构造不加载** —— 它是可选依赖，
        模型多半没装；真装了也要等第一次带 rerank 的查询才值得花那 300ms。
        Reranker.load() 自己是幂等的、失败只返回 False 不抛。
        """
        if self._reranker is not None:
            return self._reranker
        from .analyze.reranker import Reranker

        self._reranker = Reranker(self.config.model_dir / "bge-reranker-base")
        return self._reranker

    # ── E15 模型热插拔 ──────────────────────────────────────

    def model_status(self) -> dict[str, Any]:
        """
        当前各模型的真实状态：装没装、加载没加载、跑在哪个执行器上。

        🔴 **`loaded` 和 `installed` 是两回事，必须分开报。**
        模型是懒加载的（构造时不 load），所以"装了但还没加载"是**正常状态**，
        不是故障。混成一个字段的话，用户会在装完之后看到"未加载"然后
        以为装失败了，跑去重装一遍。
        """
        model_dir = self.config.model_dir
        emb = self._embedder
        rr = self._reranker
        return {
            "textEmbedder": {
                "id": "bge-small-zh-v1.5",
                "installed": (model_dir / "bge-small-zh-v1.5" / "model.onnx").exists(),
                "loaded": bool(emb is not None and getattr(emb, "ready", False)),
                "provider": getattr(emb, "provider", None) if emb is not None else None,
                "dim": getattr(emb, "dim", None) if emb is not None else None,
                # 🔴 这一条是整个功能最要紧的信息，见 reload_models 的注释
                "hotSwappable": False,
                "why": "索引里的向量是这个模型算出来的。换成别的模型，"
                "**旧向量和新查询不在同一个空间里** —— 搜索不会报错，只会开始返回不相干的结果。"
                "要换必须整库重新索引",
            },
            "reranker": {
                "id": "bge-reranker-base",
                "installed": (model_dir / "bge-reranker-base" / "model.onnx").exists(),
                "loaded": bool(rr is not None and getattr(rr, "ready", False)),
                "hotSwappable": True,
                "why": "精排只是把已经搜到的几条重新排序，不写索引，随时可换",
            },
            "preferGpu": bool(self.config.prefer_gpu),
            "note": "「已安装但未加载」是正常的 —— 模型是用到才加载的，不是坏了",
        }

    def reload_models(self, *, prefer_gpu: bool | None = None) -> dict[str, Any]:
        """
        E15 —— **不重启引擎**换执行器 / 重载模型。

        🔴 **能热换的只有「同一个模型换执行器（CPU ↔ 核显）」和「精排模型」。**
        文本向量模型**不能**在线换成另一个模型：库里几十万条向量都是旧模型算的，
        换了之后新查询的向量和旧向量根本不在同一个空间里 ——
        **搜索不会报错**，只会开始返回一堆不相干的东西。
        这正是那种"运行正常、功能无效"的故障，而且用户几乎不可能自己诊断出来。
        所以这个方法**只重建会话，不换模型身份**。

        换执行器是安全的：同一份权重、同一个输出空间，只是算的地方从 CPU
        挪到核显。`ann_index` 的 `model_tag` 不变，索引照用。
        """
        changed: list[str] = []
        if prefer_gpu is not None and self.config.prefer_gpu != prefer_gpu:
            self.config.prefer_gpu = prefer_gpu
            changed.append(f"执行器偏好 → {'核显' if prefer_gpu else 'CPU'}")

        # 丢掉旧实例。ONNX 会话的释放靠 GC，这里只需要断引用；
        # 下一次用到时 `_get_*` 会重新构造并懒加载
        had_emb = self._embedder is not None
        had_rr = self._reranker is not None
        self._embedder = None
        self._reranker = None

        new_emb = self._get_query_embedder()
        new_rr = self._get_reranker()
        if self.search is not None:
            # 🔴 **必须把新实例塞回检索器。** 只置空 `self._embedder` 的话，
            # `self.search` 手里还攥着旧的那个 —— 表现是"重载成功了但什么都没变"，
            # 而且日志上一切正常。装完模型那条路径当年就是这么修的
            self.search.embedder = new_emb
            if hasattr(self.search, "reranker"):
                self.search.reranker = new_rr
        if had_emb:
            changed.append("文本向量会话已重建")
        if had_rr:
            changed.append("精排会话已重建")

        return {
            "ok": True,
            "changed": changed or ["没有需要重建的（模型都还没加载过）"],
            "status": self.model_status(),
            "note": "换的是**执行器**不是模型本身 —— 同一份权重、同一个向量空间，索引不用重建。"
            "新会话是懒加载的：下一次搜索时才真正建起来，那一次会慢 300ms 左右",
        }

    def image_vector_for(self, item_id: str | None, path: str | None) -> Any:
        """
        拿一张图的 CLIP 向量。优先用库里已经算好的，没有再现算。

        用库里的：向量已经在 vec_items 里，直接读比重算快得多，
        而且保证和索引时用的是同一个模型版本。
        """
        import numpy as np

        if item_id:
            conn = self.db.connect()
            row = conn.execute(
                "SELECT v.embedding FROM vec_items v "
                "JOIN items i ON i.rowid = v.item_rowid WHERE i.id = ?",
                (item_id,),
            ).fetchone()
            if row is not None and row["embedding"] is not None:
                return np.frombuffer(row["embedding"], dtype=np.float32)
            # 库里没有向量，退回按路径现算
            it = self.repo.get_item(item_id)
            if it is not None:
                path = str(it["locator"])

        if not path:
            return None

        from pathlib import Path as _P

        from .analyze.image import ImageEmbedder, open_image

        p = _P(path)
        if not p.exists():
            return None
        emb = ImageEmbedder(self.config.model_dir / "clip-vit-b32")
        if not emb.available():
            return None
        try:
            return emb.encode_one(open_image(p))
        except Exception as e:  # noqa: BLE001
            log.warning("现算图片向量失败：%s", e)
            return None

    def warmup_async(self) -> None:
        """
        后台预热，不阻塞启动。

        🔴 **两条独立的线程，不是一条。** 这里原来是一条线程顺序做三件事：
           ① 探测本机 SearXNG（一次真实 HTTP 请求）
           ② 加载并预热向量模型
           ③ 加载 ANN 索引
        问题在于 ① 排在最前面，而**绝大多数机器上根本没有自建 SearXNG** ——
        那个请求要一直等到连接超时才返回。在它超时之前，②③ 一步都不会开始，
        也就是说：**一个可有可无的网络探测，挡住了语义检索这个核心本地能力。**

        症状极难往这个方向想：用户断网或没装 SearXNG 时，启动后头几秒
        搜索"只出关键词结果，语义的过一会儿才来"，看起来像模型加载慢，
        实际慢的是一个跟模型毫无关系的网络探测。**而且断网时更严重**——
        断网正是「本地优先」最该发挥价值的时候。

        拆成两条之后，模型预热的耗时只取决于模型本身。网络探测该多久多久，
        它慢不影响任何本地功能。
        """
        import threading

        def warm_models() -> None:
            """核心路径：向量模型 + ANN 索引。**不碰网络。**"""
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
                return
            self._load_ann_index(emb.dim, emb.model_id)

        def probe_network() -> None:
            """
            S2：顺手探一下本机有没有自建的 SearXNG，有就自动启用。

            单独一条线程，**慢或超时都不影响任何本地功能**。
            联网总闸关着时直接跳过——关了网还去发探测请求，
            无论探测多"无害"，都是违背用户明确设置的行为。
            """
            if not getattr(self.config, "allow_network", True):
                return
            self._autodetect_websearch()

        threading.Thread(target=warm_models, daemon=True, name="warmup-models").start()
        threading.Thread(target=probe_network, daemon=True, name="warmup-net").start()

    def _autodetect_websearch(self) -> None:
        """
        探测并自动启用本机自建的 SearXNG。

        用一次性的事件循环跑，而不是复用 FastAPI 那个 —— 这里是普通
        后台线程，没有运行中的事件循环可用；而为了一次探测去抢主循环
        的调度，是拿冷启动的确定性换一件可有可无的事。
        """
        web = getattr(self, "web", None)
        if web is None:
            return
        try:
            got = asyncio.run(web.autodetect_local())
        except Exception as e:  # noqa: BLE001 — 探测失败绝不能影响引擎可用
            log.debug("SearXNG 自动探测出错（忽略）：%s", e)
            return
        if got.get("enabled"):
            self.events.publish("websearch.autodetect", got)
        else:
            log.debug("没有可用的本机 SearXNG：%s", got.get("reason"))

    def _load_ann_index(self, dim: int, model_id: str) -> None:
        """
        A17：从磁盘加载持久化的 ANN 索引，放在后台预热线程里而不是
        `initialize()` 同步做——大索引（百万级向量）光是读盘反序列化
        就可能要几秒，同步做会把 A1「冷启动 ≤2s」直接顶穿。

        这里补的是"引擎重启、库本来就很大"这条路径：只靠摄取流水线里
        `_setup_ann_index` 那份（见 `pipeline.py`）只会在**下一次真的
        写入新内容**时才触发，一个已经攒了几十万块、纯粹重启引擎的用户
        永远等不到那一刻——语义检索会一直停留在暴力扫描，直到他下次投喂新内容。
        """
        if self.repo.ann_index is not None:
            return  # 已经被摄取流水线设置过了
        try:
            from .search.ann_index import AnnIndex
        except ImportError:
            return

        index_path = self.db.path.parent / "ann_index.usearch"
        model_tag = f"{model_id}:{dim}"
        ann = AnnIndex(dim=dim, model_tag=model_tag, index_path=index_path)
        try:
            loaded = ann.load()
        except Exception as e:  # noqa: BLE001
            log.warning("ANN 索引加载失败：%s", e)
            return
        self.repo.ann_index = ann
        if loaded:
            log.info("ANN 索引已从磁盘加载，%d 个向量，%s",
                      ann.size, "已接管语义检索" if ann.active else "库还不够大，暂不接管")

            # 🔴 增量维护（`write_chunks` 里那份）不是每写一条就存盘一次——
            # 那样每次摄取都要付一次索引落盘的 I/O 代价，划不来。
            # 意味着"最近一次存盘"和"数据库里实际有多少向量"之间可能存在落差：
            # 引擎正常运行时不断在内存里 add()，只有下面这次 will_quit
            # 干净关闭时才会真正存盘（见 main.py）。如果上次是异常退出
            # （断电、强杀），磁盘上的索引就停留在上次存盘那一刻，
            # 比数据库实际内容少了一截——不检查的话，这部分内容会在
            # 语义检索里"消失"且没有任何报错或提示。
            # 用同样的思路验过 A14 崩溃恢复：**发现不一致就重建，不检查就是没做完**
            try:
                real_count = self.db.connect().execute(
                    "SELECT COUNT(*) AS n FROM vec_chunks"
                ).fetchone()["n"]
            except Exception:  # noqa: BLE001
                real_count = ann.size
            if real_count > ann.size:
                log.warning(
                    "ANN 索引里 %d 个向量，但库里实际有 %d 个（多半是上次没正常关闭）"
                    "，后台重建补上差的 %d 个",
                    ann.size, real_count, real_count - ann.size,
                )
                self.rebuild_ann_async()
            return

        # 没有磁盘文件（第一次用这个功能，或者索引文件丢了）。
        # 如果库已经大到该用 ANN 了，自动在后台建一次——不用用户知道
        # "还要手动点一下重建"这回事，这正是"自动配置需要的工具与内容"那条要求。
        # 建的过程中查询照样走暴力扫描，只是慢一点，不会因为在重建就搜不出结果。
        from .search.ann_index import ANN_THRESHOLD

        try:
            count = self.db.connect().execute(
                "SELECT COUNT(*) AS n FROM vec_chunks"
            ).fetchone()["n"]
        except Exception:  # noqa: BLE001
            count = 0
        if count >= ANN_THRESHOLD:
            log.info("库里已有 %d 个向量，超过 ANN 阈值，后台自动建一次索引", count)
            self.rebuild_ann_async()

    def rebuild_ann_async(self) -> None:
        """后台全量重建 ANN 索引。规模大时要跑几分钟，不能挡着请求线程。"""
        import threading

        ann = self.repo.ann_index
        if ann is None:
            return

        def run() -> None:
            t0 = time.time()
            try:
                n = ann.rebuild_from_db(self.db.connect())
                log.info("ANN 重建完成：%d 个向量，耗时 %.1fs", n, time.time() - t0)
            except Exception as e:  # noqa: BLE001
                log.warning("ANN 重建失败：%s", e)

        threading.Thread(target=run, daemon=True, name="ann-rebuild").start()

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
                # 任务落盘跟订阅者数量无关——没人盯着界面时也不能让进度
                # 只活在内存里，这条不能放进下面那个"没人订阅就跳过"里
                self._flush_active_jobs()
                if self.events.subscriber_count == 0:
                    continue
                self.events.publish("engine.status", self.status_snapshot())
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                log.debug("状态推送出错：%s", e)

    async def deferred_jobs_loop(self) -> None:
        """
        后台补跑队列：OCR、语音转写、图片描述（C4）、人脸聚类（C5）。

        🔴 这条循环补的是一个真实存在、这次之前没人发现的缺口——
        `IngestPipeline.run_deferred_ocr()` / `run_deferred_transcribe()`
        这两个方法**写好了，但从来没有任何代码调用过它们**。全仓搜索
        只有函数定义和一处注释提到它们，`ingest_paths()`（真实摄取批处理的
        入口）跑完主流程就直接返回，压根没有触发后台补跑这一步。
        之前"补跑前后对比"的测试是在测试脚本里**手动直接调用**这两个方法
        验证逻辑本身对不对，这证明了"补跑逻辑是对的"，但没有证明
        "真实跑起来的引擎会自动补跑"——而它确实不会，这是两件事。
        图片里的文字、视频里的话，在真实运行的应用里会一直停在 pending，
        除非用户自己写代码调用这两个方法。现在把它们真正接上，
        新加的图片描述、人脸聚类也走同一条队列，不用再犯第二次同样的错。
        """
        from .ingest.pipeline import IngestPipeline

        while True:
            try:
                await asyncio.sleep(self._deferred_interval)
                pipeline = self.pipeline
                if not isinstance(pipeline, IngestPipeline):
                    continue

                did_any = False

                n = await asyncio.to_thread(pipeline.run_deferred_ocr, 20)
                did_any = did_any or n > 0

                n = await asyncio.to_thread(pipeline.run_deferred_transcribe, 2)
                did_any = did_any or n > 0

                if self.config.enable_image_description and self.cloud.vision_configured:
                    n = await asyncio.to_thread(
                        pipeline.run_deferred_description, 10,
                        self.cloud.provider, self.cloud.api_key,
                        self.cloud.base_url, self.cloud.vision_model,
                    )
                    did_any = did_any or n > 0

                if self.config.enable_face_clustering:
                    n = await asyncio.to_thread(pipeline.run_deferred_faces, 20)
                    did_any = did_any or n > 0

                # 回收站过期清理不需要跟着这条循环的 3~15 秒节奏跑——
                # 30 天保留期，6 小时检查一次绰绰有余，没必要每次循环都查一遍表
                now = time.time()
                if now - self._last_trash_purge > 6 * 3600:
                    self._last_trash_purge = now
                    n = await asyncio.to_thread(self.repo.purge_expired_trash)
                    if n:
                        log.info("回收站清掉了 %d 条过期记录", n)

                self._deferred_interval = 3.0 if did_any else 15.0
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                log.warning("后台补跑任务出错：%s", e)
                self._deferred_interval = 15.0

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """记下事件循环，工作线程要靠它把事件安全地送回来。"""
        self._loop = loop

    # ── 端口发现 ────────────────────────────────────────────
    #
    # 端口是每次启动动态挑的，所以 MCP 服务器和 CLI 没法写死地址。
    # 引擎启动时把端口写进 data 目录下的 engine.json，
    # 它们读这个文件就能连上**桌面端已经拉起来的那个引擎**，
    # 不用各自再起一个（各起一个的话会有多个进程抢同一个库文件）。

    @property
    def endpoint_file(self) -> Path:
        return self.config.data_dir / "engine.json"

    def write_endpoint(self) -> None:
        import json
        import os

        try:
            self.endpoint_file.write_text(
                json.dumps(
                    {
                        "port": self.config.port,
                        "host": self.config.host,
                        "pid": os.getpid(),
                        "dataDir": str(self.config.data_dir),
                        "startedAt": time.time(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("写端口文件失败，MCP 和 CLI 将无法自动发现引擎：%s", e)

    def clear_endpoint(self) -> None:
        try:
            self.endpoint_file.unlink(missing_ok=True)
        except OSError:
            pass

    # ── 摄取任务：状态持久化 ──────────────────────────────────
    #
    # 🔴 之前任务状态只在 `self._jobs` 这个内存字典里，引擎一重启就没了——
    # `job_detail()`/`control_job()` 只能回一句「没有这个任务（可能引擎
    # 重启过）」。`jobs` 表在 schema.sql 里早就定义好了，但从没有代码真的
    # 往里写过东西，是张没人用的死表。这里把它接上。
    #
    # 不是每次进度变化都写库：`note_item()` 是单文件级别的回调，一次十万
    # 文件的摄取就是十万次调用，逐条 UPDATE 正是 A1 那类「写锁竞争」的
    # 来源。真正落盘的时机只有三个：①任务创建 ②终态（done/failed/
    # cancelled，一次性事件）③`status_loop()` 里每 2 秒一次的节流 flush。
    # 摊下来一次摄取无论多少文件，写库次数只跟"运行了多少秒"成正比，
    # 跟"有多少文件"无关。

    def _persist_job(self, job_id: str, job: dict[str, Any]) -> None:
        conn = self.db.connect()
        detail = {
            "current": job.get("current"),
            "error": job.get("error"),
            "items": list(job.get("items", []))[:500],
            "itemsTruncated": bool(job.get("itemsTruncated", False)),
        }
        started_iso = datetime.fromtimestamp(job.get("startedAt", time.time()), UTC).isoformat()
        now_iso = datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO jobs (id, created_at, updated_at, status, source, total_items,
                               done_items, failed_items, skipped_items, targets_json,
                               allow_cloud, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at = excluded.updated_at,
                status = excluded.status,
                total_items = excluded.total_items,
                done_items = excluded.done_items,
                failed_items = excluded.failed_items,
                skipped_items = excluded.skipped_items,
                detail_json = excluded.detail_json
            """,
            (
                job_id,
                started_iso,
                now_iso,
                job.get("status", "running"),
                job.get("source", "file"),
                int(job.get("total", 0)),
                int(job.get("done", 0)),
                int(job.get("failed", 0)),
                int(job.get("skipped", 0)),
                job.get("targetsJson", "[]"),
                int(self.config.allow_cloud),
                json.dumps(detail, ensure_ascii=False),
            ),
        )

    def _flush_active_jobs(self) -> None:
        """`status_loop()` 每 2 秒调一次——只落盘还在跑/暂停的任务。"""
        for job_id, job in list(self._jobs.items()):
            if job.get("status") not in ("running", "paused"):
                continue
            try:
                self._persist_job(job_id, job)
            except Exception as e:  # noqa: BLE001
                log.debug("任务状态落盘失败（不影响任务本身继续跑）：%s", e)

    def _load_persisted_job(self, job_id: str) -> dict[str, Any] | None:
        """`self._jobs` 里没有时的兜底——查 `jobs` 表，覆盖"引擎重启过"这个场景。"""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT status, total_items, done_items, failed_items, skipped_items, "
            "created_at, detail_json FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        detail = json.loads(row["detail_json"] or "{}")
        try:
            started = datetime.fromisoformat(row["created_at"]).timestamp()
        except ValueError:
            started = 0.0
        return {
            "jobId": job_id,
            "status": row["status"],
            "total": row["total_items"],
            "done": row["done_items"],
            "failed": row["failed_items"],
            "skipped": row["skipped_items"],
            "current": detail.get("current"),
            "startedAt": started,
            "paused": row["status"] == "paused",
            "items": detail.get("items", []),
            "itemsTruncated": bool(detail.get("itemsTruncated", False)),
            "error": detail.get("error"),
        }

    def _reconcile_stale_jobs(self) -> None:
        """
        引擎启动时跑一次：上次运行时还标着 running/paused 的任务，
        只可能是没正常关闭（强杀/崩溃/断电）——线程和 `JobControl` 早就
        没了，没法「继续」，只能如实标成失败，而不是让它永远停在
        「运行中」骗界面显示一个再也不会前进的进度条。
        """
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT id, total_items, done_items, detail_json FROM jobs "
            "WHERE status IN ('queued', 'running', 'paused')"
        ).fetchall()
        if not rows:
            return
        now_iso = datetime.now(UTC).isoformat()
        for row in rows:
            detail = json.loads(row["detail_json"] or "{}")
            detail["error"] = f"引擎重启，任务被中断（当时进度 {row['done_items']}/{row['total_items']}）"
            conn.execute(
                "UPDATE jobs SET status = 'failed', updated_at = ?, detail_json = ? WHERE id = ?",
                (now_iso, json.dumps(detail, ensure_ascii=False), row["id"]),
            )
        log.warning("发现 %d 个任务在上次引擎运行时被中断，已标记为失败", len(rows))

    # ── 目录监控 ────────────────────────────────────────────

    def set_watch_folders(self, folders: list[str]) -> None:
        """桌面端每次"监听的目录"列表变化都会调这个（引擎就绪时也会调一次全量同步）。"""
        if self.watcher is not None:
            self.watcher.set_folders(folders)

    def _on_watch_changed(self, paths: list[Path]) -> None:
        """
        监控到的变化去抖之后批量走这里——复用 `start_ingest`，跟手动投喂
        是同一条路径：F2 驾驶舱能看到进度，敏感文件闸照常生效，
        已经索引过的内容（fingerprint 没变）照常被 `ingest_file` 跳过。
        """
        if not paths or self.pipeline is None:
            return
        log.info("目录监控：%d 个文件变化，开始投喂", len(paths))
        self.start_ingest(paths, recursive=False, source="file")

    def _on_watch_removed(self, paths: list[Path]) -> None:
        """文件被删了——找到对应的库记录，走回收站（跟手动删除同一条路径）。"""
        if self.repo is None:
            return
        removed = 0
        for p in paths:
            row = self.repo.find_by_locator(str(p))
            if row is not None:
                self.repo.soft_delete_item(str(row["id"]))
                removed += 1
        if removed:
            log.info("目录监控：%d 个文件被删除，已从库里移除（进回收站）", removed)
            self.events.publish(
                "toast",
                {"level": "info", "message": f"监控到 {removed} 个文件被删除，已从库里移除（回收站里能找回）"},
            )

    # ── 摄取任务 ────────────────────────────────────────────

    def start_ingest(
        self,
        paths: list[Path | str],
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

        from .ingest.pipeline import JobControl

        job_id = uuid.uuid4().hex[:16]
        control = JobControl()
        # F2 驾驶舱要的三样：还要多久 / 现在卡在哪个 / 哪些失败了。
        # `items` 只留**失败和跳过**的明细 —— 成功的那几万条留着毫无用处，
        # 却能把一个后台任务的内存吃到几十兆
        self._jobs[job_id] = {
            "status": "running",
            "total": 0,
            "done": 0,
            "failed": 0,
            "skipped": 0,
            "control": control,
            "items": [],
            "startedAt": time.time(),
            "source": source,
            "targetsJson": json.dumps([str(p) for p in paths], ensure_ascii=False),
        }
        try:
            self._persist_job(job_id, self._jobs[job_id])
        except Exception as e:  # noqa: BLE001
            log.debug("任务创建时落盘失败（不影响任务本身跑）：%s", e)

        # 🔴 `note_item` 会被线程池里的多个 worker 同时调用。
        # `job["done"] += 1` 是「读-改-写」三步，两个线程撞上就会丢计数 ——
        # 表现是进度条永远差那么几个数，而且**不报错**
        job_lock = threading.Lock()

        def note_total(total: int) -> None:
            """
            🔴 **总数必须在展开目录之后立刻报一次。**
            以前只在 `ingest_paths` 返回后才写 `total`，而那是整个任务**结束**的时候 ——
            中间几分钟里驾驶舱拿到的是 `0 / 0`，进度条一直贴在 0%，
            然后突然跳到 100%。「还要多久」是 F2 存在的头号理由，而它一直是坏的。
            """
            job = self._jobs.get(job_id)
            if job is not None:
                job["total"] = total

        def note_item(path: str, status: str, detail: str) -> None:
            job = self._jobs.get(job_id)
            if job is None:
                return
            with job_lock:
                job["current"] = path
                # 🔴 计数也要在这里累，不能等任务结束再从 stats 抄一遍 ——
                # 同一个原因：中途查到的 done 永远是 0
                if status == "done":
                    job["done"] = int(job.get("done", 0)) + 1
                elif status == "skipped":
                    job["skipped"] = int(job.get("skipped", 0)) + 1
                else:
                    job["failed"] = int(job.get("failed", 0)) + 1
                if status in ("failed", "skipped"):
                    items = job["items"]
                    # 上限 500 条：再多用户也不会一条条看，而无上限的列表
                    # 在一次十万文件的投喂里能自己吃掉几百兆
                    if len(items) < 500:
                        items.append({"path": path, "status": status, "error": detail})
                    else:
                        job["itemsTruncated"] = True

        def run() -> None:
            try:
                stats = self.pipeline.ingest_paths(
                    paths,
                    recursive=recursive,
                    source=source,
                    tags=tags,
                    control=control,
                    on_item=note_item,
                    on_total=note_total,
                )
                job = self._jobs.get(job_id, {})
                self._jobs[job_id] = {
                    **job,
                    "status": "cancelled" if stats.cancelled else "done",
                    "total": stats.total,
                    "done": stats.done,
                    "failed": stats.failed,
                    "skipped": stats.skipped,
                    "current": None,
                }
                self.events.publish(
                    "ingest.job",
                    {
                        "jobId": job_id,
                        "status": "cancelled" if stats.cancelled else "done",
                        "totalItems": stats.total,
                        "doneItems": stats.done,
                        "failedItems": stats.failed,
                        "skippedItems": stats.skipped,
                        "elapsedSec": round(stats.elapsed, 1),
                    },
                )
                try:
                    self._persist_job(job_id, self._jobs[job_id])
                except Exception as e:  # noqa: BLE001
                    log.debug("任务终态落盘失败：%s", e)
            except Exception as e:  # noqa: BLE001
                # 🔴 **这里以前整个字典替换成 `{"status": "failed", "error": ...}`** ——
                # 把 `items`（失败清单）、`total/done`、`startedAt`、`control` 全丢了。
                # 后果是任务崩掉时驾驶舱显示「失败，0 条问题」，而真相是
                # 前面可能已经有几十条失败明细，全被这一行擦掉了。
                # **出错的时候恰恰是最需要那份明细的时候。**
                job = self._jobs.get(job_id, {})
                self._jobs[job_id] = {**job, "status": "failed", "error": str(e), "current": None}
                self.events.publish("toast", {"level": "error", "message": f"摄取失败：{e}"})
                try:
                    self._persist_job(job_id, self._jobs[job_id])
                except Exception as persist_err:  # noqa: BLE001
                    log.debug("任务失败态落盘失败：%s", persist_err)

        threading.Thread(target=run, daemon=True, name=f"ingest-{job_id}").start()
        return job_id

    # ── F2 批量驾驶舱：查一个任务 / 暂停 / 继续 / 取消 ────────

    def job_detail(self, job_id: str) -> dict[str, Any] | None:
        """
        查一个摄取任务的实时状态。

        🔴 返回的字典里**不能带 `control` 对象** —— 它要被 JSON 序列化发给界面，
        带上去会直接抛 `TypeError: Object of type JobControl is not JSON serializable`。
        这类错误发生在响应序列化阶段，FastAPI 会回 500 而不是给出有用信息。
        """
        job = self._jobs.get(job_id)
        if job is None:
            # 内存里没有——不代表任务不存在，可能是引擎重启过、
            # 或者这本来就是上一次运行时跑完的任务。查持久化的 jobs 表。
            return self._load_persisted_job(job_id)
        control = job.get("control")
        return {
            "jobId": job_id,
            "status": job.get("status", "running"),
            "total": job.get("total", 0),
            "done": job.get("done", 0),
            "failed": job.get("failed", 0),
            "skipped": job.get("skipped", 0),
            "current": job.get("current"),
            "startedAt": job.get("startedAt", 0.0),
            "paused": bool(control.paused) if control is not None else False,
            "items": list(job.get("items", [])),
            "itemsTruncated": bool(job.get("itemsTruncated", False)),
            "error": job.get("error"),
        }

    def control_job(self, job_id: str, action: str) -> dict[str, Any]:
        """
        pause / resume / cancel。

        🔴 **不做乐观更新**：界面上的按钮状态严格跟着这里返回的真实值走。
        任务已经跑完了还回一个"已暂停"，用户会盯着一个永远不动的进度条。
        """
        job = self._jobs.get(job_id)
        if job is None:
            persisted = self._load_persisted_job(job_id)
            if persisted is None:
                return {"ok": False, "note": "没有这个任务"}
            return {
                "ok": False,
                "note": f"这个任务不在运行中了（状态：{persisted['status']}），控制不了",
                "status": persisted["status"],
            }
        control = job.get("control")
        if control is None:
            return {"ok": False, "note": "这个任务不支持暂停（它是旧版本起的）"}
        if job.get("status") != "running":
            return {"ok": False, "note": f"任务已经{job.get('status')}了，控制不了", "status": job.get("status")}
        if action == "pause":
            control.pause()
        elif action == "resume":
            control.resume()
        elif action == "cancel":
            control.cancel()
        else:
            return {"ok": False, "note": f"不认识的动作：{action}"}
        return {
            "ok": True,
            "paused": control.paused,
            "cancelled": control.cancelled,
            # 取消是「当前这批文件做完就停」，不是立刻断在半路 ——
            # 半路断会留下写了一半的索引记录。这句必须让用户看见，
            # 否则点了取消进度还在动，会以为没生效
            "note": "已暂停（正在处理的那个文件会做完）"
            if action == "pause"
            else "已继续"
            if action == "resume"
            else "已取消（正在处理的那批文件会做完再停）",
        }

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
