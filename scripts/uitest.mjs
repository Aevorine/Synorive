/**
 * 界面端到端验证：启动 → 索引 → 搜索 → 截图 + 读取实测值
 * ====================================================================
 * 全程走 CDP，不动真实鼠标键盘，也不要求窗口在最前。
 *
 * 用法：node scripts/uitest.mjs <输出目录> [端口]
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
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? 'JS 出错');
    return r.result.value;
  }
  async shot(name) {
    const s = await this.send('Page.captureScreenshot', { format: 'png' });
    const p = join(outDir, name);
    writeFileSync(p, Buffer.from(s.data, 'base64'));
    console.log(`[ui] 截图 → ${p}`);
  }
  close() {
    this.ws?.close();
  }
}

async function findTarget() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(2000) });
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

// ── 1. 等引擎就绪 ───────────────────────────────────────
console.log('[ui] 等引擎就绪…');
let engine = null;
for (let i = 0; i < 60; i++) {
  engine = await cdp.js('window.synorive.engine.getState()');
  if (engine?.lifecycle === 'ready') break;
  await sleep(1000);
}
console.log(`[ui] 引擎 ${engine?.lifecycle}　端口 ${engine?.port}　启动 ${engine?.bootMs}ms`);
if (engine?.lifecycle !== 'ready') problems.push(`引擎没就绪：${engine?.lifecycle}`);

// ── 2. 索引项目自己的文件 ───────────────────────────────
console.log('[ui] 触发索引…');
const ingest = await cdp.js(`
  (async () => {
    const port = ${engine?.port ?? 0};
    const r = await fetch('http://127.0.0.1:' + port + '/api/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        targets: [
          'D:\\\\Documents\\\\WorkDocuments\\\\Github\\\\Synorive\\\\docs',
          'D:\\\\Documents\\\\WorkDocuments\\\\Github\\\\Synorive\\\\engine\\\\synorive',
          'D:\\\\Documents\\\\WorkDocuments\\\\Github\\\\Synorive\\\\README.md'
        ],
        source: 'file', recursive: true
      })
    });
    return await r.json();
  })()
`);
console.log(`[ui] jobId=${ingest?.jobId}`);

let stats = null;
for (let i = 0; i < 60; i++) {
  await sleep(1500);
  stats = await cdp.js(
    `fetch('http://127.0.0.1:${engine?.port}/api/stats').then(r => r.json())`,
  );
  process.stdout.write(`\r[ui] 已索引 ${stats.items} 条 / ${stats.chunks} 块   `);
  if (stats.items >= 25) break;
}
console.log();
if (!stats || stats.items < 5) problems.push(`索引太少：${JSON.stringify(stats)}`);

// ── 3. 在搜索框里真的敲字 ───────────────────────────────
console.log('[ui] 在搜索框输入「中文分词」…');
await cdp.js(`document.querySelector('.searchbox__input').focus()`);
await cdp.send('Input.insertText', { text: '中文分词' });
await sleep(2500); // 等三级瀑布两波都回来

const probe = await cdp.js(`(() => {
  const cs = (sel, prop) => { const e = document.querySelector(sel); return e ? getComputedStyle(e)[prop] : null; };
  const cards = [...document.querySelectorAll('.card')];
  return {
    query: document.querySelector('.searchbox__input')?.value,
    subtitle: document.querySelector('.page__subtitle')?.textContent,
    resultCount: cards.length,
    firstTitle: cards[0]?.querySelector('.card__title')?.textContent ?? null,
    firstWhy: cards[0]?.querySelector('.card__why')?.textContent ?? null,
    hasHighlight: !!document.querySelector('.card__snippet em'),
    virtualized: !!document.querySelector('.results__inner'),
    totalHeight: document.querySelector('.results__inner')?.style.height ?? null,
    sliders: [...document.querySelectorAll('.slider__label')].map(e => e.textContent),
    // 字体规范复核
    pageTitleSize: cs('.page__title','fontSize'),
    pageTitleFamily: cs('.page__title','fontFamily'),
    cardTitleSize: cs('.card__title','fontSize'),
    snippetSize: cs('.card__snippet','fontSize'),
    searchInputSize: cs('.searchbox__input','fontSize'),
    bodySize: cs('body','fontSize'),
  };
})()`);
console.log('[ui] 实测：');
console.log(JSON.stringify(probe, null, 2));

if (!probe.resultCount) problems.push('搜索没出结果');
if (!probe.virtualized) problems.push('虚拟滚动容器不存在');
if (probe.sliders?.length !== 6) problems.push(`排序滑块应有 6 个，实得 ${probe.sliders?.length}`);
if (probe.bodySize !== '16px') problems.push(`正文字号 ${probe.bodySize} != 16px（小四）`);
if (probe.searchInputSize !== '18.67px') problems.push(`搜索框 ${probe.searchInputSize} != 18.67px（四号）`);
if (probe.pageTitleSize !== '24px') problems.push(`界面主标题 ${probe.pageTitleSize} != 24px（小二）`);
if (probe.cardTitleSize !== '18.67px') problems.push(`结果标题 ${probe.cardTitleSize} != 18.67px（四号）`);
if (!probe.pageTitleFamily?.includes('Source Han Serif')) problems.push('主标题没用思源宋体');

await cdp.shot('search-light.png');

// ── 4. 深色主题 ─────────────────────────────────────────
await cdp.js(`document.documentElement.setAttribute('data-theme','dark')`);
await sleep(700);
await cdp.shot('search-dark.png');
await cdp.js(`document.documentElement.setAttribute('data-theme','light')`);

// ── 5. 滚动帧率（A4 ≥55fps）────────────────────────────
console.log('[ui] 量滚动帧率…');
const fps = await cdp.js(`
  (async () => {
    const el = document.querySelector('.results');
    if (!el) return null;
    const frames = [];
    let last = performance.now();
    let raf;
    const tick = (t) => { frames.push(t - last); last = t; raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    for (let i = 0; i < 60; i++) {
      el.scrollTop = (i * 37) % Math.max(1, el.scrollHeight - el.clientHeight);
      await new Promise(r => requestAnimationFrame(r));
    }
    cancelAnimationFrame(raf);
    const valid = frames.slice(2).filter(d => d > 0 && d < 200);
    valid.sort((a,b)=>a-b);
    const median = valid[Math.floor(valid.length/2)] || 16.7;
    const p95 = valid[Math.floor(valid.length*0.95)] || 16.7;
    return { medianFps: 1000/median, p95Fps: 1000/p95, samples: valid.length,
             scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
  })()
`);
console.log(`[ui] 滚动帧率：中位 ${fps?.medianFps?.toFixed(1)} fps　P95 ${fps?.p95Fps?.toFixed(1)} fps　（滚动区 ${fps?.scrollHeight}px）`);
if (fps && fps.medianFps < 55) problems.push(`滚动中位帧率 ${fps.medianFps.toFixed(1)} < 55（A4 不达标）`);

console.log();
if (problems.length) {
  for (const p of problems) console.error(`✗ ${p}`);
  cdp.close();
  process.exit(1);
}
console.log('✓ 界面端到端全部通过');
cdp.close();
process.exit(0);
