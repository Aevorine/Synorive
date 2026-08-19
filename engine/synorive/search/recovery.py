"""
D9 零结果补救
====================================================================
搜不到东西的时候，用户是**彻底卡住**的 —— 他不知道是自己拼错了、
筛选卡太死、库里压根没这类内容，还是这东西还没索引完。
只给一个"没有结果"的空状态，等于让他自己猜，而他没有任何线索可以猜。

所以这里不做"猜他想搜什么"，做的是**把他缺的信息补齐**，
每条都必须带上"点了会怎样"的确切数字（比如"去掉时间筛选 → 37 条"），
让他一眼看见下一步该点哪里。

⚠️ 每条建议都是**先真跑一次拿到确切条数**才给出来的。
   "要不要试试去掉筛选" 这种没有数字的建议毫无价值 —— 用户点进去
   还是零结果的话，比不给建议更让人恼火。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from ..store.text import to_index_text, to_query

log = logging.getLogger("synorive.search.recovery")

#: 每类建议最多给几条。给多了就成了另一种形式的"你自己猜"
MAX_PER_KIND = 4

#: 拼音表最多收多少个词（按词频取前 N）。全量算一遍在几十万词的库上要几秒，
#: 而拼音纠错本来就只是"搜不到时的一条退路"，覆盖高频词已经够用。
PINYIN_VOCAB_MAX = 20000


@dataclass
class Suggestion:
    """一条补救建议。kind 决定界面怎么渲染，payload 是点击后要执行的动作。"""

    kind: str          # drop-filter | split-term | did-you-mean | broaden | indexing | empty-library
    label: str         # 给用户看的一句人话
    count: int         # 按这条做能得到几条结果。**必须是真跑出来的**
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "count": self.count, "payload": self.payload}


#: 筛选项 → 给用户看的名字。顺序就是建议的优先级：
#: 时间和目录最常卡死人，类型次之，标签一般是用户自己刚点的
FILTER_LABELS: list[tuple[str, str]] = [
    ("timeFrom", "时间"),
    ("timeTo", "时间"),
    ("scopes", "目录范围"),
    ("excludeScopes", "排除目录"),
    ("sizeMinBytes", "大小"),
    ("sizeMaxBytes", "大小"),
    ("extensions", "文件类型"),
    ("modalities", "类型"),
    ("sources", "来源"),
    ("tags", "标签"),
]


def _edit_distance_le(a: str, b: str, limit: int) -> bool:
    """
    编辑距离是否 ≤ limit。只用来做拼写纠错，词都很短，
    用最朴素的 DP 就够了，不值得引依赖。
    长度差超过 limit 直接否掉，省掉大部分计算。
    """
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(v)
            best = min(best, v)
        if best > limit:      # 这一行最小值都超了，后面不可能降回来
            return False
        prev = cur
    return prev[-1] <= limit


def _split_pinyin(key: str, table: dict[str, list[str]]) -> list[str]:
    """
    把一串连写的拼音按最长匹配切成库里真有的词。

    `jiqixuexi` → `jiqi` + `xuexi` → ["机器", "学习"]

    最长优先：`jiqi` 比 `ji` 更可能是用户想打的整词。切不干净就整个放弃 ——
    切出一半汉字一半残留拉丁的查询，比不建议还糟。
    末尾允许一小截切不出来的残留（用户可能只打了半个词），
    那种情况下用已经切出来的部分做前缀建议。
    """
    n = len(key)
    i = 0
    words: list[str] = []
    while i < n:
        best_len = 0
        best_word = ""
        # 拼音音节最长 6 个字母（zhuang/chuang），一个词最多几个音节，
        # 上限给到 12 足够覆盖两三字词
        for ln in range(min(12, n - i), 0, -1):
            cand = table.get(key[i : i + ln])
            if cand:
                best_len, best_word = ln, cand[0]
                break
        if best_len == 0:
            break
        words.append(best_word)
        i += best_len
    if not words:
        return []
    # 剩下的尾巴太长说明这串根本不是拼音（多半是英文词），别硬凑
    if n - i > 3:
        return []
    return words


class RecoveryPlanner:
    """
    零结果时算补救方案。

    它需要能重新发起检索（`run`），所以由 SearchEngine 注入自己的检索函数，
    而不是反过来 import —— 免得两个模块互相引用。
    """

    def __init__(
        self,
        conn_factory: Callable[[], sqlite3.Connection],
        run: Callable[[str, dict[str, Any]], int],
    ) -> None:
        self._conn = conn_factory
        self._run = run   # (查询词, 筛选) -> 命中条数

    # ── 主入口 ──────────────────────────────────────────────

    def plan(self, text_query: str, filters: dict[str, Any], *, total_items: int,
             weak: bool = False) -> dict[str, Any]:
        # 库是空的就别废话了，直接告诉他去投喂 —— 这时候任何"换个词试试"都是噪音
        if total_items == 0:
            return {
                "reason": "empty-library",
                "message": "库里还什么都没有。先投喂点东西进来，再回来搜。",
                "suggestions": [],
            }

        suggestions: list[Suggestion] = []
        suggestions += self._drop_filters(text_query, filters)
        suggestions += self._split_terms(text_query, filters)
        suggestions += self._did_you_mean(text_query, filters)
        suggestions += self._pinyin_match(text_query, filters)

        # 还在索引的话，"没搜到"很可能只是还没轮到那些文件
        pending = self._pending_count()
        if pending > 0:
            suggestions.append(Suggestion(
                kind="indexing",
                label=f"还有 {pending:,} 条正在分析，搜不到的东西可能只是还没轮到",
                count=pending,
            ))

        # ⚠️ 不能写 bool(filters)：Filters.to_dict() 返回的是**全部键**，
        #    没设的那些是 None / []，字典本身永远非空 —— 判出来永远是"有筛选"，
        #    于是"库里确实没有"会被误报成"换个筛选组合试试"。
        has_filters = any(v not in (None, [], "") for v in filters.values())
        reason = _pick_reason(suggestions, has_filters)
        msg = _message_for(reason, text_query)
        if weak:
            # 有结果但都很勉强 —— 措辞必须和"一条都没有"区分开。
            # 说成"没搜到"会让用户直接忽略下面那几条其实可能有用的东西。
            msg = "没有很匹配的，下面是最接近的几条。" + (
                "换个说法可能会更准。" if reason == "nothing-matched" else msg
            )
        return {
            "reason": "weak-match" if weak else reason,
            "message": msg,
            "suggestions": [s.to_dict() for s in suggestions],
        }

    # ── 各类建议 ────────────────────────────────────────────

    def _drop_filters(self, q: str, filters: dict[str, Any]) -> list[Suggestion]:
        """
        逐个去掉筛选看还剩几条。
        这是最常见的零结果原因：用户点了几个筛选忘了，然后以为库里没有。
        """
        active = {k: v for k, v in filters.items() if v not in (None, [], "")}
        if not active:
            return []

        out: list[Suggestion] = []
        seen_labels: set[str] = set()

        # 先试"全部去掉"，它的信息量最大：告诉用户问题是不是出在筛选上
        if len(active) > 1:
            n = self._run(q, {})
            if n > 0:
                out.append(Suggestion(
                    kind="drop-filter",
                    label=f"去掉全部筛选 → {n} 条",
                    count=n,
                    payload={"drop": list(active.keys())},
                ))

        for key, label in FILTER_LABELS:
            if key not in active or label in seen_labels:
                continue
            # 同一个标签下的键要一起去掉（date_from / date_to 都叫"时间"）
            keys = [k for k, lb in FILTER_LABELS if lb == label and k in active]
            rest = {k: v for k, v in active.items() if k not in keys}
            n = self._run(q, rest)
            if n > 0:
                seen_labels.add(label)
                out.append(Suggestion(
                    kind="drop-filter",
                    label=f"去掉「{label}」筛选 → {n} 条",
                    count=n,
                    payload={"drop": keys},
                ))
            if len(out) >= MAX_PER_KIND:
                break
        return out

    def _split_terms(self, q: str, filters: dict[str, Any]) -> list[Suggestion]:
        """
        把查询拆成词，逐个试。
        用户常常一次输入好几个概念，其中一个库里没有，整条就废了 ——
        告诉他"哪个词有、哪个词没有"，比让他自己二分快得多。
        """
        terms = [t for t in to_index_text(q).split() if len(t) >= 2]
        if len(terms) < 2:
            return []

        hits: list[tuple[str, int]] = []
        for t in terms[:8]:
            n = self._run(t, filters)
            hits.append((t, n))

        found = [(t, n) for t, n in hits if n > 0]
        missing = [t for t, n in hits if n == 0]
        if not found:
            return []

        out: list[Suggestion] = []
        # 有词完全搜不到 —— 这就是零结果的直接原因，单独说清楚
        if missing:
            out.append(Suggestion(
                kind="split-term",
                label=f"「{'、'.join(missing[:3])}」在库里一条都没有，去掉它再搜",
                count=max(n for _, n in found),
                payload={"query": " ".join(t for t, _ in found)},
            ))
        for t, n in sorted(found, key=lambda x: -x[1])[:MAX_PER_KIND - len(out)]:
            out.append(Suggestion(
                kind="split-term",
                label=f"只搜「{t}」 → {n} 条",
                count=n,
                payload={"query": t},
            ))
        return out

    def _did_you_mean(self, q: str, filters: dict[str, Any]) -> list[Suggestion]:
        """
        拼错纠正：拿查询词去比对库里真实出现过的词。

        候选词从 FTS 词表里取（而不是拿一本通用词典）—— 只有库里真有的词
        才值得建议，建议一个库里没有的"正确写法"是在浪费用户一次点击。
        """
        terms = [t for t in to_index_text(q).split() if 2 <= len(t) <= 12]
        if not terms:
            return []

        vocab = self._vocabulary()
        if not vocab:
            return []

        out: list[Suggestion] = []
        for t in terms[:4]:
            if t in vocab:
                continue
            # 词越长容忍越多错，但最多 2 —— 再多就不是"打错了"而是"另一个词"
            limit = 1 if len(t) <= 3 else 2
            cands = [
                v for v in vocab
                if v != t and abs(len(v) - len(t)) <= limit and _edit_distance_le(t, v, limit)
            ]
            if not cands:
                continue
            # 挑库里出现最多的那个：它最可能是用户想打的
            best = max(cands, key=lambda v: vocab[v])
            fixed = q.replace(t, best) if t in q else best
            n = self._run(fixed, filters)
            if n > 0:
                out.append(Suggestion(
                    kind="did-you-mean",
                    label=f"是不是想搜「{best}」 → {n} 条",
                    count=n,
                    payload={"query": fixed},
                ))
            if len(out) >= MAX_PER_KIND:
                break
        return out

    def _pinyin_match(self, q: str, filters: dict[str, Any]) -> list[Suggestion]:
        """
        整串拼音 → 汉字词。打 `jiqixuexi` 找到「机器学习」。

        ── 为什么这条和 `_did_you_mean` 分开 ────────────────────
        编辑距离治的是"打错一个字"（机气学习 → 机器学习），它对
        `jiqixuexi` 完全无能为力 —— 拉丁串和汉字词之间的编辑距离是词长本身。
        中文输入法没切换、或者干脆懒得切，是很常见的一种输入，
        而现在的表现是**一条结果都没有**，用户只会以为库里没这东西。

        ── 为什么要切分，不能整串查表 ──────────────────────────
        🔴 词表来自 FTS，而 FTS 里存的是**分好词的**：「机器学习」在词表里
           是「机器」和「学习」两条，整串 `jiqixuexi` 一次都查不到。
           第一版就是这么写的，测出来永远返回空 —— 不报错，只是永远不工作。
           所以按最长匹配把拼音串切开，和输入法做的是同一件事。

        🔴 **只在纯拉丁字母的词上触发。** 英文查询（`transformer`）也会走到
           这儿，但它切不出任何汉字词，自然什么都不建议 —— 不会误伤。

        🔴 **候选只从库里真有的词里取，且要真的能搜出东西才建议。**
           建议一个点进去还是零结果的写法，等于骗用户点一下。
        """
        terms = [t for t in to_index_text(q).split() if 4 <= len(t) <= 24]
        latin = [t for t in terms if t.isascii() and t.isalpha()]
        if not latin:
            return []

        table = self._pinyin_table()
        if not table:
            return []

        out: list[Suggestion] = []
        for t in latin[:2]:
            words = _split_pinyin(t.lower(), table)
            if not words:
                continue
            guess = " ".join(words)
            fixed = q.replace(t, guess) if t in q else guess
            n = self._run(fixed, filters)
            if n > 0:
                out.append(Suggestion(
                    kind="did-you-mean",
                    label=f"按拼音，是不是想搜「{guess}」 → {n} 条",
                    count=n,
                    payload={"query": fixed},
                ))
            if len(out) >= MAX_PER_KIND:
                break
        return out

    def _pinyin_table(self) -> dict[str, list[str]]:
        """库里每个词的整串拼音 → 词。懒建一次，之后复用。"""
        cached = getattr(self, "_pinyin_cache", None)
        vocab = self._vocabulary()
        if cached is not None and getattr(self, "_pinyin_vocab_n", -1) == len(vocab):
            return cached
        try:
            from pypinyin import Style, lazy_pinyin
        except ImportError:
            # 没装就没有这条补救路，其余补救照常。不报错、不影响搜索
            log.debug("pypinyin 不可用，跳过拼音纠错")
            self._pinyin_cache = {}
            self._pinyin_vocab_n = len(vocab)
            return {}

        table: dict[str, list[str]] = {}
        for w, freq in sorted(vocab.items(), key=lambda x: -x[1])[:PINYIN_VOCAB_MAX]:
            # 只给含汉字的词算拼音；纯英文词算出来还是它自己，进表只是噪声
            if not any("一" <= ch <= "鿿" for ch in w):
                continue
            key = "".join(lazy_pinyin(w, style=Style.NORMAL))
            if not key or not key.isascii():
                continue
            table.setdefault(key, []).append(w)
        self._pinyin_cache = table
        self._pinyin_vocab_n = len(vocab)
        return table

    # ── 数据 ────────────────────────────────────────────────

    def _vocabulary(self) -> dict[str, int]:
        """
        FTS 词表 + 词频。
        用 fts5vocab 直接读索引里的词，不用自己再扫一遍全库文本。
        表可能不存在（老库），取不到就返回空、不报错 —— 纠错功能没了不影响搜索。
        """
        try:
            conn = self._conn()
        except Exception as e:  # noqa: BLE001
            # 🔴 **拿连接这一步也要包住。** 原来只包了下面那条查询 ——
            #    库文件被挪走 / 切库正在重启时 `self._conn()` 本身会抛，
            #    而这个异常会一路冒到 `search()` 里，**把整次搜索变成 500**。
            #    补救建议挂了顶多是少几条建议，绝不该拖垮主功能。
            log.warning("D9 取词表时拿不到连接，纠错建议不可用：%s", e)
            return {}
        try:
            # ⚠️ 建在 temp 里就必须显式写主库名。
            #    写成 fts5vocab('chunks_fts','row') 时它会去找 **temp.chunks_fts**，
            #    报 "no such fts5 table"。而这个异常原本被下面的 except 静默吞掉，
            #    表现出来就是"纠错功能安静地一直没生效"——不报错，只是永远没有建议。
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS temp.chunks_vocab "
                "USING fts5vocab('main', 'chunks_fts', 'row')"
            )
            rows = conn.execute(
                "SELECT term, cnt FROM temp.chunks_vocab "
                "WHERE length(term) BETWEEN 2 AND 12 ORDER BY cnt DESC LIMIT 8000"
            ).fetchall()
        except sqlite3.OperationalError as e:
            # 取不到词表只是少了纠错建议，不该影响检索；但要留下痕迹，
            # 否则下次又变成"安静地不工作"
            log.warning("D9 取 FTS 词表失败，纠错建议不可用：%s", e)
            return {}
        return {r[0]: r[1] for r in rows}

    def _pending_count(self) -> int:
        try:
            conn = self._conn()
        except Exception:  # noqa: BLE001
            # 同 _vocabulary：拿不到连接不该把整次搜索拖成 500
            return 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM items WHERE status IN ('queued','running','partial')"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0


def _pick_reason(suggestions: list[Suggestion], had_filters: bool) -> str:
    kinds = {s.kind for s in suggestions}
    if "drop-filter" in kinds:
        return "filters-too-narrow"
    if "did-you-mean" in kinds:
        return "typo"
    if "split-term" in kinds:
        return "term-not-in-library"
    if "indexing" in kinds:
        return "still-indexing"
    return "no-filters-narrow" if had_filters else "nothing-matched"


def _message_for(reason: str, q: str) -> str:
    return {
        "filters-too-narrow": "筛选卡得太死了，去掉几个就有结果。",
        "typo": "这几个词库里没有，可能是打错了。",
        "term-not-in-library": "其中有词库里一条都没有，去掉它就能搜到。",
        "still-indexing": "还在分析，等一下再搜可能就有了。",
        "no-filters-narrow": "换个筛选组合试试。",
        "nothing-matched": f"库里确实没有和「{q}」相关的东西。换个说法，或者先把对应的文件投喂进来。",
    }.get(reason, "换个说法试试。")
