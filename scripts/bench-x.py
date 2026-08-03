#!/usr/bin/env python3
"""
X1~X8 性能验收基准 —— 逐条跑标准并取证
====================================================================

## 这个脚本存在的理由

台账里 X1~X8 八条指标，之前**全部标着「没有实测」**，而其中两条
（X2 3.2s vs 目标 3.0s、X3 8.4s vs 目标 8.0s）还带着"当前实测"的数字 ——
那个数字来自哪一次、什么条件下测的，没人说得清。**没有可复现的测法，
指标就只是一句愿望。**

所以这里把八条标准变成八段可以重跑的代码，每条输出：
  实测值 / 目标值 / 过不过 / **原始样本**（不是只给个平均数）

## 🔴 只依赖标准库

不用 requests、不用 pytest、不用 psutil。理由有两个：
  ① 它要能在**随包分发的那份 embeddable 运行时**上直接跑
     （`resources/pyruntime/python.exe scripts/bench-x.py`），
     而那份运行时只装了引擎的核心依赖，没有测试工具链
  ② 基准脚本自己引入依赖，等于把"装依赖的耗时和失败"混进被测对象

## 🔴 报 P95 不报平均

平均数会把"十次里有一次卡了 12 秒"抹平成一个好看的数字，
而用户感受到的恰恰是那一次。X2/X3 的标准写的就是 P95，
样本数不够时（<20）P95 的意义有限，**这一点会在报告里明说，不装作精确**。

## 用法

    # 先把引擎跑起来（另一个终端）：
    engine\\.venv\\Scripts\\python.exe -m synorive.main --port 8731 --data-dir <你的库>

    python scripts/bench-x.py                    # 跑全部（联网项要能出网）
    python scripts/bench-x.py --only X1,X7       # 只跑某几条
    python scripts/bench-x.py --skip-net         # 跳过所有要出网的（X2/X3/X6）
    python scripts/bench-x.py --json out.json    # 另存机器可读的原始样本

## 跑不了的那一条

**X4（UI 永不阻塞 / ≥55fps）不在这里** —— 它测的是渲染进程的帧率和
输入延迟，必须有真实界面在跑。那条走 `node scripts/stress-ui.mjs`，
本脚本会在报告里把它列成「本工具测不了」而不是悄悄跳过。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:8731"

#: 每条指标的目标值。**照抄台账原文，不许在这里"顺手放宽"** ——
#: 改目标是产品决定，不是跑基准的人能顺手做的事
TARGETS = {
    "X1": ("投喂到可搜", 3.0, "s"),
    "X2": ("联网快搜 P95", 3.0, "s"),
    "X3": ("深挖出简报 P95", 8.0, "s"),
    "X4": ("UI 永不阻塞 100ms / ≥55fps", None, ""),
    "X5": ("研究会话内存", 400.0, "MB"),
    "X6": ("单引擎失败耗时增量", 15.0, "%"),
    "X7": ("缓存命中二次返回", 0.2, "s"),
    "X8": ("断网零空屏", None, ""),
}


# ── 基础设施 ────────────────────────────────────────────────


class Bench:
    """
    🔴 **`/health` 在应用根上，其余接口全挂在 `/api` 前缀下**
    （`main.py` 的 `include_router(router, prefix="/api")`）。

    第一版这里把两者混为一谈，结果是：健康检查 200 → 打印"引擎在线" →
    其余七条基准全部 404 → 报告里三条 skip 两条 fail。
    **一个只测了握手的基准，看起来却像在测性能。**
    """

    def __init__(self, base: str, timeout: float = 120.0, prefix: str = "/api") -> None:
        self.base = base.rstrip("/")
        self.prefix = prefix.rstrip("/")
        self.timeout = timeout

    def call(self, path: str, payload: Any = None, method: str | None = None) -> tuple[Any, float]:
        """发一次请求，返回 (解析后的 body, 耗时秒)。

        🔴 **耗时从发请求前量到 body 读完为止**，不是量到响应头。
        只量到头的话，流式接口会得到一个漂亮但毫无意义的数字 ——
        用户等的是内容出来，不是 TCP 握完手。
        """
        url = self.base + (path if path.startswith("/health") else self.prefix + path)
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
            raw = r.read()
        dt = time.perf_counter() - t0
        try:
            return json.loads(raw.decode("utf-8")), dt
        except (ValueError, UnicodeDecodeError):
            return raw, dt

    def alive(self) -> bool:
        try:
            body, _ = self.call("/health")
            return bool(body)
        except (urllib.error.URLError, OSError, TimeoutError):
            return False


def p95(xs: list[float]) -> float:
    """P95。样本少的时候取最大值 —— **宁可偏保守，不许偏乐观**。

    `statistics.quantiles` 在 n<20 时会插值出一个比任何实测值都小的数，
    那等于在样本不足的情况下自己给自己放水。
    """
    if not xs:
        return float("nan")
    if len(xs) < 20:
        return max(xs)
    return statistics.quantiles(xs, n=20)[18]


def rss_mb(pid: int) -> float | None:
    """拿引擎的常驻内存（MB）—— **整棵进程树，不是单个进程**。

    🔴 引擎是 uvicorn，起来之后是**父进程 + 工作进程**两个。
    父进程只做转发，常驻 4MB；真正装着模型和索引的是工作进程，137MB。
    只量父进程的话会得到「4MB，远低于 400MB 目标 ✅」——
    **一个通过了的假指标**，而且数字小得离谱这件事本身不会触发任何告警。
    所以这里把 pid 和它全部后代加起来。

    优先 psutil（引擎自己就依赖它，多半装着）；没有就退回 Windows 的
    `tasklist`（那条路只能量单个进程，会在报告里注明）。两条路都没有
    就返回 None —— **返回 0 是不行的**，0 会被当成"内存占用极小"
    记进报告，而真相是"根本没测到"。
    """
    try:
        import psutil  # type: ignore[import-untyped]

        proc = psutil.Process(pid)
        total = proc.memory_info().rss
        for kid in proc.children(recursive=True):
            try:
                total += kid.memory_info().rss
            except psutil.Error:
                pass  # 刚好退出了，跳过就是
        return total / 1048576.0
    except Exception:  # noqa: BLE001 - 装没装、进程在不在，都走同一条退路
        pass
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(  # noqa: S603
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout
        # "python.exe","1234","Console","1","123,456 K"
        parts = [p.strip('" ') for p in out.strip().split('","')]
        if len(parts) >= 5:
            return float(parts[4].replace(",", "").replace(" K", "").strip()) / 1024.0
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


class Result:
    def __init__(self, key: str) -> None:
        self.key = key
        self.name, self.target, self.unit = TARGETS[key]
        self.value: float | None = None
        self.samples: list[float] = []
        self.note = ""
        self.status = "skip"  # pass / fail / skip / manual

    def judge(self, value: float, *, lower_is_better: bool = True) -> None:
        self.value = value
        if self.target is None:
            self.status = "manual"
            return
        ok = value <= self.target if lower_is_better else value >= self.target
        self.status = "pass" if ok else "fail"

    def line(self) -> str:
        icon = {"pass": "✅", "fail": "❌", "skip": "⏭️", "manual": "🔑"}[self.status]
        if self.value is None:
            got = "—"
        else:
            got = f"{self.value:.3f}{self.unit}" if self.unit == "s" else f"{self.value:.1f}{self.unit}"
        tgt = f"≤{self.target}{self.unit}" if self.target is not None else "人工判定"
        s = f"{icon} {self.key} {self.name}：实测 {got} ｜ 目标 {tgt}"
        if self.samples:
            shown = " ".join(f"{x:.2f}" for x in self.samples[:12])
            s += f"\n      样本({len(self.samples)})：{shown}"
        if self.note:
            s += f"\n      备注：{self.note}"
        return s


# ── 八条基准 ────────────────────────────────────────────────


def bench_x1(b: Bench, r: Result) -> None:
    """X1 投喂到可搜 ≤3s。

    测法：造一个内容唯一的临时文本文件 → POST /ingest → 轮询 /search
    直到那段独有内容能被搜到，量的是**从提交到搜得到**的墙钟时间。

    🔴 不是量 /ingest 的返回耗时。那个接口一提交就返回 jobId，
    量它永远是几十毫秒 —— 一个漂亮且完全无关的数字。
    用户关心的是"我拖进去的东西什么时候能搜到"。
    """
    import tempfile
    import uuid

    token = "synbench" + uuid.uuid4().hex[:12]
    tmp = os.path.join(tempfile.gettempdir(), f"{token}.txt")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"这是一份基准测试用的临时文档。唯一标记：{token}。\n" * 20)

    t0 = time.perf_counter()
    try:
        b.call("/ingest", {"targets": [tmp], "recursive": False})
    except urllib.error.HTTPError as e:
        r.note = f"提交摄取就失败了：HTTP {e.code}"
        return

    found = False
    while time.perf_counter() - t0 < 30.0:
        try:
            body, _ = b.call("/search", {"query": token, "limit": 5})
            # 🔴 检索结果的字段名是 **hits**，不是 results。
            # 第一版这里写的 results 恒为空 → X1 每次都跑满 30 秒超时 →
            # 报成"链路没打通"，而链路其实是好的。
            # **读错字段的基准，测出来的是一个必然失败的常数**，
            # 而它和真失败在报告上长得一模一样（这正是本项目 #2 类 bug：
            # 跨边界字段名对不上，编译器查不到、运行时不报错）
            if body.get("hits"):
                found = True
                break
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.1)
    dt = time.perf_counter() - t0

    try:
        os.remove(tmp)
    except OSError:
        pass

    if not found:
        r.note = "30 秒内都没搜到 —— 不是慢，是这条链路根本没打通（摄取失败或索引没写）"
        r.status = "fail"
        r.value = dt
        return
    r.samples = [dt]
    r.judge(dt)


def bench_x2(b: Bench, r: Result, rounds: int) -> None:
    """X2 联网快搜 P95 ≤3.0s。

    🔴 **每一轮换一个查询词，且 useCache=False。**
    同一个词跑十遍，第二遍开始全是缓存命中 —— 那测的是 X7 不是 X2，
    而且会给出一个好得离谱的假数字。
    """
    queries = [
        "本地优先 检索 架构", "sqlite fts5 中文分词", "onnxruntime 量化 推理",
        "electron 主进程 IPC 安全", "PBKDF2 迭代次数 建议", "向量检索 ANN 召回率",
        "jieba 自定义词典", "FastAPI 后台任务", "Room 数据库 迁移", "AES-GCM nonce 重用",
    ]
    for i in range(rounds):
        q = queries[i % len(queries)]
        try:
            _, dt = b.call("/web/search", {"query": q, "limit": 10, "useCache": False})
            r.samples.append(dt)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            r.note = f"第 {i + 1} 轮请求失败：{e}"
    if not r.samples:
        r.status = "fail"
        r.note = r.note or "一轮都没跑成 —— 检查联网总闸 allow_network"
        return
    if len(r.samples) < 20:
        r.note = (f"样本只有 {len(r.samples)} 个，P95 取的是最大值（偏保守）。"
                  "要严格的 P95 至少跑 20 轮：--rounds 20")
    r.judge(p95(r.samples))


def bench_x3(b: Bench, r: Result, rounds: int) -> None:
    """X3 深挖出简报 P95 ≤8.0s。"""
    queries = [
        "端到端加密 同步 协议设计", "多模态检索 评测基准",
        "Windows 桌面应用 打包 独立运行时", "中文全文检索 分词 权衡",
    ]
    for i in range(rounds):
        q = queries[i % len(queries)]
        try:
            body, dt = b.call("/web/research", {"query": q, "rounds": 1, "limit": 8})
            r.samples.append(dt)
            # 🔴 只看耗时不看内容 = 最典型的静默失败：接口 200、
            # 耗时 0.3 秒、简报是空的，而这会被记成"性能极佳"
            # 🔴 简报的实际字段是 consensus / disputes / timeline / numbers /
            # matrix / openQuestions ——**没有 text / sections / paragraphs**。
            # 我这个基准前一版查的就是后面那三个不存在的键，于是每一轮都被
            # 判成"简报是空的"。**同一个 X1/X8 上栽过的跟头，在这里又栽了一次**：
            # 跨边界读字段全靠猜，猜错既不报错也不为空指针，只是结论恒为假。
            brief = (body or {}).get("briefing") or {}
            filled = [k for k in ("consensus", "disputes", "timeline", "numbers", "openQuestions")
                      if brief.get(k)]
            if not filled:
                r.note = "⚠️ 有一轮返回 200 但简报六个内容字段全空 —— 快是因为没干活，这个耗时不算数"
            elif not brief.get("consensus"):
                r.note = (f"有一轮简报没有共识段（只有 {'/'.join(filled)}）"
                          "—— 不算空，但内容偏薄")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            r.note = f"第 {i + 1} 轮失败：{e}"
    if not r.samples:
        r.status = "fail"
        r.note = r.note or "一轮都没跑成"
        return
    if len(r.samples) < 20:
        r.note = (r.note + " ｜ " if r.note else "") + f"样本 {len(r.samples)} 个，P95 取最大值"
    r.judge(p95(r.samples))


def bench_x5(b: Bench, r: Result, pid: int | None) -> None:
    """X5 研究会话内存 ≤400MB。

    测法：先记一次基线，跑一串检索 + 一次深挖（模拟一场研究会话），
    再记一次峰值。**报的是会话后的占用，不是启动瞬间的占用** ——
    "越用越卡"这件事只有跑起来才看得见。
    """
    if pid is None:
        r.note = "没给引擎进程号（--engine-pid），量不到内存。这不是通过，是没测"
        return
    base = rss_mb(pid)
    if base is None:
        r.note = f"拿不到 PID {pid} 的内存 —— 进程不在？装个 psutil 再试"
        return
    for q in ["检索", "视频 分析", "加密 同步", "打包 独立"]:
        try:
            b.call("/search", {"query": q, "limit": 20})
        except (urllib.error.URLError, OSError):
            pass
    try:
        b.call("/web/research", {"query": "本地检索 架构", "rounds": 1, "limit": 6})
    except (urllib.error.URLError, OSError, TimeoutError):
        pass
    peak = rss_mb(pid) or base
    r.samples = [base, peak]
    r.note = f"会话前 {base:.0f}MB → 会话后 {peak:.0f}MB（父进程 + 全部工作进程之和）"
    r.judge(peak)


def bench_x6(b: Bench, r: Result, pairs: int = 6) -> None:
    """X6 单引擎失败不拖累整体：总耗时增量 ≤15%。

    测法：全阵容跑一遍拿基线，再在阵容里塞一个**不存在的引擎名**跑一遍。
    一个引擎失败时，正确的行为是其余的照常返回、总耗时基本不变；
    错误的行为是所有人一起等它超时。

    🔴 **改成配对测量了，因为上一版测不准。** 上一版的毛病有两个，
    单纯加样本量治不了：

      ① 基线用查询词 `q`、降级组用 `q + " x"` —— **比的是两个不同的查询**，
         在两个不同的时刻、面对不同的网络状况。它们之间的差异远大于
         "一个坏引擎"造成的差异
      ② 只跑 3 对，然后拿"中位数之差"当结论

    实测后果：基线 6 个样本落在 2.73~5.81 之间，**样本自身的波动比
    要测的那个差值还大**，于是同一份代码连着测出 +14.2% / +69.1% / +37.3%
    三个数，全是噪声。「+14.2% 压线通过」那次是运气不是证据。

    现在：**同一个查询词内部比**（配对差分能抵掉"这个词本来就慢"和
    "这一刻网络本来就差"这两个共同因素），**交替先后顺序**（抵掉第二次
    跑总是沾点热的便宜），取每一对的增量比的中位数。
    并且**把离散度一起报出来** —— 波动大到盖过阈值时直接判"测不准"，
    而不是给一个看起来很确定的假数字。
    """
    try:
        engines_body, _ = b.call("/web/engines")
    except (urllib.error.URLError, OSError) as e:
        r.note = f"取不到引擎列表：{e}"
        return
    names = [e.get("id") or e.get("name") for e in (engines_body or {}).get("engines", [])]
    names = [n for n in names if n]
    if len(names) < 2:
        r.note = f"可用引擎只有 {len(names)} 个，测不出'单个失败的影响'"
        return

    # 🔴 塞一个**根本不存在的引擎名**，而不是拔掉一个真引擎。
    # 拔掉真引擎会同时减少工作量，两个变量混在一起，
    # 测出来的"变快了"毫无意义
    bad = [*names, "__synorive_bench_nonexistent__"]

    def once(q: str, lineup: list[str]) -> float | None:
        try:
            _, dt = b.call("/web/search",
                           {"query": q, "limit": 10, "useCache": False, "engines": lineup})
            return dt
        except (urllib.error.URLError, OSError, TimeoutError):
            return None

    deltas: list[float] = []
    base_ts: list[float] = []
    degraded_ts: list[float] = []
    for i in range(max(1, pairs)):
        q = f"基准 单引擎失败 第{i}轮"
        # 交替先后：偶数轮先跑基线，奇数轮先跑降级组。
        # 不交替的话，"后跑的那个总是沾连接池预热的光"会被整个记到
        # 降级组头上，系统性地把增量压低 —— 一个看不见的偏向
        if i % 2 == 0:
            t_base, t_bad = once(q, names), once(q, bad)
        else:
            t_bad, t_base = once(q, bad), once(q, names)
        if t_base is None or t_bad is None or t_base <= 0:
            continue
        base_ts.append(t_base)
        degraded_ts.append(t_bad)
        deltas.append((t_bad - t_base) / t_base * 100.0)

    if not deltas:
        r.note = "没有一对跑成，算不出增量"
        return

    delta = statistics.median(deltas)
    lo, hi = min(deltas), max(deltas)
    r.samples = deltas
    r.unit = "%"
    r.note = (
        f"{len(deltas)} 对配对测量（同一查询词内部比，交替先后）："
        f"基线中位 {statistics.median(base_ts):.2f}s → 含坏引擎 "
        f"{statistics.median(degraded_ts):.2f}s ｜ 每对增量 {lo:+.1f}% ~ {hi:+.1f}%"
    )
    r.judge(max(0.0, delta))

    # 波动大不等于测不准 —— 要看它**是否影响结论**：
    #   最差的一对都达标        → 通过。再怎么抖也翻不了案，离散度无关
    #   中位数就已经超标        → 不达标。同上，结论稳的
    #   中位达标但最差的超标    → **只有这种情况才叫测不准**，如实说
    #
    # 第一版守卫只看跨度、不看结论，把「14 对里最差的一对才 +14.3%、
    # 中位还是负的」也判成测不准 —— 那是把保守做成了另一种不准确
    tgt = float(r.target or 0)
    if len(deltas) >= 3 and hi > tgt >= delta:
        r.status = "manual"
        r.note += (
            f"\n      ⚠️ **测不准，别下结论**：中位 {delta:+.1f}% 达标，"
            f"但最差的一对到了 {hi:+.1f}%，跨过了 {tgt:.0f}% 的阈值。"
            f"加大 --x6-pairs 或换个网络稳定的时段再测"
        )


def bench_x7(b: Bench, r: Result) -> None:
    """X7 相同查询 10 分钟内缓存命中，二次返回 ≤200ms。"""
    q = "缓存命中基准 " + str(int(time.time()))
    try:
        _, cold = b.call("/web/search", {"query": q, "limit": 10, "useCache": True})
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        r.note = f"第一次（冷）就失败了：{e}"
        return
    time.sleep(0.5)
    try:
        body, warm = b.call("/web/search", {"query": q, "limit": 10, "useCache": True})
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        r.note = f"第二次（热）失败：{e}"
        return
    r.samples = [cold, warm]
    # 🔴 快不等于命中。二次请求可能只是因为对方 CDN 热了，
    # 而缓存层压根没工作 —— 那样这条指标是假通过。查一下缓存统计
    hit_note = ""
    try:
        stats, _ = b.call("/web/cache")
        hits = (stats or {}).get("hits")
        if isinstance(hits, int):
            hit_note = f"，缓存累计命中 {hits} 次"
        if hits == 0:
            hit_note = "，⚠️ 缓存命中数仍是 0 —— 快是别的原因，这条不算通过"
    except (urllib.error.URLError, OSError):
        pass
    r.note = f"冷 {cold:.2f}s → 热 {warm:.3f}s{hit_note}"
    r.judge(warm)


def bench_x8(b: Bench, r: Result) -> None:
    """X8 断网/断源零空屏：降级到本地库并明确告知。

    🔴 **这条不真的拔网线。** 拔网会连带影响本机其他一切，
    而且没法在脚本里可靠地还原。改测「引擎在联网被关掉时的行为」——
    也就是 `allow_network=False` 那条路径，它和真断网走的是同一段降级代码。

    判据不是"没报错"，是**三件事同时成立**：
      ① HTTP 不是 5xx（没崩）
      ② 返回里带一句说明为什么没有联网结果（不是空屏）
      ③ 本地检索仍然出结果（真的降级了，不是干脆什么都不做）
    """
    checks: list[str] = []
    try:
        body, _ = b.call("/search", {"query": "检索", "limit": 5})
        if body.get("hits"):  # 同上：字段名是 hits
            checks.append("✓ 本地检索仍有结果")
        else:
            checks.append("✗ 本地检索是空的（库里可能就没东西，换个词或先投喂）")
    except (urllib.error.URLError, OSError) as e:
        checks.append(f"✗ 本地检索报错：{e}")

    try:
        body, _ = b.call("/web/prewarm", {"queries": ["断网测试"], "limit": 3})
        note = (body or {}).get("note") or ""
        if "联网已关闭" in note or "没有预热" in note:
            checks.append("✓ 联网关闭时有明确说明文案，不是静默空结果")
        else:
            checks.append(f"· 联网是开着的，这条要在设置里关掉联网总闸再跑一次（当前 note：{note or '无'}）")
    except urllib.error.HTTPError as e:
        checks.append(f"✗ 返回 HTTP {e.code} —— 断源时应当降级而不是报错")
    except (urllib.error.URLError, OSError) as e:
        checks.append(f"✗ 请求失败：{e}")

    r.status = "manual"
    r.note = " ｜ ".join(checks) + "。完整验证要在界面上关掉联网总闸+重启引擎，看有没有空白页"


# ── 编排 ────────────────────────────────────────────────────


def main() -> int:
    # 🔴 Windows 控制台默认按 GBK 编码 stdout，打 ✅/❌ 直接抛
    # UnicodeEncodeError —— **而且是在跑完全部基准之后、打印报告的那一刻抛**，
    # 于是几分钟的测试结果连同退出码一起丢掉。
    # 这和台账里 pv-hook 那次「按 GBK 解码 UTF-8 导致中文规则全失效」
    # 是同一个根因：**Windows 上任何跨进程的文本边界都要显式定死编码，不能靠默认值。**
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Synorive X1~X8 性能基准")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"引擎地址（默认 {DEFAULT_BASE}）")
    ap.add_argument("--api-prefix", default="/api", help="接口前缀（默认 /api；/health 不受它影响）")
    ap.add_argument("--only", default="", help="只跑这几条，逗号分隔，如 X1,X7")
    ap.add_argument("--skip-net", action="store_true", help="跳过要出网的 X2/X3/X6")
    ap.add_argument("--rounds", type=int, default=6, help="X2/X3 各跑几轮（默认 6，严格 P95 要 ≥20）")
    ap.add_argument("--x6-pairs", type=int, default=6,
                    help="X6 跑几对配对测量（默认 6；波动大时加到 12~20 才判得准）")
    ap.add_argument("--engine-pid", type=int, default=None, help="引擎进程号，X5 量内存要用")
    ap.add_argument("--json", default="", help="把原始样本另存成 JSON")
    a = ap.parse_args()

    want = {s.strip().upper() for s in a.only.split(",") if s.strip()} or set(TARGETS)
    if a.skip_net:
        want -= {"X2", "X3", "X6"}

    b = Bench(a.base, prefix=a.api_prefix)
    print(f"引擎：{a.base}")
    if not b.alive():
        print("❌ 引擎没响应 —— 先把它跑起来：")
        print("   engine\\.venv\\Scripts\\python.exe -m synorive.main --port 8731 --data-dir <库路径>")
        return 2
    print("✅ 引擎在线\n")

    results: list[Result] = []
    runners = {
        "X1": lambda r: bench_x1(b, r),
        "X2": lambda r: bench_x2(b, r, a.rounds),
        "X3": lambda r: bench_x3(b, r, max(2, a.rounds // 2)),
        "X5": lambda r: bench_x5(b, r, a.engine_pid),
        "X6": lambda r: bench_x6(b, r, a.x6_pairs),
        "X7": lambda r: bench_x7(b, r),
        "X8": lambda r: bench_x8(b, r),
    }

    for key in sorted(TARGETS):
        r = Result(key)
        if key not in want:
            r.note = "本次没选它"
            results.append(r)
            continue
        if key == "X4":
            r.status = "manual"
            r.note = "本工具测不了（要真实渲染进程）→ 跑 node scripts/stress-ui.mjs"
            results.append(r)
            continue
        print(f"跑 {key} {r.name} …", flush=True)
        t0 = time.perf_counter()
        try:
            runners[key](r)
        except Exception as e:  # noqa: BLE001 - 一条崩了不能带走其余七条
            r.status = "fail"
            r.note = f"基准本身抛异常：{type(e).__name__}: {e}"
        print(f"   ({time.perf_counter() - t0:.1f}s)")
        results.append(r)

    print("\n" + "=" * 68)
    print("X1~X8 验收结果")
    print("=" * 68)
    for r in results:
        print(r.line())

    ok = sum(1 for r in results if r.status == "pass")
    bad = sum(1 for r in results if r.status == "fail")
    man = sum(1 for r in results if r.status == "manual")
    skipped = sum(1 for r in results if r.status == "skip")
    print("-" * 68)
    print(f"通过 {ok} ｜ 不达标 {bad} ｜ 需人工/另测 {man} ｜ 没跑 {skipped}")
    # 🔴 「需人工」和「通过」分开计数，不合并。合并的那一刻，
    # 报告就从"测出来的"变成"看起来测过了"
    if man or skipped:
        print("⚠️ 需人工/没跑的那几条**不算通过**，别把它们混进达标数里")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(
                [{"key": r.key, "name": r.name, "status": r.status, "value": r.value,
                  "target": r.target, "unit": r.unit, "samples": r.samples, "note": r.note}
                 for r in results],
                f, ensure_ascii=False, indent=2,
            )
        print(f"原始样本已存：{a.json}")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
