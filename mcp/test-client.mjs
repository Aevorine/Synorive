/**
 * 用真正的 MCP 客户端连自己的服务器，逐个调工具。
 * 只看"编译通过"是不够的 —— MCP 最常见的失败是
 * stdout 被日志污染导致协议直接坏掉，那种编译期完全看不出来。
 *
 * 跑：node mcp/test-client.mjs
 */

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const serverPath = join(here, 'dist', 'index.js');
const repoRoot = resolve(here, '..');

const failures = [];
function check(cond, msg) {
  if (!cond) failures.push(msg);
  return cond;
}

const transport = new StdioClientTransport({
  command: process.execPath,
  args: [serverPath],
  env: {
    ...process.env,
    SYNORIVE_DATA_DIR: join(repoRoot, 'data'),
  },
  stderr: 'pipe',
});

const client = new Client({ name: 'synorive-test', version: '0.1.0' });

console.log('连接 MCP 服务器…');
await client.connect(transport);
console.log('已连接\n');

// ── ① 工具清单 ─────────────────────────────────────────────
console.log('='.repeat(70));
console.log('① 工具清单');
console.log('='.repeat(70));
const { tools } = await client.listTools();
for (const t of tools) {
  const params = Object.keys(t.inputSchema?.properties ?? {});
  console.log(`  ${t.name.padEnd(24)} ${(t.title || '').padEnd(20)} 参数：${params.join(', ') || '无'}`);
}
check(tools.length === 8, `工具数应为 8，实得 ${tools.length}`);
const names = new Set(tools.map((t) => t.name));
for (const n of [
  'synorive_search', 'synorive_ingest', 'synorive_analyze', 'synorive_get_content',
  'synorive_similar', 'synorive_timeline', 'synorive_graph', 'synorive_status',
]) {
  check(names.has(n), `缺工具 ${n}`);
}

async function call(name, args, label) {
  console.log();
  console.log('='.repeat(70));
  console.log(label ?? name);
  console.log('='.repeat(70));
  const t0 = Date.now();
  const r = await client.callTool({ name, arguments: args });
  const dt = Date.now() - t0;
  const text = r.content?.map((c) => c.text ?? '').join('\n') ?? '';
  console.log(text.split('\n').slice(0, 22).map((l) => '  ' + l).join('\n'));
  if (text.split('\n').length > 22) console.log('  …');
  console.log(`  [${dt}ms${r.isError ? '  ⚠ isError' : ''}]`);
  return { text, isError: !!r.isError };
}

// ── ② 状态（也验证引擎发现机制）─────────────────────────────
const st = await call('synorive_status', {}, '② synorive_status —— 顺带验证引擎自动发现');
check(!st.isError, `status 报错：${st.text.slice(0, 200)}`);
check(/索引内容\s+\d+\s+条/.test(st.text), 'status 没返回索引条数');

// ── ③ 投喂 ─────────────────────────────────────────────────
const ing = await call(
  'synorive_ingest',
  { targets: [join(repoRoot, 'docs')], recursive: true, tags: ['mcp测试'] },
  '③ synorive_ingest',
);
check(!ing.isError, `ingest 报错：${ing.text.slice(0, 200)}`);
check(/任务号/.test(ing.text), 'ingest 没返回任务号');

await new Promise((r) => setTimeout(r, 12000));

// ── ④ 检索 ─────────────────────────────────────────────────
const s1 = await call(
  'synorive_search',
  { query: '中文分词的选型', limit: 3 },
  '④ synorive_search —— 中文语义检索',
);
check(!s1.isError, `search 报错：${s1.text.slice(0, 200)}`);
check(/相关度/.test(s1.text), 'search 没返回结果');

const s2 = await call(
  'synorive_search',
  { query: 'type:md 界面', limit: 3 },
  '⑤ synorive_search —— D10 语法（应识别出扩展名筛选）',
);
check(/识别到的筛选/.test(s2.text), 'D10 语法没被识别');

// ── ⑥ 取正文 ───────────────────────────────────────────────
const m = s1.text.match(/id：(\w+)/);
if (m) {
  const c = await call(
    'synorive_get_content',
    { itemId: m[1], maxChars: 500 },
    '⑥ synorive_get_content',
  );
  check(!c.isError && c.text.length > 100, '取正文失败');

  const sim = await call(
    'synorive_similar',
    { itemId: m[1], limit: 3 },
    '⑦ synorive_similar',
  );
  check(!sim.isError, `similar 报错：${sim.text.slice(0, 160)}`);
} else {
  failures.push('搜索结果里没有 id，取正文和相似内容测不了');
}

// ── ⑧ 时间轴与图谱 ─────────────────────────────────────────
const tl = await call('synorive_timeline', { bucket: 'day', limit: 10 }, '⑧ synorive_timeline');
check(!tl.isError, `timeline 报错：${tl.text.slice(0, 160)}`);

const g = await call('synorive_graph', { limit: 10 }, '⑨ synorive_graph');
check(!g.isError, `graph 报错：${g.text.slice(0, 160)}`);

// ── ⑩ 错误处理 ──────────────────────────────────────────────
// 两种错要分开测：**参数不合法**该被 schema 提前拦下（SDK 层报错），
// **参数合法但对象不存在**才走到我的处理函数里（我要给人话）。
// 第一版把两者混为一谈，用了一个低于下限的 maxChars，
// 结果测到的是 SDK 的校验而不是我的错误处理 —— 测错了东西。
const badSchema = await call(
  'synorive_get_content',
  { itemId: 'x', maxChars: 100 },
  '⑩a 参数不合法 —— schema 该提前拦下',
);
check(badSchema.isError, '低于下限的参数应该被 schema 拦下');
check(/validation|Invalid/i.test(badSchema.text), 'schema 校验没生效');

const badId = await call(
  'synorive_get_content',
  { itemId: 'deadbeefdeadbeefdeadbeef', maxChars: 500 },
  '⑩b 参数合法但 id 不存在 —— 该给人话',
);
check(badId.isError, '不存在的 id 应该返回 isError');
check(/出错了/.test(badId.text), `错误信息应该是人话，实得：${badId.text.slice(0, 120)}`);

console.log();
console.log('='.repeat(70));
await client.close();
if (failures.length) {
  for (const f of failures) console.error(`✗ ${f}`);
  process.exit(1);
}
console.log('✓ MCP 全部通过');
process.exit(0);
