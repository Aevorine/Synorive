/**
 * 界面侧纯函数测试
 * ====================================================================
 * 在这之前**前端一行代码都没被测过**（仓库里没有任何 JS 测试设施）。
 * 而这一轮新增的几个纯函数恰恰都在"错了不报错、只是结果悄悄不对"的位置上：
 *
 *   compose.ts::stripHighlight   剥不干净 → 导出的 Markdown 里混着 <em>，
 *                                预览看不出来（浏览器当标签渲染了），交出去才发现
 *   compose.ts::compose          引用编号和锚点对不上 → 点引用跳不过去
 *   heavy.worker::highlightSegments  切片错位 → 高亮标错字，或者死循环卡住 Worker
 *   heavy.worker::diffLines      diff 错了 → 一屏红绿条看起来很专业但全是错的
 *   useSelection                 选中状态和复选框对不上 → 出稿漏条或多条
 *
 * 只测**纯函数**：不碰 DOM、不碰 zustand 之外的状态、不 mock 一堆东西。
 * 需要 mock 才能测的，说明那个函数该拆了。
 *
 * 跑：npm test --workspace=@synorive/desktop
 */

import { describe, expect, it, beforeEach } from 'vitest';
import { compose, composeHtml, stripHighlight } from '../compose';
import { diffLines, highlightSegments } from '../heavy.worker';
import { MAX_SELECTION, useSelection } from '../useSelection';
import type { SearchHit } from '@synorive/shared-types';

// ── 夹具 ────────────────────────────────────────────────

function hit(id: string, over: Partial<SearchHit['item']> = {}, rest: Partial<SearchHit> = {}): SearchHit {
  return {
    item: {
      id,
      fingerprint: `fp-${id}`,
      modality: 'text',
      source: 'file',
      status: 'ready',
      title: `标题 ${id}`,
      locator: `D:/库/${id}.md`,
      createdAt: '2026-01-01T00:00:00Z',
      updatedAt: '2026-01-01T00:00:00Z',
      openCount: 0,
      tags: [],
      ...over,
    },
    score: 0.5,
    highlight: `…这里是<em>命中</em>的片段…`,
    ...rest,
  };
}

// ── stripHighlight ──────────────────────────────────────

describe('stripHighlight', () => {
  it('剥掉 <em> 标记和两头省略号，中间内容一个字不动', () => {
    expect(stripHighlight('…前面<em>命中</em>后面…')).toBe('前面命中后面');
  });

  it('省略号在标记前面时也要剥干净', () => {
    // 🔴 顺序陷阱：先去省略号再删标签的话，`…<em>x` 开头的片段
    //    第一个字符是 `…` 之外的东西，会躲过 trim 留下一个孤零零的省略号
    expect(stripHighlight('…<em>开头就命中</em>后面')).toBe('开头就命中后面');
  });

  it('没有标记时原样返回', () => {
    expect(stripHighlight('干净的一句话')).toBe('干净的一句话');
  });

  it('undefined / 空串回空串，不抛', () => {
    expect(stripHighlight(undefined)).toBe('');
    expect(stripHighlight('')).toBe('');
  });

  it('只剥 <em>，资料本身带的尖括号一个字不动', () => {
    // 🔴 这条断言原来写反了（要求"剥完不能还剩任何尖括号"），
    //    它把一个 bug 当成了需求：资料里本来就有的 `<script>`、XML、代码片段
    //    会被静默删掉一段，而稿子看起来完全正常。
    //    硬规矩②是「摘录逐字照搬」——**删掉的字比留下的标签危险得多**。
    const out = stripHighlight('…<em>光圈</em>写成 <aperture> 也行…');
    expect(out).toBe('光圈写成 <aperture> 也行');
    expect(out).not.toContain('<em>');
  });
});

// ── compose ─────────────────────────────────────────────

describe('compose', () => {
  const opts = { title: '测试稿', format: 'markdown' as const, cite: 'gb' as const, includeSnippets: true };

  it('空选中返回空串，不返回一份只有标题的空稿', () => {
    // 空稿存下来之后用户打开发现里面什么都没有，只会以为导出坏了
    expect(compose([], opts)).toBe('');
  });

  it('每一段都带出处，且引用编号和锚点一一对应', () => {
    const md = compose([hit('a'), hit('b')], opts);
    // 正文里的 [[n]](#ref-n)
    expect(md).toContain('(#ref-1)');
    expect(md).toContain('(#ref-2)');
    // 参考区里的 <a id="ref-n">
    expect(md).toContain('<a id="ref-1">');
    expect(md).toContain('<a id="ref-2">');
    // 🔴 数量必须相等：多一个锚点就是一个点不过去的引用
    const refs = md.match(/\(#ref-\d+\)/g) ?? [];
    const anchors = md.match(/<a id="ref-\d+">/g) ?? [];
    expect(refs.length).toBe(anchors.length);
  });

  it('摘录逐字照搬，不改写不润色', () => {
    const h = hit('a');
    h.highlight = '…光圈越大<em>景深</em>越浅…';
    const md = compose([h], opts);
    expect(md).toContain('光圈越大景深越浅');
  });

  it('Markdown 引用块里**每一行**都要有 >，否则第二行会脱出引用块', () => {
    const h = hit('a');
    h.highlight = '第一行\n第二行';
    const md = compose([h], opts);
    expect(md).toContain('> 第一行');
    expect(md).toContain('> 第二行');
  });

  it('没有摘录时明说，不留一个空引用块', () => {
    const h = hit('a');
    h.highlight = undefined;
    const md = compose([h], opts);
    expect(md).toContain('没有可用的摘录片段');
  });

  it('国标体例在没有作者时不硬凑，标 [Z] 而不是 [M]', () => {
    const md = compose([hit('a')], opts);
    expect(md).toContain('[Z]');
    expect(md).not.toContain('[M]');
  });

  it('关掉摘录时只出来源清单，不出摘录段', () => {
    const md = compose([hit('a')], { ...opts, includeSnippets: false });
    expect(md).toContain('参考来源');
    expect(md).not.toContain('## 摘录');
  });

  it('纯文本格式里不许出现 Markdown 标记', () => {
    const txt = compose([hit('a')], { ...opts, format: 'plain' });
    expect(txt).not.toMatch(/^#/m);
    expect(txt).not.toContain('](#ref-');
  });
});

describe('composeHtml', () => {
  it('转义尖括号和引号 —— 摘录文本可能来自公网，不转义就是注入', () => {
    const h = hit('a');
    h.highlight = '<script>alert(1)</script>';
    const html = composeHtml([h], {
      title: '<img onerror=x>', format: 'markdown', cite: 'gb', includeSnippets: true,
    });
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<img onerror');
    expect(html).toContain('&lt;script&gt;');
  });

  it('打印样式表必须被内联进去 —— 那个窗口不加载设计令牌', () => {
    const html = composeHtml([hit('a')], {
      title: 't', format: 'markdown', cite: 'gb', includeSnippets: true,
    });
    expect(html).toContain('<style>');
    // 🔴 打印文档里出现 var(--syn-*) 就是错的：它解析成空值，
    //    打出来是一份没有颜色和字号的 PDF
    expect(html.slice(html.indexOf('<style>'), html.indexOf('</style>')))
      .not.toContain('var(--syn-');
  });
});

// ── highlightSegments ───────────────────────────────────

describe('highlightSegments', () => {
  it('长词优先，避免「注意力机制」被「注意力」先切掉一半', () => {
    const segs = highlightSegments('讲的是注意力机制', ['注意力', '注意力机制']);
    const hitSeg = segs.find((s) => s.hit);
    expect(hitSeg?.text).toBe('注意力机制');
  });

  it('拼回去必须逐字等于原文 —— 少一个字就是改写了原文', () => {
    const text = '光圈越大景深越浅，注意力机制也是同理。';
    const segs = highlightSegments(text, ['景深', '注意力机制']);
    expect(segs.map((s) => s.text).join('')).toBe(text);
  });

  it('空词 / 空数组不死循环，直接原样返回', () => {
    // 🔴 没有 guard 的话空串会让 indexOf 永远返回 0：Worker 占满一个核，
    //    而主线程毫无察觉（界面不卡，只是结果永远不回来）
    expect(highlightSegments('abc', [])).toEqual([{ text: 'abc', hit: false }]);
    expect(highlightSegments('abc', ['']).map((s) => s.text).join('')).toBe('abc');
  });

  it('一个词都没命中时返回单段未命中', () => {
    const segs = highlightSegments('毫不相干', ['景深']);
    expect(segs).toEqual([{ text: '毫不相干', hit: false }]);
  });

  it('同一个词出现多次要全部标出来', () => {
    const segs = highlightSegments('景深与景深', ['景深']);
    expect(segs.filter((s) => s.hit).length).toBe(2);
  });
});

// ── diffLines ───────────────────────────────────────────

describe('diffLines', () => {
  it('完全相同 → 全部 same', () => {
    const d = diffLines('a\nb\nc', 'a\nb\nc');
    expect(d.every((l) => l.kind === 'same')).toBe(true);
  });

  it('中间插一行时，**后面的行不能全被标成改动过**', () => {
    // 🔴 逐行比对会把插入点之后的所有行都标成 del+add，
    //    那种 diff 看一眼就知道是错的，用户直接不信它。LCS 才不会
    const d = diffLines('a\nb\nc', 'a\nx\nb\nc');
    const same = d.filter((l) => l.kind === 'same').map((l) => l.text);
    expect(same).toEqual(['a', 'b', 'c']);
    expect(d.filter((l) => l.kind === 'add').map((l) => l.text)).toEqual(['x']);
    expect(d.filter((l) => l.kind === 'del')).toHaveLength(0);
  });

  it('删一行', () => {
    const d = diffLines('a\nb\nc', 'a\nc');
    expect(d.filter((l) => l.kind === 'del').map((l) => l.text)).toEqual(['b']);
  });

  it('一侧为空时全部算新增 / 删除', () => {
    expect(diffLines('', 'a\nb').filter((l) => l.kind === 'add').length).toBeGreaterThan(0);
    expect(diffLines('a\nb', '').filter((l) => l.kind === 'del').length).toBeGreaterThan(0);
  });

  it('超过 3000 行退化成逐行比，且不崩、不丢行', () => {
    const a = Array.from({ length: 3200 }, (_, i) => `l${i}`).join('\n');
    const b = Array.from({ length: 3200 }, (_, i) => (i === 10 ? 'CHANGED' : `l${i}`)).join('\n');
    const d = diffLines(a, b);
    expect(d.length).toBeGreaterThanOrEqual(3200);
    expect(d.some((l) => l.kind === 'add' && l.text === 'CHANGED')).toBe(true);
  });
});

// ── useSelection ────────────────────────────────────────

describe('useSelection', () => {
  beforeEach(() => useSelection.getState().clear());

  it('toggle 加进去再点一下拿掉，ids 和 picked 始终同步', () => {
    const s = useSelection.getState();
    s.toggle(hit('a'));
    expect(useSelection.getState().picked).toHaveLength(1);
    expect(useSelection.getState().ids.has('a')).toBe(true);
    useSelection.getState().toggle(hit('a'));
    expect(useSelection.getState().picked).toHaveLength(0);
    expect(useSelection.getState().ids.has('a')).toBe(false);
  });

  it('按选中顺序排列 —— 出稿的段落顺序就是用户挑的顺序', () => {
    const s = useSelection.getState();
    s.toggle(hit('c'));
    useSelection.getState().toggle(hit('a'));
    useSelection.getState().toggle(hit('b'));
    expect(useSelection.getState().picked.map((h) => h.item.id)).toEqual(['c', 'a', 'b']);
  });

  it('挑满上限之后不再加，并置 warnedFull', () => {
    for (let i = 0; i < MAX_SELECTION + 5; i++) {
      useSelection.getState().toggle(hit(`i${i}`));
    }
    expect(useSelection.getState().picked).toHaveLength(MAX_SELECTION);
    expect(useSelection.getState().warnedFull).toBe(true);
  });

  it('toggleAll：这一批没全选时补齐，全选了才清空', () => {
    const batch = [hit('a'), hit('b'), hit('c')];
    useSelection.getState().toggle(batch[0]!);          // 只选了一条
    useSelection.getState().toggleAll(batch);            // → 应该补齐成三条
    expect(useSelection.getState().picked).toHaveLength(3);
    useSelection.getState().toggleAll(batch);            // → 这次才清空
    expect(useSelection.getState().picked).toHaveLength(0);
  });

  it('toggleAll 清空时**只清这一批**，别的批次选中的要留着', () => {
    useSelection.getState().toggle(hit('keep'));
    const batch = [hit('a'), hit('b')];
    useSelection.getState().toggleAll(batch);
    useSelection.getState().toggleAll(batch);
    expect(useSelection.getState().picked.map((h) => h.item.id)).toEqual(['keep']);
  });

  it('remove 单条之后 ids 也要跟着掉 —— 两边不同步会让复选框显示错', () => {
    useSelection.getState().toggle(hit('a'));
    useSelection.getState().remove('a');
    expect(useSelection.getState().ids.has('a')).toBe(false);
    expect(useSelection.getState().picked).toHaveLength(0);
  });
});
