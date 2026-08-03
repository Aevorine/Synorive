"""
查询扩写与多语并行 —— S4
====================================================================
**要治的病**：用中文查询去问所有引擎，等于把英文世界整个漏掉。
「向量数据库 召回率」在中文圈能搜到一堆二手教程，而同一个问题的
一手资料（论文、官方文档、GitHub issue）几乎全是英文的
`vector database recall`。用户不该为了搜到好东西先自己翻译一遍。

**三条扩写路线，按可靠性排序，能停就停**：

① **内置术语表**（离线、零延迟、零依赖）
   常见技术/学术名词的中英对照。覆盖有限，但命中的都准。

② **维基百科跨语言链接**（免 Key、免费、无需模型）
   中文维基「向量数据库」这一条目自带 `langlinks`，直接给出英文条目名
   `Vector database`。这是**真人维护的对照表**，比任何机器翻译都准，
   而且天然覆盖专有名词、机构名、人名 —— 恰恰是机器翻译最容易错的那类。

③ **云端模型**（要 Key，质量最好，默认不走）
   只在用户已经配了云端且显式允许时才用。翻译一个查询词要花钱、要出网、
   要等一个来回，而前两条已经能覆盖大多数情况。

**为什么不装一个本地翻译模型**：最小的中英翻译模型也有几百 MB，
而它在这个场景里只需要翻**几个词**。为几个词背几百 MB 不划算 ——
这和阶段 3 决定"中文搜图靠 OCR 不上 874MB 的 jina-clip"是同一个判断。

**扩写出来的变体不是平等的**：原查询永远排第一、权重最高。
变体是用来**补充召回**的，不是用来替换原查询的 —— 翻译永远可能错，
而用户输入的原话一定是他想要的。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import httpx

log = logging.getLogger("synorive.websearch")

#: 变体的权重上限。原查询恒为 1.0，变体一律低于它 ——
#: 这样融合排序时，只被变体命中的结果永远排在被原查询命中的后面
VARIANT_WEIGHT = 0.7
GLOSSARY_WEIGHT = 0.75
WIKI_WEIGHT = 0.8


@dataclass
class QueryVariant:
    """一个查询变体。**必须带 why**——用户要能看懂我替他改了什么、为什么。"""

    text: str
    lang: str = "zh"
    kind: str = "original"     # original / translated / trimmed / scoped / term
    why: str = ""
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "lang": self.lang, "kind": self.kind,
            "why": self.why, "weight": round(self.weight, 2),
        }


# ────────────────────────────────────────────────────────────────
# ① 内置术语表
#
# 刻意只收**高频且翻译容易出错**的词。一份几千条的词表维护不动，
# 而且大词表的边际收益很低 —— 常见词维基能覆盖，生僻词词表也没有。
# ────────────────────────────────────────────────────────────────
_GLOSSARY: dict[str, str] = {
    # 检索 / 数据
    "向量数据库": "vector database", "向量检索": "vector search",
    "全文检索": "full-text search", "倒排索引": "inverted index",
    "召回率": "recall", "准确率": "precision", "精排": "reranking",
    "粗排": "candidate retrieval", "语义检索": "semantic search",
    "混合检索": "hybrid search", "近似最近邻": "approximate nearest neighbor",
    "知识图谱": "knowledge graph", "实体抽取": "named entity recognition",
    "分词": "word segmentation", "嵌入": "embedding", "词向量": "word embedding",
    "检索增强生成": "retrieval augmented generation",
    # 机器学习
    "大语言模型": "large language model", "微调": "fine-tuning",
    "提示词": "prompt", "推理": "inference", "训练": "training",
    "过拟合": "overfitting", "梯度下降": "gradient descent",
    "注意力机制": "attention mechanism", "变换器": "transformer",
    "卷积神经网络": "convolutional neural network", "强化学习": "reinforcement learning",
    "量化": "quantization", "蒸馏": "knowledge distillation",
    "多模态": "multimodal", "扩散模型": "diffusion model",
    # 工程
    "并发": "concurrency", "并行": "parallelism", "线程池": "thread pool",
    "内存泄漏": "memory leak", "垃圾回收": "garbage collection",
    "熔断": "circuit breaker", "限流": "rate limiting", "缓存穿透": "cache penetration",
    "负载均衡": "load balancing", "分布式": "distributed",
    "微服务": "microservices", "容器": "container", "持续集成": "continuous integration",
    "单元测试": "unit test", "回归测试": "regression test",
    "性能瓶颈": "performance bottleneck", "延迟": "latency", "吞吐": "throughput",
    "死锁": "deadlock", "竞态条件": "race condition", "幂等": "idempotent",
    "事务": "transaction", "索引": "index", "分片": "sharding",
    "读写分离": "read-write splitting", "主从复制": "replication",
    # 安全
    "跨站脚本": "cross-site scripting", "注入攻击": "injection attack",
    "服务端请求伪造": "server-side request forgery", "中间人攻击": "man-in-the-middle",
    "端到端加密": "end-to-end encryption", "零信任": "zero trust",
    "最小权限": "least privilege", "供应链攻击": "supply chain attack",
    # 学术
    "综述": "survey", "预印本": "preprint", "同行评审": "peer review",
    "被引次数": "citation count", "影响因子": "impact factor",
    "开放获取": "open access", "撤稿": "retraction", "复现": "reproducibility",
    "消融实验": "ablation study", "基准测试": "benchmark",
    "元分析": "meta-analysis", "对照实验": "controlled experiment",
    "显著性": "statistical significance", "置信区间": "confidence interval",
}

#: 疑问词/口语壳。搜索引擎对这些词几乎不敏感，去掉能显著提高召回
_QUESTION_SHELL = re.compile(
    r"^(请问|想问一下|谁能告诉我|有没有人知道|我想知道|帮我查一下|帮我搜一下|"
    r"如何|怎么|怎样|为什么|为啥|是什么|什么是|哪个|哪些|多少|能不能|可不可以)"
)
_TAIL_SHELL = re.compile(r"(呢|吗|啊|呀|吧|的话|一下|请教|求助|谢谢)+[？?。.！!]*$")


def trim_query(query: str) -> str | None:
    """
    去掉口语壳。「怎么解决向量检索太慢的问题呢」→「向量检索太慢 解决」。

    只在**真的削掉了东西且剩下的还够长**时才返回变体 ——
    把「什么是 RAG」削成「RAG」是有用的，把「RAG」削成空字符串是灾难。
    """
    s = (query or "").strip()
    trimmed = _TAIL_SHELL.sub("", _QUESTION_SHELL.sub("", s)).strip(" ，,。.？?！!")
    if trimmed and trimmed != s and len(trimmed) >= 2:
        return trimmed
    return None


def glossary_translate(query: str) -> tuple[str, list[str]] | None:
    """
    用内置术语表把查询里认得的中文术语换成英文。

    **整句只换认得的部分**，剩下的中文原样留着 —— 一个"半中半英"的查询词
    看起来别扭，但 Bing/Mojeek 对它的处理其实很好（英文术语是强信号）。
    强行把整句译成英文反而要真正的翻译模型。
    """
    hits: list[str] = []
    out = query
    # 长词优先替换，否则「向量检索」会被「向量」先吃掉半截
    for zh in sorted(_GLOSSARY, key=len, reverse=True):
        if zh in out:
            out = out.replace(zh, _GLOSSARY[zh])
            hits.append(f"{zh}→{_GLOSSARY[zh]}")
    if not hits:
        return None
    out = re.sub(r"\s+", " ", out).strip()
    return (out, hits) if out and out != query else None


# ────────────────────────────────────────────────────────────────
# ② 维基百科跨语言链接
# ────────────────────────────────────────────────────────────────
#: 维基媒体的 UA 政策要求带可联系标识，伪装浏览器 UA 反而 403。
#: 这条实测过（台账阶段 8），换成这个就通了
_WIKI_UA = "Synorive/1.0 (local research tool; https://github.com/Fusheng201)"


async def wiki_langlink(
    client: httpx.AsyncClient, term: str, *, src: str = "zh", dst: str = "en"
) -> str | None:
    """
    查一个词在另一种语言的维基条目名。查不到返回 None ——**绝不瞎猜**。

    先搜到条目再取 langlinks，两步走。直接拿用户输入当条目名会大量落空：
    用户打的是「向量数据库怎么选」，条目名是「向量数据库」。
    """
    try:
        r = await client.get(
            f"https://{src}.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "srsearch": term,
                "srlimit": "1", "format": "json", "utf8": "1",
            },
            headers={"User-Agent": _WIKI_UA},
        )
        items = ((r.json().get("query") or {}).get("search")) or []
        if not items:
            return None
        title = items[0].get("title") or ""
        if not title:
            return None
        r2 = await client.get(
            f"https://{src}.wikipedia.org/w/api.php",
            params={
                "action": "query", "titles": title, "prop": "langlinks",
                "lllang": dst, "format": "json", "utf8": "1",
            },
            headers={"User-Agent": _WIKI_UA},
        )
        pages = ((r2.json().get("query") or {}).get("pages")) or {}
        for page in pages.values():
            for ll in page.get("langlinks") or []:
                got = (ll.get("*") or "").strip()
                if got:
                    return got
    except (httpx.HTTPError, ValueError, KeyError, AttributeError):
        return None
    return None


def _key_terms(query: str, limit: int = 3) -> list[str]:
    """
    挑出查询里最像"专有名词"的几个词，拿去问维基。

    只挑长词：短词（2 字以内）在维基上要么没条目要么歧义严重，
    问了也是白问，还要付一个网络往返。
    """
    from ..store.text import segment

    words: list[str] = []
    for raw in re.findall(r"[\w一-鿿]+", query):
        words.extend(segment(raw))
    cands = [w for w in words if len(w) >= 3 or re.fullmatch(r"[A-Za-z]{3,}", w)]
    # 长的更可能是专有名词
    cands.sort(key=len, reverse=True)
    seen: set[str] = set()
    out: list[str] = []
    for w in cands:
        if w.lower() in seen:
            continue
        seen.add(w.lower())
        out.append(w)
        if len(out) >= limit:
            break
    return out


# ────────────────────────────────────────────────────────────────
# 对外：一次扩写
# ────────────────────────────────────────────────────────────────
async def expand_query(
    query: str,
    *,
    lang: str = "zh",
    cross_lingual: bool = True,
    use_wiki: bool = True,
    timeout_s: float = 2.5,
    max_variants: int = 3,
) -> list[QueryVariant]:
    """
    把一个查询扩成几个变体。**原查询恒在第一位。**

    `timeout_s` 是**整个扩写阶段**的预算，不是单次请求的。扩写是锦上添花：
    它超时了就少几个变体，绝不该拖慢搜索本身 —— 这和跳转链解析
    给整批 3 秒硬预算是同一个判断。
    """
    q = (query or "").strip()
    if not q:
        return []
    out: list[QueryVariant] = [
        QueryVariant(text=q, lang=lang, kind="original", why="你输入的原话", weight=1.0)
    ]
    if len(q) < 2:
        return out

    seen = {q.lower()}

    def add(v: QueryVariant) -> None:
        k = v.text.lower()
        if k in seen or len(out) > max_variants:
            return
        seen.add(k)
        out.append(v)

    # 口语壳（离线，零成本，先做）
    t = trim_query(q)
    if t:
        add(QueryVariant(
            text=t, lang=lang, kind="trimmed",
            why="去掉了疑问词和口语壳，搜索引擎对这些词不敏感",
            weight=VARIANT_WEIGHT,
        ))

    is_cn = bool(re.search(r"[一-鿿]", q))
    if not (cross_lingual and is_cn):
        return out

    # 术语表（离线）
    g = glossary_translate(q)
    if g:
        add(QueryVariant(
            text=g[0], lang="en", kind="translated",
            why="内置术语表换成英文：" + "、".join(g[1][:4]),
            weight=GLOSSARY_WEIGHT,
        ))

    # 维基跨语言链接（要出网，放最后，且整段有硬预算）
    if use_wiki and len(out) <= max_variants:
        try:
            await asyncio.wait_for(
                _wiki_variants(q, add, weight=WIKI_WEIGHT), timeout=timeout_s
            )
        except (TimeoutError, asyncio.CancelledError):
            log.debug("查询扩写的维基这一路超时，跳过（不影响搜索）")
    return out


async def _wiki_variants(query: str, add, *, weight: float) -> None:
    terms = _key_terms(query)
    if not terms:
        return
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.5)) as client:
        got = await asyncio.gather(
            *(wiki_langlink(client, t) for t in terms), return_exceptions=True
        )
    pairs = [
        (zh, en) for zh, en in zip(terms, got, strict=False)
        if isinstance(en, str) and en and en.lower() != zh.lower()
    ]
    if not pairs:
        return
    # 把认出来的词换掉，其余留着。和术语表同一条思路
    text = query
    for zh, en in pairs:
        text = text.replace(zh, en)
    text = re.sub(r"\s+", " ", text).strip()
    if text.lower() != query.lower():
        add(QueryVariant(
            text=text, lang="en", kind="translated",
            why="维基百科跨语言对照：" + "、".join(f"{z}→{e}" for z, e in pairs[:3]),
            weight=weight,
        ))


# ────────────────────────────────────────────────────────────────
# 变体 → 引擎 的分派
# ────────────────────────────────────────────────────────────────
#: 中文覆盖强的几家。英文变体派给它们是浪费一次请求
_CN_ENGINES = {"baidu", "so360"}
#: 英文覆盖强的几家
_EN_ENGINES = {"mojeek", "brave", "serper", "tavily", "exa", "duckduckgo"}


def route_variants(
    variants: list[QueryVariant], engine_ids: list[str]
) -> list[tuple[QueryVariant, list[str]]]:
    """
    决定每个变体派给哪几家引擎。

    **不是每个变体都发给每家** —— 那是 N×M 次请求，延迟和被限流的风险
    都按乘法涨。按语言分派：中文变体不发给 Mojeek（它中文很弱），
    英文变体不发给百度（它英文结果基本是机翻站）。
    两边都通吃的（cn.bing / searxng / wikipedia）跟着原查询走。
    """
    out: list[tuple[QueryVariant, list[str]]] = []
    for v in variants:
        if v.kind == "original":
            out.append((v, list(engine_ids)))
            continue
        if v.lang.startswith("en"):
            picked = [e for e in engine_ids if e in _EN_ENGINES]
        else:
            picked = [e for e in engine_ids if e in _CN_ENGINES]
        if picked:
            out.append((v, picked))
    return out
