#!/usr/bin/env python
"""
D8 秒答卡 —— 端到端
====================================================================
这个功能最大的风险不是"给不出卡"，是**给了一张错卡**：
用户会直接采信它、不再往下看。所以测试的重心放在"该不给的时候真的不给"：

  · 不是问句 → 不给（搜「光圈」是想浏览一批结果，甩一句话反而挡路）
  · 全是弱匹配 → 不给（连匹配上都谈不上，摘出来的只会是巧合撞词）
  · 库里没有 → 不给
  · 首屏那一波 → 不给（语义还没跑完，可能有更对的在后面）

还要验**摘录的句子必须在原文里逐字存在** —— 这是"只摘不生成"的硬约束，
一旦哪天有人把它改成模型生成，这条断言会立刻炸。

用法：python -m tests.test_answer
"""

from __future__ import annotations

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

DOCS = [
    ("摄影_光圈景深.md",
     "光圈开得越大，景深越浅，焦平面前后清晰的范围越窄。拍人像时常用大光圈把背景化成柔和的色块。"
     "拍风光则要把光圈收到中等偏小，才能让近处和远处同时清楚。"),
    ("金融_两种还款.md",
     "等额本息每月还款额固定，前期还的绝大部分是利息，总利息更多。"
     "等额本金每月归还的本金相同，利息随本金减少而递减，前期月供高但总支出少。"),
    ("旅行_倒时差.md",
     "向东飞比向西飞更难适应，因为要把生物钟提前。落地后按当地时间安排三餐，白天多晒太阳。"),
    ("宠物_猫应激.md",
     "换环境或家里来生人都可能让猫躲起来不吃不喝。硬把它拽出来只会加重恐惧，"
     "正确做法是给一个封闭安静的空间，让它自己出来。"),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Engine:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Engine":
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "synorive.main", "--port", str(self.port),
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
        d = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=d,
            headers={"Content-Type": "application/json"} if d else {},
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def search(self, q: str, **kw: object) -> dict:
        return self.call("/api/search",
                         {"query": q, "limit": 8, "stage": "semantic", "answer": True, **kw})


def main() -> int:
    if not MODEL_DIR.exists():
        print(f"✗ 模型目录不存在：{MODEL_DIR}")
        return 1

    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-d8"
    shutil.rmtree(data_dir, ignore_errors=True)
    corpus = data_dir / "corpus"
    corpus.mkdir(parents=True)
    bodies: dict[str, str] = {}
    for name, body in DOCS:
        (corpus / name).write_text(f"# {name[:-3]}\n\n{body}\n", encoding="utf-8")
        bodies[name] = body

    problems: list[str] = []
    with Engine(data_dir) as eng:
        eng.call("/api/ingest", {"targets": [str(corpus)], "source": "file", "recursive": True})
        for _ in range(300):
            s = eng.call("/api/stats")
            if s.get("ready", 0) >= len(DOCS):
                break
            time.sleep(1)
        if s.get("ready", 0) < len(DOCS):
            print(f"✗ 索引没跑完：{s}")
            return 1
        print(f"语料 {len(DOCS)} 篇已索引\n")
        line = "─" * 70

        # ── ① 该给的时候给，而且摘的句子必须在原文里逐字存在 ──
        print(line)
        print("① 问句 + 有答案 → 给卡，且句子必须是原文逐字摘录")
        print(line)
        ASK = ["光圈怎么影响景深", "等额本息和等额本金有什么区别", "怎么倒时差", "猫为什么躲起来不吃东西"]
        given = 0
        for q in ASK:
            r = eng.search(q)
            card = r.get("answer")
            if not card:
                print(f"  ○ 「{q}」没给卡（结果 {r['totalEstimate']} 条）")
                continue
            given += 1
            allbody = "".join(bodies.values())
            verbatim = card["text"] in allbody
            print(f"  {'✓' if verbatim else '✗'} 「{q}」")
            print(f"      摘录：{card['text']}")
            print(f"      出处：{card['title']}　覆盖率 {card['coverage']}　kind={card['kind']}")
            if not verbatim:
                problems.append(f"「{q}」摘出来的句子不在原文里 —— 这是生成不是摘录，绝对不允许")
            if card["kind"] != "extract":
                problems.append("kind 必须是 extract，界面据此说明这是原文摘录")
            if not card.get("itemId") or not card.get("locator"):
                problems.append(f"「{q}」的卡没带出处，用户无从核对")
        if given == 0:
            problems.append("四道问句一张卡都没给 —— 判据太严，功能等于没有")
        print(f"  → 给了 {given}/{len(ASK)} 张")

        # ── ② 不是问句就不给 ──
        print()
        print(line)
        print("② 不是问句 → 不给（浏览型查询甩一句话反而挡路）")
        print(line)
        for q in ["光圈", "花椒", "等额本金"]:
            r = eng.search(q)
            has = "answer" in r
            print(f"  {'✗' if has else '✓'} 「{q}」{'给了卡' if has else '没给'}（结果 {r['totalEstimate']} 条）")
            if has:
                problems.append(f"「{q}」不是问句却给了秒答卡")

        # ── ③ 库里没有 / 全是弱匹配 → 不给 ──
        print()
        print(line)
        print("③ 库里没有这类东西 → 不给")
        print(line)
        for q in ["量子纠缠是怎么回事", "区块链共识算法有什么区别"]:
            r = eng.search(q)
            has = "answer" in r
            print(f"  {'✗' if has else '✓'} 「{q}」{'给了卡' if has else '没给'}　"
                  f"weakMatch={r.get('weakMatch')}")
            if has:
                problems.append(f"「{q}」库里没有对应内容却给了秒答卡")

        # ── ④ 首屏那一波不给 ──
        print()
        print(line)
        print("④ keyword 首屏 → 不给（语义还没跑，可能有更对的在后面）")
        print(line)
        r = eng.search("光圈怎么影响景深", stage="keyword")
        has = "answer" in r
        print(f"  {'✗' if has else '✓'} 首屏{'给了卡' if has else '没给'}（结果 {r['totalEstimate']} 条）")
        if has:
            problems.append("首屏那一波就给了秒答卡")

        # ── ⑤ 没开开关就不该有这个字段 ──
        r = eng.call("/api/search", {"query": "光圈怎么影响景深", "limit": 5, "stage": "semantic"})
        if "answer" in r:
            problems.append("没传 answer=true 也返回了秒答卡")

    print()
    print("=" * 70)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ D8 秒答卡通过（该给才给 / 逐字摘录 / 带出处 / 四类情形都不误给）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
