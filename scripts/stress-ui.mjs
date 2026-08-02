/**
 * A9 满负载 UI 掉帧 + A19 五万文件不崩
 * ====================================================================
 * 这两条要一起测才有意义：满负载恰恰就是大批量投喂的时候。
 *
 * 「使用时不卡顿」是用户写在需求里的核心诉求，而空载测帧率是没有说服力的 ——
 * 谁家的界面空载会卡？要测就在引擎吃满 CPU 的时候测。
 *
 * 用法：node scripts/stress-ui.mjs <语料目录> <输出目录> [端口]
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const corpus = process.argv[2];
const outDir = process.argv[3] ?? '.';
const port = Number(process.argv[4] ?? 9260);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
mkdirSync(outDir, { recursive: true });

class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.pending = new Map(); }
  async connect() {
    this.ws = new WebSocket(this.url);
    await new Promise((res, rej) => {
      this.ws.addEventListener('open', res, { once: true });
      this.ws.addEventListener('error', () => rej(new Error('CDP 连接失败')), { once: true });
    });
    this.ws.addEventListener('message', (ev) => {
      const m = JSON.parse(String(ev.data));
      const p = this.pending.get(m.id);
      if (p) { this.pending.delete(m.id); m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result); }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (this.pending.delete(id)) rej(new Error(`CDP ${method} 超时`)); }, 60_000);
    });
  }
  async js(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? 'JS 出错');
    return r.result.value;
  }
  async shot(name) {
    const s = await this.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(outDir, name), Buffer.from(s.data, 'base64'));
  }
  close() { this.ws?.close(); }
}

async function findTarget() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(2000) });
      const t = (await r.json()).find((x) => x.type === 'page' && x.webSocketDebuggerUrl);
      if (t) return t;
    } catch { /* 还没起来 */ }
    await sleep(400);
  }
  throw new Error('30 秒内没找到页面');
}

/**
 * 在页面里滚动列表并采样帧间隔。
 *
 * ⚠️ 每一次等帧都要带超时。Chromium 检测到窗口被遮挡会**停掉**
 *    requestAnimationFrame，`await new Promise(r => requestAnimationFrame(r))`
 *    就永远不 resolve —— 整个测量脚本挂死，而且看起来像"CDP 超时"，
 *    完全指不到真正的原因。踩过一次。
 *    （应用侧已经用 backgroundThrottling:false 关掉了节流，
 *      但测量代码自己也要有兜底，不能依赖被测对象配置正确。）
 */
const MEASURE_FPS = (durationMs) => `
  (async () => {
    const el = document.querySelector('.results') || document.scrollingElement;
    const frames = [];
    let last = performance.now();
    let raf, stop = false;
    const tick = (t) => { frames.push(t - last); last = t; if (!stop) raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);

    // 带超时的等帧：rAF 被停掉时 200ms 后自己往下走，不会挂死
    const nextFrame = () => new Promise(r => {
      let done = false;
      const t = setTimeout(() => { if (!done) { done = true; r('timeout'); } }, 200);
      requestAnimationFrame(() => { if (!done) { done = true; clearTimeout(t); r('frame'); } });
    });

    const t0 = performance.now();
    let i = 0, timeouts = 0;
    while (performance.now() - t0 < ${durationMs}) {
      const max = Math.max(1, el.scrollHeight - el.clientHeight);
      el.scrollTop = (i++ * 53) % max;
      if (await nextFrame() === 'timeout') {
        if (++timeouts > 20) break;   // rAF 彻底停了，别死等
      }
    }
    stop = true; cancelAnimationFrame(raf);

    const valid = frames.slice(3).filter(d => d > 0 && d < 500).sort((a,b)=>a-b);
    if (!valid.length) return { samples: 0, rafStalled: true, timeouts };
    const q = (p) => valid[Math.min(valid.length-1, Math.floor(valid.length*p))];
    return {
      samples: valid.length,
      timeouts,
      medianFps: 1000/q(0.5),
      p95Fps: 1000/q(0.95),
      worstFps: 1000/valid[valid.length-1],
      // 掉帧 = 帧间隔超过 33ms（低于 30fps）的帧数
      dropped: valid.filter(d => d > 33).length,
      scrollHeight: el.scrollHeight,
    };
  })()
`;

const target = await findTarget();
const cdp = new Cdp(target.webSocketDebuggerUrl);
await cdp.connect();
await cdp.send('Page.enable');
await cdp.send('Runtime.enable');

const problems = [];

// 等引擎
let engine = null;
for (let i = 0; i < 60; i++) {
  engine = await cdp.js('window.synorive.engine.getState()');
  if (engine?.lifecycle === 'ready') break;
  await sleep(1000);
}
console.log(`引擎 ${engine?.lifecycle}　端口 ${engine?.port}`);
if (engine?.lifecycle !== 'ready') { console.error('引擎没就绪'); process.exit(1); }

// ── ① 先索引一小批，让搜索页有内容可滚 ──────────────────────
console.log('准备：先索引一小批让列表有内容…');
await cdp.js(`fetch('http://127.0.0.1:${engine.port}/api/ingest', {
  method:'POST', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({ targets: ['${corpus.replace(/\\/g, '\\\\')}\\\\d0000'], source:'file', recursive:true })
}).then(r=>r.json())`);
for (let i = 0; i < 40; i++) {
  await sleep(1500);
  const s = await cdp.js(`fetch('http://127.0.0.1:${engine.port}/api/stats').then(r=>r.json())`);
  if (s.items >= 150) break;
}
await cdp.js(`document.querySelector('.searchbox__input').focus()`);
await cdp.send('Input.insertText', { text: '向量检索' });
await sleep(3000);
const hitCount = await cdp.js(`document.querySelectorAll('.card').length`);
console.log(`搜索页已有结果（视口内 ${hitCount} 张卡片）`);

// ── ② 空载帧率（基线）────────────────────────────────────────
console.log('\n① 空载滚动帧率（基线）…');
const idle = await cdp.js(MEASURE_FPS(4000));
console.log(`   中位 ${idle.medianFps.toFixed(1)} fps　P95 ${idle.p95Fps.toFixed(1)} fps　` +
            `最差 ${idle.worstFps.toFixed(1)} fps　掉帧 ${idle.dropped}/${idle.samples}`);
await cdp.shot('stress-idle.png');

// ── ③ 满负载：投喂 5 万文件，同时滚 ─────────────────────────
console.log('\n② A19：投喂 5 万文件，同时测帧率…');
const t0 = Date.now();
const job = await cdp.js(`fetch('http://127.0.0.1:${engine.port}/api/ingest', {
  method:'POST', headers:{'Content-Type':'application/json'},
  body: JSON.stringify({ targets: ['${corpus.replace(/\\/g, '\\\\')}'], source:'file', recursive:true })
}).then(r=>r.json())`);
console.log(`   任务号 ${job.jobId}，提交耗时 ${Date.now() - t0}ms（枚举 5 万文件不能卡住这里）`);
if (Date.now() - t0 > 15_000) problems.push(`提交 5 万文件耗时 ${Date.now() - t0}ms，枚举太慢`);

// 等引擎真的开始吃 CPU
let busy = false;
for (let i = 0; i < 40; i++) {
  await sleep(1000);
  const h = await cdp.js(`fetch('http://127.0.0.1:${engine.port}/health').then(r=>r.json())`);
  if (h.cpuPercent > 40 || h.activeJobs > 0) { busy = true; break; }
}
console.log(`   引擎已满负载：${busy}`);

// 满负载下测三轮
const loaded = [];
for (let round = 1; round <= 3; round++) {
  const m = await cdp.js(MEASURE_FPS(4000));
  const h = await cdp.js(`fetch('http://127.0.0.1:${engine.port}/health').then(r=>r.json())`);
  const s = await cdp.js(`fetch('http://127.0.0.1:${engine.port}/api/stats').then(r=>r.json())`);
  loaded.push(m);
  console.log(`   第 ${round} 轮：中位 ${m.medianFps.toFixed(1)} fps　P95 ${m.p95Fps.toFixed(1)} fps　` +
              `最差 ${m.worstFps.toFixed(1)}　掉帧 ${m.dropped}/${m.samples}　` +
              `｜引擎 CPU ${h.cpuPercent}% 内存 ${h.memoryMb}MB 已索引 ${s.items}`);
}
await cdp.shot('stress-loaded.png');

// ── ④ 满负载下界面还能不能用 ───────────────────────────────
console.log('\n③ 满负载下界面是否仍然可用…');
const t1 = Date.now();
await cdp.js(`document.querySelector('.searchbox__input').focus()`);
await cdp.send('Input.insertText', { text: '并发' });
await sleep(2500);
const stillWorks = await cdp.js(`(() => ({
  cards: document.querySelectorAll('.card').length,
  subtitle: document.querySelector('.page__subtitle')?.textContent ?? '',
  frozen: document.body.getAttribute('data-frozen') === 'true',
}))()`);
console.log(`   满负载下敲字搜索：${stillWorks.cards} 条结果　${stillWorks.subtitle}　` +
            `响应耗时 ${Date.now() - t1}ms`);
if (stillWorks.cards === 0) problems.push('满负载下搜索返回 0 条（界面被拖死了）');

// 引擎内存有没有爆
const finalHealth = await cdp.js(`fetch('http://127.0.0.1:${engine.port}/health').then(r=>r.json())`);
const finalStats = await cdp.js(`fetch('http://127.0.0.1:${engine.port}/api/stats').then(r=>r.json())`);
console.log(`\n   引擎最终：内存 ${finalHealth.memoryMb} MB　已索引 ${finalStats.items} 条`);

// ── 判定 ────────────────────────────────────────────────────
const worstMedian = Math.min(...loaded.map((m) => m.medianFps));
const totalDropped = loaded.reduce((a, m) => a + m.dropped, 0);
const totalSamples = loaded.reduce((a, m) => a + m.samples, 0);

console.log('\n' + '='.repeat(72));
console.log(`A9  空载中位帧率        ${idle.medianFps.toFixed(1)} fps`);
console.log(`A9  满负载中位帧率      ${worstMedian.toFixed(1)} fps（三轮最差的一轮）`);
console.log(`A9  满负载掉帧          ${totalDropped}/${totalSamples} 帧（>33ms 算掉帧）`);
console.log(`A19 5 万文件提交耗时    ${Date.now() - t0}ms 内启动，引擎未崩`);
console.log(`A19 引擎内存            ${finalHealth.memoryMb} MB`);
console.log('='.repeat(72));

if (worstMedian < 55) problems.push(`满负载中位帧率 ${worstMedian.toFixed(1)} < 55`);
if (totalDropped > totalSamples * 0.02) {
  problems.push(`满负载掉帧 ${totalDropped}/${totalSamples} 超过 2%`);
}
if (finalHealth.memoryMb > 1500) problems.push(`引擎内存 ${finalHealth.memoryMb} MB 超 1500`);

if (problems.length) { for (const p of problems) console.error(`✗ ${p}`); cdp.close(); process.exit(1); }
console.log('✓ A9 / A19 通过');
cdp.close();
process.exit(0);
