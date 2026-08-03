#!/usr/bin/env python
"""
L3 PDF 分节 —— 解析器单测 + 端到端（真下一篇 arXiv 论文验证）
====================================================================
分两层验证，理由和这个项目一贯的做法一样——合成用例负责"判据本身对不对"，
真实数据负责"判据在真论文的排版噪声下还立不立得住"：

  ① 合成 PDF：自己拼一篇结构清楚的假论文（PyMuPDF 能直接造 PDF），
     断言标题被切对了、正文没有被误判、编号前缀能被正确剥掉
  ② 真实 arXiv PDF：现学 L1 已经打通的下载能力抓一篇真论文，
     跑完整的 parse → chunk → 入库 → 检索 → 命中带 section 字段这条链路。
     真论文的排版远比合成用例乱（页眉页脚、双栏、图表穿插），
     这一步验的是"判据保守到不会被这些噪声带偏"。

用法：python -m tests.test_pdf_sections
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

problems: list[str] = []
skipped: list[str] = []


def check(cond: bool, ok: str, bad: str) -> bool:
    print(f"  {'✓' if cond else '✗'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)
    return cond


def make_synthetic_pdf(path: Path) -> None:
    """
    拼一篇结构清楚的假论文：标题、五个标准章节、一段"看起来像标题但不是"的正文
    （用来验证不会误判），一个带编号前缀的标题（"3. Results"）。
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    y = 72
    lines = [
        ("A Study of Synthetic Test Documents", 16),
        ("", 11),
        ("Abstract", 13),
        ("This paper studies nothing in particular. It is a synthetic fixture.", 11),
        ("", 11),
        ("1. Introduction", 13),
        ("Introduction text goes here and talks about background context.", 11),
        ("We should note that Methods vary widely across the literature.", 11),
        ("", 11),
        ("2. Method", 13),
        ("We used a simple approach involving three steps described below.", 11),
        ("", 11),
        ("3. Results", 13),
        ("The results show that everything worked as expected in all cases.", 11),
        ("", 11),
        ("References", 13),
        ("[1] Someone. A Paper About Methods and Results. 2020.", 11),
    ]
    for text, size in lines:
        if text:
            page.insert_text((72, y), text, fontsize=size)
        y += size + 6
    doc.save(str(path))
    doc.close()


def test_synthetic() -> None:
    print("─" * 70)
    print("① 合成 PDF —— 章节切分判据本身对不对")
    print("─" * 70)
    from synorive.ingest.parsers import parse

    tmp = Path(os.environ.get("TMP", "/tmp")) / "syn-pdf-section-test"
    tmp.mkdir(parents=True, exist_ok=True)
    pdf_path = tmp / "fake_paper.pdf"
    make_synthetic_pdf(pdf_path)

    doc = parse(pdf_path)
    sections_seen = [s.section for s in doc.segments]
    print(f"  切出 {len(doc.segments)} 段，章节序列：{sections_seen}")

    check("Abstract" in sections_seen, "识别出 Abstract 段", f"没识别出 Abstract：{sections_seen}")
    check("Introduction" in sections_seen, "识别出 Introduction 段", f"没识别出 Introduction：{sections_seen}")
    check("Method" in sections_seen, "识别出 Method 段（编号前缀「2.」被正确剥掉）",
          f"没识别出 Method：{sections_seen}")
    check("Results" in sections_seen, "识别出 Results 段（编号前缀「3.」被正确剥掉）",
          f"没识别出 Results：{sections_seen}")
    check("References" in sections_seen, "识别出 References 段", f"没识别出 References：{sections_seen}")

    # 反面：正文里那句"We should note that Methods vary..."不能被误判成新章节标题——
    # 它是一整句话不是单独一行的"Method"，整行匹配的判据应该挡住它
    intro_seg = next((s for s in doc.segments if s.section == "Introduction"), None)
    check(
        intro_seg is not None and "Methods vary widely" in intro_seg.text,
        "正文里提到「Methods」的句子没有被误判成新章节的开始（还留在 Introduction 段里）",
        "正文一句话被误判成了新章节标题——判据太松了",
    )

    # References 里的那条引用标题里也带着"Methods"和"Results"，同样不能被误判
    ref_seg = next((s for s in doc.segments if s.section == "References"), None)
    check(
        ref_seg is not None and "Someone" in ref_seg.text,
        "参考文献里题目带 Methods/Results 字样的条目没有被拆成新章节",
        "参考文献条目被误判成了章节标题",
    )


def test_migration() -> None:
    print()
    print("─" * 70)
    print("② 数据库迁移 —— 老库（没有 section 列）升级后不能报错")
    print("─" * 70)
    import sqlite3

    from synorive.store.db import Database, _COLUMN_MIGRATIONS, _migrate_columns

    tmp = Path(os.environ.get("TMP", "/tmp")) / "syn-pdf-migration-test"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    db_path = tmp / "old.db"

    # 手搓一个"老版本"的 chunks 表——没有 section 列，模拟真实存在的旧库
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY, item_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'body',
            page INTEGER, start_sec REAL, end_sec REAL, bbox_json TEXT, token_count INTEGER
        )
    """)
    conn.execute("INSERT INTO chunks VALUES ('c1','i1',0,'旧数据','body',1,NULL,NULL,NULL,10)")
    conn.commit()
    conn.close()

    cols_before = set()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
    check("section" not in cols_before, "构造出的老库确实没有 section 列（前提条件成立）",
          "测试前提就不对，老库不该有这列")

    _migrate_columns(conn)
    conn.commit()
    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
    check("section" in cols_after, "迁移后 section 列出现了", "迁移没有加上 section 列")

    old_row = conn.execute("SELECT * FROM chunks WHERE id='c1'").fetchone()
    check(old_row["text"] == "旧数据" and old_row["section"] is None,
          "老数据完好无损，新列的值是 NULL 不是报错", "老数据在迁移中丢了或变了")

    # 迁移函数必须是幂等的——再跑一遍不能因为"列已存在"而报 ALTER TABLE 错
    try:
        _migrate_columns(conn)
        idempotent = True
    except sqlite3.OperationalError as e:
        idempotent = False
        print(f"    异常：{e}")
    check(idempotent, "迁移函数重复执行不报错（幂等）", "迁移函数不是幂等的，重复跑会报错")
    conn.close()

    # 再走一遍真实的 Database.initialize()，确认整条路径（含新库）也不出问题
    real_db = Database(tmp / "fresh.db")
    real_db.initialize()
    conn2 = real_db.connect()
    cols_fresh = {r["name"] for r in conn2.execute("PRAGMA table_info(chunks)")}
    check("section" in cols_fresh, "全新数据库的 chunks 表也带 section 列（CREATE TABLE 那条也对）",
          "全新数据库没有 section 列")
    real_db.close()


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
        model_dir = ROOT.parent / "data" / "models"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "synorive.main", "--port", str(self.port),
             "--data-dir", str(self.data_dir), "--model-dir", str(model_dir),
             "--allow-cloud"],
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
                    raise RuntimeError(f"引擎退出了：\n{err[-2000:]}") from None
                time.sleep(1)
        raise RuntimeError("引擎没起来")

    def __exit__(self, *a: object) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def call(self, path: str, payload: dict | None = None, timeout: float = 60) -> dict:
        d = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=d,
            headers={"Content-Type": "application/json"} if d else {},
        )
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())


def test_real_paper() -> None:
    print()
    print("─" * 70)
    print("③ 端到端：真下一篇 arXiv 论文 → 索引 → 检索 → 命中带 section")
    print("─" * 70)
    import httpx

    # 找一篇有清晰 Method/Results 结构的经典论文；用 arXiv 的固定 ID 而不是
    # 现搜，这样测试不受当天搜索结果排序变化影响
    pdf_url = "https://arxiv.org/pdf/1706.03762"  # Attention Is All You Need
    tmp = Path(os.environ.get("TMP", "/tmp")) / "syn-pdf-real-test"
    shutil.rmtree(tmp, ignore_errors=True)
    corpus = tmp / "corpus"
    corpus.mkdir(parents=True)
    pdf_path = corpus / "attention_is_all_you_need.pdf"

    try:
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            r = client.get(pdf_url)
        if r.status_code != 200 or len(r.content) < 10_000:
            skipped.append(f"下载真实论文失败（HTTP {r.status_code}），③ 全部跳过，不计入通过")
            print(f"  ⚠ 下载失败：HTTP {r.status_code}，跳过这一节")
            return
        pdf_path.write_bytes(r.content)
        print(f"  下载成功：{len(r.content) / 1024:.0f} KB")
    except httpx.HTTPError as e:
        skipped.append(f"下载真实论文异常（{e}），③ 全部跳过，不计入通过")
        print(f"  ⚠ 下载异常：{e}，跳过这一节")
        return

    # 先在解析层直接验证（不用起引擎也能看结构对不对）
    from synorive.ingest.parsers import parse

    doc = parse(pdf_path)
    sections_found = sorted({s.section for s in doc.segments if s.section})
    print(f"  真实论文里识别出的章节：{sections_found}")
    check(len(sections_found) >= 2,
          f"真实论文里识别出 {len(sections_found)} 种章节（有噪声也切出来了）",
          f"真实论文几乎没切出章节：{sections_found} —— 判据可能太严了")

    with Engine(tmp / "data") as eng:
        ingest = eng.call("/api/ingest", {"targets": [str(corpus)], "source": "file", "recursive": True})
        for _ in range(120):
            s = eng.call("/api/stats")
            if s.get("ready", 0) >= 1:
                break
            time.sleep(1)
        else:
            problems.append("真实论文索引超时没跑完")
            return

        res = eng.call("/api/search", {"query": "self-attention mechanism", "limit": 10, "stage": "semantic"})
        hits = res.get("hits") or []
        check(bool(hits), f"检索「self-attention mechanism」拿到 {len(hits)} 条", "检索没结果")

        with_section = [h for h in hits if (h.get("location") or {}).get("section")]
        check(bool(with_section),
              f"{len(with_section)}/{len(hits)} 条命中带着 section 字段",
              "命中结果里一条都没有 section 字段 —— 说明分节信息在检索链路上丢了")
        if with_section:
            top = with_section[0]
            print(f"    示例：「{top['item']['title'][:30]}」"
                  f" 第 {top['location'].get('page')} 页 · {top['location']['section']}")


def main() -> int:
    test_synthetic()
    test_migration()
    test_real_paper()

    print()
    print("=" * 70)
    for s in skipped:
        print(f"⚠ 跳过（不算通过）：{s}")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ L3 PDF 分节通过" + ("（含上面标注的跳过项）" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
