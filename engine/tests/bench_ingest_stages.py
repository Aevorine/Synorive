#!/usr/bin/env python
"""
A7 分段计时 —— 那缺失的 60% 到底花在哪
====================================================================
**为什么需要这个脚本**：A7 端到端实测 47 块/秒，而 `embedder.py` 自己的
实测注释写着同一颗 CPU 上 `intra=4 → 247 段/秒`、流水线里每 worker
`intra=1 → 110 段/秒`。**瓶颈不在嵌入模型。**

2026-08-03 已经排除了两处：
  · `write_chunks` 是单事务批量写，不是每块提交
  · ANN 插入改成批量（3.45 倍）后，它每块只占 0.34ms / 21ms = 1.6%

剩下的只能靠量。这个脚本把单文件入库拆成六段各自计时：
    fingerprint → parse → enrich → chunk → embed → write

🔴 **强嫌疑是 enrich**（C9 摘要 + C10 实体）。它夹在 extract 和 chunk 之间，
   对每个文件的**最多 12 万字**跑一遍摘要和实体抽取，而它对"能不能搜到"
   完全不是必需的 —— 它决定的是结果列表里显示哪一行。
   如果它真占大头，那 A7 的解法就不是"优化它"，而是**把它挪到后台**
   （和 OCR、语音转写一样：先让内容能搜到，摘要慢慢补）。
   —— 但这只是假设，**先量再改**。

🔴 **这个脚本不改数据库、不写正式库。** 它建一个临时 data-dir，跑完就能删。
   在用户的真实库上跑基准，最好的情况是污染统计，最坏的情况是写坏数据。

用法：
    python -m tests.bench_ingest_stages --dir <要跑的文件夹>
    python -m tests.bench_ingest_stages --dir engine/synorive --limit 60
    python -m tests.bench_ingest_stages --dir <dir> --json out.json

读数怎么看：
    占比最大的那一段就是 A7 的答案。如果 embed 只占三成上下，
    就证实了"瓶颈不在模型"这个判断，改的地方在别处。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STAGES = ("fingerprint", "parse", "enrich", "chunk", "embed", "write")


def _fmt_ms(v: float) -> str:
    return f"{v:8.1f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bench_ingest_stages",
        description="A7：把单文件入库拆成六段各自计时，找出那缺失的 60%",
    )
    ap.add_argument("--dir", type=Path, required=True, help="要跑的文件夹（只读，不会改动）")
    ap.add_argument("--limit", type=int, default=80, help="最多跑几个文件")
    ap.add_argument("--model-dir", type=Path, default=None,
                    help="向量模型目录。不给就用默认 data/models —— 模型缺失时 embed 段会显示 0")
    ap.add_argument("--json", type=Path, default=None, help="把结果另存一份 JSON")
    a = ap.parse_args(argv)

    from synorive.analyze.embedder import TextEmbedder
    from synorive.analyze.enrich import enrich
    from synorive.ingest.chunker import chunk_segments
    from synorive.ingest.parsers import CODE_EXT, ParseError, iter_supported, parse
    from synorive.ingest.pipeline import file_fingerprint
    from synorive.store.db import Database
    from synorive.store.repository import ChunkRow, Repository

    files = list(iter_supported(a.dir))[: a.limit]
    if not files:
        print(f"✗ {a.dir} 里没有能解析的文件")
        return 1

    # 🔴 临时库。绝不在用户的真实库上跑基准 ——
    #    最好的情况是污染统计，最坏的情况是写坏数据
    tmp = Path(tempfile.mkdtemp(prefix="synorive-bench-"))
    model_dir = a.model_dir or (Path.cwd() / "data" / "models")
    db = Database(tmp / "bench.db")
    db.initialize()
    repo = Repository(db)

    emb: Any = None
    try:
        # 🔴 `threads=1` 走构造参数，不是事后赋值 —— 它在 `load()` 里被读进
        #    SessionOptions，会话建好之后再改就没用了（而且不报错）。
        #    流水线里每 worker 正是 intra=1，不复现这个条件的话
        #    量出来的 embed 段比线上快，整个结论都会偏。
        e = TextEmbedder(model_dir, threads=1)
        e.load()
        # 判据是 `ready`（`_session is not None`），不是 `available` ——
        # 后者压根不存在，写错了会在这里 AttributeError 而不是优雅跳过
        if e.ready:
            emb = e
            print(f"  向量模型已加载：{e.model_id} @ {e.provider}，intra=1（复现流水线条件）")
            # 🔴 **向量表是按嵌入维度懒建的，不建就没有 vec_chunks。**
            #    真实流水线在 `_setup_ann_index` 那一步做这件事；
            #    脚本不做的话，一旦有了向量，write 段每个文件都抛
            #    `no such table: vec_chunks` —— 而第一次跑（没模型、
            #    embeddings=None）根本走不到那行，所以完全看不出来。
            db.ensure_vector_tables(e.dim, e.model_id)
    except Exception as ex:  # noqa: BLE001
        print(f"  （向量模型不可用，embed 段会是 0：{ex}）")

    totals: dict[str, float] = defaultdict(float)
    per_file: list[dict[str, Any]] = []
    chunk_total = 0
    ok = 0

    print(f"跑 {len(files)} 个文件，临时库 {tmp}")
    print()

    t_all = time.perf_counter()
    for i, p in enumerate(files, 1):
        row: dict[str, Any] = {"file": p.name}
        try:
            t = time.perf_counter()
            file_fingerprint(p)
            row["fingerprint"] = (time.perf_counter() - t) * 1000

            t = time.perf_counter()
            try:
                doc = parse(p)
            except ParseError:
                continue
            row["parse"] = (time.perf_counter() - t) * 1000
            if not doc.segments or doc.char_count == 0:
                continue

            t = time.perf_counter()
            enrich(doc.full_text[:120_000], is_code=p.suffix.lower() in CODE_EXT)
            row["enrich"] = (time.perf_counter() - t) * 1000

            t = time.perf_counter()
            chunks = chunk_segments(doc.segments)
            row["chunk"] = (time.perf_counter() - t) * 1000
            if not chunks:
                continue

            vectors = None
            t = time.perf_counter()
            if emb is not None:
                vectors = emb.encode([c.text for c in chunks])
            row["embed"] = (time.perf_counter() - t) * 1000

            item_id, _ = repo.upsert_item(
                fingerprint=file_fingerprint(p), modality="text", source="folder",
                title=p.stem, locator=str(p), status="analyzing",
            )
            rows = [
                ChunkRow(text=c.text, channel="text", index=n, token_count=len(c.text))
                for n, c in enumerate(chunks)
            ]
            t = time.perf_counter()
            repo.write_chunks(item_id, rows, vectors)
            row["write"] = (time.perf_counter() - t) * 1000

            row["chunks"] = len(chunks)
            chunk_total += len(chunks)
            ok += 1
            for s in STAGES:
                totals[s] += row.get(s, 0.0)
            per_file.append(row)
        except Exception as ex:  # noqa: BLE001
            print(f"  ! {p.name}: {ex}")
        if i % 20 == 0:
            print(f"  …{i}/{len(files)}")

    wall = time.perf_counter() - t_all
    if ok == 0:
        print("✗ 一个文件都没跑成")
        return 1

    grand = sum(totals.values())
    print()
    print("=" * 62)
    print(f"跑成 {ok} 个文件 / {chunk_total} 块 ｜ 墙钟 {wall:.1f}s")
    print(f"端到端吞吐 {chunk_total / wall:.1f} 块/秒   （A7 目标见 metrics.py，原记录 47）")
    print("=" * 62)
    print(f"{'阶段':<14}{'总计 ms':>10}{'占比':>9}{'每块 ms':>10}")
    for s in STAGES:
        pct = totals[s] / grand * 100 if grand else 0
        print(f"{s:<14}{_fmt_ms(totals[s]):>10}{pct:>8.1f}%{totals[s] / chunk_total:>10.2f}")
    print("-" * 62)
    print(f"{'合计':<14}{_fmt_ms(grand):>10}{100.0:>8.1f}%{grand / chunk_total:>10.2f}")
    print()

    top = max(STAGES, key=lambda s: totals[s])
    print(f"→ 占比最大的是 **{top}**（{totals[top] / grand * 100:.1f}%）。")
    if top == "enrich":
        print("  这印证了那个假设：enrich（C9 摘要 + C10 实体）对'能不能搜到'不是必需的，")
        print("  它决定的只是结果列表显示哪一行。**解法是把它挪到后台**（像 OCR 和转写那样），")
        print("  而不是去优化它 —— 先让内容能搜到，摘要慢慢补。")
    elif top == "embed":
        print("  那么'瓶颈不在模型'这个判断是错的，A7 得回到模型侧（量化 / GPU）。")
    else:
        print(f"  改的地方在 {top}，不是嵌入模型 —— 和 embedder.py 的实测注释一致。")

    print()
    print(f"墙钟 {wall:.1f}s vs 分段合计 {grand / 1000:.1f}s，")
    print("  差值是没被计时的部分（upsert_item / set_stage / 进程调度）。")
    print("  🔴 **差值很大的话，说明瓶颈在这个脚本没覆盖到的地方**，别急着下结论。")

    if a.json:
        a.json.write_text(
            json.dumps({
                "files": ok, "chunks": chunk_total, "wallSec": round(wall, 2),
                "chunksPerSec": round(chunk_total / wall, 1),
                "stageTotalsMs": {s: round(totals[s], 1) for s in STAGES},
                "stagePct": {s: round(totals[s] / grand * 100, 1) for s in STAGES},
                "medianPerFileMs": {
                    s: round(statistics.median([r.get(s, 0.0) for r in per_file]), 2)
                    for s in STAGES
                },
                "note": "临时库跑的，不碰真实数据；embed 用 intra=1 复现流水线里的条件",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n已存 {a.json}")

    print(f"\n临时库还在 {tmp}，看完可以删。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
