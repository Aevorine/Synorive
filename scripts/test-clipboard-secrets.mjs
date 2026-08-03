/**
 * E4 剪贴板哨兵 · 密钥识别的正反用例
 * ====================================================================
 * 这个守卫拦的是"密码/验证码/私钥被自动收进一个可全文检索、还能被
 * Claude Code 通过 MCP 读到的库"。**它错一次的代价和平时的 bug 不是一个量级。**
 *
 * 所以两个方向都要测，缺一不可：
 *   漏拦（该拦没拦）→ 隐私事故
 *   误拦（不该拦拦了）→ 功能残废。实测就误拦过网址，而链接恰恰是最该收的一类，
 *                       「自动归档纯链接」整个功能会静默失效。
 *
 * 直接从 clipboard.ts 里抠出那两个纯函数跑，不起 Electron ——
 * 测的是真源码，不是拷贝出来的一份副本（副本会和实现慢慢脱节）。
 *
 * 用法：node scripts/test-clipboard-secrets.mjs
 */

import { readFileSync } from 'node:fs';

const src = readFileSync('apps/desktop/electron/main/clipboard.ts', 'utf8');
const from = src.indexOf('const SECRET_PATTERNS');
const to = src.indexOf('const URL_ONLY');
if (from < 0 || to < 0) {
  console.error('✗ 在 clipboard.ts 里找不到 SECRET_PATTERNS…URL_ONLY 这段 —— 实现改了，这个测试要跟着改');
  process.exit(1);
}

const body = src
  .slice(from, to)
  .replace(/: RegExp\[\]/g, '')
  .replace(/\(s: string\)/g, '(s)')
  .replace(/: boolean/g, '')
  .replace(/export /g, '');

const mod = await import(
  'data:text/javascript;base64,' +
    Buffer.from(body + '\nexport { looksLikeSecret, looksHighEntropy };').toString('base64')
);

/** 必须拦下来 —— 漏一条就是隐私事故 */
const MUST_BLOCK = [
  ['OpenAI key', 'sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'],
  ['GitHub token', 'ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456'],
  ['私钥', '-----BEGIN RSA PRIVATE KEY-----\nMIIEow...'],
  ['Bearer', 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdefghijklmno'],
  ['JWT', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc'],
  ['写着密码', 'password: Tr0ub4dor&3'],
  ['短信验证码（整段就是它）', '482915'],
  ['管理器随机密码', 'xK9#mQ2$vL7@pR4!'],
  // 12~15 位这一段是最常见的手工密码长度，端到端实测漏过一次
  ['13 位随机密码', 'p7!Nq9@Wz4#Rb'],
  ['12 位随机密码', 'Zx4$Kd2!Nq9@'],
  ['随机长串', 'aB3dE7gH1jK4mN8pQ2sT'],
  // 结构化豁免不能变成后门：URL 里带着 token 照样得拦
  ['带 token 的网址', 'https://api.example.com/v1?key=sk-AbCdEfGhIjKlMnOpQrStUvWx0123'],
];

/** 必须放过去 —— 拦一条就有功能被静默弄残 */
const MUST_PASS = [
  ['普通中文', '明天下午三点开会，记得把上周的数据整理好带过来'],
  ['一句英文', 'The quick brown fox jumps over the lazy dog'],
  ['网址', 'https://github.com/Aevorine/Synorive'],
  ['带查询串的网址', 'https://www.example.com/search?q=%E5%90%91%E9%87%8F%E6%A3%80%E7%B4%A2&page=2'],
  ['Windows 路径', 'D:\\Documents\\WorkDocuments\\Github\\Synorive\\README.md'],
  ['Unix 路径', '/usr/local/share/fonts/NotoSerifSC-Regular.otf'],
  ['代码片段', 'const total = items.reduce((a, b) => a + b.size, 0);'],
  ['句子里含 6 位数', '这个季度营收 482915 元，比上季度增长了两成'],
  ['短词', '向量检索'],
  ['带连字符的标题', '2026-08-02 周会纪要'],
  // 收紧到 12 位之后最容易被误伤的一类：词+数字拼出来的标识符
  ['词+数字标识符', 'Synorive2026'],
  ['驼峰变量名', 'clipboardAutoArchiveLinks'],
  ['产品型号', 'iPhone15ProMax'],
  ['提交短哈希', 'a371bad'],
];

let bad = 0;
console.log('必须拦下来的（漏 = 隐私事故）：');
for (const [name, s] of MUST_BLOCK) {
  const blocked = mod.looksLikeSecret(s);
  if (!blocked) bad++;
  console.log(`  ${blocked ? '✓' : '✗ 漏了'} ${name.padEnd(24)} ${JSON.stringify(s.slice(0, 46))}`);
}

console.log('\n必须放过去的（拦 = 功能残废）：');
for (const [name, s] of MUST_PASS) {
  const blocked = mod.looksLikeSecret(s);
  if (blocked) bad++;
  console.log(`  ${blocked ? '✗ 误拦' : '✓'} ${name.padEnd(24)} ${JSON.stringify(s.slice(0, 46))}`);
}

console.log();
if (bad) {
  console.error(`✗ ${bad} 条不对`);
  process.exit(1);
}
console.log(`✓ ${MUST_BLOCK.length + MUST_PASS.length} 条正反用例全过`);
