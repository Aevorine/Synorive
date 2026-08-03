"""
引擎健康记分与自动排班 —— S1
====================================================================
**要治的病**：现在派哪几家引擎出去，是一份写死的名单。而实测（2026-08-02，
23 条通道逐个实调）已经证明这份名单**每隔一段时间就会过期一次** ——
Google 强制了 JS、DuckDuckGo 的 html 端点改成 JS 落地页、SearXNG 公共实例
集体封代理 IP。写死名单意味着每次都要人去改代码。

所以这一层做的是：**记住每家最近的表现，下一轮自己决定派谁**。

三个设计决定：

① **记分只看最近 N 次，不看历史总量**
   引擎的死活是会变的 —— 一家昨天被限流今天恢复正常，用一个累计成功率
   去评价它，它要几百次才爬得回来。滑动窗口（默认 30 次）忘得掉旧账。

② **`challenged` 只轻扣，`broken` 才重扣**
   沿用 `engines.py` 那套四态区分：被限流是"稍后再来"，解析坏了才是"这家废了"。
   把两者按同样力度扣分，会把百度这种"偶尔弹验证码但平时最好用"的引擎排到最后。

③ **永远留一个探索位**
   纯按分数排会让暂时失败的引擎**永远没机会翻身**（分低 → 不派 → 没有新数据 →
   分永远低）。所以每轮固定挑一个"最久没试过的"塞进阵容。代价是每轮多花一个
   引擎的时间，换来的是名单能自动恢复，不用人工干预。

状态落盘到 `data/websearch-health.json`，引擎重启后接着用 —— 不落盘的话
每次重启都要重新学一遍，而学习期正好是用户最需要它靠谱的时候。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .engines import BaseEngine, ParseOutcome

log = logging.getLogger("synorive.websearch")

#: 滑动窗口大小。30 次足够反映"最近的状态"，又不至于被一次抖动带偏
WINDOW = 30

#: 一轮默认派几家。超过这个数收益递减而延迟线性涨 ——
#: 实测三家引擎对同一查询的结果集本来就几乎不相交（台账坑 43），
#: 但第 6 家开始新增的独立站点数已经很少
DEFAULT_LINEUP = 5

#: 分数下限：低于这个值的引擎不会被常规选中，只能靠探索位复活
FLOOR = 0.08

#: 延迟惩罚的参考值（毫秒）。比这快的不扣分，慢的按比例扣
LATENCY_REF_MS = 2500.0


@dataclass
class EngineStat:
    """一家引擎的滑动统计。"""

    #: 最近 WINDOW 次的结果，每项是 ParseOutcome 的值
    recent: list[str] = field(default_factory=list)
    #: 延迟的指数滑动平均（毫秒）。None = 还没成功过
    ewma_ms: float | None = None
    #: 最近一次被派出去的时间（time.time()），探索位据此挑"最久没试的"
    last_tried: float = 0.0
    #: 累计计数，只用于展示，不参与排班
    total: int = 0
    total_ok: int = 0

    def record(self, outcome: ParseOutcome, elapsed_ms: int) -> None:
        self.recent.append(outcome.value)
        if len(self.recent) > WINDOW:
            del self.recent[: len(self.recent) - WINDOW]
        self.last_tried = time.time()
        self.total += 1
        if outcome is ParseOutcome.OK:
            self.total_ok += 1
            # 只用成功的那几次更新延迟 —— 失败往往是超时，
            # 把 8000ms 的超时算进平均延迟，会让一家"平时 800ms、
            # 偶尔超时"的引擎看起来比它实际慢好几倍
            self.ewma_ms = (
                float(elapsed_ms) if self.ewma_ms is None
                else 0.7 * self.ewma_ms + 0.3 * float(elapsed_ms)
            )

    @property
    def score(self) -> float:
        """
        0~1 的健康分。没有数据时给 0.6 —— 略高于中位，
        让**没试过的引擎有机会被选中**（冷启动时所有引擎都是这个分，
        排班退化成按注册顺序，正是我们想要的默认行为）。
        """
        if not self.recent:
            return 0.6
        w = {
            ParseOutcome.OK.value: 1.0,
            # 有结果页但确实没结果 —— 引擎本身是好的，只是这个词冷门。
            # 给 0.8 而不是 1.0，是因为一家老返回空的引擎确实不如老有结果的有用
            ParseOutcome.EMPTY.value: 0.8,
            # 被限流：这家没坏，慢一点就好。轻扣
            ParseOutcome.CHALLENGED.value: 0.35,
            # 解析坏了 / 超时：真出问题了
            ParseOutcome.BROKEN.value: 0.0,
        }
        base = sum(w.get(o, 0.0) for o in self.recent) / len(self.recent)

        # 延迟惩罚：最多扣 25%。慢不是致命问题，拿不到结果才是
        if self.ewma_ms and self.ewma_ms > LATENCY_REF_MS:
            over = min(2.0, self.ewma_ms / LATENCY_REF_MS - 1.0)
            base *= 1.0 - 0.125 * over
        return max(0.0, min(1.0, base))

    def to_dict(self) -> dict[str, Any]:
        n = len(self.recent)
        return {
            "score": round(self.score, 3),
            "samples": n,
            "okRate": round(
                sum(1 for o in self.recent if o == ParseOutcome.OK.value) / n, 3
            ) if n else None,
            "avgMs": int(self.ewma_ms) if self.ewma_ms else None,
            "lastTried": int(self.last_tried) or None,
            "total": self.total,
            "totalOk": self.total_ok,
            "recent": self.recent[-10:],
        }


class EngineScheduler:
    """
    引擎排班器。一个实例常驻在 `MetaSearch` 里。

    对外只有三件事：`observe()` 喂结果、`lineup()` 要阵容、`table()` 给界面看。
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self.stats: dict[str, EngineStat] = {}
        self._path = state_path
        self._dirty = False
        self._load()

    # ── 记分 ────────────────────────────────────────────────
    def observe(self, engine_id: str, outcome: ParseOutcome, elapsed_ms: int) -> None:
        st = self.stats.setdefault(engine_id, EngineStat())
        st.record(outcome, elapsed_ms)
        self._dirty = True

    def score_of(self, engine_id: str) -> float:
        return self.stats.get(engine_id, EngineStat()).score

    # ── 排班 ────────────────────────────────────────────────
    def lineup(
        self,
        candidates: list[BaseEngine],
        *,
        size: int = DEFAULT_LINEUP,
        explore: bool = True,
    ) -> tuple[list[BaseEngine], list[tuple[str, str]]]:
        """
        从候选里挑出这一轮要派的阵容。

        返回 `(选中的, [(被跳过的id, 原因)])` —— **跳过的也要交回去**，
        沿用 `_pick` 那条教训：静默丢弃一个引擎，用户看到的是"结果少了"
        而不是"这家今天状态不好没派它去"，两者的处置完全不同。
        """
        if not candidates:
            return [], []
        if size <= 0 or size >= len(candidates):
            return list(candidates), []

        ranked = sorted(
            candidates,
            key=lambda e: (-self.score_of(e.id), e.id),
        )
        picked: list[BaseEngine] = []
        skipped: list[tuple[str, str]] = []

        for e in ranked:
            if len(picked) >= size:
                break
            if self.score_of(e.id) < FLOOR and self.stats.get(e.id, EngineStat()).recent:
                continue  # 留给探索位处理，不占常规名额
            picked.append(e)

        # 探索位：从没被选中的里挑「最久没试过的」，给失败的引擎一条复活路
        if explore:
            rest = [e for e in candidates if e not in picked]
            if rest:
                oldest = min(
                    rest, key=lambda e: self.stats.get(e.id, EngineStat()).last_tried
                )
                if picked:
                    dropped = picked.pop()  # 让出一个位置，总数不变
                    skipped.append((dropped.id, "本轮让位给探索位（分数排最后）"))
                picked.append(oldest)

        for e in candidates:
            if e not in picked and not any(s[0] == e.id for s in skipped):
                sc = self.score_of(e.id)
                skipped.append((
                    e.id,
                    f"最近表现分 {sc:.2f}，本轮没排上（每轮最多派 {size} 家）",
                ))
        return picked, skipped

    # ── 展示（界面的引擎健康仪表盘）────────────────────────
    def table(self, engines: list[BaseEngine]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for e in engines:
            st = self.stats.get(e.id)
            row: dict[str, Any] = {
                "id": e.id,
                "label": e.label,
                "kind": e.kind,
                "group": e.group,
                "needsKey": e.needs_key,
                "needsBrowser": e.needs_browser,
                "note": e.note,
                "score": round(self.score_of(e.id), 3),
                "verdict": _verdict(self.score_of(e.id), st),
            }
            row.update(st.to_dict() if st else {"samples": 0, "avgMs": None})
            out.append(row)
        out.sort(key=lambda r: -float(r["score"]))
        return out

    # ── 落盘 ────────────────────────────────────────────────
    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("引擎健康状态读取失败，从零开始学：%s", e)
            return
        for eid, d in (raw.get("engines") or {}).items():
            st = EngineStat()
            st.recent = [str(x) for x in (d.get("recent") or [])][-WINDOW:]
            st.ewma_ms = d.get("ewmaMs")
            st.last_tried = float(d.get("lastTried") or 0.0)
            st.total = int(d.get("total") or 0)
            st.total_ok = int(d.get("totalOk") or 0)
            self.stats[eid] = st

    def save(self) -> None:
        """
        落盘。**只在真的有变化时写**——搜索是高频操作，每次都写盘
        等于给每轮搜索加一次磁盘 IO，而这份状态丢一点也无所谓。
        """
        if not self._path or not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "savedAt": int(time.time()),
                "engines": {
                    eid: {
                        "recent": st.recent,
                        "ewmaMs": st.ewma_ms,
                        "lastTried": st.last_tried,
                        "total": st.total,
                        "totalOk": st.total_ok,
                    }
                    for eid, st in self.stats.items()
                },
            }
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
            tmp.replace(self._path)
            self._dirty = False
        except OSError as e:
            log.warning("引擎健康状态落盘失败（不影响搜索）：%s", e)


def _verdict(score: float, st: EngineStat | None) -> str:
    """一句人话，直接显示在仪表盘上。用户不该需要理解 0.73 是什么意思。"""
    if not st or not st.recent:
        return "还没试过"
    if score >= 0.75:
        return "状态良好"
    if score >= 0.45:
        recent = st.recent[-8:]
        if recent.count(ParseOutcome.CHALLENGED.value) >= 2:
            return "经常被限流，降速就能用"
        return "时好时坏"
    if score >= FLOOR:
        return "最近多数失败"
    return "已基本不可用，只在探索轮试它"
