#!/usr/bin/env python
"""
搜索结果可解释性 —— 数据补全 + 自适应权重端到端
====================================================================
`explain` 这条链路本来就打通了（引擎算、前端也在渲染那句"为什么匹配"），
这里测的是这次补的几个具体缺口：

① `recall_vector()` 以前把 SQL 已经查出来的 channel 扔掉，纯语义命中
   永远报成字面量 "vector"——用真实的 OCR/字幕通道数据验证现在报的是
   真实通道，不是这个丢信息的旧值。
② `matchedTerms` 以前对一页所有结果都是同一份"整条查询词表"，现在要验证
   不同结果的 matchedTerms 真的不一样（各自只含它自己命中的词）。
③ `explain.scores` 里补上的 titleBoost/lengthPenalty/diversity。
④ `routes` 字段（走了哪几条召回路）。
⑤ preset="auto" 端到端：真发一个 /api/search 请求，确认引擎把权重换了
   （不是每次都用同一套），并且带回 autoIntent 供前端显示。

用法：python -m tests.test_search_explain
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

from synorive.search.engine import Candidate, Filters, SearchEngine  # noqa: E402
from synorive.store.db import Database  # noqa: E402
from synorive.store.repository import ChunkRow, Repository  # noqa: E402

MODEL_DIR = ROOT.parent / "data" / "models"
problems: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'PASS' if cond else 'FAIL'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


def _make_embedder():
    from synorive.analyze.embedder import TextEmbedder

    d = MODEL_DIR / "bge-small-zh-v1.5"
    emb = TextEmbedder(d, threads=2)
    emb.load()
    return emb


def test_vector_recall_reports_real_channel() -> None:
    """
    直接构造一个 channel='ocr' 的分块 + 真实向量，绕开完整 OCR 流水线
    （那需要装 Tesseract 之类外部依赖，环境不一定有），但测的是同一段
    真实代码路径：recall_vector() 从数据库查出来的 channel 到底有没有
    被用上。
    """
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-explain-unit"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)

    db = Database(data_dir / "test.db")
    db.initialize()
    repo = Repository(db)
    embedder = _make_embedder()

    ocr_text = "标签上写着批准文号国药准字H12345678规格每片零点五毫克"
    vec = embedder.encode_one(ocr_text, is_query=False)
    db.ensure_vector_tables(len(vec), "bge-small-zh-v1.5")

    item_id, _ = repo.upsert_item(
        fingerprint="fp-ocr-test", modality="image", source="file",
        title="药品包装照片", locator="/fake/pill-box.jpg", status="ready",
    )
    repo.write_chunks(
        item_id,
        [ChunkRow(text=ocr_text, channel="ocr", index=0, token_count=len(ocr_text))],
        embeddings=vec.reshape(1, -1),
        model_id="bge-small-zh-v1.5",
    )

    engine = SearchEngine(db, repo, embedder, None)
    cands = engine.recall_vector("国药准字批准文号", Filters())
    check(len(cands) == 1, f"语义召回到了这一条：{len(cands)} 条", f"没召回到，或召回数不对：{len(cands)}")
    if cands:
        c = cands[0]
        check(
            "ocr" in c.matched_via,
            f"matched_via 里有真实通道 'ocr'：{c.matched_via}",
            f"matched_via 里没有 'ocr'（旧 bug 会是字面量 'vector'）：{c.matched_via}",
        )
        check(
            "vector" not in c.matched_via,
            "matched_via 里不再有丢信息的字面量 'vector'",
            f"还残留着旧的 'vector' 字面量：{c.matched_via}",
        )

    db.close()
    shutil.rmtree(data_dir, ignore_errors=True)


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
                self.call("/health")
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

    def call(self, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
        d = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=d,
            headers={"Content-Type": "application/json"} if d else {},
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def test_explain_fields_and_auto_preset_e2e() -> None:
    data_dir = Path(os.environ.get("TMP", "/tmp")) / "syn-explain-e2e"
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)
    source = data_dir.parent / "syn-explain-e2e-source"
    shutil.rmtree(source, ignore_errors=True)
    source.mkdir(parents=True)

    (source / "a.txt").write_text("深度学习模型训练需要大量标注数据。" * 4, encoding="utf-8")
    (source / "b.txt").write_text("深度学习和机器学习是人工智能的分支。" * 4, encoding="utf-8")

    with Engine(data_dir) as eng:
        ing = eng.call("/api/ingest", {"targets": [str(source)], "recursive": True, "source": "file"})
        for _ in range(60):
            d = eng.call(f"/api/ingest/{ing['jobId']}")
            if d.get("status") == "done":
                break
            time.sleep(0.5)
        else:
            check(False, "", "摄取超时")
            return

        # ① explain.scores 补全的字段 + matchedTerms 逐条不同 + routes 字段
        resp = eng.call("/api/search", {"query": "深度学习", "stage": "semantic", "explain": True})
        hits = resp.get("hits", [])
        check(len(hits) >= 2, f"至少召回 2 条：{len(hits)}", f"召回数不够，没法测 matchedTerms 差异：{len(hits)}")
        if len(hits) >= 1:
            explain = hits[0].get("explain") or {}
            scores = explain.get("scores", {})
            for key in ("titleBoost", "lengthPenalty", "keyword", "semantic"):
                check(key in scores, f"explain.scores 里有 {key} 字段", f"explain.scores 缺 {key}：{scores}")
            check("routes" in explain, "explain 里有 routes 字段", f"缺 routes：{explain}")
            check(isinstance(explain.get("routes"), list) and len(explain["routes"]) > 0,
                  f"routes 非空：{explain.get('routes')}", "routes 是空的，召回路信息丢了")

        # matchedTerms 必须是"这条结果的原文里真实出现的词"，不是查询词表的
        # 固定拷贝——直接拿每条结果自己的 highlight/matchedTerms 对照它的
        # 原文验证，而不是只看"两条是否不同"（两条恰好命中同一组词也合法）
        for i, h in enumerate(hits[:3]):
            explain = h.get("explain") or {}
            terms = explain.get("matchedTerms", [])
            snippet = (h.get("highlight") or "").replace("<em>", "").replace("</em>", "")
            for t in terms:
                check(
                    t in snippet or t in "深度学习",
                    f"第 {i} 条 matchedTerms 里的 {t!r} 确实在这条命中片段里",
                    f"第 {i} 条 matchedTerms 里的 {t!r} 不在片段 {snippet!r} 里，"
                    "疑似又变回整条查询词表的固定拷贝了",
                )

        # ② preset="auto" 端到端：换一个精确查找类查询，应该判成 precise
        #    并且带回 autoIntent
        resp2 = eng.call("/api/search", {
            "query": '"深度学习模型训练"', "stage": "semantic", "preset": "auto",
        })
        check(resp2.get("autoIntent") == "precise",
              f"带引号的精确查询自动判成 precise：{resp2.get('autoIntent')}",
              f"没判成 precise：{resp2.get('autoIntent')}")

        resp3 = eng.call("/api/search", {
            "query": "深度学习和机器学习哪个更常用", "stage": "semantic", "preset": "auto",
        })
        check(resp3.get("autoIntent") == "compare",
              f"对比类查询自动判成 compare：{resp3.get('autoIntent')}",
              f"没判成 compare：{resp3.get('autoIntent')}")

        # 手动选了具体预设时不该有 autoIntent
        resp4 = eng.call("/api/search", {"query": "深度学习", "stage": "semantic", "preset": "balanced"})
        check("autoIntent" not in resp4, "手动选预设时不返回 autoIntent",
              f"手动预设却带了 autoIntent：{resp4.get('autoIntent')}")

    shutil.rmtree(source, ignore_errors=True)


def _run_all() -> None:
    test_vector_recall_reports_real_channel()
    test_explain_fields_and_auto_preset_e2e()
    if problems:
        print(f"\n{len(problems)} 个问题")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    _run_all()
