#!/usr/bin/env node
/**
 * MCP 联网工具 —— 用真正的 MCP 客户端连一次
 * ============================================================
 * 「编译过」和「Claude Code 真能调通」是两件事。中间隔着：
 * 工具有没有注册上、schema 合不合法、返回是不是把协议搞坏了、
 * 以及最要命的一条 —— **stdout 有没有被日志污染**
 * （症状是"服务器无响应"，不是报错，很难往这个方向想）。
 *
 * 跑：node scripts/test-mcp-web.mjs
 */
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SERVER = join(ROOT, 'mcp', 'dist', 'index.js');

const problems = [];
const skipped = [];
const ok = (c, good, bad) => {
  console.log(`  ${c ? '✓' : '✗'} ${c ? good : bad}`);
  if (!c) problems.push(bad);
  return c;
};
const line = '─'.repeat(70);

const WEB_TOOLS = [
  'synorive_web_search',
  'synorive_research',
  'synorive_scholar',
  'synorive_read_url',
  'synorive_web_engines',
];

const client = new Client({ name: 'synorive-web-test', version: '1.0.0' }, { capabilities: {} });
const transport = new StdioClientTransport({
  command: process.execPath,
  args: [SERVER],
  stderr: 'pipe',
});

await client.connect(transport);

try {
  console.log(line);
  console.log('① 工具注册 —— 13 个都在，且新加的五个 schema 合法');
  console.log(line);
  const { tools } = await client.listTools();
  const names = tools.map((t) => t.name).sort();
  console.log(`  已注册 ${tools.length} 个：${names.join(', ')}`);
  ok(tools.length >= 13, `共 ${tools.length} 个工具`, `只有 ${tools.length} 个工具，少于 13`);
  for (const t of WEB_TOOLS) {
    ok(names.includes(t), `${t} 已注册`, `${t} 没注册上`);
  }
  // 描述里必须写清能力边界，否则 Claude 会把标注当成事实判定
  const ws = tools.find((t) => t.name === 'synorive_web_search');
  ok(
    /判断不了|不要把低信誉/.test(ws?.description ?? ''),
    'web_search 的描述里写明了「判断不了这句话本身是不是事实」',
    'web_search 的描述没写能力边界 —— Claude 会把可信度标注当成事实核查结果',
  );
  const rs = tools.find((t) => t.name === 'synorive_research');
  ok(
    /逐字存在|不是改写/.test(rs?.description ?? ''),
    'research 的描述里写明了「逐字摘录、不是改写总结」',
    'research 的描述没说清是摘录不是生成',
  );

  console.log();
  console.log(line);
  console.log('② synorive_web_engines —— 引擎清单与熔断状态');
  console.log(line);
  const eng = await client.callTool({ name: 'synorive_web_engines', arguments: {} });
  const engText = eng.content?.[0]?.text ?? '';
  ok(!eng.isError, '调用成功', `调用失败：${engText.slice(0, 200)}`);
  ok(/网页搜索/.test(engText) && /学术文献源/.test(engText), '分组显示正确', '分组没显示');
  ok(
    /要浏览器渲染/.test(engText),
    'Google/Yandex 标出了「要浏览器渲染」而不是笼统地不可用',
    '拿不到结果的引擎没说明原因',
  );
  console.log(
    engText
      .split('\n')
      .slice(0, 6)
      .map((l) => `    ${l}`)
      .join('\n'),
  );

  console.log();
  console.log(line);
  console.log('③ synorive_web_search —— 结果必须带可信度标注');
  console.log(line);
  const ws1 = await client.callTool({
    name: 'synorive_web_search',
    arguments: { query: 'sqlite wal 模式', limit: 6 },
  });
  const t1 = ws1.content?.[0]?.text ?? '';
  if (ws1.isError || /没有结果/.test(t1)) {
    skipped.push(`联网搜索：没拿到结果（${t1.slice(0, 90)}）`);
    console.log(`  ⚠ 跳过：${t1.slice(0, 120)}`);
  } else {
    ok(t1.length > 100, `返回 ${t1.length} 字`, '返回内容太短');
    ok(
      /官方|学术|主流媒体|社区|未收录|低信誉/.test(t1),
      '每条带来源分级',
      '结果里没有来源分级标注',
    );
    ok(/孤证|独立来源/.test(t1), '标了孤证 / 多源印证', '没标独立来源数');
    ok(!/<em>|<\/em>/.test(t1), '没有残留的高亮标记', '返回里有 <em> 标记，说明没清理');
    console.log(
      t1
        .split('\n')
        .slice(0, 8)
        .map((l) => `    ${l}`)
        .join('\n'),
    );
  }

  console.log();
  console.log(line);
  console.log('④ synorive_scholar —— 文献检索');
  console.log(line);
  const sc = await client.callTool({
    name: 'synorive_scholar',
    arguments: { query: 'write-ahead logging recovery', limit: 8 },
  });
  const t2 = sc.content?.[0]?.text ?? '';
  if (sc.isError || /没有结果/.test(t2)) {
    skipped.push(`文献检索：没拿到结果（${t2.slice(0, 90)}）`);
    console.log(`  ⚠ 跳过：${t2.slice(0, 120)}`);
  } else {
    ok(/按 DOI 合并为/.test(t2), '报出了合并前后的条数', '没报合并情况');
    ok(/DOI:/.test(t2), '带 DOI（可精确定位）', '没带 DOI');
    ok(/被引 \d+/.test(t2), '带被引数', '没带被引数');
    ok(/收录于/.test(t2), '标出每篇被哪几家收录', '没标来源');
    console.log(
      t2
        .split('\n')
        .slice(0, 9)
        .map((l) => `    ${l}`)
        .join('\n'),
    );
  }

  console.log();
  console.log(line);
  console.log('⑤ synorive_read_url —— 链接秒析 + SSRF 防护必须仍然生效');
  console.log(line);
  const bad = await client.callTool({
    name: 'synorive_read_url',
    arguments: { url: 'http://192.168.1.1/admin' },
  });
  const tb = bad.content?.[0]?.text ?? '';
  ok(
    bad.isError === true && /内网/.test(tb),
    `内网地址被拒：${tb.slice(0, 60)}`,
    `内网地址没被拒！返回：${tb.slice(0, 120)} —— 这条通道能被用来探内网`,
  );

  const good = await client.callTool({
    name: 'synorive_read_url',
    arguments: { url: 'https://www.sqlite.org/wal.html', maxChars: 1200 },
  });
  const tg = good.content?.[0]?.text ?? '';
  if (good.isError) {
    skipped.push(`链接秒析：抓取失败（${tg.slice(0, 90)}）`);
    console.log(`  ⚠ 跳过：${tg.slice(0, 120)}`);
  } else {
    ok(/站点：/.test(tg), '返回了站点信息', '没返回站点');
    ok(/来源分级/.test(tg), '返回了来源分级', '没返回来源分级');
    ok(tg.length > 400, `正文 ${tg.length} 字`, '正文太短，可能没抓到');
  }

  console.log();
  console.log(line);
  console.log('⑥ 错误处理 —— 参数不合法要被 schema 拦，不能悄悄按默认值跑');
  console.log(line);
  let threw = false;
  try {
    const r = await client.callTool({
      name: 'synorive_scholar',
      arguments: { query: 'x', limit: 9999 },
    });
    threw = r.isError === true;
  } catch {
    threw = true;
  }
  ok(threw, 'limit=9999 被拦下', 'limit=9999 没被拦，会静默按别的值跑');
} finally {
  await client.close().catch(() => {});
}

console.log();
console.log('='.repeat(70));
for (const s of skipped) console.log(`⚠ 跳过（不算通过）：${s}`);
for (const p of problems) console.log(`✗ ${p}`);
if (problems.length) process.exit(1);
console.log(`✓ MCP 联网工具通过${skipped.length ? '（含上面标注的跳过项）' : ''}`);
