"""
G 组可量化技术指标 —— 目标值的唯一真相源
====================================================================
把 G1~G9 九条指标写成**代码里的常量**，而不是只写在 `task-progress.md` 里。

**为什么值得单独建一个文件**：写在文档里的目标值，改代码的时候没人会去
对照；写成常量以后，它可以被接口读、被基准脚本读、被界面显示 ——
三处看到的是同一个数，不会出现"文档说 3.0s、代码里超时设成 5s"这种
自己跟自己打架的情况。

🔴 **这里只定义目标，不做判定**。`observe()` 返回的是运行期自然积累的
采样，采样量小的时候抖动极大（跑两次搜索就说"P95 达标"是自欺欺人）。
真正的达标结论来自 `engine/tests/bench_*.py`，那是专门的基准测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Budget:
    """一条指标。`how` 写清**怎么才算测过了** —— 没有这条，指标就是口号。"""

    id: str
    label: str
    target: str
    how: str
    #: 这条指标目前有没有对应的基准脚本。没有的**如实标 False**，
    #: 不写的话下次看到这张表会以为九条都测过了
    has_bench: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "target": self.target,
            "how": self.how, "hasBench": self.has_bench,
        }


BUDGETS: tuple[Budget, ...] = (
    Budget(
        "G1", "投喂到可搜", "≤3s",
        "拖一个文件进来到它能被搜到为止（**首字节式**：不等 OCR/转写跑完，"
        "只要基本信息已入库、关键词能命中就算）",
    ),
    Budget(
        "G2", "联网快搜 P95", "≤3.0s",
        "`/web/search` 端到端 P95，20 次不同查询取样。"
        "当前记录 3.2s —— B1 首字节竞速改的就是这条的**体感**，"
        "但 P95 本身要靠 B3 熔断和 B4 缓存降下来",
    ),
    Budget(
        "G3", "深挖出简报 P95", "≤8.0s",
        "`/web/research` rounds=2 端到端 P95。当前记录 8.4s",
    ),
    Budget(
        "G4", "UI 永不阻塞", "任何操作 100ms 内有反馈；联网期间 ≥55fps",
        "渲染层埋点：从 click 事件到第一次 DOM 变更 ≤100ms；"
        "联网请求进行中用 `requestAnimationFrame` 采样帧率",
    ),
    Budget(
        "G5", "研究会话内存", "≤400MB",
        "连续深挖 10 轮后引擎进程 RSS。防的是长时间挖掘越用越卡",
    ),
    Budget(
        "G6", "单引擎失败不拖累", "总耗时增量 ≤15%",
        "人为让一家引擎超时，对比总耗时。B3 熔断直接服务这条",
    ),
    Budget(
        "G7", "缓存命中", "10 分钟内二次返回 ≤200ms",
        "同一查询连搜两次，第二次耗时。B4 直接服务这条",
    ),
    Budget(
        "G8", "断网零空屏", "降级到本地库并明确告知",
        "拔网线后搜索：必须出本地结果 + 一行说明，**不能是空白页**",
    ),
    Budget(
        "G9", "批量投喂不掉帧", "1000 文件全程 ≥55fps",
        "拖 1000 个混合类型文件进来，全程采样渲染帧率",
    ),
)


def observe(rt: Any) -> dict[str, Any]:
    """
    运行期采样。**取不到的项一律给 None 并说明原因，不填 0** ——
    填 0 会在界面上显示成"0ms，远优于目标"，那是彻头彻尾的谎报。
    """
    out: dict[str, Any] = {}

    web = getattr(rt, "web", None)
    if web is not None:
        try:
            out["cache"] = web.cache_stats()
        except Exception:       # noqa: BLE001
            out["cache"] = None
        try:
            health = web.engine_health()
            table = health.get("table") or []
            # 字段名是 `avgMs`（`EngineStat.to_dict`），不是 ewmaMs ——
            # 写错的话这里会安静地得到一个空列表，然后报「没有采样」，
            # 而真相是数据一直都在。典型的静默失败第④问
            lat = [
                float(r.get("avgMs"))
                for r in table
                if isinstance(r, dict) and r.get("avgMs") is not None
            ]
            out["engineLatencyMs"] = {
                "count": len(lat),
                "median": round(sorted(lat)[len(lat) // 2], 1) if lat else None,
                "max": round(max(lat), 1) if lat else None,
            }
            out["breaker"] = health.get("breaker")
        except Exception:       # noqa: BLE001
            out["engineLatencyMs"] = None

    try:
        import os

        import psutil       # 可选依赖，没装就报 None 而不是让整个接口 500

        out["rssMb"] = round(psutil.Process(os.getpid()).memory_info().rss / 1048576, 1)
    except Exception:           # noqa: BLE001
        out["rssMb"] = None
        out["rssNote"] = "没装 psutil，读不到内存占用（这不影响任何功能）"

    out["allowNetwork"] = bool(getattr(rt.config, "allow_network", True))
    out["note"] = (
        "这些是运行期自然积累的采样，**不是基准测试结果**。"
        "样本少的时候抖动很大，别拿它当验收证据"
    )
    return out
