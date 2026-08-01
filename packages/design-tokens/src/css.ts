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

  lines.push('  /* ── 品牌原色（仅图标 / 启动页 / 关于页） ── */');
  for (const [k, v] of Object.entries(brand)) lines.push(`  --syn-brand-${kebab(k)}: ${v};`);

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

/* 跟随系统：仅在未手动指定 data-theme 时生效 */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']):not([data-theme='dark']) {
${colorVars('dark')}

${shadowVars('dark')}

    color-scheme: dark;
  }
}
`;
}

export default generateCss;
