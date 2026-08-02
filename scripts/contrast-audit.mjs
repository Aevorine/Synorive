/**
 * B6 对比度审计 —— 直接读设计令牌，把不达标的算出来，并给出最小修正值
 * ====================================================================
 * 为什么不在浏览器里测：色值来自令牌，令牌是唯一真相源。在浏览器里测只能
 * 发现"当前主题下这一对不合格"，而这里能一次扫完**两套主题 × 全部配对**，
 * 而且能直接算出"改成什么色就合格"。改完配色复跑一次就知道有没有连坐。
 *
 * 配对表照着 CSS 里的**真实用法**列，不是照着"看起来应该测什么"列 ——
 * warning 用在 .dep__degrade（12px 小字）和徽章文字上，那它就得按正文
 * 4.5:1 卡，不能按装饰色 3:1 放行。
 *
 * 用法：node scripts/contrast-audit.mjs [--fix-suggest]
 */

import { palette } from '../packages/design-tokens/dist/index.js';

// ── 色彩计算 ────────────────────────────────────────────────

function toRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) throw new Error(`认不出的色值：${hex}`);
  const v = parseInt(m[1], 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}
const toHex = ([r, g, b]) =>
  '#' + [r, g, b].map((x) => Math.round(Math.max(0, Math.min(255, x))).toString(16).padStart(2, '0').toUpperCase()).join('');

/** WCAG 2.1 相对亮度 */
function luminance(hex) {
  const ch = toRgb(hex).map((x) => {
    const s = x / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}
function contrast(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** RGB↔HSL：改亮度时保住色相和饱和度，不然品牌色会跑味 */
function rgb2hsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0;
  if (d) {
    if (mx === r) h = ((g - b) / d) % 6;
    else if (mx === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const l = (mx + mn) / 2;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  return [h, s, l];
}
function hsl2rgb([h, s, l]) {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] =
    h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] :
    h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [(r + m) * 255, (g + m) * 255, (b + m) * 255];
}

/**
 * 二分找出"色相饱和度不变、只调明度"能过线的最近色值。
 * 往哪个方向调由底色决定：底色亮就把前景压暗，底色暗就把前景提亮。
 */
function solve(fg, bg, target, margin = 0.06) {
  const [h, s, l0] = rgb2hsl(toRgb(fg));
  const bgLum = luminance(bg);
  const goDarker = bgLum > 0.18;          // 亮底 → 前景往暗走
  let lo = goDarker ? 0 : l0;
  let hi = goDarker ? l0 : 1;
  let best = null;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    const cand = toHex(hsl2rgb([h, s, mid]));
    if (contrast(cand, bg) >= target + margin) {
      best = cand;
      // 过线了 → 往"改动更小"的方向收，找刚好够用的那个
      if (goDarker) lo = mid; else hi = mid;
    } else {
      if (goDarker) hi = mid; else lo = mid;
    }
  }
  return best;
}

// ── 配对表：照 CSS 真实用法列 ───────────────────────────────
// min = 4.5 → 这个色确实拿来写字（含小字）
// min = 3.0 → 只做图形/边框/圆点，或只出现在 ≥24px 大标题上
const PAIRS = [
  // 正文层次
  ['text', 'bg', '主文字 / 纸底', 4.5],
  ['text', 'bgElevated', '主文字 / 卡片底', 4.5],
  ['text', 'bgSunken', '主文字 / 输入框底', 4.5],
  ['text', 'bgHover', '主文字 / 悬停底', 4.5],
  ['text', 'bgSelected', '主文字 / 选中底', 4.5],
  ['textSecondary', 'bg', '次要文字 / 纸底', 4.5],
  ['textSecondary', 'bgElevated', '次要文字 / 卡片底', 4.5],
  ['textMuted', 'bg', '辅助文字 / 纸底', 4.5],
  ['textMuted', 'bgElevated', '辅助文字 / 卡片底', 4.5],
  ['textMuted', 'bgSunken', '辅助文字 / 输入框底', 4.5],
  ['textInverse', 'primary', '反白字 / 主按钮', 4.5],
  ['textInverse', 'danger', '反白字 / 危险按钮', 4.5],

  // 语义色当文字用（页面底）
  ['primary', 'bg', '主色文字 / 纸底', 4.5],
  ['primary', 'bgElevated', '主色文字 / 卡片底', 4.5],
  ['success', 'bg', '成功文字 / 纸底', 4.5],
  ['warning', 'bg', '强调文字 / 纸底', 4.5],
  ['danger', 'bg', '警示文字 / 纸底', 4.5],
  ['info', 'bg', '信息文字 / 纸底', 4.5],

  // 徽章：文字压在同色系浅底上（.badge--money / --version / --time 等）
  ['primary', 'primarySubtle', '主色徽章字 / 底', 4.5],
  ['success', 'successSubtle', '成功徽章字 / 底', 4.5],
  ['warning', 'warningSubtle', '强调徽章字 / 底', 4.5],
  ['danger', 'dangerSubtle', '警示徽章字 / 底', 4.5],
  ['info', 'infoSubtle', '信息徽章字 / 底', 4.5],

  // 检索高亮：命中词底色上压主文字
  ['text', 'highlight', '主文字 / 命中高亮底', 4.5],
  ['text', 'highlightSemantic', '主文字 / 语义高亮底', 4.5],

  // 图形类：3:1 就够（WCAG 1.4.11 非文本对比）
  // 只把**表意**的图形算进来。纯装饰的分隔线、hover 边框、空态字形不在规范范围内 ——
  // 把它们一并拉到 3:1 会让整个界面变重，那是拿"过检查"换实际观感。
  ['borderFocus', 'bg', '聚焦环 / 纸底', 3.0],
  ['borderFocus', 'bgElevated', '聚焦环 / 卡片底', 3.0],
  ['success', 'bgElevated', '状态圆点·就绪 / 状态栏', 3.0],
  ['warning', 'bgElevated', '状态圆点·忙 / 状态栏', 3.0],
  ['danger', 'bgElevated', '状态圆点·失败 / 状态栏', 3.0],
  // 空闲圆点原本用 border-strong（1.67:1，看不见），已改用 text-muted
  ['textMuted', 'bgElevated', '状态圆点·空闲 / 状态栏', 3.0],
];

/**
 * 明确豁免、且**写下理由**的配对。
 * 不写理由的豁免等于偷偷降标准 —— 下一个人（包括未来的我）分不清
 * "这条想过了" 和 "这条漏了"。
 */
const EXEMPT = [
  ['borderStrong', 'bg', '强调边框 / 纸底', '纯装饰：滚动条滑块、chip/btn/搜索框的 hover 边框、空态字形。控件静止态已有 border 标识边界，hover 只是锦上添花'],
];

// ── 跑 ──────────────────────────────────────────────────────

let failed = 0;
const suggestions = new Map();   // `${theme}.${token}` → 建议色值集合

for (const theme of ['light', 'dark']) {
  const p = palette[theme];
  console.log(`\n${'━'.repeat(78)}`);
  console.log(`  ${theme === 'light' ? '浅色' : '深色'}主题`);
  console.log('━'.repeat(78));

  for (const [fgK, bgK, label, min] of PAIRS) {
    const fg = p[fgK];
    const bg = p[bgK];
    const r = contrast(fg, bg);
    const ok = r >= min;
    if (!ok) failed++;
    console.log(
      `  ${ok ? '✓' : '✗'} ${label.padEnd(24)} ${fg} on ${bg}  = ${r.toFixed(2).padStart(5)}:1  (需 ≥${min})`
    );
    if (ok) continue;
    {
      const fix = solve(fg, bg, min);
      const key = `${theme}.${fgK}`;
      if (fix) {
        if (!suggestions.has(key)) suggestions.set(key, []);
        suggestions.get(key).push({ against: bgK, min, fix, got: contrast(fix, bg) });
        console.log(`      └─ 改成 ${fix} → ${contrast(fix, bg).toFixed(2)}:1`);
      } else {
        console.log(`      └─ 只调明度救不回来，得换底色或换色相`);
      }
    }
  }

  for (const [fgK, bgK, label, why] of EXEMPT) {
    const r = contrast(p[fgK], p[bgK]);
    console.log(`  ○ ${label.padEnd(24)} ${p[fgK]} on ${p[bgK]}  = ${r.toFixed(2).padStart(5)}:1  已豁免`);
    console.log(`      └─ ${why}`);
  }
}

if (suggestions.size) {
  console.log(`\n${'━'.repeat(78)}`);
  console.log('  修正建议（同一个令牌被多条约束卡住时取最严的那个）');
  console.log('━'.repeat(78));
  for (const [key, list] of suggestions) {
    // 一个令牌可能同时对几种底色不达标 —— 取"最暗/最亮"的那个建议才能全过
    const [theme, token] = key.split('.');
    const bgLum = luminance(palette[theme].bg);
    const pick = list.reduce((a, b) =>
      (bgLum > 0.18 ? luminance(a.fix) < luminance(b.fix) : luminance(a.fix) > luminance(b.fix)) ? a : b
    );
    console.log(
      `  ${key.padEnd(24)} ${palette[theme][token]} → ${pick.fix}` +
      `　（卡最紧的是 ${pick.against}，需 ≥${pick.min}）`
    );
  }
}

console.log(`\n${'━'.repeat(78)}`);
if (failed) {
  console.error(`✗ ${failed} 对不达标`);
  process.exit(1);
}
console.log('✓ 全部配对达到 WCAG AA');
