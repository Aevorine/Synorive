"""
右栏「生成版简报」—— 把摘录简报改写成通顺的行文，但出处永不由模型编造
====================================================================
🔴 核心设计约束，别在后续改动中弄丢：

**模型只能引用我给它编号的证据，不能自己造 URL。**

具体做法：把左栏那份摘录简报（`research.build_briefing()` 的产物，
每条都逐字来自原文）按顺序编号，喂给模型时明确说"只能用 [1][2] 这种
标号引用，禁止提任何标号之外的信息"。模型吐出来的文本里的 [n] 标记，
拿真实的证据列表原样渲染成可点击链接——**链接是我们自己拼的，不是模型给的**。

这样即使模型编了话（幻觉），最多是编出一段不该有的论述，
**它编不出一个假来源**：链接只从 [n] 标记生成，标记对应的 URL 来自我们自己的表；
而如果模型不听提示词的话、直接在正文里写了个裸网址，`_strip_rogue_urls` 会在
渲染前把它剥掉——这才是真正的约束，提示词那句话只是降低发生的概率，
代码里这一刀才保证"发生了也不会展示出去"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .adapters import CloudAdapter, CloudAdapterError

SYSTEM_PROMPT = """你是一个严谨的研究助理，任务是把一批经过核实的原文摘录改写成一段通顺、有条理的中文简报。

硬性规则（违反任何一条都是失败）：
1. 你只能陈述下面「证据列表」里出现过的内容，不能补充任何证据列表之外的事实、数字或说法。
2. 每一个论点后面必须标注它依据的编号，格式是 [1]、[2] 这种方括号数字，可以一句话标多个编号如 [1][3]。
3. 绝对不要输出任何网址、链接或来源名称——你不知道真实链接是什么，编出来的链接会误导用户。
   出处只用编号标注，真实链接由系统自动附加。
4. 如果证据之间有矛盾，如实说"这一点上说法不一致"并把两种说法都列出、各自标注编号，不要自己选一个当结论。
5. 不确定、证据不足的地方，直接说"证据不足"，不要靠常识或训练知识填补。
6. 用简体中文，语气平实，不要标题党式的感叹句。"""


@dataclass
class Citation:
    n: int
    text: str
    url: str
    title: str
    site: str


def _collect_citations(briefing: dict[str, Any]) -> list[Citation]:
    """把摘录简报里所有带出处的证据编号，编号顺序 = 出现顺序（用户读起来对得上）。"""
    out: list[Citation] = []
    seen: set[tuple[str, str]] = set()

    def add(ev: dict[str, Any]) -> None:
        key = (ev.get("url", ""), ev.get("text", ""))
        if not ev.get("url") or key in seen:
            return
        seen.add(key)
        out.append(Citation(
            n=len(out) + 1, text=ev.get("text", ""), url=ev.get("url", ""),
            title=ev.get("title", ""), site=ev.get("site", ""),
        ))

    for topic in briefing.get("consensus", []):
        for ev in topic.get("evidence", []):
            add(ev)
    for topic in briefing.get("disputes", []):
        for pair in topic.get("conflicts", []):
            add(pair.get("a", {}))
            add(pair.get("b", {}))
    for n in briefing.get("numbers", []):
        # numbers 用的字段名和 evidence 不同（sentence 不是 text），转一下形状
        if n.get("url"):
            add({"url": n["url"], "text": n.get("sentence", ""),
                 "title": n.get("title", ""), "site": n.get("site", "")})
    return out


def _render_evidence_block(citations: list[Citation]) -> str:
    lines = []
    for c in citations:
        lines.append(f"[{c.n}] （来自 {c.site or c.title}）{c.text}")
    return "\n".join(lines)


#: 匹配模型输出里的 [数字] 或 [数字][数字] 引用标记
_CITE_RE = re.compile(r"\[(\d+)\]")
#: 防御性剥离：提示词里明说了模型不该输出网址，但"不该"是请求不是约束。
#: 万一模型不听话直接写了一个网址（幻觉编的，或从训练数据里回忆出的），
#: 这一步在渲染前把它剥掉——**这才是真正的安全边界**，
#: 提示词那句话只是降低发生概率，这一步才是保证发生了也不会展示出去
_BARE_URL_RE = re.compile(r"https?://\S+")


def _linkify(text: str, citations: list[Citation]) -> str:
    """
    把模型输出里的 [n] 换成 Markdown 链接 `[n](真实URL)`。

    **这里才是安全边界真正落地的地方**：不管模型在 [n] 前后写了什么话，
    链接目标永远来自我们自己维护的 `citations` 表，模型没有任何办法
    让链接指向它自己编的地址——因为提示词里从一开始就不允许它输出 URL，
    这里的替换逻辑也只认数字标记，不解析文本里可能出现的其他"看起来像链接"的东西。
    """
    by_n = {c.n: c for c in citations}

    def repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        c = by_n.get(n)
        if c is None:
            return m.group(0)  # 模型引用了一个不存在的编号，原样保留，不假装能链接
        return f"[{n}]({c.url})"

    return _CITE_RE.sub(repl, text)


def _strip_rogue_urls(text: str) -> str:
    """模型不该输出任何裸链接——万一它还是写了，替换成一句说明而不是放行。"""
    return _BARE_URL_RE.sub("〔已剔除：模型输出了未经核实的链接〕", text)


async def synthesize(
    query: str,
    briefing: dict[str, Any],
    *,
    adapter: CloudAdapter,
    model: str,
) -> dict[str, Any]:
    """
    从摘录简报生成一版通顺的行文。失败时抛 `CloudAdapterError`，
    调用方（路由层）负责转成对用户友好的错误信息 —— 这里不吞异常，
    云端调用失败必须让用户知道，不能悄悄退化成"什么都没生成"。
    """
    citations = _collect_citations(briefing)
    if not citations:
        return {
            "text": "",
            "citations": [],
            "warning": "左边的摘录简报里没有可引用的证据，没有东西可以改写。",
        }

    user_prompt = (
        f"用户想了解的问题：{query}\n\n"
        f"证据列表（只能引用这些，编号是唯一允许出现的出处标记）：\n"
        f"{_render_evidence_block(citations)}\n\n"
        f"请把以上证据改写成一段 200~400 字的中文简报，每句关键论点标注编号。"
    )

    try:
        result = await adapter.chat(system=SYSTEM_PROMPT, user=user_prompt, model=model)
    except CloudAdapterError:
        raise  # 路由层处理，这里不吞

    safe_text = _strip_rogue_urls(result.text)
    linked = _linkify(safe_text, citations)
    # 统计模型实际用到了哪几个编号——界面可以据此把没被引用的证据标"未被采纳"，
    # 也能反过来发现模型是不是引用了一个编号之外的东西（那种情况上面 repl() 会原样保留 [n]）
    used = sorted({int(x) for x in _CITE_RE.findall(result.text)})
    return {
        "text": linked,
        "citations": [
            {"n": c.n, "url": c.url, "title": c.title, "site": c.site, "used": c.n in used}
            for c in citations
        ],
        "model": result.model,
        "usage": result.usage,
        "kind": "generated",  # 界面必须显著标出这是模型改写的，别和左栏摘录混同
    }
