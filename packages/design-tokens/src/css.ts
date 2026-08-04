/**
 * 令牌 → CSS 自定义属性
 * 生成 :root（浅色）与 [data-theme="dark"]（深色）两套变量。
 * 组件里一律写 var(--syn-color-primary)，永远不写具体色值。
 */

import {
  fontSize,
  fontFamily,
  fontWeight,
  lineHeight,
  letterSpacing,
  palette,
  spacing,
  radius,
  shadow,
  shadowDark,
  motion,
  zIndex,
  layout,
  densityScale,
  brand,
  type ThemeName,
} from './tokens.js';

const kebab = (s: string) => s.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase();

function colorVars(theme: ThemeName): string {
  return Object.entries(palette[theme])
    .map(([k, v]) => `  --syn-color-${kebab(k)}: ${v};`)
    .join('\n');
}

function shadowVars(theme: ThemeName): string {
  const src = theme === 'dark' ? shadowDark : shadow;
  return Object.entries(src)
    .map(([k, v]) => `  --syn-shadow-${kebab(k)}: ${v};`)
    .join('\n');
}

/** 与主题无关的静态变量：字号、字族、间距、圆角、动效、层级、布局 */
function staticVars(): string {
  const lines: string[] = [];

  lines.push('  /* ── 字号（中文字号换算，1pt = 4/3 px） ── */');
  for (const [k, v] of Object.entries(fontSize)) {
    lines.push(`  --syn-fs-${kebab(k)}: ${v.px}px; /* ${v.cn} */`);
  }

  lines.push('  /* ── 字族（Times New Roman 在前，汉字自动回落宋体） ── */');
  for (const [k, v] of Object.entries(fontFamily)) {
    lines.push(`  --syn-ff-${kebab(k)}: ${v};`);
  }

  lines.push('  /* ── 字重 / 行高 / 字距 ── */');
  for (const [k, v] of Object.entries(fontWeight)) lines.push(`  --syn-fw-${kebab(k)}: ${v};`);
  for (const [k, v] of Object.entries(lineHeight)) lines.push(`  --syn-lh-${kebab(k)}: ${v};`);
  for (const [k, v] of Object.entries(letterSpacing)) lines.push(`  --syn-ls-${kebab(k)}: ${v};`);

  lines.push('  /* ── 间距（8px 栅格） ── */');
  for (const [k, v] of Object.entries(spacing)) lines.push(`  --syn-space-${kebab(k)}: ${v}px;`);

  lines.push('  /* ── 圆角 ── */');
  for (const [k, v] of Object.entries(radius)) lines.push(`  --syn-radius-${kebab(k)}: ${v}px;`);

  lines.push('  /* ── 动效 ── */');
  for (const [k, v] of Object.entries(motion.duration)) lines.push(`  --syn-dur-${kebab(k)}: ${v}ms;`);
  for (const [k, v] of Object.entries(motion.easing)) lines.push(`  --syn-ease-${kebab(k)}: ${v};`);

  lines.push('  /* ── 层级 ── */');
  for (const [k, v] of Object.entries(zIndex)) lines.push(`  --syn-z-${kebab(k)}: ${v};`);

  lines.push('  /* ── 布局 ── */');
  lines.push(`  --syn-layout-top-bar: ${layout.topBarHeight}px;`);
  lines.push(`  --syn-layout-side-bar: ${layout.sideBarWidth}px;`);
  lines.push(`  --syn-layout-side-bar-collapsed: ${layout.sideBarWidthCollapsed}px;`);
  lines.push(`  --syn-layout-status-bar: ${layout.statusBarHeight}px;`);
  lines.push(`  --syn-layout-search-max: ${layout.searchBoxMaxWidth}px;`);
  lines.push(`  --syn-layout-content-pad: ${layout.contentPadding}px;`);
  lines.push(`  --syn-layout-detail-panel: ${layout.detailPanelWidth}px;`);
  for (const [k, v] of Object.entries(layout.resultRowHeight)) {
    lines.push(`  --syn-row-h-${kebab(k)}: ${v}px;`);
  }

  lines.push('  /* ── B1 主舞台（大输入区） ── */');
  // 这两个是**无量纲**的，不能带 px：
  //   verticalAnchor 是比例（0.32），带上 px 会让 calc 整个失效 ——
  //     失效方式是"输入框跑到屏幕外面"，不报错、只是看不见
  //   inputRows 是行数，给 <textarea rows> 用，带 px 就是一个假单位
  const UNITLESS = new Set(['verticalAnchor', 'inputRows']);
  for (const [k, v] of Object.entries(layout.stage)) {
    lines.push(`  --syn-stage-${kebab(k)}: ${typeof v === 'number' && !UNITLESS.has(k) ? `${v}px` : v};`);
  }

  lines.push('  /* ── 品牌原色（仅图标 / 启动页 / 关于页） ── */');
  for (const [k, v] of Object.entries(brand)) lines.push(`  --syn-brand-${kebab(k)}: ${v};`);

  return lines.join('\n');
}

/**
 * 密度变量（B5）
 *
 * 一档一个 `:root[data-density='x']` 块。组件写 `var(--syn-d-gap)` 就自动跟着变，
 * **不需要每个组件各写三条 `[data-density]` 规则** —— 那正是原来那套失效的原因：
 * 要求每个组件自觉去响应，结果 41 个组件里只有 1 个响应了。
 *
 * `standard` 这一档同时写进 `:root`，这样即使 `data-density` 属性因为任何原因
 * 没被设上（首帧、设置读取失败、渲染进程刚起来），变量也**永远有值**。
 * 没有兜底的话表现是全应用间距塌成 0，比"密度不生效"难看得多。
 */
function densityVars(name: keyof typeof densityScale): string {
  const d = densityScale[name];
  const lines: string[] = [];
  lines.push(`  --syn-d-scale: ${d.scale};`);
  lines.push(`  --syn-d-gap: ${d.gap}px;`);
  lines.push(`  --syn-d-control: ${d.control}px;`);
  lines.push(`  --syn-d-card-pad: ${d.cardPad}px;`);
  lines.push(`  --syn-d-line-height: ${d.lineHeight};`);
  lines.push(`  --syn-d-content-pad: ${d.contentPad}px;`);
  // 由总倍率乘出来的间距阶梯 —— 组件用这一组，就自动是响应密度的
  for (const [k, v] of Object.entries(spacing)) {
    if (v === 0) continue;
    lines.push(`  --syn-d-space-${kebab(k)}: ${Math.round(v * d.scale)}px;`);
  }
  lines.push(`  --syn-d-row-h: var(--syn-row-h-${kebab(name)});`);
  return lines.join('\n');
}

export function generateCss(): string {
  return `/* ============================================================
 * Synorive 设计令牌 —— 自动生成，请勿手改
 * 源文件：packages/design-tokens/src/tokens.ts
 * 重新生成：npm run build:tokens
 * ============================================================ */

:root {
${staticVars()}

  /* ── 密度默认档（standard）：属性没设上时的兜底，见 densityVars 注释 ── */
${densityVars('standard')}

  /* ── 颜色（浅色，默认） ── */
${colorVars('light')}

  /* ── 阴影（浅色） ── */
${shadowVars('light')}

  color-scheme: light;
}

:root[data-theme='dark'] {
${colorVars('dark')}

${shadowVars('dark')}

  color-scheme: dark;
}

/* 纸感：长时间阅读档。用独立色板而不是整层 filter —— 见 tokens.ts 里 paper 的注释，
   那一层 filter 同时也是滚动掉帧的来源之一。阴影沿用浅色那套。 */
:root[data-theme='paper'] {
${colorVars('paper')}

${shadowVars('light')}

  color-scheme: light;
}

/* 跟随系统：仅在未手动指定 data-theme 时生效。
   ⚠️ paper 也要排除 —— 漏了它的话，用户选了纸感而系统是深色时，
   这条媒体查询会把 paper 的颜色整个盖掉，表现是"选了没反应"。 */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']):not([data-theme='dark']):not([data-theme='paper']) {
${colorVars('dark')}

${shadowVars('dark')}

    color-scheme: dark;
  }
}

/* ── 密度三档（B5） ── */
:root[data-density='compact'] {
${densityVars('compact')}
}

:root[data-density='standard'] {
${densityVars('standard')}
}

:root[data-density='comfortable'] {
${densityVars('comfortable')}
}
`;
}

export default generateCss;
