"""
图片摄取端到端：快速通道 → 延后 OCR → 检索 → 近重复。

验证的核心主张：
  · 图片索引走快速通道（不含 OCR），速度要够快
  · OCR 作为延后阶段补跑，补完之后图里的文字能搜到
  · 同一张图的不同尺寸能被 pHash 认出来
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING)

from synorive.analyze.embedder import TextEmbedder  # noqa: E402
from synorive.ingest.pipeline import IngestPipeline  # noqa: E402
from synorive.search.engine import SearchEngine  # noqa: E402
from synorive.store.db import Database  # noqa: E402
from synorive.store.repository import Repository  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_DIR = ROOT / "data" / "models"
WORK = Path(tempfile.gettempdir()) / "synorive_imgpipe"
DB = WORK / "test.db"
FONT = r"C:\Windows\Fonts\simsun.ttc"

failures: list[str] = []


def make_corpus() -> Path:
    if WORK.exists():
        shutil.rmtree(WORK)
    corpus = WORK / "corpus"
    corpus.mkdir(parents=True)

    # ① 项目自己的图标：39 张，其中大量是同一张图的不同尺寸 → 天然的近重复语料
    icons = ROOT / "apps" / "desktop" / "resources" / "icons"
    n_icons = 0
    if icons.exists():
        for p in icons.glob("*.png"):
            shutil.copy2(p, corpus / p.name)
            n_icons += 1

    # ② 三张带不同中文内容的"截图"，用来验证 OCR 补跑后能不能搜到
    # ⚠️ 每行是一个完整短语。
    #    第一版按固定 16 字硬切，把「显示器支架」切成了「…显示器支」+「架…」，
    #    「张伟」切成了「张」+「伟」—— OCR 读到的本来就是两行，搜不到是**测试数据的错**，
    #    不是 OCR 或检索的错。造测试数据时把词切断，测出来的失败是假的。
    texts = {
        "会议纪要.png": ["季度预算评审会议纪要", "参会人 张伟 李娜", "决议通过"],
        "配置说明.png": ["数据库连接配置", "主机地址 端口", "超时时间 重试次数"],
        "购物清单.png": ["购物清单 咖啡豆", "打印纸 显示器支架", "合计 860 元"],
    }
    f = ImageFont.truetype(FONT, 32)
    for name, lines in texts.items():
        im = Image.new("RGB", (1200, 400), (250, 249, 246))
        d = ImageDraw.Draw(im)
        for i, line in enumerate(lines):
            d.text((50, 50 + i * 70), line, font=f, fill=(31, 41, 51))
        im.save(corpus / name)

    print(f"  语料：{n_icons} 张图标 + {len(texts)} 张中文截图 = {n_icons + len(texts)} 张")
    return corpus


def main() -> int:
    corpus = make_corpus()
    db = Database(DB)
    db.initialize()
    repo = Repository(db)
    pipe = IngestPipeline(repo, MODEL_DIR, concurrency=4)

    print("=" * 74)
    print("① 快速通道摄取（不含 OCR）")
    print("=" * 74)
    t0 = time.perf_counter()
    stats = pipe.ingest_paths([corpus])
    dt = time.perf_counter() - t0
    st = repo.stats()
    rate = stats.total / dt
    print(f"  {stats.total} 张 / {dt:.2f}s = {rate:.1f} 张/秒")
    print(f"  入库 {st['items']} 条（同内容重复的被指纹去重）")
    print("  注：这个数字含 4 个线程各加载一次 CLIP 模型的时间（每个约 0.3s）。")
    print("      17 张的小语料上启动开销占大头，稳态吞吐见 tests/test_image_analysis.py")
    print("      的独立基准（照片 4 线程 19.35 张/秒）。")
    if stats.failed:
        failures.append(f"有 {stats.failed} 张失败")
    # 只拦"慢到不正常"的情况。精确的吞吐由独立基准负责测，
    # 在这里写一个含冷启动的阈值只会得到一个随语料大小漂移的假指标。
    if rate < 2:
        failures.append(f"快速通道只有 {rate:.1f} 张/秒，慢到不正常")

    print()
    print("=" * 74)
    print("② 摄取完立刻搜 —— 此时 OCR 还没跑，图里的文字应该搜不到")
    print("=" * 74)
    emb = TextEmbedder(MODEL_DIR / "bge-small-zh-v1.5")
    se = SearchEngine(db, repo, emb)
    before = se.search("季度预算评审", limit=5)
    print(f"  搜「季度预算评审」→ {before['totalEstimate']} 条")
    for h in before["hits"][:3]:
        print(f"    {Path(h['item']['locator']).name}")

    print()
    print("=" * 74)
    print("③ 后台补跑 OCR")
    print("=" * 74)
    t0 = time.perf_counter()
    n = pipe.run_deferred_ocr(limit=100)
    dt2 = time.perf_counter() - t0
    print(f"  补跑 {n} 张 / {dt2:.1f}s = {n / max(dt2, 1e-6):.2f} 张/秒")
    if n == 0:
        failures.append("OCR 一张都没补跑")

    print()
    print("=" * 74)
    print("④ OCR 补完后再搜同样的词 —— 现在应该搜得到了")
    print("=" * 74)
    for q, want in (
        ("季度预算评审", "会议纪要.png"),
        ("数据库连接配置", "配置说明.png"),
        ("显示器支架", "购物清单.png"),
        ("张伟", "会议纪要.png"),
    ):
        r = se.search(q, limit=5)
        names = [Path(h["item"]["locator"]).name for h in r["hits"]]
        ok = want in names[:3]
        print(f"  {'✓' if ok else '✗'} 「{q}」→ {r['totalEstimate']} 条　前三：{names[:3]}")
        if not ok:
            failures.append(f"OCR 补完后搜「{q}」没命中 {want}")

    print()
    print("=" * 74)
    print("⑤ 近重复检测（同一张图的不同尺寸）")
    print("=" * 74)
    conn = db.connect()
    row = conn.execute(
        "SELECT id, locator, meta_json FROM items WHERE locator LIKE '%icon-512%' LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id, locator, meta_json FROM items WHERE modality='image' LIMIT 1"
        ).fetchone()
    if row is not None:
        import json as _json

        ph = _json.loads(str(row["meta_json"] or "{}")).get("phash")
        dups = repo.find_near_duplicates(ph or "", exclude_item=str(row["id"]))
        base = Path(str(row["locator"])).name
        print(f"  基准 {base}　pHash {ph}")
        print(f"  找到 {len(dups)} 张近重复：")
        for d in dups[:8]:
            it = repo.get_item(d)
            if it:
                print(f"    {Path(str(it['locator'])).name}")
        if not dups:
            failures.append("图标目录里那么多同图不同尺寸，一张近重复都没找到")

    print()
    print("=" * 74)
    print("⑥ 图片元数据是否入库")
    print("=" * 74)
    r = conn.execute(
        "SELECT COUNT(*) n FROM items WHERE modality='image' AND meta_json LIKE '%phash%'"
    ).fetchone()
    print(f"  带 pHash 的图片 {r['n']} 条")
    v = conn.execute("SELECT COUNT(*) n FROM vec_items").fetchone()
    print(f"  带图像向量的 {v['n']} 条")
    ocr_chunks = conn.execute("SELECT COUNT(*) n FROM chunks WHERE channel='ocr'").fetchone()
    print(f"  OCR 产生的分块 {ocr_chunks['n']} 条")
    if v["n"] == 0:
        failures.append("图像向量一条都没写进去")
    if ocr_chunks["n"] == 0:
        failures.append("OCR 分块一条都没写进去")

    print()
    print("=" * 74)
    if failures:
        for x in failures:
            print(f"✗ {x}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
