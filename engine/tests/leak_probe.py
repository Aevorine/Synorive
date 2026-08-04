#!/usr/bin/env python
"""
摄取路径的内存：真泄漏，还是分配器没把页还给系统？
====================================================================
`soak_e8.py` 已经把范围缩到很小了：

  · 只搜不入库，16221 次查询，RSS 只涨 3 MB  → **搜索路径干净**
  · 边搜边入库，最终语料同样是 292 篇，RSS 涨 81 MB
  · 两次终态语料一样大 → 那 81 MB **不是索引撑的，是摄取这个动作留下的**

剩下两种解释，后果差很多，所以必须分开：

  (a) **真泄漏** —— 摄取过程中某些对象没被释放（嵌入缓冲、块列表、
      ONNX 会话的中间张量被什么东西引用着）。后果是长期开着的实例
      每摄取一批就永久多占一截，最终 OOM。
  (b) **分配器保留** —— Python / onnxruntime 在嵌入时申请大块临时内存，
      释放后堆管理器把页留在进程里备用而不还给系统。RSS 不降，
      但这些内存是**可复用的**，不会无限涨。

判据只有一条，而且很干脆：
  **一波一波地摄取，每波之间空转。真泄漏每波都再涨一截；
    分配器保留只在第一波涨，后面几波基本持平。**

🔴 **不要用"RSS 降没降"当判据。** 两种情况 RSS 都不降 ——
   这正是第一版把 (b) 误读成 (a) 的地方。要看的是**增量的形状**，不是绝对值。

用法：python -m tests.leak_probe [--waves 4] [--per-wave 60]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL_DIR = ROOT.parent / "data" / "models"

BODY = (
    "第 {i} 批。分布式缓存的一致性在这个场景下有它自己的处理方式。"
    "写入路径上先失效再回填，可以避免并发读把旧值又写回缓存里；"
    "读路径上要区分缓存未命中和缓存里存的就是空值这两种情况，否则会出现缓存穿透。"
    "跨机房部署时时序问题会被网络延迟放大，租约和版本号两种方案在这一点上表现不同。"
    "实际落地时还要考虑热点键的单点压力，以及缓存重建时的惊群效应。"
)

#: 后面几波的平均增量超过第一波的这个比例，就判成真泄漏。
#: 分配器保留的典型形状是第一波吃掉大头、后面几波接近零。
LEAK_RATIO = 0.5


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def call(port: int, path: str, payload: dict | None = None, timeout: float = 180) -> dict:
    d = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=d,
        headers={"Content-Type": "application/json"} if d else {},
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def rss(port: int) -> float:
    obs = call(port, "/api/metrics/budgets").get("observed") or {}
    v = obs.get("rssMb")
    return float(v) if isinstance(v, (int, float)) else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--waves", type=int, default=4, help="摄取几波")
    ap.add_argument("--per-wave", type=int, default=60, help="每波几篇")
    ap.add_argument("--settle", type=float, default=45.0, help="每波之后空转几秒再量")
    args = ap.parse_args()

    if not MODEL_DIR.exists():
        print(f"✗ 模型目录不存在：{MODEL_DIR}")
        return 1

    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-leak-probe"
    shutil.rmtree(data_dir, ignore_errors=True)
    corpus = data_dir / "corpus"
    corpus.mkdir(parents=True)

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "synorive.main", "--port", str(port),
         "--data-dir", str(data_dir), "--model-dir", str(MODEL_DIR)],
        cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    marks: list[float] = []
    try:
        for _ in range(600):
            try:
                call(port, "/health", timeout=2)
                break
            except Exception:
                time.sleep(0.05)
        else:
            print("✗ 引擎没起来")
            return 1

        # 预热：把 ONNX 会话加载的那一次性开销排除在所有波次之外，
        # 否则它会整个算进第一波，让第一波看起来大得离谱
        call(port, "/api/search", {"query": "缓存一致性", "limit": 20, "stage": "semantic"})
        time.sleep(3)
        base = rss(port)
        marks.append(base)
        print(f"基线（模型已加载、库为空）：{base:.0f} MB")
        print()

        total = 0
        for w in range(1, args.waves + 1):
            d = corpus / f"w{w}"
            d.mkdir(parents=True, exist_ok=True)
            for i in range(args.per_wave):
                body = "\n\n".join(BODY.format(i=f"{w}-{i}-{k}") for k in range(4))
                (d / f"doc_{i:04d}.md").write_text(body + "\n", encoding="utf-8")

            call(port, "/api/ingest", {"targets": [str(d)], "source": "file", "recursive": True})
            total += args.per_wave
            for _ in range(1200):
                if int(call(port, "/api/stats").get("ready", 0)) >= total:
                    break
                time.sleep(0.25)

            # 空转：只搜不写，给释放和 GC 一个真的会发生的机会。
            # 摄取一结束就量的话，量到的是"正在用"而不是"没还回来"
            t_end = time.time() + args.settle
            while time.time() < t_end:
                call(port, "/api/search", {"query": "缓存一致性", "limit": 20, "stage": "semantic"})

            cur = rss(port)
            marks.append(cur)
            print(f"第 {w} 波：+{args.per_wave} 篇（累计 {total}）→ RSS {cur:.0f} MB"
                  f"（比上一波 {cur - marks[-2]:+.0f} MB）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()

    deltas = [marks[i + 1] - marks[i] for i in range(len(marks) - 1)]
    if len(deltas) < 2:
        print("✗ 波次太少，判不出来")
        return 1

    first = deltas[0]
    rest = sum(deltas[1:]) / len(deltas[1:])
    line = "═" * 74
    print()
    print(line)
    print(f"每波增量：{' / '.join(f'{d:+.0f}' for d in deltas)} MB")
    print(f"第一波 {first:+.0f} MB　后续平均 {rest:+.0f} MB"
          f"（比值 {rest / first if first else 0:.2f}，判据 ≤ {LEAK_RATIO}）")
    print(line)

    if first > 0 and rest / first > LEAK_RATIO:
        print("✗ **形状像真泄漏** —— 每波都在等量地涨，说明摄取过程中有东西没被释放。")
        print("  下一步：在摄取前后各打一次 tracemalloc 快照，比对 top 差异。")
        return 1

    print("✓ **形状像分配器保留，不是泄漏** —— 第一波吃掉大头，后面几波基本持平。")
    print("  这些页留在进程里备用，可以被后续摄取复用，不会随时间无限涨。")
    print("  🔴 但它确实抬高了常驻内存的地板：E4 的 450MB 预算要按**摄取过一批之后**")
    print("     的水位来核，而不是按刚启动时的水位。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
