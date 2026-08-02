/**
 * 逐页巡检：走遍全部六个页面，每页截图 + 读关键实测值。
 *
 * 只截首页是不够的 —— 界面最容易出问题的地方恰恰是那些
 * "写完就没再打开过"的次要页面。
 *
 * 用法：node scripts/uitour.mjs <输出目录> [端口]
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const outDir = process.argv[2] ?? '.';
const port = Number(process.argv[3] ?? 9222);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
mkdirSync(outDir, { recursive: true });

class Cdp {
  constructor(url) {
    this.url = url;
    this.id = 0;
    this.pending = new Map();
  }
  async connect() {
    this.ws = new WebSocket(this.url);
    await new Promise((res, rej) => {
      this.ws.addEventListener('open', res, { once: true });
      this.ws.addEventListener('error', () => rej(new Error('CDP 连接失败')), { once: true });
    });
    this.ws.addEventListener('message', (ev) => {
      const m = JSON.parse(String(ev.data));
      const p = this.pending.get(m.id);
      if (p) {
        this.pending.delete(m.id);
        m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.delete(id)) rej(new Error(`CDP ${method} 超时`));
      }, 30_000);
    });
  }
  async js(expression) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.exception?.description ?? 'JS 出错');
    }
    return r.result.value;
  }
  async shot(name) {
    const s = await this.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(outDir, name), Buffer.from(s.data, 'base64'));
  }
  close() {
    this.ws?.close();
  }
}

async function findTarget() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`, {
        signal: AbortSignal.timeout(2000),
      });
      const t = (await r.json()).find((x) => x.type === 'page' && x.webSocketDebuggerUrl);
      if (t) return t;
    } catch {
      /* 还没起来 */
    }
    await sleep(400);
  }
  throw new Error('30 秒内没找到页面');
}

const target = await findTarget();
const cdp = new Cdp(target.webSocketDebuggerUrl);
await cdp.connect();
await cdp.send('Page.enable');
await cdp.send('Runtime.enable');

const problems = [];

// 等引擎就绪
let engine = null;
for (let i = 0; i < 60; i++) {
  engine = await cdp.js('window.synorive.engine.getState()');
  if (engine?.lifecycle === 'ready') break;
  await sleep(1000);
}
console.log(`引擎 ${engine?.lifecycle}　端口 ${engine?.port}　启动 ${engine?.bootMs}ms`);
if (engine?.lifecycle !== 'ready') problems.push(`引擎没就绪：${engine?.lifecycle}`);

// 索引一点内容，否则所有页面都是空状态，测不出真东西
console.log('索引语料…');
await cdp.js(`
  fetch('http://127.0.0.1:${engine?.port}/api/ingest', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ targets: [
      'D:\\\\Documents\\\\WorkDocuments\\\\Github\\\\Synorive\\\\docs',
      'D:\\\\Documents\\\\WorkDocuments\\\\Github\\\\Synorive\\\\engine\\\\synorive',
      'D:\\\\Documents\\\\WorkDocuments\\\\Github\\\\Synorive\\\\apps\\\\desktop\\\\resources\\\\icons'
    ], source:'file', recursive:true })
  }).then(r=>r.json())
`);
for (let i = 0; i < 50; i++) {
  await sleep(1500);
  const s = await cdp.js(
    `fetch('http://127.0.0.1:${engine?.port}/api/stats').then(r=>r.json())`,
  );
  process.stdout.write(`\r  ${s.items} 条 / ${s.chunks} 块   `);
  if (s.items >= 40) break;
}
console.log();

// ── 逐页巡检 ────────────────────────────────────────────────
const PAGES = [
  { id: 'search', name: '搜索', probe: '.searchbox__input', pre: async () => {
      await cdp.js(`document.querySelector('.searchbox__input').focus()`);
      await cdp.send('Input.insertText', { text: '中文分词' });
      await sleep(2500);
    } },
  { id: 'library', name: '文件管理器', probe: '.filterbar' },
  { id: 'analyze', name: '分析中心', probe: '.deplist' },
  { id: 'timeline', name: '时间轴', probe: '.timeline, .empty' },
  { id: 'graph', name: '知识图谱', probe: '.entitygrid, .empty' },
  { id: 'settings', name: '设置', probe: '.segmented' },
];

for (const p of PAGES) {
  // 通过点侧栏切页，走真实交互路径而不是直接改 store
  const clicked = await cdp.js(`(() => {
    const items = [...document.querySelectorAll('.sidebar__item')];
    const el = items.find(e => e.textContent.trim() === ${JSON.stringify(p.name)});
    if (!el) return false;
    el.click();
    return true;
  })()`);
  if (!clicked) {
    problems.push(`侧栏里点不到「${p.name}」`);
    continue;
  }
  await sleep(1400);
  if (p.pre) await p.pre();

  const info = await cdp.js(`(() => {
    const cs = (sel, prop) => { const e = document.querySelector(sel); return e ? getComputedStyle(e)[prop] : null; };
    const title = document.querySelector('.page__title');
    return {
      title: title?.textContent ?? null,
      titleSize: cs('.page__title','fontSize'),
      titleFamily: (cs('.page__title','fontFamily')||'').includes('Source Han Serif'),
      probeFound: !!document.querySelector(${JSON.stringify(p.probe)}),
      hasEmpty: !!document.querySelector('.empty'),
      hasError: !!document.querySelector('.banner--error'),
      errorText: document.querySelector('.banner--error')?.textContent ?? null,
      bodyText: (document.querySelector('.page__body')?.textContent ?? '').slice(0, 90).replace(/\\s+/g,' '),
    };
  })()`);

  const mark = info.probeFound && !info.hasError ? '✓' : '✗';
  console.log(`${mark} ${p.name.padEnd(8)} 标题「${info.title}」${info.titleSize} ` +
              `思源=${info.titleFamily}　${info.hasEmpty ? '空状态' : '有内容'}`);
  if (info.errorText) console.log(`    ⚠ ${info.errorText.slice(0, 120)}`);
  if (info.bodyText) console.log(`    ${info.bodyText}`);

  if (!info.probeFound) problems.push(`${p.name}：找不到关键元素 ${p.probe}`);
  if (info.hasError) problems.push(`${p.name}：页面报错 ${info.errorText?.slice(0, 100)}`);
  if (info.titleSize !== '24px') problems.push(`${p.name}：主标题 ${info.titleSize} != 24px`);
  if (!info.titleFamily) problems.push(`${p.name}：主标题没用思源宋体`);
  if (info.title !== p.name) problems.push(`${p.name}：标题显示为「${info.title}」`);

  await cdp.shot(`page-${p.id}.png`);
}

// 深色模式各页各截一张
await cdp.js(`document.documentElement.setAttribute('data-theme','dark')`);
for (const p of ['library', 'analyze', 'graph']) {
  const name = PAGES.find((x) => x.id === p).name;
  await cdp.js(`(() => {
    const el = [...document.querySelectorAll('.sidebar__item')].find(e => e.textContent.trim() === ${JSON.stringify('')} );
  })()`);
  await cdp.js(`(() => {
    const items = [...document.querySelectorAll('.sidebar__item')];
    const el = items.find(e => e.textContent.trim() === ${JSON.stringify(name)});
    if (el) el.click();
  })()`);
  await sleep(1000);
  await cdp.shot(`dark-${p}.png`);
}
await cdp.js(`document.documentElement.setAttribute('data-theme','light')`);

console.log();
console.log(`截图 ${PAGES.length + 3} 张 → ${outDir}`);
if (problems.length) {
  for (const x of problems) console.error(`✗ ${x}`);
  cdp.close();
  process.exit(1);
}
console.log('✓ 六个页面全部通过');
cdp.close();
process.exit(0);
