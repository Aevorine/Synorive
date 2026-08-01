// 把编译后的令牌吐成 dist/tokens.css
//
// ⚠️ Windows 坑：动态 import() 不能直接吃 "D:\...\css.js" 这种绝对路径，
//    Node 会把盘符当成未知协议报 ERR_UNSUPPORTED_ESM_URL_SCHEME。
//    必须用 pathToFileURL() 转成 file:// URL。
import { writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const cssJs = join(here, 'dist', 'css.js');

if (!existsSync(cssJs)) {
  console.error(`[design-tokens] FAIL: 没找到 ${cssJs} —— tsc 是不是没跑成功？`);
  process.exit(1);
}

const { generateCss } = await import(pathToFileURL(cssJs).href);

const out = join(here, 'dist', 'tokens.css');
mkdirSync(dirname(out), { recursive: true });
const css = generateCss();
writeFileSync(out, css, 'utf8');

const varCount = (css.match(/--syn-[a-z0-9-]+:/g) || []).length;
console.log(`[design-tokens] wrote ${out}`);
console.log(`[design-tokens] ${css.length} bytes, ${varCount} 个 CSS 变量`);

// 静默失败自查：生成了但内容是空的，比没生成更难发现
if (css.length < 2000 || varCount < 60) {
  console.error(`[design-tokens] FAIL: 产物可疑（${css.length} 字节 / ${varCount} 变量），期望 ≥2000 字节且 ≥60 变量`);
  process.exit(1);
}
