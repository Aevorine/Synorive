#!/usr/bin/env python
"""
C4 ANN 索引自动落盘 —— 单元级
====================================================================
不起引擎、不加载模型，直接构造 AnnIndex 打假向量。整个测试几秒钟。

── 为什么这几条必须测 ──────────────────────────────────────
在这个改动之前，索引**只在引擎干净关闭时落盘**。一次强杀 / 断电 /
装更新被结束进程，上次存盘之后新增的向量就全丢了。丢了之后的表现极隐蔽：
`vec_chunks` 一条不少、搜索不报错，**只是那批内容在语义检索里查不到**。

所以要验四件事，每一条都对应一种会静默出事的写法：

  ① 攒够阈值真的会落盘 —— 不落的话这个改动等于没做
  ② **没攒够就别落** —— 每写一条存一次，会把大批摄取拖慢一个数量级
  ③ **落盘失败不能清账** —— 乐观清零的话，一次失败会让计数归零，
     要再攒满一轮才重试，中间那批"以为存过了"的向量其实一直没落盘
  ④ **落盘出异常不能把调用方带崩** —— 它是个优化，失败的后果应该是
     "退回只在关闭时存"，而不是让正在跑的摄取整批失败

用法：python -m tests.test_ann_autosave
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

problems: list[str] = []
skipped: list[str] = []


def check(cond: bool, ok: str, bad: str) -> None:
    print(f"  {'✓' if cond else '✗'} {ok if cond else bad}")
    if not cond:
        problems.append(bad)


def main() -> int:
    try:
        import numpy as np
    except ImportError:
        print("✗ 没装 numpy")
        return 1
    try:
        from synorive.search import ann_index as mod
        from synorive.search.ann_index import AUTOSAVE_EVERY, AnnIndex
    except ImportError as e:
        print(f"⚠ 跳过（不算通过）：usearch 没装，ANN 功能整体不可用：{e}")
        return 0

    tmp = Path(os.environ.get("TMP", "/tmp")) / "syn-ann-autosave"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    DIM = 8
    line = "─" * 70

    def vecs(n: int) -> np.ndarray:
        # 固定种子，测试不能靠随机数决定通不通过
        rng = np.random.default_rng(1234)
        v = rng.standard_normal((n, DIM)).astype("float32")
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def wait_saved(p: Path, timeout: float = 10.0) -> bool:
        """落盘在后台线程里做，要等。**不能用固定 sleep** —— 机器慢的时候
        固定 sleep 会假阴性，机器快的时候又白等。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if p.exists() and p.stat().st_size > 0:
                return True
            time.sleep(0.05)
        return False

    # ── ① 没攒够阈值不落盘 ──────────────────────────────────
    print(line)
    print(f"① 没攒够 {AUTOSAVE_EVERY} 条时不该落盘（否则大批摄取会被落盘拖垮）")
    print(line)
    p1 = tmp / "a.usearch"
    idx = AnnIndex(dim=DIM, model_tag="t:8", index_path=p1)
    n_small = min(100, AUTOSAVE_EVERY - 1)
    idx.add_many(list(range(1, n_small + 1)), vecs(n_small))
    time.sleep(0.4)  # 给后台线程一个真的会跑的机会，然后确认它没跑
    check(not p1.exists(),
          f"加了 {n_small} 条（< {AUTOSAVE_EVERY}）没落盘",
          f"只加了 {n_small} 条就落盘了 —— 阈值没起作用，摄取会被拖慢")

    # ── ② 攒够阈值真的落盘 ──────────────────────────────────
    print()
    print(line)
    print(f"② 攒够 {AUTOSAVE_EVERY} 条要在后台落盘")
    print(line)
    rest = AUTOSAVE_EVERY - n_small + 10
    idx.add_many(list(range(n_small + 1, n_small + rest + 1)), vecs(rest))
    saved = wait_saved(p1)
    check(saved, f"落盘了，文件 {p1.stat().st_size if p1.exists() else 0} 字节",
          "攒够阈值之后没落盘 —— 这个改动等于没做")
    meta = p1.with_suffix(".meta")
    check(meta.exists() and meta.read_text(encoding="utf-8").strip() == "t:8",
          "同时写了 .meta（模型标签），重启才认得出这份索引",
          ".meta 没写或内容不对 —— 下次启动会判定模型变了而丢弃整份索引")

    # ── ③ 落盘之后能原样读回来 ──────────────────────────────
    print()
    print(line)
    print("③ 落盘的索引要能读回来，且条数对得上（不然存了也白存）")
    print(line)
    before = idx.size
    idx2 = AnnIndex(dim=DIM, model_tag="t:8", index_path=p1)
    loaded = idx2.load()
    check(loaded and idx2.size == before,
          f"读回 {idx2.size} 条，和存之前的 {before} 条一致",
          f"读回来是 {idx2.size} 条，存之前是 {before} 条 —— 对不上")

    # ── ④ 落盘失败：不清账、不把调用方带崩 ──────────────────
    print()
    print(line)
    print("④ 落盘失败时：不能清账，也不能把调用方带崩")
    print(line)
    p2 = tmp / "b.usearch"
    idx3 = AnnIndex(dim=DIM, model_tag="t:8", index_path=p2)
    idx3.add_many(list(range(1, 51)), vecs(50))

    boom_calls = {"n": 0}
    real_save = AnnIndex.save

    def boom(self: AnnIndex) -> None:
        boom_calls["n"] += 1
        raise OSError("磁盘满了（测试注入）")

    AnnIndex.save = boom  # type: ignore[method-assign]
    try:
        # 直接把 _dirty 顶到阈值，触发一次注定失败的落盘
        idx3._dirty = AUTOSAVE_EVERY
        crashed = False
        try:
            idx3.add_many([9001], vecs(1))
        except Exception as e:  # noqa: BLE001
            crashed = True
            problems.append(f"落盘失败把 add_many 带崩了：{e}")
        check(not crashed,
              "落盘抛异常时 add_many 照常返回（它只是个优化，不该拖垮摄取）",
              "落盘失败让 add_many 抛出来了")

        for _ in range(60):
            if boom_calls["n"] > 0 and not idx3._saving:
                break
            time.sleep(0.05)
        check(boom_calls["n"] > 0, "确实尝试过落盘（注入的异常被触发了）",
              "根本没调到 save —— 这一条什么都没验到")
        check(idx3._dirty >= AUTOSAVE_EVERY,
              f"失败之后 _dirty 保持在 {idx3._dirty}，下一批改动会再试",
              f"失败之后 _dirty 被清成 {idx3._dirty} —— "
              "那一大批向量会以为已经存过了，其实一直没落盘")
        check(idx3._saving is False,
              "_saving 闸已释放，不会卡死后续的自动落盘",
              "_saving 卡在 True —— 之后永远不会再自动落盘了")
    finally:
        AnnIndex.save = real_save  # type: ignore[method-assign]

    # ── ⑤ 还原之后要真的能存（确认上面的猴子补丁收干净了）──
    print()
    print(line)
    print("⑤ 还原之后要真的能存 —— 确认第④步的猴子补丁没留在原地")
    print(line)
    idx3._dirty = AUTOSAVE_EVERY
    idx3.add_many([9002], vecs(1))
    check(wait_saved(p2), "还原后落盘成功",
          "还原后依然存不进去 —— 猴子补丁没收干净，后面所有测试都不可信")

    print()
    print("=" * 70)
    for sk in skipped:
        print(f"⚠ 跳过（不算通过）：{sk}")
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ C4 ANN 自动落盘通过（阈值生效 / 能读回 / 失败不清账不崩 / 闸能释放）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
