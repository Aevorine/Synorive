#!/usr/bin/env python
"""
A3 Ask 模式 —— 端到端
====================================================================
这个功能最大的风险和秒答卡是**同一个**：给出一段读起来很像答案、
但其实是撞词撞出来的东西。用户会直接采信它。

所以测试重心在四件事上，一件比一件重要：

  ① **摘录必须在原文里逐字存在** —— 这是"只摘不生成"的硬约束。
     哪天有人把它改成模型生成，这条断言会立刻炸。
  ② **答不上的时候必须说答不上**，而且要给出**具体到能照做**的建议。
     `enough:false` 却给一段像模像样的文字，比直接说"没有"糟得多。
  ③ **永远返回一个完整对象，不返回 None，不报 4xx**。
     "库里没有这个答案"是正常业务结果 —— 用 4xx 表达它，
     会让调用方的错误处理分支里混进一堆正常情况。
  ④ **多来源不重复**：同一份资料被索引两遍（改过名、存过副本）在真实库里
     很常见，不去重的话答案会把同一句话说三遍，看起来像坏了。

用法：python -m tests.test_ask
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL_DIR = ROOT.parent / "data" / "models"

#: 前两篇**故意讲同一件事的不同侧面** —— 用来验"多段互补答上一个问题"
#: 是被当成好答案（覆盖率取并集）而不是被平均分拉低。
#: 第三篇是第一篇的近似副本，用来验去重。
DOCS = [
    ("光圈与景深.md",
     "光圈开得越大，景深越浅，焦平面前后清晰的范围越窄。"
     "拍人像时常用大光圈把背景化成柔和的色块。"),
    ("景深的其他影响因素.md",
     "除了光圈，焦距和拍摄距离同样决定景深。焦距越长景深越浅，离被摄物越近景深也越浅。"),
    ("光圈笔记_副本.md",
     "光圈开得越大，景深越浅，焦平面前后清晰的范围越窄。"
     "这一条是从另一份笔记里抄过来的。"),
    ("完全无关的菜谱.md",
     "番茄炒蛋的关键是鸡蛋先炒到半凝固就盛出，最后再回锅拌匀，这样蛋才嫩。"),
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

    def ask(self, q: str, **kw: object) -> dict:
        return self.call("/api/ask", {"query": q, **kw})


def main() -> int:
    if not MODEL_DIR.exists():
        print(f"✗ 模型目录不存在：{MODEL_DIR}")
        return 1

    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-a3-ask"
    shutil.rmtree(data_dir, ignore_errors=True)
    corpus = data_dir / "corpus"
    corpus.mkdir(parents=True)
    bodies: dict[str, str] = {}
    for name, body in DOCS:
        (corpus / name).write_text(f"# {name[:-3]}\n\n{body}\n", encoding="utf-8")
        bodies[name] = body
    allbody = "".join(bodies.values())

    problems: list[str] = []
    skipped: list[str] = []
    line = "─" * 70

    with Engine(data_dir) as eng:
        eng.call("/api/ingest", {"targets": [str(corpus)], "source": "file", "recursive": True})
        s: dict = {}
        for _ in range(300):
            s = eng.call("/api/stats")
            if s.get("ready", 0) >= len(DOCS):
                break
            time.sleep(1)
        if s.get("ready", 0) < len(DOCS):
            print(f"✗ 索引没跑完：{s}")
            return 1
        print(f"语料 {len(DOCS)} 篇已索引\n")

        # ── ① 逐字摘录（最硬的一条）────────────────────────
        print(line)
        print("① 答得上时：每一段都必须在原文里逐字存在")
        print(line)
        r = eng.ask("光圈怎么影响景深")
        a = r.get("ask") or {}
        print(f"  enough={a.get('enough')}　覆盖率={a.get('coverage')}　"
              f"{len(a.get('passages', []))} 段 / {len(a.get('sources', []))} 个来源")
        if not a.get("passages"):
            problems.append("「光圈怎么影响景深」一段都没摘到 —— 语料里明明有直接答案")
        for p in a.get("passages", []):
            verbatim = p["text"] in allbody
            print(f"  {'✓' if verbatim else '✗'} {p['text'][:44]}…　← {p['title']}")
            if not verbatim:
                problems.append(f"这一段不在原文里，是生成不是摘录：{p['text'][:60]}")
            if p.get("kind") != "extract":
                problems.append("passage.kind 必须是 extract")
            if not p.get("itemId") or not p.get("locator"):
                problems.append("每一段都必须带 itemId + locator，否则点不回原文")
            if not p.get("matched"):
                problems.append("matched 为空 —— 界面就没法说明"
                                "它凭什么被选中，覆盖率也无从核对")

        # ── ② 多段互补要被当成好答案，不是被平均掉 ──────────
        print()
        print(line)
        print("② 多来源互补：两篇各答一半，应该判 enough 且引用两个来源")
        print(line)
        r = eng.ask("影响景深的因素有哪些")
        a = r.get("ask") or {}
        srcn = len(a.get("sources", []))
        print(f"  enough={a.get('enough')}　覆盖率={a.get('coverage')}　来源 {srcn} 个")
        for sd in a.get("sources", []):
            print(f"      · {sd['title']}")
        if srcn < 2:
            # 不判失败：召回本身受模型影响，样本又小。但要标出来不算通过
            skipped.append(f"多来源互补只引到 {srcn} 个来源，没验到跨来源合并这条")
        elif not a.get("enough"):
            # 🔴 这一条是硬断言，不是 skip：两段都真的答上了，却判"只答上一部分"
            #    并在界面上挂一条「没找到关于影响、因素的内容」的警示 —— 那是错的。
            #    根因是问题框架词（影响/因素）被算进了覆盖率分母，
            #    见 ask.py 的 _FRAME_TERMS。**这条挂了说明那批词又被算回去了。**
            problems.append(
                f"两段正确答案都引到了却判 enough=false（覆盖率 {a.get('coverage')}）"
                " —— 框架词很可能又被算进分母了，见 ask.py::_FRAME_TERMS"
            )

        # ── ③ 去重：近似副本不该让同一句话出现两次 ───────────
        print()
        print(line)
        print("③ 近似副本去重：同一句话不能在答案里出现两遍")
        print(line)
        r = eng.ask("光圈怎么影响景深")
        texts = [p["text"] for p in (r.get("ask") or {}).get("passages", [])]
        dupe = len(texts) != len(set(texts))
        # 更严的一条：两段高度相似（不只是完全相等）也算重复
        near = False
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                a1, b1 = texts[i], texts[j]
                short = min(len(a1), len(b1))
                if short and sum(1 for k in range(short) if a1[k] == b1[k]) / short > 0.9:
                    near = True
        print(f"  {len(texts)} 段，完全重复={dupe}　高度相似={near}")
        if dupe:
            problems.append("答案里出现了完全相同的两段 —— 去重没生效")
        if near:
            problems.append("答案里出现了高度相似的两段 —— DEDUPE_RATIO 没拦住近似副本")

        # ── ④ 答不上：必须说清楚，且给能照做的建议 ───────────
        print()
        print(line)
        print("④ 库里没有的问题：不许硬答，必须给原因 + 具体建议")
        print(line)
        r = eng.ask("量子纠缠的贝尔不等式怎么推导")
        a = r.get("ask") or {}
        print(f"  enough={a.get('enough')}　why={a.get('why')}")
        for t in a.get("suggest", []) or []:
            print(f"      建议：{t}")
        if a.get("enough"):
            problems.append("库里根本没有这个主题，却判了 enough=true —— 这是最危险的一种错")
        if not a.get("why"):
            problems.append("答不上时必须给 why，只给一个空列表等于让用户自己猜")
        if not a.get("suggest"):
            problems.append("答不上时必须给 suggest")
        for t in a.get("suggest", []) or []:
            if t.strip() in ("换个说法试试", "再试一次"):
                problems.append(f"建议「{t}」是废话 —— 用户不知道换成什么")

        # ── ⑤ 空查询：返回 200 + 完整对象，不 4xx、不崩 ──────
        print()
        print(line)
        print("⑤ 空查询 / 无实词：照样 200 + 完整对象（这是正常业务结果不是错误）")
        print(line)
        for q in ("", "的了吗"):
            try:
                r = eng.ask(q)
                a = r.get("ask") or {}
                ok = ("passages" in a) and ("enough" in a)
                print(f"  {'✓' if ok else '✗'} 「{q}」→ enough={a.get('enough')}　"
                      f"why={a.get('why')}")
                if not ok:
                    problems.append(f"「{q}」返回的对象缺字段：{list(a)}")
            except urllib.error.HTTPError as e:
                problems.append(f"「{q}」返回了 HTTP {e.code} —— 空结果不该用错误码表达")

        # ── ⑥ hits 一并带回，用户能自己核对 ────────────────
        print()
        print(line)
        print("⑥ 答案下面必须摊开读过的那几条，否则「可核对」无从谈起")
        print(line)
        r = eng.ask("光圈怎么影响景深")
        n = len(r.get("hits", []))
        print(f"  hits={n} 条　elapsedMs={r.get('elapsedMs')}")
        if n == 0:
            problems.append("/api/ask 没带回 hits —— 用户没法核对答案是从哪几条里摘的")

    print()
    print("=" * 70)
    for sk in skipped:
        print(f"⚠ 跳过（不算通过）：{sk}")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ A3 Ask 模式通过（逐字摘录 / 去重 / 答不上说清楚 / 空查询不报错 / 证据可核对）"
          + ("（含上面标注的跳过项）" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
