#!/usr/bin/env python
"""
联网搜索的 HTTP 接口 —— 端到端（W/R/L 接出去这一层）
====================================================================
上一个测试验的是「联网层自己能不能跑」，这个验的是
**它有没有真的接到接口上、Claude Code 和界面拿到的是不是同一份东西**。

这两件事分开测是有原因的：模块跑得通但没挂上路由、
或者挂上了但字段名对不上，都属于"不报错、退出码 0、功能为空"那一类。

用法：python -m tests.test_web_api
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
problems: list[str] = []
skipped: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'✓' if cond else '✗'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


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
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(180):
            try:
                self.call("/health", timeout=3)
                return self
            except Exception:
                if self.proc.poll() is not None:
                    err = (self.proc.stderr.read() or b"").decode("utf-8", "replace")
                    raise RuntimeError(f"引擎退出了：\n{err[-2500:]}") from None
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


def main() -> int:
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-webapi"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)
    line = "─" * 70

    with Engine(data_dir) as eng:
        print(line)
        print("① /api/web/engines —— 每家是什么、为什么用不了，界面全靠它")
        print(line)
        info = eng.call("/api/web/engines")
        engines = info.get("engines") or []
        check(len(engines) >= 10, f"注册了 {len(engines)} 家（含学术源）",
              f"只注册了 {len(engines)} 家")
        groups = {e["group"] for e in engines}
        check(groups == {"web", "scholar"}, f"分组正确：{sorted(groups)}",
              f"分组不对：{sorted(groups)}")
        need_browser = [e["id"] for e in engines if e.get("needsBrowser")]
        check("google" in need_browser and "yandex" in need_browser,
              f"Google/Yandex 明确标了「需要浏览器渲染」：{need_browser}",
              "拿不到结果的引擎没有标出原因 —— 用户只会以为软件坏了")
        for e in engines:
            if not e["defaultOn"]:
                check(bool(e["note"]), f"{e['id']} 默认关且写了原因",
                      f"{e['id']} 默认关却没写为什么，用户无从判断要不要开")

        print()
        print(line)
        print("② /api/web/search —— 结果 + 可信度分项 + 已排除抽屉（R11）")
        print(line)
        t0 = time.monotonic()
        res = eng.call("/api/web/search", {"query": "sqlite wal 模式", "limit": 12})
        ms = (time.monotonic() - t0) * 1000
        n = len(res.get("results") or [])
        if n == 0 and all(e["outcome"] != "ok" for e in res.get("engines", [])):
            skipped.append("联网检索：所有引擎都没返回（断网？）")
            print("  ⚠ 所有引擎都没返回，②③④ 跳过，不计入通过")
        else:
            check(n > 0, f"{n} 条结果，{ms:.0f}ms", "一条结果都没有")
            check("excluded" in res,
                  f"「已排除」抽屉一起返回了（{len(res['excluded'])} 条）",
                  "没有返回 excluded 字段 —— 被过滤的内容用户看不到也放不回")
            check(bool(res.get("trustSummary", {}).get("note")),
                  f"概览：{res.get('trustSummary', {}).get('note', '')[:56]}",
                  "没有可信度概览")
            first = (res["results"] or [{}])[0]
            t = first.get("trust") or {}
            check(bool(t.get("reasons")),
                  f"每条带可信度理由：{(t.get('reasons') or [''])[0][:40]}",
                  "结果没带可信度理由，只有一个分数说明不了任何事")
            check("tierLabel" in t and "independentSources" in t,
                  f"分项齐全：{t.get('tierLabel')} / {t.get('independentSources')} 个独立来源",
                  "可信度只有总分没有分项")

            print()
            print(line)
            print("③ /api/web/research —— 搜 + 抓 + 出摘录简报（一次调用走完）")
            print(line)
            t0 = time.monotonic()
            rr = eng.call("/api/web/research",
                          {"query": "sqlite wal 模式 原理", "fetch": 4, "limit": 12},
                          timeout=180)
            ms = (time.monotonic() - t0) * 1000
            b = rr.get("briefing") or {}
            print(f"  抓到 {rr.get('fetched')} 篇（失败 {rr.get('fetchFailed')} 篇），{ms:.0f}ms")
            check(b.get("kind") == "extract",
                  "简报标明 kind=extract（原文摘录，不是 AI 写的）", "简报没标明是摘录")
            check(all(k in b for k in
                      ("consensus", "disputes", "timeline", "numbers", "openQuestions")),
                  "五个区块齐全：共识/分歧/时间线/关键数据/还没查清",
                  f"简报区块缺失：{sorted(b)}")
            ev = [e for t_ in b.get("disputes", []) for e in t_["evidence"]] + \
                 [e for t_ in b.get("consensus", []) for e in t_["evidence"]]
            if ev:
                check(all(e.get("url") for e in ev),
                      f"{len(ev)} 条证据全部带出处（R7）", "有证据没带出处")
            else:
                print("  ○ 这一轮没摘出证据（抓到的正文太少），R7 这条没测到")
                skipped.append("研究简报：证据为空，R7 出处检查没执行到")
            check(rr.get("fetchFailed") is not None,
                  f"抓取失败数如实报出（{rr.get('fetchFailed')} 篇）",
                  "抓失败的篇数没报，简报单薄时用户不知道为什么")

        print()
        print(line)
        print("④ /api/web/scholar —— 五家学术源，按 DOI 合并（L1）")
        print(line)
        sc = eng.call("/api/web/scholar",
                      {"query": "write-ahead logging recovery", "limit": 15}, timeout=120)
        papers = sc.get("papers") or []
        ok_src = [s["id"] for s in sc.get("sources", []) if s["outcome"] == "ok"]
        if not ok_src:
            skipped.append("学术源：一家都没返回（断网？）")
            print("  ⚠ 一家都没返回，跳过")
        else:
            check(len(ok_src) >= 3, f"{len(ok_src)} 家可用：{ok_src}",
                  f"只有 {len(ok_src)} 家可用：{ok_src}")
            check(bool(papers),
                  f"{sc['totalBeforeMerge']} 条 → 合并 {sc['mergedCount']} 篇 → 返回 {len(papers)} 篇",
                  "一篇都没有")
            withdoi = [p for p in papers if (p.get("meta") or {}).get("doi")]
            check(len(withdoi) >= len(papers) * 0.6,
                  f"{len(withdoi)}/{len(papers)} 篇带 DOI",
                  f"只有 {len(withdoi)}/{len(papers)} 篇带 DOI")

        print()
        print(line)
        print("⑤ 静默失败自查：接口存在 ≠ 真的挂上了")
        print(line)
        try:
            openapi = eng.call("/openapi.json")
            paths = openapi.get("paths") or {}
        except Exception:
            paths = {}
        for p in ("/api/web/search", "/api/web/research", "/api/web/scholar",
                  "/api/web/engines"):
            check(p in paths, f"{p} 出现在 OpenAPI 里（Claude Code 能发现它）",
                  f"{p} 没出现在 OpenAPI 里")
        # 参数非法必须被 schema 挡下，而不是当成默认值悄悄跑过去
        bad = None
        try:
            eng.call("/api/web/search", {"query": "x", "limit": 9999})
        except urllib.error.HTTPError as e:
            bad = e.code
        check(bad == 422, f"limit=9999 被 schema 拦下（HTTP {bad}）",
              f"非法参数没被拦，返回 {bad} —— 会静默按别的值跑")

    print()
    print("=" * 70)
    for s in skipped:
        print(f"⚠ 跳过（不算通过）：{s}")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ 联网接口层通过" + ("（含上面标注的跳过项）" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
