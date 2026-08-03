/**
 * 渲染代理 —— 8.5
 * ============================================================
 * Google/Yandex 这类要求执行 JavaScript 才能拿到结果的搜索引擎，
 * Python 引擎进程没有浏览器内核跑不动。往引擎里再塞一个（比如 Playwright）
 * 要多背几百 MB 依赖，而桌面端本来就带着一个完整的 Chromium（Electron 41）——
 * 所以让引擎"借用"这里的浏览器，而不是自己再长一个。
 *
 * 协议很朴素，特意不复用 `/events` 那条 WebSocket（那条是纯单向广播，
 * 硬改成双向 RPC 会让一个本来很稳的通道变脆）：
 *
 *   ① 这里起一个只听 127.0.0.1 的极简 HTTP 服务，只认 POST /render
 *   ② 引擎就绪后，定期把这个端口告诉引擎（POST /api/render/register）
 *   ③ 引擎需要渲染时直接 POST 到这个端口
 *   ④ 注册 45 秒过期（引擎那边定的），这里每 20 秒续一次 ——
 *      续不上 = 引擎会认为渲染不可用，不会有"以为能用其实早联系不上"的假可用窗口
 */
import { BrowserWindow } from 'electron';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';

/** 单次渲染的硬上限。搜索结果页正常几秒内加载完，超过这个时长基本是卡死了 */
const MAX_RENDER_MS = 15_000;
const HEARTBEAT_MS = 20_000;
const REGISTER_TIMEOUT_MS = 3_000;
/** 请求体大小上限 —— 这个服务只应该收到一个 URL，超出说明有人在乱发 */
const MAX_BODY_BYTES = 4096;

let hiddenWindow: BrowserWindow | null = null;
let server: Server | null = null;
let serverPort = 0;
let serverStarting: Promise<number> | null = null;
let heartbeat: ReturnType<typeof setInterval> | null = null;
/** 同一个隐藏窗口不能同时 loadURL 两次，用这个把并发请求串成队列 */
let queue: Promise<unknown> = Promise.resolve();

function getHiddenWindow(): BrowserWindow {
  if (hiddenWindow && !hiddenWindow.isDestroyed()) return hiddenWindow;
  hiddenWindow = new BrowserWindow({
    show: false,
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      images: false, // 只要 DOM 文本，图片只会拖慢加载、占带宽
    },
  });
  return hiddenWindow;
}

async function renderOnce(url: string, timeoutMs: number): Promise<string> {
  const wc = getHiddenWindow().webContents;
  const deadline = Math.min(Math.max(timeoutMs, 1000), MAX_RENDER_MS);

  await Promise.race([
    wc.loadURL(url),
    new Promise((_, reject) => setTimeout(() => reject(new Error('页面加载超时')), deadline)),
  ]);
  // 给页面里的异步脚本一点时间把结果渲染出来 ——
  // 搜索结果页多数是 loadURL 返回后又有一波异步请求才把列表填进 DOM 的
  await new Promise((r) => setTimeout(r, 800));
  return wc.executeJavaScript('document.documentElement.outerHTML');
}

function renderQueued(url: string, timeoutMs: number): Promise<string> {
  const run = queue.then(
    () => renderOnce(url, timeoutMs),
    () => renderOnce(url, timeoutMs), // 上一个请求失败也不该拖累这一个
  );
  queue = run.catch(() => undefined);
  return run;
}

function handleRequest(req: IncomingMessage, res: ServerResponse): void {
  if (req.method !== 'POST' || req.url !== '/render') {
    res.writeHead(404).end();
    return;
  }
  let body = '';
  let tooLarge = false;
  req.on('data', (c: Buffer) => {
    body += c;
    if (body.length > MAX_BODY_BYTES) {
      tooLarge = true;
      req.destroy();
    }
  });
  req.on('end', () => {
    if (tooLarge) {
      res.writeHead(413).end();
      return;
    }
    void (async () => {
      try {
        const parsed = JSON.parse(body || '{}') as { url?: string; timeoutMs?: number };
        if (!parsed.url) throw new Error('缺少 url');
        const html = await renderQueued(parsed.url, parsed.timeoutMs ?? 12_000);
        res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ html }));
      } catch (e) {
        res
          .writeHead(200, { 'Content-Type': 'application/json' })
          .end(JSON.stringify({ error: e instanceof Error ? e.message : String(e) }));
      }
    })();
  });
}

function ensureRenderServer(): Promise<number> {
  if (serverPort) return Promise.resolve(serverPort);
  if (serverStarting) return serverStarting;
  serverStarting = new Promise((resolve, reject) => {
    const s = createServer(handleRequest);
    s.once('error', reject);
    // 只绑本机回环地址 —— 这个服务能让人打开任意网址，绝不能被局域网访问到
    s.listen(0, '127.0.0.1', () => {
      const addr = s.address();
      serverPort = typeof addr === 'object' && addr ? addr.port : 0;
      server = s;
      resolve(serverPort);
    });
  });
  return serverStarting;
}

async function registerOnce(enginePort: number): Promise<void> {
  try {
    const port = await ensureRenderServer();
    await fetch(`http://127.0.0.1:${enginePort}/api/render/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ port }),
      signal: AbortSignal.timeout(REGISTER_TIMEOUT_MS),
    });
  } catch {
    // 静默失败，下一次心跳会再试。引擎那边的注册会在 45 秒后自然过期 ——
    // 比起报错更好的行为是"下次心跳自己会好"，不需要额外的重试逻辑
  }
}

/** 引擎每次就绪（含重启）都要重新调用——端口会变，旧的注册对应的是死端口 */
export function startRegistering(enginePort: number): void {
  stopRegistering();
  void registerOnce(enginePort);
  heartbeat = setInterval(() => void registerOnce(enginePort), HEARTBEAT_MS);
}

export function stopRegistering(): void {
  if (heartbeat) {
    clearInterval(heartbeat);
    heartbeat = null;
  }
}

/** 应用退出时彻底收掉——隐藏窗口和本地 HTTP 服务都不该活过主进程 */
export function teardown(): void {
  stopRegistering();
  server?.close();
  server = null;
  serverPort = 0;
  serverStarting = null;
  if (hiddenWindow && !hiddenWindow.isDestroyed()) hiddenWindow.destroy();
  hiddenWindow = null;
}
