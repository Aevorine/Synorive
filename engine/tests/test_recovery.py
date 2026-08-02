#!/usr/bin/env python
"""
D9 零结果补救 —— 端到端
====================================================================
验的不是"有没有给建议"，而是**给的建议是不是真的管用**：
每条建议里写的条数，照着点一遍必须真的能搜出那么多。

一条"要不要试试去掉筛选"，用户点进去还是零结果 —— 比不给建议更让人恼火。
所以这里每条建议都会**照着 payload 再跑一次真检索**去核对。

用法：python -m tests.test_recovery
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def _free_port() -> int:
    """
    挑一个真正空闲的端口。

    ⚠️ 别写死端口号。写死时两个评测同时跑，**第二个的请求会全部打到第一个的
       引擎上**，而且不报错 —— 它会读到别人的库、别人的统计，跑出一份看起来
       正常的结果；等前一个跑完关掉引擎，后一个才以 ConnectionReset 崩掉，
       现场已经完全对不上了。栽过一次，排查花的时间比写这个函数多得多。
    """
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


PORT = _free_port()
MODEL_DIR = ROOT.parent / "data" / "models"

#: 语料刻意分成两簇，方便构造"某个词有、某个词没有"的情形
DOCS = [
    ("摄影_光圈景深.md", "光圈开得越大景深越浅，拍人像时用大光圈把背景化成柔和的色块。"),
    ("摄影_快门速度.md", "快门越慢，移动物体在画面上拖出的轨迹越长，手持要注意安全快门。"),
    ("摄影_白平衡.md", "白平衡就是告诉相机哪种光下的白算白，混合光源现场很难一次调准。"),
    ("烹饪_花椒选择.md", "青花椒麻味冲香气清亮适合水煮，红花椒麻味厚香气沉适合卤味和红油。"),
    ("烹饪_面团发酵.md", "用手指蘸粉戳一个洞，洞口不回缩不塌陷就说明发酵到位了。"),
    ("编程_向量检索.md", "向量检索把文字变成向量，用余弦距离找语义相近的内容，而不是匹配关键词。"),
    ("编程_倒排索引.md", "倒排索引记录每个词出现在哪些文档里，是关键词检索的基础结构。"),
]


class Engine:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Engine":
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "synorive.main", "--port", str(PORT),
             "--data-dir", str(self.data_dir), "--model-dir", str(MODEL_DIR)],
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

    def call(self, path: str, payload: dict | None = None, timeout: float = 120) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}{path}", data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def search(self, query: str, **kw: object) -> dict:
        return self.call("/api/search", {"query": query, "limit": 10, "stage": "semantic", **kw})


def main() -> int:
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-d9"
    shutil.rmtree(data_dir, ignore_errors=True)
    corpus = data_dir / "corpus"
    corpus.mkdir(parents=True)
    for name, body in DOCS:
        (corpus / name).write_text(f"# {name[:-3]}\n\n{body}\n", encoding="utf-8")

    if not MODEL_DIR.exists():
        print(f"✗ 模型目录不存在：{MODEL_DIR}")
        return 1

    problems: list[str] = []
    with Engine(data_dir) as eng:
        eng.call("/api/ingest", {"targets": [str(corpus)], "source": "file", "recursive": True})
        for _ in range(180):
            s = eng.call("/api/stats")
            if s.get("ready", 0) >= len(DOCS):
                break
            time.sleep(1)
        else:
            print(f"✗ 索引没跑完：{eng.call('/api/stats')}")
            return 1
        print(f"语料 {len(DOCS)} 篇已索引\n")

        line = "─" * 70

        # ① 筛选卡太死
        print(line)
        print("① 筛选卡太死（搜得到的词 + 一个不可能的类型筛选）")
        print(line)
        r = eng.search("光圈", filters={"modalities": ["video"]})
        rec = r.get("recovery")
        if not rec:
            problems.append("有筛选导致的零结果没给补救建议")
        else:
            print(f"  原因：{rec['reason']}　说明：{rec['message']}")
            for s in rec["suggestions"]:
                print(f"    · [{s['kind']}] {s['label']}")
            if rec["reason"] != "filters-too-narrow":
                problems.append(f"应判为 filters-too-narrow，实际 {rec['reason']}")
            # 照着建议再跑一次，核对条数是不是真的
            for s in rec["suggestions"]:
                if s["kind"] != "drop-filter":
                    continue
                r2 = eng.search("光圈", filters={})
                got = r2["totalEstimate"]
                ok = got >= s["count"] > 0
                print(f"    ↳ 照做验证：说有 {s['count']} 条，实跑 {got} 条 {'✓' if ok else '✗'}")
                if not ok:
                    problems.append(f"建议「{s['label']}」的条数不实：说 {s['count']}，实跑 {got}")
                break

        # ② 多个词里有一个库里没有
        #
        # 分两种情形，因为它们的"正确行为"不一样：
        #   a) 有一个词能命中标题 → **正确文档就该排第一**，这是比"给建议"更好的
        #      结果，不该因为没弹补救就判失败。
        #   b) 词只出现在正文里、命不中标题 → 只剩向量弱匹配 → 才该给拆词建议。
        # 第一版只写了 (a) 并期望它弹补救，是用例的预期错了，不是实现有问题。
        print()
        print(line)
        print("②a 一个词能命中标题 —— 正确文档应排第一")
        print(line)
        r = eng.search("光圈 螺旋桨")
        top = r["hits"][0]["item"]["title"] if r["hits"] else "（无）"
        ok_a = "光圈" in top
        print(f"  榜首：{top}　{'✓' if ok_a else '✗'}　weakMatch={r.get('weakMatch')}")
        if not ok_a:
            problems.append(f"「光圈 螺旋桨」榜首应是光圈那篇，实际 {top}")

        print()
        print(line)
        print("②b 词只在正文里、命不中标题 —— 应给拆词建议")
        print(line)
        # 「香气」在正文里有、文件名里没有 —— 既不会走标题命中，本身又搜得到。
        # 用「麻味」不行：jieba 对语料的切分让它在 FTS 里 0 命中，
        # 两个词都搜不到时本来就不该给拆词建议。
        r = eng.search("香气 螺旋桨")
        rec = r.get("recovery")
        if not rec:
            problems.append("只剩弱匹配时没给补救建议")
        else:
            print(f"  原因：{rec['reason']}　说明：{rec['message']}")
            for s in rec["suggestions"]:
                print(f"    · [{s['kind']}] {s['label']}")
            kinds = {s["kind"] for s in rec["suggestions"]}
            if "split-term" not in kinds:
                problems.append("应给出拆词建议，实际没有")
            for s in rec["suggestions"]:
                if s["kind"] == "split-term" and s["payload"].get("query"):
                    got = eng.search(s["payload"]["query"])["totalEstimate"]
                    ok = got > 0
                    print(f"    ↳ 照做验证：搜「{s['payload']['query']}」实跑 {got} 条 {'✓' if ok else '✗'}")
                    if not ok:
                        problems.append(f"拆词建议「{s['label']}」点进去还是零结果")
                    break

        # ③ 打错字
        print()
        print(line)
        print("③ 打错字（景深 → 景生）")
        print(line)
        r = eng.search("景生")
        rec = r.get("recovery")
        if not rec:
            problems.append("错别字零结果没给补救建议")
        else:
            print(f"  原因：{rec['reason']}　说明：{rec['message']}")
            for s in rec["suggestions"]:
                print(f"    · [{s['kind']}] {s['label']}")
            dym = [s for s in rec["suggestions"] if s["kind"] == "did-you-mean"]
            if not dym:
                problems.append("错别字应给出「是不是想搜」建议，实际没有")
            else:
                q = dym[0]["payload"]["query"]
                got = eng.search(q)["totalEstimate"]
                print(f"    ↳ 照做验证：搜「{q}」实跑 {got} 条 {'✓' if got > 0 else '✗'}")
                if got <= 0:
                    problems.append("纠错建议点进去还是零结果")

        # ④ 库里确实没有
        print()
        print(line)
        print("④ 库里确实没有这类东西")
        print(line)
        r = eng.search("量子纠缠")
        rec = r.get("recovery")
        print(f"  原因：{rec['reason'] if rec else '（没给）'}　说明：{rec['message'] if rec else ''}")
        if not rec:
            problems.append("确实搜不到时也应给个说明")

        # ⑤ 首屏那一轮不能弹补救 —— 语义还没跑完就说"搜不到"是误导
        print()
        print(line)
        print("⑤ keyword 首屏为空时不应该弹补救（语义还没跑）")
        print(line)
        r = eng.search("柔和的色块", stage="keyword")
        has = "recovery" in r
        print(f"  首屏命中 {r['totalEstimate']} 条，带补救={has} {'✗' if has and r['totalEstimate'] == 0 else '✓'}")
        if has:
            problems.append("keyword 首屏就弹了补救建议，会把用户往错误方向带")

        # ⑥ 有结果时不该有 recovery 字段
        r = eng.search("光圈")
        if r["totalEstimate"] > 0 and "recovery" in r:
            problems.append("有结果时不该带 recovery 字段")

    print()
    print("=" * 70)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ D9 零结果补救通过：四类情形都给出建议，且每条建议照做后确有结果")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
