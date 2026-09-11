/**
 * 纯图标按钮的悬停提示扫描 —— 验收标准 C3
 * ====================================================================
 * 用户的要求原话是：「指针停止在导航栏的图标会显示功能名称，要求对于界面内
 * 隐藏功能的图标也能在指针停止在界面内的图标会显示对应功能名称。」
 *
 * 这件事靠自觉做不到 —— 每次加一个新图标按钮都可能忘。所以做成一道闸：
 * 只要一个 <button> 里**看不到任何文字**，它就必须同时有
 *   title=…        给鼠标用户（悬停浮出系统提示）
 *   aria-label=…   给读屏软件用户
 * 缺任何一个直接判失败。
 *
 * ⚠️ 只有 aria-label 不算数：aria-label 鼠标悬停时**不显示**。
 *    2026-08-27 实测就抓到过一个这样的按钮（ComposeBar.tsx 的发送键）。
 *
 * 扫的是 JSX 字面量 <button>。用组件封装出来的按钮（<IconButton …/>）扫不到，
 * 那类要靠组件自己在内部保证——本脚本会把见到的这类组件名列出来供人工确认。
 *
 * 用法：node scripts/check-icon-tooltips.mjs
 * 退出码 0 = 通过，1 = 发现缺提示的图标按钮
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const SCAN_DIRS = [join(ROOT, 'apps', 'desktop', 'src')];
const SCAN_EXT = new Set(['.tsx', '.jsx']);

/** 这些字符单独出现不算"文字"（箭头/圆点这类本身就是图形） */
const NON_TEXT = /^[\s·•▾▴▸◂×✕✓←→↑↓…\-–—|/\\]*$/u;

function walk(dir, out = []) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    if (name === 'node_modules' || name === 'dist' || name === 'out') continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) walk(full, out);
    else if (SCAN_EXT.has(extname(name))) out.push(full);
  }
  return out;
}

/**
 * 从 src[i] 开始（i 指向 '<'），找到这个开标签的 '>' 位置。
 * 需要跳过字符串和 {} 表达式，否则 title={a > b ? 'x' : 'y'} 会把标签提前截断。
 */
function endOfOpenTag(src, i) {
  let depth = 0;
  let quote = null;
  for (let p = i; p < src.length; p += 1) {
    const c = src[p];
    if (quote) {
      if (c === '\\') p += 1;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === '`') {
      quote = c;
      continue;
    }
    if (c === '{') depth += 1;
    else if (c === '}') depth -= 1;
    else if (c === '>' && depth === 0) return p;
  }
  return -1;
}

/** 找到与开标签配对的 </button>，考虑嵌套（实际不会嵌套，但便宜） */
function endOfElement(src, afterOpen) {
  let depth = 1;
  let p = afterOpen;
  while (p < src.length) {
    const open = src.indexOf('<button', p);
    const close = src.indexOf('</button', p);
    if (close === -1) return -1;
    if (open !== -1 && open < close) {
      depth += 1;
      p = open + 7;
      continue;
    }
    depth -= 1;
    if (depth === 0) return close;
    p = close + 8;
  }
  return -1;
}

/**
 * 把 JSX 内容里"用户真能看见的字"抠出来。
 *
 * 🔴 2026-08-27 踩过的坑：第一版只把 `{}` 里的**字符串字面量**当文字，于是
 *    `<button>{m.label}</button>` 被判成"纯图标按钮"，一口气报了 10 个假警报。
 *    **假警报比查不出更危险 —— 它会让人去给一个本来就有文字的按钮加 title。**
 *    现在的判据：`{}` 里只要不是明显的 JSX 元素（`<` 开头）或注释，一律**假定它会渲染出文字**。
 *    宁可漏报也不误报。
 */
function visibleText(inner) {
  let out = '';
  let hasExpr = false;
  let p = 0;
  while (p < inner.length) {
    const c = inner[p];
    if (c === '<') {
      // 跳过一个完整的子标签（含其属性里的引号与花括号）
      const end = endOfOpenTag(inner, p);
      p = end === -1 ? inner.length : end + 1;
      continue;
    }
    if (c === '{') {
      // 表达式：只把里面的字符串字面量当作可见文字
      let depth = 0;
      let quote = null;
      let q = p;
      let lit = '';
      for (; q < inner.length; q += 1) {
        const d = inner[q];
        if (quote) {
          if (d === '\\') {
            q += 1;
            continue;
          }
          if (d === quote) {
            quote = null;
            continue;
          }
          lit += d;
          continue;
        }
        if (d === '"' || d === "'" || d === '`') {
          quote = d;
          continue;
        }
        if (d === '{') depth += 1;
        else if (d === '}') {
          depth -= 1;
          if (depth === 0) break;
        }
      }
      out += lit;
      const body = inner.slice(p + 1, q).trim();
      if (body && !body.startsWith('<') && !body.startsWith('/*')) hasExpr = true;
      p = q + 1;
      continue;
    }
    out += c;
    p += 1;
  }
  return { text: out, hasExpr };
}

const files = SCAN_DIRS.flatMap((d) => walk(d));
let scanned = 0;
let buttons = 0;
let iconOnly = 0;
const violations = [];
const wrapperComponents = new Set();

for (const file of files) {
  const src = readFileSync(file, 'utf8');
  scanned += 1;

  // 顺手记录一下自定义按钮组件，报告里提醒人工确认
  for (const m of src.matchAll(/<([A-Z][A-Za-z0-9]*(?:Button|Btn|IconAction))\b/g)) {
    wrapperComponents.add(m[1]);
  }

  let p = 0;
  while (true) {
    const at = src.indexOf('<button', p);
    if (at === -1) break;
    const gt = endOfOpenTag(src, at);
    if (gt === -1) break;
    buttons += 1;

    const attrs = src.slice(at + 7, gt);
    const selfClosing = src[gt - 1] === '/';
    let inner = '';
    let next = gt + 1;
    if (!selfClosing) {
      const close = endOfElement(src, gt + 1);
      if (close === -1) break;
      inner = src.slice(gt + 1, close);
      next = close + 8;
    }

    const { text, hasExpr } = visibleText(inner);
    const hasText = hasExpr || !NON_TEXT.test(text);
    if (!hasText) {
      iconOnly += 1;
      const hasTitle = /(^|\s)title\s*=/.test(attrs);
      const hasAria = /(^|\s)aria-label\s*=/.test(attrs);
      if (!hasTitle || !hasAria) {
        const line = src.slice(0, at).split('\n').length;
        const missing = [!hasTitle && 'title', !hasAria && 'aria-label'].filter(Boolean).join(' 和 ');
        violations.push({ file, line, missing });
      }
    }
    p = next;
  }
}

console.log('-'.repeat(64));
console.log(`扫描 ${scanned} 个文件，${buttons} 个 <button>，其中纯图标 ${iconOnly} 个`);
if (wrapperComponents.size > 0) {
  console.log(`ℹ 见到这些自定义按钮组件（本脚本扫不进去，需组件内部自己保证）：${[...wrapperComponents].join(', ')}`);
}
if (violations.length > 0) {
  for (const v of violations) {
    console.error(`✗ ${relative(ROOT, v.file).split(sep).join('/')}:${v.line} 纯图标按钮缺 ${v.missing}`);
  }
  console.error(`✗ ${violations.length} 个图标按钮鼠标悬停时不显示功能名（验收标准 C3 未通过）`);
  console.error('   修法：给这个 <button> 同时加 title="功能名 —— 一句话说明" 和 aria-label="功能名"');
  process.exit(1);
}
console.log('✓ 每个纯图标按钮都同时有 title 和 aria-label —— 悬停能看见功能名（C3 通过）');
