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
/**
 * 请求体大小上限。
 *
 * 🔴 C13 之后**不能再是 4KB**：登录态抓取要把整串 cookie 传进来，
 * 一个登录过的站点动辄十几个 cookie、几 KB。4KB 会让请求被 413 掐掉，
 * 而调用方看到的只是"抓取失败"，完全想不到是长度限制。
 */
const MAX_BODY_BYTES = 256 * 1024;

/** C12 整页截图的高度上限。无限长的页面（无限滚动）会把内存吃光 */
const MAX_CAPTURE_HEIGHT = 20_000;
/** C12 截图宽度。固定 1280 而不是跟随窗口 —— 归档要的是可复现，不是好看 */
const CAPTURE_WIDTH = 1280;

/**
 * `/render` 的并行通道数。
 *
 * 🔴 **这个数必须和 `engine/synorive/websearch/meta.py` 里的
 * `RENDER_PARALLEL` 对上。** 它原来等价于 1（所有请求共用一个隐藏窗口、
 * 排在同一条队列上），后果是实测出来的一个必然失败：
 *
 *   引擎那边 Google 和 Yandex **同时**发起渲染请求，各自带着 12 秒预算。
 *   这边第一个开始渲染，第二个在队列里等 —— 它**一秒都没开始渲染**，
 *   可它的 12 秒已经在走了。等轮到它时预算基本见底，必定超时。
 *   于是界面上一家报"超时，本轮放弃"、另一家报"没有返回渲染结果"，
 *   看起来像两家引擎都坏了，实际上是它们互相把对方挤死了。
 *   **两家浏览器渲染引擎永远不可能在同一轮里都成功。**
 *
 * 2 是按"目前只有 Google / Yandex 两家要渲染"定的。每条通道是一个
 * 常驻的隐藏窗口，空着也占内存，所以不无脑往大了开。
 */
const RENDER_LANES = 2;

/**
 * 一条渲染通道。
 *
 * 🔴 **每条通道有自己的 `partition`，这不是可选的**：`preparePage`
 * 每次抓取前都会 `clearStorageData({storages:['cookies']})`，
 * 共用一个分区的话，A 请求清 cookie 会把 B 请求刚注入的登录态一起清掉 ——
 * 而 B 拿回来的会是一个"未登录"的页面，**不报错**，只是内容不对。
 * 分区隔开之后每条通道就是一个独立的浏览器配置，互不可见。
 */
interface RenderLane {
  win: BrowserWindow | null;
  partition: string;
  /** 这条通道上正在跑的那个请求；空闲时是 null */
  busy: Promise<unknown> | null;
}

const lanes: RenderLane[] = Array.from({ length: RENDER_LANES }, (_, i) => ({
  win: null,
  partition: `render-text-${i}`,
  busy: null,
}));

let captureWindow: BrowserWindow | null = null;
let server: Server | null = null;
let serverPort = 0;
let serverStarting: Promise<number> | null = null;
let heartbeat: ReturnType<typeof setInterval> | null = null;
/** 截图共用一个窗口且要 setSize，必须串起来跑。渲染不再走这条队列 */
let captureQueue: Promise<unknown> = Promise.resolve();

function getLaneWindow(lane: RenderLane): BrowserWindow {
  if (lane.win && !lane.win.isDestroyed()) return lane.win;
  lane.win = new BrowserWindow({
    show: false,
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      images: false, // 只要 DOM 文本，图片只会拖慢加载、占带宽
      partition: lane.partition, // 和截图窗口、和别的通道都分开，cookie 互不串
    },
  });
  return lane.win;
}

/**
 * C12 截图专用窗口。**必须和取 DOM 那个分开**，两个原因：
 *
 * 🔴 ① 那个窗口是 `images: false` 建的。拿它截图会得到一张
 * **一张图片都没有**的归档 —— 而且不报错、不警告，存下来的 PNG
 * 看起来就是"这个网页本来就没图"。整页截图归档的价值一大半在版面，
 * 没有图的版面等于没归档。
 *
 * 🔴 ② 截图要 `setSize()` 把窗口撑到整页高。共用一个窗口的话，
 * 同时在跑的 `/render` 会被莫名其妙地改掉视口尺寸，
 * 有些站点会因此渲染成移动版布局。
 */
function getCaptureWindow(): BrowserWindow {
  if (captureWindow && !captureWindow.isDestroyed()) return captureWindow;
  captureWindow = new BrowserWindow({
    show: false,
    width: CAPTURE_WIDTH,
    height: 900,
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      images: true, // 归档要版面，图片必须加载
      partition: 'render-capture',
    },
  });
  return captureWindow;
}

/**
 * C13 —— 把调用方给的 cookie 塞进隐藏窗口的会话。
 *
 * 🔴 **写进的是隐藏窗口自己的 session，不是默认 session。**
 * 用默认 session 的话，这些 cookie 会和用户在应用里其他地方的浏览状态
 * 混在一起，而且**退出应用之后还留在磁盘上** —— 那是把别人的登录凭证
 * 无限期存在本机，性质完全变了。隐藏窗口用的是内存分区，进程退出即消失。
 *
 * 🔴 **每次抓取前先清一遍。** 不清的话上一次抓 A 站留下的 cookie
 * 会跟着这一次去访问 B 站 —— 跨站发送凭证，是真正的安全事故。
 */
async function applyCookies(
  win: BrowserWindow,
  cookies: { name: string; value: string; domain?: string; path?: string }[],
  url: string,
): Promise<string[]> {
  const ses = win.webContents.session;
  await ses.clearStorageData({ storages: ['cookies'] });
  const failed: string[] = [];
  for (const c of cookies) {
    try {
      await ses.cookies.set({
        url,
        name: c.name,
        value: c.value,
        ...(c.domain ? { domain: c.domain } : {}),
        path: c.path ?? '/',
      });
    } catch (e) {
      // 🔴 逐条记失败**并且要回报给调用方**。静默跳过的话，
      // 少了关键的那个 session cookie，抓回来的就是登录页 ——
      // 而调用方拿到一个 200 和一份 HTML，完全看不出哪里不对
      failed.push(`${c.name}: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  return failed;
}

/** 抓取前的公共步骤：可选注入 cookie → 加载 → 等异步渲染 */
async function preparePage(
  win: BrowserWindow,
  url: string,
  timeoutMs: number,
  cookies?: { name: string; value: string; domain?: string; path?: string }[],
): Promise<{ wc: Electron.WebContents; cookieFailures: string[] }> {
  const wc = win.webContents;
  const deadline = Math.min(Math.max(timeoutMs, 1000), MAX_RENDER_MS);

  let cookieFailures: string[] = [];
  if (cookies?.length) cookieFailures = await applyCookies(win, cookies, url);
  else await wc.session.clearStorageData({ storages: ['cookies'] });

  await Promise.race([
    wc.loadURL(url),
    new Promise((_, reject) => setTimeout(() => reject(new Error('页面加载超时')), deadline)),
  ]);
  // 给页面里的异步脚本一点时间把结果渲染出来 ——
  // 搜索结果页多数是 loadURL 返回后又有一波异步请求才把列表填进 DOM 的
  await new Promise((r) => setTimeout(r, 800));
  return { wc, cookieFailures };
}

async function renderOnce(
  lane: RenderLane,
  url: string,
  timeoutMs: number,
  cookies?: { name: string; value: string; domain?: string; path?: string }[],
): Promise<{ html: string; cookieFailures: string[] }> {
  const { wc, cookieFailures } = await preparePage(getLaneWindow(lane), url, timeoutMs, cookies);
  const html = (await wc.executeJavaScript(
    'document.documentElement.outerHTML',
  )) as string;
  return { html, cookieFailures };
}

/**
 * C12 —— 整页截图归档。
 *
 * 🔴 **必须把窗口撑到整页高度再截，不能滚动拼接。**
 * 拼接方案在有 `position: fixed` 顶栏的页面上会把顶栏重复画好几遍，
 * 而且懒加载图片在滚过去之后才开始加载 —— 拼出来的图上半截有图、
 * 下半截空白。撑高之后一次性截，Chromium 自己会把整页排好。
 *
 * 🔴 **高度要封顶。** 无限滚动的页面撑起来能到几十万像素，
 * 那是一张几个 G 的位图，直接把内存吃光。截断了要**说出来**。
 */
async function captureOnce(
  url: string,
  timeoutMs: number,
  cookies?: { name: string; value: string; domain?: string; path?: string }[],
): Promise<{ png: string; width: number; height: number; truncated: boolean; cookieFailures: string[] }> {
  const win = getCaptureWindow();
  // 🔴 **先把窗口复位再加载。** 上一次截图可能把它撑到了两万像素高，
  // 带着那个尺寸去加载下一个页面会有两个后果：① 响应式站点按超大视口
  // 渲染成完全不同的布局；② 一整屏两万像素的合成缓冲白占几百兆内存。
  // 而这两件事都不会报错，只会让第二张截图莫名其妙地和第一张不一样
  win.setSize(CAPTURE_WIDTH, 900);
  const { wc, cookieFailures } = await preparePage(win, url, timeoutMs, cookies);

  // 取整页高度。`body` 和 `documentElement` 都要看：不同站点的
  // 滚动容器不一样，只看一个的话在另一半站点上会拿到视口高度（=只截首屏）
  const raw = (await wc.executeJavaScript(
    `Math.max(
       document.body ? document.body.scrollHeight : 0,
       document.documentElement ? document.documentElement.scrollHeight : 0,
       600
     )`,
  )) as number;
  const full = Math.max(600, Math.round(Number(raw) || 600));
  const height = Math.min(full, MAX_CAPTURE_HEIGHT);

  win.setSize(CAPTURE_WIDTH, height);
  // 撑高之后要再给一拍：懒加载的图片这时候才进入视口开始加载
  await new Promise((r) => setTimeout(r, 700));

  const img = await wc.capturePage();
  const png = img.toPNG();
  if (png.length === 0) {
    // 🔴 `capturePage()` 失败时**返回空 buffer 而不抛异常** ——
    // 不查长度的话会存下一个 0 字节的 .png，而归档记录看起来完全正常
    throw new Error('截图返回了 0 字节（窗口可能已被销毁，或页面完全空白）');
  }
  return {
    png: png.toString('base64'),
    width: CAPTURE_WIDTH,
    height,
    truncated: full > MAX_CAPTURE_HEIGHT,
    cookieFailures,
  };
}

/**
 * C12 截图串起来跑：截图共用一个窗口，而且要 `setSize` 把它撑到整页高，
 * 不排队的话一个请求会把另一个请求的页面截进去。
 */
function runCaptureQueued<T>(fn: () => Promise<T>): Promise<T> {
  const run = captureQueue.then(fn, fn); // 上一个请求失败也不该拖累这一个
  captureQueue = run.catch(() => undefined);
  return run;
}

/**
 * 挑一条空闲的渲染通道跑；全忙就等最早空出来的那条。
 *
 * 通道内部仍然是串行的（同一个 BrowserWindow 不能同时 `loadURL` 两次），
 * 但通道之间是并行的 —— 这正是 Google 和 Yandex 同时来的时候需要的。
 */
async function runOnLane<T>(fn: (lane: RenderLane) => Promise<T>): Promise<T> {
  let target = lanes.find((l) => l.busy === null);
  while (!target) {
    // 全忙：等任意一条跑完。用 `catch` 兜住是因为我们只关心"空出来了"，
    // 上一个请求成没成功与这一个无关
    await Promise.race(lanes.map((l) => l.busy ?? Promise.resolve()).map((p) => p.catch(() => undefined)));
    target = lanes.find((l) => l.busy === null);
  }
  const lane = target;
  const run = fn(lane);
  // 🔴 `busy` 必须在 finally 里清，而且要**确认清的是自己那一次**。
  // 直接 `target.busy = null` 会把后一个请求刚挂上去的 promise 清掉，
  // 于是那条通道被当成空闲、又派进来一个请求 —— 同一个窗口两个 loadURL，
  // 回来的 HTML 是哪个页面的全看运气
  const tracked: Promise<unknown> = run.catch(() => undefined).finally(() => {
    if (lane.busy === tracked) lane.busy = null;
  });
  lane.busy = tracked;
  return run;
}

function handleRequest(req: IncomingMessage, res: ServerResponse): void {
  const path = req.url ?? '';
  if (req.method !== 'POST' || (path !== '/render' && path !== '/capture')) {
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
        const parsed = JSON.parse(body || '{}') as {
          url?: string;
          timeoutMs?: number;
          /** C13 登录态：调用方给的 cookie。不给就抓匿名页面 */
          cookies?: { name: string; value: string; domain?: string; path?: string }[];
        };
        if (!parsed.url) throw new Error('缺少 url');
        // 🔴 只放行 http/https。不挡的话 `file:///C:/Users/...` 会被原样加载，
        // 等于给任何能发到这个端口的东西一个读本机文件的入口
        if (!/^https?:\/\//i.test(parsed.url)) throw new Error('只支持 http/https 网址');
        const timeoutMs = parsed.timeoutMs ?? 12_000;

        const out =
          path === '/capture'
            ? await runCaptureQueued(() => captureOnce(parsed.url!, timeoutMs, parsed.cookies))
            : await runOnLane((lane) => renderOnce(lane, parsed.url!, timeoutMs, parsed.cookies));
        res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(out));
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
  for (const lane of lanes) {
    if (lane.win && !lane.win.isDestroyed()) lane.win.destroy();
    lane.win = null;
    lane.busy = null;
  }
  // 🔴 截图窗口也要收。漏掉的话主进程退不干净 —— 一个 show:false 的
  // BrowserWindow 照样会让 Electron 认为还有窗口活着
  if (captureWindow && !captureWindow.isDestroyed()) captureWindow.destroy();
  captureWindow = null;
}
