/**
 * A4 一键成稿 —— 把挑中的结果变成一份能交出去的稿子
 * ====================================================================
 * 用户原话对应的是「目前一点实用价值也没有」这条：
 * **软件的产出停在屏幕上，交不出去。** 搜到了、读到了，然后呢？
 * 还得自己一条条复制粘贴、自己补出处、自己排版 ——
 * 那一步的工作量往往比搜索本身还大，而它恰恰是"有没有用"的分水岭。
 *
 * ── 三条硬规矩 ────────────────────────────────────────────
 * ① **每一段都必须带出处，没有例外。** 一份不能核对的稿子是负资产 ——
 *    你自己过两周都不知道那句话是从哪来的。
 * ② **摘录逐字照搬，不改写、不润色、不"顺一下语句"。**
 *    和 Ask 模式同一条纪律：改过的句子和原文哪怕差一个字，
 *    引用就核对不上，而核对不上的引用等于没有引用。
 * ③ **引擎给的 highlight 带 `<em>` 标记和两头的省略号，必须先剥干净。**
 *    直接写进稿子的话，导出的 Markdown 里会出现一串 `<em>` ——
 *    在预览里看不出来（浏览器把它当标签渲染了），只有交出去之后才发现。
 */

import type { SearchHit } from '@synorive/shared-types';
import { printCss } from '@synorive/design-tokens';

export type ComposeFormat = 'markdown' | 'plain';
/** 引用体例。gb = 国标 GB/T 7714 的简化形态；apa = 作者-年份；plain = 就写路径 */
export type CiteStyle = 'gb' | 'apa' | 'plain';

export interface ComposeOptions {
  title: string;
  format: ComposeFormat;
  cite: CiteStyle;
  /** 把摘录也写进去。关掉就只出一份"我引用了这些资料"的清单 */
  includeSnippets: boolean;
}

/**
 * 剥掉引擎高亮标记和两头省略号，还原成原文片段。
 *
 * 顺序很重要：**先删标记再去省略号**。反过来的话 `…<em>` 开头的片段
 * 会因为第一个字符是 `…` 之外的东西而躲过 trim，留下一个孤零零的省略号。
 *
 * 🔴 **只删 `<em>`，不做通用的"删掉所有尖括号里的东西"。**
 *    第一版写的是 `.replace(/<[^>]+>/g, '')`，被 `pure.test.ts` 抓出来：
 *    资料里本来就带尖括号的内容（代码片段、XML、`<未知>` 这种占位写法）
 *    会被**静默删掉一段**，而稿子里剩下的那句话看起来完全正常 ——
 *    这正是硬规矩②「摘录逐字照搬」要防的事：和原文对不上的引用等于没有引用。
 *    引擎全仓只产出 `<em>` 一种标记（`search/engine.py:1173`，唯一产地），
 *    所以那条通用规则从来没有多删对过东西，只会多删错东西。
 *
 *    安全性不靠这里兜：`composeHtml` 对每一段摘录都过 `esc()`，
 *    打印窗口那边还关掉了 `javascript`。**用"改写原文"换安全是最差的一档做法** ——
 *    它既没让转义变得可省，又让稿子失去了可核对性。
 */
export function stripHighlight(s: string | undefined): string {
  if (!s) return '';
  return s
    .replace(/<\/?em>/g, '')
    .replace(/^[…\s]+/, '')
    .replace(/[…\s]+$/, '')
    .trim();
}

function locLabel(hit: SearchHit): string {
  const loc = hit.location;
  if (!loc) return '';
  if (typeof loc.page === 'number') return `第 ${loc.page} 页`;
  if (typeof loc.startSec === 'number') {
    const m = Math.floor(loc.startSec / 60);
    const s = Math.floor(loc.startSec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  }
  if (loc.section) return loc.section;
  return '';
}

function year(hit: SearchHit): string {
  const t = hit.item.contentTime;
  if (!t) return 'n.d.';
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? 'n.d.' : String(d.getFullYear());
}

/** 一条参考文献。**不编造作者** —— 本地文件通常没有作者字段，硬编一个是造假 */
function citation(hit: SearchHit, n: number, style: CiteStyle): string {
  const { item } = hit;
  const t = item.title || item.locator;
  const where = item.locator;
  const loc = locLabel(hit);
  switch (style) {
    case 'gb':
      // GB/T 7714 的简化形态：没有作者和出版信息时不硬凑，
      // 用 [Z]（其他/未定类型）而不是随便标成 [M] 专著
      return `[${n}] ${t}[Z]. ${year(hit)}. ${where}${loc ? `, ${loc}` : ''}.`;
    case 'apa':
      return `(${n}) ${t}. (${year(hit)}). ${where}${loc ? `, ${loc}` : ''}`;
    default:
      return `[${n}] ${t} —— ${where}${loc ? `（${loc}）` : ''}`;
  }
}

/**
 * 生成稿件。返回纯字符串，调用方决定是保存、复制还是转 PDF。
 *
 * **空选中返回空串**，不返回一份只有标题的空稿 ——
 * 那种文件存下来之后，用户打开发现里面什么都没有，
 * 只会以为是导出坏了。
 */
export function compose(hits: SearchHit[], opts: ComposeOptions): string {
  if (!hits.length) return '';

  const md = opts.format === 'markdown';
  const L: string[] = [];
  const stamp = new Date().toLocaleString('zh-CN');

  L.push(md ? `# ${opts.title}` : opts.title);
  L.push('');
  L.push(
    md
      ? `> 由 Synorive 从本地资料整理，共 ${hits.length} 条来源 · ${stamp}`
      : `由 Synorive 从本地资料整理，共 ${hits.length} 条来源 · ${stamp}`,
  );
  L.push('');

  if (opts.includeSnippets) {
    L.push(md ? '## 摘录' : '摘录');
    L.push('');
    hits.forEach((h, i) => {
      const n = i + 1;
      const text = stripHighlight(h.highlight) || h.item.snippet || '';
      const loc = locLabel(h);
      L.push(md ? `### ${n}. ${h.item.title || h.item.locator}` : `${n}. ${h.item.title || h.item.locator}`);
      if (loc) L.push(md ? `*${loc}*` : `（${loc}）`);
      L.push('');
      if (text) {
        // Markdown 用引用块：一眼看出"这是原文不是我写的"。
        // 多行摘录每一行都要加 `>`，漏了的话第二行会脱出引用块
        L.push(md ? text.split('\n').map((x) => `> ${x}`).join('\n') : `    ${text}`);
      } else {
        L.push(md ? '> （这一条没有可用的摘录片段）' : '    （这一条没有可用的摘录片段）');
      }
      L.push('');
      L.push(md ? `— 出处 [[${n}]](#ref-${n})` : `— 出处 [${n}]`);
      L.push('');
    });
  }

  L.push(md ? '## 参考来源' : '参考来源');
  L.push('');
  hits.forEach((h, i) => {
    const line = citation(h, i + 1, opts.cite);
    // Markdown 里给每条参考加一个锚点，上面的 [[n]] 点得过来。
    // 用 <a id> 而不是 {#id} —— 后者是部分解析器的扩展语法，
    // 在 GitHub 和多数编辑器里不生效，点了没反应
    L.push(md ? `<a id="ref-${i + 1}"></a>${line}` : line);
    L.push('');
  });

  return L.join('\n').replace(/\n{3,}/g, '\n\n');
}

/**
 * Markdown → 一份自包含的打印 HTML（走 `doc.exportPdf`）。
 *
 * 🔴 **这份 HTML 会被 `printToPDF` 加载，里面嵌着可能来自公网的摘录文本。**
 *    所以只做最小转义 + 不引任何脚本。打印窗口那边也已经关掉了
 *    `nodeIntegration` / `javascript`（见 `electron/main/pdf.ts`），
 *    但两道防线都要在 —— 只靠对面那道，改动那边的人不会知道这边的假设。
 *
 * 🔴 **配色不能写 `var(--syn-*)`。** 这份文档是自包含的、不加载设计令牌，
 *    写变量会解析成空值，打出来是一片没有颜色的东西。
 *    这个坑 `websearch/export.py` 已经踩过一次。
 */
export function composeHtml(hits: SearchHit[], opts: ComposeOptions): string {
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const body: string[] = [];
  body.push(`<h1>${esc(opts.title)}</h1>`);
  body.push(
    `<p class="meta">由 Synorive 从本地资料整理，共 ${hits.length} 条来源 · ${esc(new Date().toLocaleString('zh-CN'))}</p>`,
  );

  if (opts.includeSnippets) {
    body.push('<h2>摘录</h2>');
    hits.forEach((h, i) => {
      const n = i + 1;
      const loc = locLabel(h);
      const text = stripHighlight(h.highlight) || h.item.snippet || '';
      body.push(`<section><h3>${n}. ${esc(h.item.title || h.item.locator)}</h3>`);
      if (loc) body.push(`<p class="loc">${esc(loc)}</p>`);
      body.push(`<blockquote>${esc(text) || '（这一条没有可用的摘录片段）'}</blockquote>`);
      // 内部锚点：printToPDF 会把它写成 PDF link annotation，点得动
      body.push(`<p class="cite"><a href="#ref-${n}">出处 [${n}]</a></p></section>`);
    });
  }

  body.push('<h2>参考来源</h2><ol class="refs">');
  hits.forEach((h, i) => {
    body.push(`<li id="ref-${i + 1}">${esc(citation(h, i + 1, opts.cite))}</li>`);
  });
  body.push('</ol>');

  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>${esc(opts.title)}</title>
<style>${printCss}</style></head><body>${body.join('\n')}</body></html>`;
}

// 打印样式表住在设计令牌包里，不在这儿 —— 那是这个仓库里
// **唯一允许出现字面量色值和字号的地方**，而这份样式表必须写死
// （`printToPDF` 的窗口是自包含的，不加载 tokens.css，
//   写 var(--syn-*) 会解析成空值）。理由全文见 `tokens.ts` 的 printCss。
