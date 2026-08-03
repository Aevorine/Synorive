/**
 * 硬编码样式扫描 —— 验收标准 B4
 * ====================================================================
 * 「界面要统一」这条要求靠自觉做不到，只能靠"根本没有第二个地方能定义颜色"。
 * 这个脚本就是那道闸：业务代码里出现色值或裸字号，直接判失败。
 *
 * 放行的地方只有两处：
 *   packages/design-tokens/  —— 令牌本体，色值就该写在那儿
 *   src/styles/global.css / shell.css 里引用 var(--syn-*) 的行
 *
 * 用法：node scripts/check-hardcoded-style.mjs
 * 退出码 0 = 通过，1 = 发现硬编码
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

/** 要扫的目录 */
const SCAN_DIRS = [
  join(ROOT, 'apps', 'desktop', 'src'),
  join(ROOT, 'apps', 'desktop', 'electron'),
];

/** 白名单：这些路径里允许出现字面量 */
const ALLOW_PATHS = [
  `packages${sep}design-tokens`,
  `src${sep}styles${sep}fonts.css`,
  `src${sep}styles${sep}fonts${sep}`,
];

const SCAN_EXT = new Set(['.ts', '.tsx', '.css', '.js', '.jsx']);

/**
 * 违规规则
 *
 * ⚠️ 这里**故意不用**否定预查 `(?!var\()` 来排除合法写法。
 *    `/font-size\s*:\s*(?!var\()[^;]+/` 看着对，实际会误报：
 *    `\s*` 能回溯成零宽，位置停在空格上，`(?!var\()` 于是判定成立
 *    （下一个字符是空格不是 v），把全部正确写法都报成违规。
 *    改成：先把值整个捕获出来，再用普通代码判断它是不是 var(…)。
 *    正则的表达力不该用在这种地方 —— 判断逻辑写成代码才看得懂、才测得了。
 */
const RULES = [
  {
    id: 'hex-color',
    re: /#[0-9a-fA-F]{3,8}\b/g,
    msg: '硬编码十六进制色值',
    // 主进程设窗口背景色必须给字面量（BrowserWindow 不认 CSS 变量），
    // 那一处写 allow-hardcoded 注释放行
    ok: (_value, line) => line.includes('allow-hardcoded'),
  },
  {
    id: 'rgb-color',
    re: /\brgba?\s*\(\s*\d+[\s,]/g,
    msg: '硬编码 rgb()/rgba() 色值',
    ok: (_value, line) => line.includes('allow-hardcoded'),
  },
  {
    id: 'font-size',
    re: /font-size\s*:\s*([^;{}]+)/g,
    msg: 'font-size 没走令牌变量',
    ok: (value) => /^var\(--syn-/.test(value.trim()) || value.trim() === 'inherit',
  },
  {
    id: 'font-family',
    re: /font-family\s*:\s*([^;{}]+)/g,
    msg: 'font-family 没走令牌变量',
    ok: (value, _line, ctx) =>
      ctx.inFontFace || /^var\(--syn-/.test(value.trim()) || value.trim() === 'inherit',
  },
];

function walk(dir, out = []) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === 'out' || name === 'dist') continue;
      walk(p, out);
    } else if (SCAN_EXT.has(extname(name))) {
      out.push(p);
    }
  }
  return out;
}

function isAllowed(path) {
  return ALLOW_PATHS.some((a) => path.includes(a));
}

let violations = 0;
let scanned = 0;

for (const dir of SCAN_DIRS) {
  for (const file of walk(dir)) {
    if (isAllowed(file)) continue;
    scanned += 1;

    const text = readFileSync(file, 'utf8');
    const lines = text.split('\n');
    let inFontFace = false;

    lines.forEach((line, i) => {
      if (/@font-face/.test(line)) inFontFace = true;
      if (inFontFace && line.includes('}')) inFontFace = false;

      // 注释行不算
      const trimmed = line.trim();
      if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) return;

      // 豁免标记允许写在上面几行的注释里（长行后面再挂注释太丑）
      const nearby = lines.slice(Math.max(0, i - 3), i + 1).join('\n');

      for (const rule of RULES) {
        rule.re.lastIndex = 0;
        let m;
        while ((m = rule.re.exec(line)) !== null) {
          // 有捕获组就用捕获组的值，没有（色值类）就用整个匹配
          const value = m[1] ?? m[0];
          if (rule.ok(value, nearby, { inFontFace })) continue;

          violations += 1;
          console.error(
            `✗ ${relative(ROOT, file)}:${i + 1}  ${rule.msg}\n    ${trimmed.slice(0, 110)}`,
          );
        }
      }
    });
  }
}

// ────────────────────────────────────────────────────────────────
// F9 —— 锚点 5 令牌自检
// ────────────────────────────────────────────────────────────────
// 上面那一轮只保证「业务代码里没有字面量」。但那不足以保证
// **界面真的是宋体小四 + Times New Roman** —— 只要令牌本身写错了，
// 全应用会整整齐齐地一起错，而且扫描器一个字都不会报。
//
// 这是"检查了但检查的不是要害"的典型：合规率 100%，结论却可能是错的。
// 所以这一段直接去读令牌文件，断言那几个值本身对不对。
const TOKENS = join(ROOT, 'packages', 'design-tokens', 'dist', 'tokens.css');

/** 锚点 5 要求的字号（1pt = 1.3333px），容差 0.5px 给四舍五入 */
const REQUIRED_SIZES = [
  ['--syn-fs-body', 16, '正文＝小四'],
  ['--syn-fs-emphasis', 18.67, '强调＝四号'],
  ['--syn-fs-section-title', 21.33, '区块标题＝三号'],
  ['--syn-fs-page-title', 24, '页面标题＝小二'],
];

let anchorFails = 0;
try {
  const css = readFileSync(TOKENS, 'utf8');

  const grab = (name) => {
    const m = new RegExp(`${name}\\s*:\\s*([^;]+);`).exec(css);
    return m ? m[1].trim() : null;
  };

  for (const [name, want, why] of REQUIRED_SIZES) {
    const raw = grab(name);
    const got = raw ? Number.parseFloat(raw) : NaN;
    if (!Number.isFinite(got) || Math.abs(got - want) > 0.5) {
      anchorFails += 1;
      console.error(`✗ 令牌 ${name} = ${raw ?? '（没找到）'}，锚点 5 要求 ${want}px（${why}）`);
    }
  }

  // 西文 Times New Roman 必须**排在最前**：字体是逐字符回退的，
  // 排在宋体后面的话西文会先被宋体的内嵌西文字形吃掉，
  // 症状是"看着是有衬线，但不是 Times New Roman"——极难用肉眼发现
  for (const name of ['--syn-ff-body', '--syn-ff-display', '--syn-ff-tabular']) {
    const raw = grab(name) ?? '';
    if (!/^"Times New Roman"/.test(raw)) {
      anchorFails += 1;
      console.error(`✗ 令牌 ${name} 的第一顺位不是 "Times New Roman"：${raw || '（没找到）'}`);
    }
    if (!/SimSun|宋体|Serif SC/.test(raw)) {
      anchorFails += 1;
      console.error(`✗ 令牌 ${name} 里没有中文宋体回退：${raw || '（没找到）'}`);
    }
  }
} catch (e) {
  anchorFails += 1;
  console.error(`✗ 读不到令牌文件 ${relative(ROOT, TOKENS)}：${e.message}`);
  console.error('   （令牌是构建产物，先跑一次 npm run build --workspace=@synorive/design-tokens）');
}

console.log('-'.repeat(64));
console.log(`扫描 ${scanned} 个文件`);
if (violations > 0) {
  console.error(`✗ 发现 ${violations} 处硬编码样式 —— 一律改成 var(--syn-*) 令牌`);
}
if (anchorFails > 0) {
  console.error(`✗ 锚点 5 令牌自检失败 ${anchorFails} 项 —— 字体/字号规则本身就写错了`);
}
if (violations > 0 || anchorFails > 0) process.exit(1);
console.log('✓ 零硬编码色值与字号，全部走设计令牌（验收标准 B4 通过）');
console.log('✓ 锚点 5：正文小四／强调四号／西文 Times New Roman 优先，令牌值本身也对（F9）');
