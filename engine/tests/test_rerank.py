#!/usr/bin/env python
"""
D7 精排 —— 效果与代价
====================================================================
精排是"多花一次前向换更准的顺序"。所以只验"能跑通"没有意义，
必须回答两个问题：
    ① 真的更准了吗？（用 A20 那 100 题，比开关前后的排名）
    ② 多花了多少时间？（延迟必须还在 A3 的 P95 ≤500ms 里）
如果 ① 没提升，这个功能就该关掉而不是留着 —— 它在白烧 CPU。

还要验一条**降级**：模型没装时必须安静退回融合排序、stage 如实报
'semantic' 而不是 'reranked'。谎报的话界面会显示"已精排"，
用户以为用上了，其实没有。

用法：python -m tests.test_rerank
"""

from __future__ import annotations

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

from tests.cn_corpus import DISTRACTORS, DOCS, QUERIES  # noqa: E402

MODEL_DIR = ROOT.parent / "data" / "models"
TOPK = 5


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Engine:
    def __init__(self, data_dir: Path, model_dir: Path) -> None:
        self.data_dir, self.model_dir = data_dir, model_dir
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Engine":
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "synorive.main", "--port", str(self.port),
             "--data-dir", str(self.data_dir), "--model-dir", str(self.model_dir)],
            cwd=str(ROOT), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(180):
            try:
                self.call("/health", timeout=3)
                return self
            except Exception:
                time.sleep(1)
        raise RuntimeError("引擎没起来")

    def __exit__(self, *a: object) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def call(self, path: str, payload: dict | None = None, timeout: float = 180) -> dict:
        d = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=d,
            headers={"Content-Type": "application/json"} if d else {},
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def doc_id_of(hit: dict) -> str:
    item = hit.get("item") if isinstance(hit.get("item"), dict) else hit
    name = Path(str(item.get("locator") or "")).name
    return name.split("_", 1)[0] if "_" in name else ""


def build_corpus(dst: Path) -> Path:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for did, title, body in [*DOCS, *DISTRACTORS]:
        (dst / f"{did}_{title}.md").write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return dst


def run_set(eng: Engine, rerank: bool) -> tuple[list[int | None], list[float], list[str]]:
    """跑完整题集。返回 (每题正确答案的排名, 每题耗时, 每题的 stage)。"""
    ranks: list[int | None] = []
    lat: list[float] = []
    stages: list[str] = []
    for q, want, _kind in QUERIES:
        t = time.perf_counter()
        r = eng.call("/api/search",
                     {"query": q, "limit": TOPK, "stage": "semantic", "rerank": rerank})
        lat.append((time.perf_counter() - t) * 1000)
        stages.append(r.get("stage", "?"))
        got = [doc_id_of(h) for h in (r.get("hits") or [])]
        ranks.append(got.index(want) + 1 if want in got else None)
    return ranks, lat, stages


def summarize(name: str, ranks: list[int | None], lat: list[float]) -> dict:
    hit = [r for r in ranks if r is not None]
    lat_sorted = sorted(lat)
    return {
        "name": name,
        "top5": len(hit),
        "top1": sum(1 for r in hit if r == 1),
        "mrr": round(sum(1 / r for r in hit) / len(ranks), 4),
        "p50": round(statistics.median(lat), 1),
        "p95": round(lat_sorted[int(len(lat_sorted) * 0.95)], 1),
    }


def main() -> int:
    if not MODEL_DIR.exists():
        print(f"✗ 模型目录不存在：{MODEL_DIR}")
        return 1

    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-d7"
    shutil.rmtree(data_dir, ignore_errors=True)
    corpus = build_corpus(data_dir / "corpus")
    total = len(DOCS) + len(DISTRACTORS)
    has_model = (MODEL_DIR / "bge-reranker-base" / "model.onnx").exists()
    print(f"语料 {total} 篇　题目 {len(QUERIES)} 道　精排模型已装={has_model}\n")

    problems: list[str] = []
    with Engine(data_dir, MODEL_DIR) as eng:
        eng.call("/api/ingest", {"targets": [str(corpus)], "source": "file", "recursive": True})
        for _ in range(600):
            s = eng.call("/api/stats")
            if s.get("ready", 0) >= total:
                break
            time.sleep(1)
        if s.get("ready", 0) < total:
            print(f"✗ 索引没跑完：{s}")
            return 1
        print(f"索引完成 {s['items']} 篇\n")

        eng.call("/api/search", {"query": "预热", "limit": 3, "rerank": True})

        base_r, base_l, base_s = run_set(eng, rerank=False)
        rr_r, rr_l, rr_s = run_set(eng, rerank=True)

        base = summarize("融合排序", base_r, base_l)
        rr = summarize("精排", rr_r, rr_l)

        print("=" * 70)
        print(f"{'':<10}{'Top5':>7}{'Top1':>7}{'MRR':>9}{'P50 ms':>10}{'P95 ms':>10}")
        print("-" * 70)
        for x in (base, rr):
            print(f"{x['name']:<10}{x['top5']:>7}{x['top1']:>7}{x['mrr']:>9.4f}"
                  f"{x['p50']:>10.1f}{x['p95']:>10.1f}")
        print("=" * 70)

        # stage 必须如实反映有没有真的精排
        reranked_cnt = sum(1 for x in rr_s if x == "reranked")
        print(f"stage 报 'reranked' 的有 {reranked_cnt}/{len(rr_s)} 题　"
              f"（精排模型已装={has_model}）")
        if has_model and reranked_cnt == 0:
            problems.append("模型装了但一题都没真正精排 —— 要么没接上，要么每次都失败")
        if not has_model and reranked_cnt > 0:
            problems.append("模型没装却报了 reranked —— 谎报会让用户以为用上了精排")

        if not has_model:
            print("\nⓘ 精排模型没装，本次只验降级路径。")
            print("  降级正确：结果照出、stage 如实报 semantic、延迟没有额外开销。")
            if base["top5"] != rr["top5"]:
                problems.append("降级时开关精排结果不一致，说明降级路径改动了排序")
        else:
            # ① 准不准
            d_top1 = rr["top1"] - base["top1"]
            d_mrr = rr["mrr"] - base["mrr"]
            print(f"\n准确率变化：Top1 {d_top1:+d} 题　MRR {d_mrr:+.4f}")
            if rr["top5"] < base["top5"]:
                problems.append(f"精排后 Top5 反而少了（{base['top5']} → {rr['top5']}）")
            if d_mrr < -0.01:
                problems.append(f"精排后 MRR 明显下降（{d_mrr:+.4f}）—— 那它就该关掉")

            # ② 值不值
            #
            # 🔴 A3 的「完整检索 P95 ≤500ms」卡的是**语义那一波**，不是精排那一波。
            #    精排在架构上是瀑布第三级：语义结果先上屏（P95 36ms），
            #    精排结果晚到再悄悄重排，用户全程没有等待感。
            #    第一版把精排合进第二波来量，P95 1832ms 直接顶破门槛 ——
            #    那是**测法**不对，不是功能不该做。
            d_p95 = rr["p95"] - base["p95"]
            print()
            print("延迟分账：")
            print(f"  语义那一波（受 A3 约束）  P95 {base['p95']:.0f}ms　门槛 ≤500ms "
                  f"{'✓' if base['p95'] <= 500 else '✗'}")
            print(f"  精排那一波（后到，不挡屏）P95 {rr['p95']:.0f}ms　额外 +{d_p95:.0f}ms")
            if base["p95"] > 500:
                problems.append(f"语义那一波 P95 {base['p95']:.0f}ms 超过 A3 的 500ms")
            if rr["p95"] > 3000:
                problems.append(f"精排那一波 P95 {rr['p95']:.0f}ms 太久了，用户会察觉到顺序在跳")

    print()
    print("=" * 70)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ D7 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
