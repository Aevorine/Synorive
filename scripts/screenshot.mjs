/**
 * 通过 CDP 给运行中的 Electron 窗口截图
 * ====================================================================
 * 为什么不用系统截图 API：GetClientRect + PrintWindow 返回的是设备像素，
 * 在 DPR 1.25 的屏幕上会裁掉约 20% 的内容，看起来像布局溢出（踩过）。
 * CDP 的 Page.captureScreenshot 自己处理缩放，而且**不需要窗口在最前**。
 *
 * 用法：
 *   node scripts/screenshot.mjs <输出png> [端口] [等待毫秒] [主题]
 */

import { writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { mkdirSync } from 'node:fs';

const out = process.argv[2] ?? 'screenshot.png';
const port = Number(process.argv[3] ?? 9222);
const settleMs = Number(process.argv[4] ?? 2500);
const theme = process.argv[5] ?? null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function findTarget() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`, {
        signal: AbortSignal.timeout(2000),
      });
      const targets = await r.json();
      const page = targets.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
      if (page) return page;
    } catch {
      /* 调试端口还没开，继续等 */
    }
    await sleep(400);
  }
  throw new Error(`30 秒内没在 :${port} 找到可截图的页面 —— 应用是不是没起来？`);
}

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
      const msg = JSON.parse(String(ev.data));
      const p = this.pending.get(msg.id);
      if (p) {
        this.pending.delete(msg.id);
        msg.error ? p.rej(new Error(JSON.stringify(msg.error))) : p.res(msg.result);
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
      }, 20_000);
    });
  }

  close() {
    this.ws?.close();
  }
}

const target = await findTarget();
console.log(`[shot] 目标页面：${target.title || '(无标题)'}  ${target.url}`);

const cdp = new Cdp(target.webSocketDebuggerUrl);
await cdp.connect();
await cdp.send('Page.enable');
await cdp.send('Runtime.enable');

// 等界面渲染稳定：字体加载、引擎握手都要一会儿
await sleep(settleMs);

if (theme) {
  await cdp.send('Runtime.evaluate', {
    expression: `document.documentElement.setAttribute('data-theme', ${JSON.stringify(theme)})`,
  });
  await sleep(600);
}

// 顺手把界面上的关键数值读出来，截图之外还要有可核对的证据
const probe = await cdp.send('Runtime.evaluate', {
  returnByValue: true,
  expression: `(() => {
    const cs = (sel, prop) => {
      const el = document.querySelector(sel);
      return el ? getComputedStyle(el)[prop] : null;
    };
    return {
      title: document.title,
      theme: document.documentElement.getAttribute('data-theme'),
      fontScheme: document.documentElement.getAttribute('data-font-scheme'),
      bodyFontSize: cs('body', 'fontSize'),
      bodyFontFamily: cs('body', 'fontFamily'),
      pageTitleText: document.querySelector('.page__title')?.textContent ?? null,
      pageTitleSize: cs('.page__title', 'fontSize'),
      pageTitleFamily: cs('.page__title', 'fontFamily'),
      searchInputSize: cs('.searchbox__input', 'fontSize'),
      sidebarLabelSize: cs('.sidebar__label', 'fontSize'),
      statusText: document.querySelector('.statusbar')?.textContent?.trim() ?? null,
      bg: cs('body', 'backgroundColor'),
      navItems: [...document.querySelectorAll('.sidebar__label')].map(e => e.textContent),
    };
  })()`,
});
console.log('[shot] 界面实测值：');
console.log(JSON.stringify(probe.result.value, null, 2));

const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, Buffer.from(shot.data, 'base64'));
console.log(`[shot] 已保存 ${out}`);

cdp.close();
process.exit(0);
