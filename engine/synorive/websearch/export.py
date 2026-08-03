"""
研究成果导出 —— P3
====================================================================
把一次研究导成能交出去的东西：Markdown / HTML / JSON / Word。

**两条贯穿全文的约束**：

① **每一句摘录后面都必须挂着出处。** 导出物一旦离开这个软件，
   就没有"点开看原文"这个动作了 —— 那时候一句没有出处的话
   和一句编的话没有任何区别。所以导出格式里出处是**正文的一部分**，
   不是可以关掉的附录。

② **生成版和摘录版必须视觉上分得开。** 摘录是原文逐字，生成是模型改写。
   导出后混在一起，三个月后你自己都分不清哪句是谁说的。
   所以生成版一律带一个显式的段落标记。

**PDF 不在这里做**：生成排版正确、中文字体正确的 PDF 需要一整套排版引擎
（reportlab 要自己塞字体、weasyprint 要装 GTK）。而桌面端本来就是
Chromium —— 它的 `printToPDF` 排版质量比任何 Python PDF 库都好，
中文字体也是现成的。所以这里出 HTML，PDF 由桌面端打印。
**这不是偷懒，是把活交给已经擅长它的那一层。**
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

FORMATS = ("markdown", "html", "json", "docx")


def export_research(
    payload: dict[str, Any],
    *,
    fmt: str = "markdown",
    title: str | None = None,
    include_excluded: bool = False,
) -> tuple[str | bytes, str, str]:
    """
    返回 `(内容, 文件扩展名, MIME)`。

    `payload` 是 `/api/web/research` 的响应体（或项目里存的某次 run）。
    """
    f = fmt if fmt in FORMATS else "markdown"
    t = (title or payload.get("query") or "研究简报").strip()[:120]
    if f == "json":
        return (
            json.dumps(payload, ensure_ascii=False, indent=2),
            "json",
            "application/json; charset=utf-8",
        )
    md = _to_markdown(payload, title=t, include_excluded=include_excluded)
    if f == "markdown":
        return md, "md", "text/markdown; charset=utf-8"
    if f == "html":
        return _to_html(md, title=t), "html", "text/html; charset=utf-8"
    return _to_docx(payload, title=t), "docx", (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


# ────────────────────────────────────────────────────────────────
# Markdown（其余格式都从它派生）
# ────────────────────────────────────────────────────────────────
def _to_markdown(
    payload: dict[str, Any], *, title: str, include_excluded: bool
) -> str:
    b = payload.get("briefing") or {}
    v = payload.get("verification") or {}
    lines: list[str] = [
        f"# {title}",
        "",
        f"> 导出时间：{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M')}　"
        f"检索用时 {int(payload.get('elapsedMs') or 0) / 1000:.1f}s　"
        f"抓取正文 {payload.get('fetched') or 0} 篇",
        "",
    ]

    # 检索过程（S5 多轮）—— 放最前面。读简报的人有权先知道这些结论是怎么来的
    rounds = payload.get("rounds") or []
    if len(rounds) > 1:
        lines += ["## 检索过程", ""]
        for r in rounds:
            qs = r.get("queries") or []
            lines.append(f"**第 {r.get('round')} 轮**（新增 {r.get('newResults', 0)} 条结果）")
            for q in qs:
                lines.append(f"- `{q.get('text')}` — {q.get('why') or ''}")
            if r.get("skipped"):
                lines.append(f"- （没有第二轮：{r['skipped']}）")
            lines.append("")

    # 可信度概览
    ts = payload.get("trustSummary") or {}
    if ts:
        lines += ["## 结果成色", "", f"{ts.get('note') or ''}", ""]
        by_tier = ts.get("byTier") or {}
        if by_tier:
            lines.append("| 来源级别 | 条数 |")
            lines.append("|---|---|")
            for k, n in by_tier.items():
                lines.append(f"| {k} | {n} |")
            lines.append("")

    # 核查（V 组）
    if v:
        lines += ["## 核查", "", f"档位：`{v.get('level')}`　{v.get('note') or ''}", ""]
        for c in v.get("counterEvidence") or []:
            lines.append(
                f"- ⚠️ 反面材料：[{c.get('title')}]({c.get('url')}) — {c.get('site')}"
            )
        ret = v.get("retracted") or {}
        for doi, info in ret.items():
            lines.append(f"- 🔴 **已撤稿**：{info.get('title')}（DOI {doi}）")
        origin = v.get("origin") or {}
        if origin.get("note"):
            lines += ["", f"**溯源**：{origin['note']}"]
            e = origin.get("earliest") or {}
            if e:
                lines.append(
                    f"最早可查：[{e.get('title')}]({e.get('url')})"
                    f"（{e.get('published')}，{e.get('site')}）"
                )
        for cl in v.get("claims") or []:
            lines += ["", f"**断言**：{cl.get('claim')}"]
            lines.append(
                f"- 结论：`{cl.get('verdict')}`　支持 {cl.get('supportCount')}／"
                f"反驳 {cl.get('refuteCount')}　{cl.get('note') or ''}"
            )
            for s in cl.get("refute") or []:
                lines.append(f"  - 反驳：[{s.get('title')}]({s.get('url')})")
        lines.append("")

    # 简报正文
    lines += ["## 共识（原文摘录）", ""]
    if not (b.get("consensus")):
        lines.append("_没有找到 2 个以上独立站点互相印证的说法。_")
    for c in b.get("consensus") or []:
        lines.append(f"### {c.get('topic')}（{c.get('independentSites')} 个独立站点）")
        for e in c.get("evidence") or []:
            lines.append(f"- 「{e.get('text')}」")
            lines.append(f"  — [{e.get('title') or e.get('site')}]({e.get('url')})"
                         f"　{e.get('tier') or ''}")
        lines.append("")

    if b.get("disputes"):
        lines += ["## 分歧（并排放，不下结论）", ""]
        for d in b["disputes"]:
            lines.append(f"### {d.get('topic')}")
            for pair in d.get("conflicts") or []:
                a, bb = pair.get("a") or {}, pair.get("b") or {}
                lines.append(f"- A：「{a.get('text')}」— [{a.get('site')}]({a.get('url')})")
                lines.append(f"- B：「{bb.get('text')}」— [{bb.get('site')}]({bb.get('url')})")
                lines.append("")

    matrix = b.get("matrix") or {}
    if matrix.get("sites"):
        lines += ["## 一致性矩阵", "", f"{matrix.get('note') or ''}", ""]
        lines.append("| 话题 | " + " | ".join(matrix["sites"]) + " |")
        lines.append("|---" * (len(matrix["sites"]) + 1) + "|")
        sym = {"positive": "✔", "negative": "✘", "mixed": "～", "silent": ""}
        for topic, row in zip(matrix.get("topics") or [], matrix.get("cells") or [],
                              strict=False):
            lines.append(
                f"| {topic} | " + " | ".join(sym.get(c.get("stance"), "") for c in row) + " |"
            )
        lines.append("")

    if b.get("numbers"):
        lines += ["## 关键数据", ""]
        for n in b["numbers"]:
            lines.append(f"- **{n.get('value')}** —「{n.get('sentence')}」"
                         f"（[{n.get('site')}]({n.get('url')})）")
        lines.append("")

    if b.get("timeline"):
        lines += ["## 时间线", ""]
        for t in b["timeline"]:
            lines.append(f"- {t.get('published')} — [{t.get('title')}]({t.get('url')})")
        lines.append("")

    if b.get("openQuestions"):
        lines += ["## 还没查清", ""]
        lines += [f"- {q}" for q in b["openQuestions"]]
        lines.append("")

    # 生成版简报：**显式隔离**
    gen = payload.get("generated") or payload.get("generatedBriefing")
    if gen:
        lines += [
            "## AI 生成版（不是原文，是模型改写的）", "",
            "> 下面这一段由模型根据上面的摘录写成。**它可能改变了原意**，"
            "有疑问一律以上面的原文摘录为准。", "",
            str(gen.get("text") if isinstance(gen, dict) else gen), "",
        ]

    # 来源清单
    lines += ["## 全部来源", ""]
    for i, c in enumerate(payload.get("results") or [], 1):
        tr = c.get("trust") or {}
        lines.append(
            f"{i}. [{c.get('title')}]({c.get('url')}) — {c.get('site')}"
            f"　`{tr.get('tierLabel') or '未收录'}`"
            + (f"　⚠️ {'、'.join(tr.get('farmFlags') or [])}" if tr.get("farmFlags") else "")
        )
    if include_excluded and payload.get("excluded"):
        lines += ["", "## 已排除（折叠掉的，附原因）", ""]
        for c in payload["excluded"]:
            tr = c.get("trust") or {}
            lines.append(
                f"- [{c.get('title')}]({c.get('url')}) — {c.get('site')}"
                f"：{'；'.join(tr.get('reasons') or [])}"
            )
    return "\n".join(lines).rstrip() + "\n"


# ────────────────────────────────────────────────────────────────
# HTML（桌面端据此打印 PDF）
# ────────────────────────────────────────────────────────────────
#: 字体和字号跟应用内保持一致（锚点 5）：西文 Times New Roman、
#: 汉字宋体、标题更大的宋体。导出物换一套字体会让它看起来不像
#: 同一个软件出来的东西
_CSS = """
:root { color-scheme: light; }
body { font: 16px/1.75 "Times New Roman", "SimSun", 宋体, serif;
       color: #1F2933; background: #FAF9F6;
       max-width: 52em; margin: 2.5em auto; padding: 0 1.5em; }
h1, h2, h3 { font-family: "Source Han Serif SC", "SimSun", 宋体, serif;
             color: #0F4C8C; line-height: 1.35; }
h1 { font-size: 29.33px; border-bottom: 2px solid #0F4C8C; padding-bottom: .3em; }
h2 { font-size: 21.33px; margin-top: 1.8em; }
h3 { font-size: 20px; }
blockquote { border-left: 3px solid #C8871B; margin: 1em 0; padding: .2em 1em;
             color: #52606D; background: #fff; }
a { color: #0F4C8C; }
code { background: #EEF1F4; padding: .1em .35em; border-radius: 3px;
       font-family: Consolas, Menlo, monospace; font-size: .9em; }
table { border-collapse: collapse; margin: 1em 0; width: 100%; font-size: 14px; }
th, td { border: 1px solid #CBD2D9; padding: .4em .6em; text-align: left; }
th { background: #EEF1F4; }
li { margin: .25em 0; }
@media print { body { background: #fff; margin: 0; max-width: none; } a { color: #000; } }
"""


def _to_html(md: str, *, title: str) -> str:
    """
    极简 Markdown → HTML。

    **刻意不引第三方 markdown 库**：这里要渲染的语法是我们自己在
    `_to_markdown` 里写出来的，一共就那么七八种。为一个已知的、
    封闭的子集背一个依赖不划算 —— 而且第三方库默认允许内联 HTML，
    那等于把抓回来的网页标题当 HTML 渲染，是个实打实的注入面。
    """
    out: list[str] = []
    in_ul = False
    in_table = False
    for raw in md.split("\n"):
        line = _esc(raw)
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # 分隔行
            if not in_table:
                out.append("<table>")
                in_table = True
                out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
                continue
            out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False

        if line.startswith("- ") or re.match(r"^\d+\. ", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(re.sub(r'^(- |\d+\. )', '', line))}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False

        if line.startswith("### "):
            out.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    if in_ul:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>"
        + "\n".join(out)
        + "</body></html>"
    )


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(s: str) -> str:
    """行内语法。链接的 href 只允许 http/https —— 别把 `javascript:` 放进去。"""

    def link(m: re.Match[str]) -> str:
        href = m.group(2)
        if not href.startswith(("http://", "https://")):
            return m.group(1)
        return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>'

    s = _LINK.sub(link, s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


# ────────────────────────────────────────────────────────────────
# Word
# ────────────────────────────────────────────────────────────────
def _to_docx(payload: dict[str, Any], *, title: str) -> bytes:
    """
    Word 导出。`python-docx` 没装时**明确抛出可读的错误**，
    而不是悄悄退回 Markdown —— 用户点的是"导出 Word"，
    给他一个 .md 文件是最糟的处理。
    """
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError(
            "导出 Word 需要 python-docx，去分析中心的依赖面板装一下（约 250 KB）"
        ) from e

    b = payload.get("briefing") or {}
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "SimSun"
    style.font.size = Pt(12)  # 小四

    doc.add_heading(title, level=0)
    doc.add_paragraph(
        f"导出时间 {datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M')}　"
        f"抓取正文 {payload.get('fetched') or 0} 篇"
    )

    def section(name: str) -> None:
        doc.add_heading(name, level=1)

    section("共识（原文摘录）")
    for c in b.get("consensus") or []:
        doc.add_heading(f"{c.get('topic')}（{c.get('independentSites')} 个独立站点）", level=2)
        for e in c.get("evidence") or []:
            doc.add_paragraph(f"「{e.get('text')}」", style="List Bullet")
            doc.add_paragraph(f"　出处：{e.get('title') or e.get('site')} — {e.get('url')}")

    if b.get("disputes"):
        section("分歧")
        for d in b["disputes"]:
            doc.add_heading(str(d.get("topic")), level=2)
            for pair in d.get("conflicts") or []:
                for side in ("a", "b"):
                    ev = pair.get(side) or {}
                    doc.add_paragraph(
                        f"{side.upper()}：「{ev.get('text')}」— {ev.get('url')}",
                        style="List Bullet",
                    )

    v = payload.get("verification") or {}
    if v:
        section("核查")
        doc.add_paragraph(str(v.get("note") or ""))
        for c in v.get("counterEvidence") or []:
            doc.add_paragraph(f"反面材料：{c.get('title')} — {c.get('url')}",
                              style="List Bullet")

    section("全部来源")
    for i, c in enumerate(payload.get("results") or [], 1):
        tr = c.get("trust") or {}
        doc.add_paragraph(
            f"{i}. {c.get('title')} — {c.get('site')}"
            f"（{tr.get('tierLabel') or '未收录'}）\n　{c.get('url')}"
        )

    import io

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def safe_filename(title: str, ext: str) -> str:
    """
    文件名清洗。Windows 不允许 `\\/:*?"<>|`，而研究标题里这些字符很常见
    （「A vs B：怎么选？」）—— 不清洗的话保存会直接失败。
    """
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .") or "研究简报"
    return f"{name[:80]}.{ext}"
