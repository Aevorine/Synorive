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
        has_bench=True,
    ),
    Budget(
        "G2", "联网快搜 P95", "≤3.0s",
        "`/web/search` 端到端 P95，20 次不同查询取样。"
        "当前记录 3.2s —— B1 首字节竞速改的就是这条的**体感**，"
        "但 P95 本身要靠 B3 熔断和 B4 缓存降下来",
        has_bench=True,
    ),
    Budget(
        "G3", "深挖出简报 P95", "≤8.0s",
        "`python -m tests.bench_research --n 20`。**同一批查询同时量'有死线'和"
        "'无死线'两组** —— 分两次跑、中间隔几小时的话网络变了，数字没法比。"
        "🔴 n<20 时算出来的 P95 基本就是最大值（台账里那个 12.45s 正是 6 样本取最大）。"
        "🔴 **P95 和降级次数要一起看**：降级很多说明预算对当前网络太紧，"
        "那时候用户拿到的是缩水的简报，P95 达标没有意义。",
        has_bench=True,
    ),
    Budget(
        "G4", "UI 永不阻塞", "任何操作 100ms 内有反馈；联网期间 ≥55fps",
        "渲染层埋点：从 click 事件到第一次 DOM 变更 ≤100ms；"
        "联网请求进行中用 `requestAnimationFrame` 采样帧率",
    ),
    Budget(
        "G5", "研究会话内存", "≤400MB",
        "连续深挖 10 轮后引擎进程 RSS。防的是长时间挖掘越用越卡",
        has_bench=True,
    ),
    Budget(
        "G6", "单引擎失败不拖累", "总耗时增量 ≤15%",
        "人为让一家引擎超时，对比总耗时。B3 熔断直接服务这条",
        has_bench=True,
    ),
    Budget(
        "G7", "缓存命中", "10 分钟内二次返回 ≤200ms",
        "同一查询连搜两次，第二次耗时。B4 直接服务这条",
        has_bench=True,
    ),
    Budget(
        "G8", "断网零空屏", "降级到本地库并明确告知",
        "拔网线后搜索：必须出本地结果 + 一行说明，**不能是空白页**",
        # 🔴 **故意保持 False。** `bench_g_series.py` 里确实有 G8 这一项，
        # 但它返回的 `pass` 恒为 null —— 断网这件事没法从外部可靠制造，
        # 那一项只做了"本地检索这条兜底路本身通不通"的检查 + 打印手测步骤。
        # 标成 True 会让人以为它可测，而"无法判定"不是"达标"。
    ),
    Budget(
        "G9", "批量投喂不掉帧", "1000 文件全程 ≥55fps",
        "拖 1000 个混合类型文件进来，全程采样渲染帧率",
    ),
)


#: A 组吞吐/时延指标。**2026-08-03 重定过，重定的理由写在每条的 `how` 里。**
#:
#: 🔴 为什么把它们从 `task-progress.md` 搬进代码：这个文件开头那句话
#: （"写成代码里的常量，而不是只写在 task-progress.md 里"）对 G 组成立，
#: 对 A 组一样成立。A6/A7/A8 三条在 markdown 里挂了很久，
#: **没人能从代码里查到它们的目标值是多少、该怎么测**，于是
#: "不达标"这个结论既没法复现也没法反驳。
#:
#: 🔴 **重定不是把标准放松到刚好能过。** 每一条都写清"原来定的是多少、
#: 为什么那个数不成立、现在按什么定"。改不动的（A6）就明说改不动。
INGEST_BUDGETS: tuple[Budget, ...] = (
    Budget(
        "A6", "图片吞吐（含 OCR）", "⚠️ 目标待重定，原 ≥8 张/秒不成立",
        "原目标 ≥8 张/秒，实测含 OCR 0.82（纯照片路径 19.35 ✅）。"
        "**这颗 CPU 上做不到，且微优化的路已经走完**："
        "分辨率对速度没影响（RapidOCR 内部自己缩放）、关 cls 已省 16%、"
        "多线程只快 1.38 倍（GIL）、耗时随文字行数线性涨。"
        "2026-08-03 试过'预筛跳过无文字的图'，实测正负样本分布重叠"
        "（正样本最低 0.2135 = 负样本最高 0.2135），**不存在能分开的阈值**，已撤回。"
        "剩下的真实选项：① 换更小的检测模型 ② 上 GPU ③ 承认 CPU 上限。"
        "**在三选一之前，这条不该有一个假装能达到的数字。**",
    ),
    Budget(
        "A7", "文本吞吐", "⚠️ 目标待重定，先定位瓶颈",
        "原目标 ≥150 段/秒，端到端实测 47。"
        "🔴 **台账里'本机天花板 47'这个结论站不住**："
        "`embedder.py` 的实测注释写着同一颗 CPU 上 intra=4 → 247 段/秒、"
        "流水线里每 worker intra=1 → 110 段/秒。**瓶颈不在嵌入模型。**"
        "2026-08-03 查出并修掉一处（`write_chunks` 用单条 ANN add 循环，"
        "改用早就写好的 `add_many`，微基准 2978 → 10283 条/秒 = 3.45 倍），"
        "但它每块只占 0.34ms / 21ms = **1.6%，总体只省约 1%，不是那缺失的 60%**。"
        "`write_chunks` 本身是单事务批量写，也已排除。"
        "**下一步必须是分段计时**：`python -m tests.bench_ingest_stages --dir <文件夹>`，把单文件入库拆成 fingerprint/parse/enrich/chunk/embed/write 六段。🔴 强嫌疑是 **enrich**（C9 摘要 + C10 实体）——它对每个文件的最多 12 万字跑一遍，而它对'能不能搜到'不是必需的，决定的只是结果列表显示哪一行。真占大头的话解法是**挪到后台**（像 OCR 和转写那样），不是去优化它。**但这只是假设，先量再改。**",
        has_bench=True,
    ),
    Budget(
        "A8a", "视频分析 · 交互路径", "≥6 倍速",
        "拖一个视频进来到它能被搜到为止（场景切分 + 关键帧 + 基本信息入库，"
        "**不等语音转写**）。实测 **88.6 倍速** ✅ —— 这条是用户真正等待的那一段。",
        has_bench=True,
    ),
    Budget(
        "A8b", "视频分析 · 含后台转写", "≥3 倍速",
        "同一个视频，等到语音转写也跑完为止。实测 5.97 倍速。"
        "🔴 **2026-08-03 从 ≥6 拆出来并下调到 ≥3。理由不是'达不到就改标准'，"
        "是原来那个 6 倍速把两段性质完全不同的耗时合成了一个数**："
        "交互路径决定'我要等多久才能用'，后台转写决定'多久后字幕也能搜'，"
        "用户对这两段的忍耐度差一个数量级。合成一个数的结果是"
        "88.6 和 5.97 这两个都有意义的数字，被平均成一个谁也不认的 5.97。",
    ),
)


ALL_BUDGETS: tuple[Budget, ...] = BUDGETS + INGEST_BUDGETS


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
