#!/usr/bin/env python
"""
E8 长时间稳定性 —— **有界 soak**，不是"跑几小时"的替身
====================================================================
E8 的原始口径是"连续运行几小时不出问题"。那个不适合放进一次会话里跑，
所以这里做的是**它的一个真子集**：连续压 N 分钟，专抓两类会随时间恶化的事。

  ① **内存单调爬升**（泄漏）—— 前 1/3 和后 1/3 的 RSS 拟合出一条斜率。
     真泄漏的斜率是稳定为正的；正常的 GC 波动是围绕一条水平线上下抖。
  ② **延迟随时间恶化** —— 前 1/3 和后 1/3 的 P95 比值。索引在长跑中退化
     （比如 ANN 反复重建、连接池耗尽）会先表现成延迟慢慢变差，而不是报错。

🔴 **这份脚本不会声称"E8 达标"。** 跑 10 分钟没泄漏，不等于跑 8 小时没泄漏 ——
   有些泄漏要几万次请求才看得出来。它能给的结论只有一条：
   **"这段时间里没有发现单调恶化的迹象"**，或者反过来 **"发现了，就在这里"**。
   前者不是通过，后者是实打实的失败。把它当成 E8 的达标证明就是在自欺。

── 为什么持续压的同时还要持续摄取 ────────────────────────
只读不写的 soak 测不到这个软件真实的长跑形态：用户是边用边往库里加东西。
而"边写边搜"恰恰是最容易出事的地方 —— 写路径要动 `vec_chunks` 和 ANN 索引，
搜路径正在读它们。只压搜索的话，最可能泄漏的那条路根本没被走到。

用法：python -m tests.soak_e8 --minutes 10
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL_DIR = ROOT.parent / "data" / "models"

QUERIES = [
    "缓存一致性", "版本号", "租约", "双写", "机房延迟",
    "怎么保证缓存和数据库的数据一致",
    "分布式系统里最常见的一致性问题是什么",
    "写入的时候应该先删缓存还是先写数据库",
    "热点键会带来什么压力", "缓存重建时的惊群效应怎么避免",
]

BODY = (
    "第 {i} 批资料。分布式缓存的一致性在这个场景下有它自己的处理方式。"
    "写入路径上先失效再回填，可以避免并发读把旧值又写回缓存里；"
    "读路径上要区分缓存未命中和缓存里存的就是空值这两种情况，否则会出现缓存穿透。"
    "跨机房部署时时序问题会被网络延迟放大，租约和版本号两种方案在这一点上的表现不同。"
    "实际落地时还要考虑热点键的单点压力，以及缓存重建时的惊群效应。"
)

#: 判据。**都是相对量**，不是绝对阈值 —— 绝对阈值换台机器就得重定，
#: 而"有没有在变差"这件事跟机器无关。
RSS_GROWTH_MB = 60.0   # 后段比前段高出这么多才叫爬升（低于这个数是 GC 波动）
LAT_DEGRADE_X = 1.5    # 后段 P95 是前段的这么多倍才叫恶化


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def call(port: int, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
    d = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=d,
        headers={"Content-Type": "application/json"} if d else {},
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10.0, help="压多久")
    ap.add_argument("--seed-docs", type=int, default=60, help="开跑前先垫多少篇底")
    ap.add_argument("--add-every", type=float, default=20.0, help="每隔几秒再摄取一批")
    args = ap.parse_args()

    if not MODEL_DIR.exists():
        print(f"✗ 模型目录不存在：{MODEL_DIR}")
        return 1

    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-soak-e8"
    shutil.rmtree(data_dir, ignore_errors=True)
    corpus = data_dir / "corpus"
    corpus.mkdir(parents=True)

    def write_batch(tag: str, n: int) -> Path:
        d = corpus / tag
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            body = "\n\n".join(BODY.format(i=f"{tag}-{i}-{k}") for k in range(4))
            (d / f"doc_{i:04d}.md").write_text(body + "\n", encoding="utf-8")
        return d

    seed = write_batch("seed", args.seed_docs)

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "synorive.main", "--port", str(port),
         "--data-dir", str(data_dir), "--model-dir", str(MODEL_DIR)],
        cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    samples: list[tuple[float, float, float]] = []  # (t, 单次延迟 ms, rssMb)
    errors: list[str] = []
    n_req = 0
    n_ingest = 0

    try:
        up = False
        for _ in range(600):
            try:
                call(port, "/health", timeout=2)
                up = True
                break
            except Exception:
                time.sleep(0.05)
        if not up:
            print("✗ 引擎没起来")
            return 1

        call(port, "/api/ingest", {"targets": [str(seed)], "source": "file", "recursive": True})
        # 垫底那批索引完再开始计时，否则前段延迟里混着摄取的争用，
        # 会把"前段本来就慢"错读成"后段变好了"
        for _ in range(600):
            if int(call(port, "/api/stats").get("ready", 0)) >= args.seed_docs:
                break
            time.sleep(0.25)

        # 预热：第一次查询要加载 ONNX 会话，混进样本会污染"前段"
        for q in QUERIES[:5]:
            call(port, "/api/search", {"query": q, "limit": 20, "stage": "semantic"})

        t_start = time.perf_counter()
        deadline = t_start + args.minutes * 60
        next_add = t_start + args.add_every
        batch = 0
        last_report = t_start

        while time.perf_counter() < deadline:
            q = QUERIES[n_req % len(QUERIES)]
            t = time.perf_counter()
            try:
                call(port, "/api/search", {"query": q, "limit": 20, "stage": "semantic"}, timeout=30)
                lat = (time.perf_counter() - t) * 1000
            except Exception as e:  # noqa: BLE001
                errors.append(f"[{time.perf_counter() - t_start:.0f}s] 搜索失败：{e}")
                lat = float("nan")
            n_req += 1

            if n_req % 20 == 0:
                try:
                    obs = call(port, "/api/metrics/budgets").get("observed") or {}
                    rss = obs.get("rssMb")
                    if isinstance(rss, (int, float)) and lat == lat:
                        samples.append((time.perf_counter() - t_start, lat, float(rss)))
                except Exception as e:  # noqa: BLE001
                    errors.append(f"[{time.perf_counter() - t_start:.0f}s] 取内存失败：{e}")

            now = time.perf_counter()
            if now >= next_add:
                batch += 1
                d = write_batch(f"b{batch}", 8)
                try:
                    call(port, "/api/ingest", {"targets": [str(d)], "source": "file", "recursive": True})
                    n_ingest += 8
                except Exception as e:  # noqa: BLE001
                    errors.append(f"[{now - t_start:.0f}s] 摄取失败：{e}")
                next_add = now + args.add_every

            if now - last_report >= 60:
                mins = (now - t_start) / 60
                cur = samples[-1][2] if samples else float("nan")
                print(f"  {mins:4.1f} 分：{n_req} 次搜索 / {n_ingest} 篇入库 / RSS {cur:.0f} MB "
                      f"/ 出错 {len(errors)}")
                last_report = now
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()

    line = "═" * 74
    print()
    print(line)
    print(f"E8 有界 soak —— 压了 {args.minutes:.0f} 分钟 / {n_req} 次搜索 / "
          f"{args.seed_docs + n_ingest} 篇入库 / 采样 {len(samples)} 点")
    print(line)

    if len(samples) < 6:
        print("✗ 采样点太少，这次跑不出结论（把 --minutes 调大）")
        return 1

    k = len(samples) // 3
    head, tail = samples[:k], samples[-k:]
    rss_h = statistics.median(s[2] for s in head)
    rss_t = statistics.median(s[2] for s in tail)
    lat_h = sorted(s[1] for s in head)[int(0.95 * k) - 1 if k > 1 else 0]
    lat_t = sorted(s[1] for s in tail)[int(0.95 * k) - 1 if k > 1 else 0]

    grow = rss_t - rss_h
    ratio = lat_t / lat_h if lat_h > 0 else 1.0
    leak = grow > RSS_GROWTH_MB
    slow = ratio > LAT_DEGRADE_X

    print(f"  内存 前段中位 {rss_h:.0f} MB → 后段中位 {rss_t:.0f} MB"
          f"（{grow:+.0f} MB，判据 ≤ +{RSS_GROWTH_MB:.0f}）"
          f"　{'✗ 疑似泄漏' if leak else '✓ 没有单调爬升'}")
    print(f"  延迟 前段 P95 {lat_h:.0f} ms → 后段 P95 {lat_t:.0f} ms"
          f"（{ratio:.2f}×，判据 ≤ {LAT_DEGRADE_X}×）"
          f"　{'✗ 在恶化' if slow else '✓ 没有恶化'}")
    print(f"  出错 {len(errors)} 次　{'✗' if errors else '✓ 零失败'}")
    for e in errors[:10]:
        print(f"       └ {e}")
    if len(errors) > 10:
        print(f"       └ …还有 {len(errors) - 10} 条")

    print()
    print("  🔴 **这不是 E8 达标证明。** 它只覆盖了 E8 的一个有界子集：")
    print(f"     跑 {args.minutes:.0f} 分钟没发现恶化 ≠ 跑 8 小时没问题 ——")
    print("     有些泄漏要几万次请求才显形。结论只有「这段时间里没发现」这一条。")
    print(line)

    return 1 if (leak or slow or errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
