#!/usr/bin/env node
/**
 * 生成引擎源码的完整性清单
 * ====================================================================
 * 打包时把 `engine/synorive/**.py` 每个文件的 SHA-256 写进一张清单。
 * 引擎启动时按这张清单核对 —— 对不上就拒绝跑，并说清是哪个文件。
 *
 * ## 它防的是什么，不防什么
 *
 * **防**：装好之后有人（恶意软件、共用电脑的其他人、被篡改的更新包）
 *        往引擎里塞一行代码。引擎的源码是**随包分发的明文 .py**，
 *        改一行就能把你的资料悄悄发出去，而且完全看不出来。
 *
 * **不防**：能改清单本身的人。清单和源码放在一起，改了源码顺手改清单就绕过了。
 *        要防那个需要代码签名（清单被签在 exe 里）—— 见 electron-builder.yml。
 *        所以这一条是**和代码签名配套的**，不是替代品。
 *        说清楚这一点比装作它是万能的重要。
 *
 * 用法：node scripts/build-integrity.mjs
 * 由 `pack:win` 在打包前自动调用。
 */

import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PKG = join(ROOT, 'engine', 'synorive');
const OUT = join(PKG, 'integrity.json');

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    if (name === '__pycache__' || name === 'integrity.json') continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (p.endsWith('.py') || p.endsWith('.sql')) out.push(p);
  }
  return out;
}

const files = walk(PKG).sort();
const manifest = {};
for (const f of files) {
  // 🔴 用 posix 风格的相对路径做键。Windows 上写 `\` 的话，
  //    引擎在别的平台上核对时一条都对不上 —— 而"全部对不上"会被
  //    当成"整个包被改过"，直接拒绝启动。
  const key = relative(PKG, f).split(sep).join('/');
  manifest[key] = createHash('sha256').update(readFileSync(f)).digest('hex');
}

writeFileSync(
  OUT,
  `${JSON.stringify({ version: 1, files: manifest }, null, 0)}\n`,
  'utf8',
);
console.log(`[integrity] 写出 ${relative(ROOT, OUT)}：${files.length} 个文件`);
