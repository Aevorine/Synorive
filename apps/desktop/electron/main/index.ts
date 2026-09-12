/**
 * Synorive 桌面端 · 主进程入口
 */

import { BrowserWindow, app, dialog, globalShortcut, ipcMain, nativeTheme, shell } from 'electron';
import { randomUUID } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { isAbsolute, join, resolve as resolvePath } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AppSettings, LibraryEntry } from '@synorive/shared-types';
import { IPC, type ClipEntry, type EngineProcessState } from '../shared/ipc-contract.js';
import { ClipboardWatcher } from './clipboard.js';
import {
  launchScreenCapture,
  registerHotkeys,
  unregisterAllHotkeys,
  type HotkeyReport,
} from './hotkeys.js';
import { PeekWindow } from './peek.js';
import {
  clearCloudKey,
  clearDbKey,
  hasCloudKey,
  hasDbKey,
  loadCloudKey,
  loadDbKey,
  saveDbKey,
  engineKeyStatus,
  loadEngineKeys,
  saveCloudKey,
  saveEngineKeys,
} from './cloud-keys.js';
import { EngineManager } from './engine.js';
import { exportPdf, saveText } from './pdf.js';
import { teardown as teardownRenderer } from './render.js';
import { ensureDataDirs, loadSettings, patchSettings } from './settings.js';
import { TrayController, setLaunchAtLogin } from './tray.js';
import { UpdateManager } from './updater.js';
import { createMainWindow } from './window.js';

interface AppRef {
  isQuitting?: boolean;
}

let win: BrowserWindow | null = null;
let tray: TrayController | null = null;
let engine: EngineManager | null = null;
/**
 * 🔴 引擎重启必须串行，不能"发了就不管"。
 * 原来是 `void engine?.stop().then(() => startEngine())`——如果两次
 * 触发重启的操作挨得很近（比如快速连续切两个库），第二次调用会在第一次
 * 的 stop() 还没跑完时就读到同一个 `engine`，两条 `.then()` 谁先resolve
 * 完全看旧进程退出快慢，先resolve的那条 startEngine() 把全局 engine
 * 覆盖掉，它启动的那个子进程从此再没人管——不会被下一次 stop() 杀掉，
 * 继续常驻并持有它那个库 dataDir 下索引文件的锁，而界面显示的是另一个库。
 * 串成一条链，保证同一时刻只有一次"停旧的、起新的"在跑。
 */
let engineRestartChain: Promise<void> = Promise.resolve();
function requestEngineRestart(): Promise<void> {
  engineRestartChain = engineRestartChain.catch(() => {}).then(async () => {
    await engine?.stop();
    startEngine();
  });
  return engineRestartChain;
}
let clip: ClipboardWatcher | null = null;
/** N7 随手研究浮窗。默认关，所以默认是 null —— 开了才建 */
let peek: PeekWindow | null = null;
/** F7/A4：全局快捷键的**真实**注册结果，界面靠它显示实际生效的键 */
let hotkeyReport: HotkeyReport[] = [];
/** U 组 应用自更新。便携版/开发模式下它也存在，只是状态恒为 unsupported */
let updater: UpdateManager | null = null;
let settings: AppSettings = loadSettings();

// ── 单实例：第二次启动就把已有窗口拉到前面 ─────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    showWindow();
  });
}

// 关闭 Windows 上烦人的 GPU 沙箱告警，同时保留硬件加速
app.commandLine.appendSwitch('disable-features', 'HardwareMediaKeyHandling');

// 主窗口被隐藏到托盘后，不需要继续高频绘制状态栏。
//
// 以前这里关闭了 Chromium 的全部后台节流，window.ts 又把渲染器的
// backgroundThrottling 设为 false。结果是用户把窗口收进托盘后，React
// 动画、定时器和绘制仍持续占用 CPU；真正需要后台继续运行的摄取/搜索
// 引擎是独立的 Python 子进程，并不依赖这些开关。保留 Chromium 的默认
// 节流，前台窗口仍照常即时响应，隐藏窗口则不再为不可见 UI 消耗资源。

/**
 * 🔴 **全局兜底：任何 webContents 默认都不许弹新窗口。**
 *
 * 应用里有三个窗口在加载**不可信内容**：8.5 渲染代理的两条通道
 * （`render.ts` 里 loadURL 的是任意公网站点，而且 JS 是开着的）
 * 和 C12 截图窗口。它们原来一个 `setWindowOpenHandler` 都没有 ——
 * 页面里一句 `window.open()` 就能弹出一个**从 Synorive 里冒出来的**
 * 无边框窗口，用户完全有理由以为那是应用自己的界面。这是现成的钓鱼载体。
 *
 * 兜底放在这里而不是逐个窗口去补，是因为漏一个就等于没做，
 * 而以后新加的窗口不会有人记得补。
 *
 * ⚠️ 这条**不会**破坏主窗口和浮窗的外链流程：它们在
 * `new BrowserWindow()` 之后各自又调了一次 `setWindowOpenHandler`
 * （`window.ts` / `peek.ts`），后设的那个覆盖这一条，
 * 外链照旧交给 `shell.openExternal` 用系统浏览器打开。
 */
app.on('web-contents-created', (_e, wc) => {
  wc.setWindowOpenHandler(() => ({ action: 'deny' }));
});

function showWindow(): void {
  if (!win || win.isDestroyed()) {
    win = createMainWindow({
      runInTray: settings.runInTray,
      onCloseToTray: () => {
        /* 收进托盘，什么都不用做 */
      },
    });
    wireWindowEvents(win);
    return;
  }
  if (win.isMinimized()) win.restore();
  // 上一次是被 toggleWindow() 收起来的话，任务栏按钮被摘掉了，这里要还回去
  if (process.platform !== 'darwin') win.setSkipTaskbar(false);
  win.show();
  win.focus();
}

/**
 * 显示 ↔ 收起来回切。托盘左键单击和全局唤起键共用这一条。
 *
 * 🔴 **收起必须用 `hide()`，不能用 `minimize()`。** Windows 上最小化的窗口
 *    在任务栏里仍然占着一格 —— 用户要的是"再点一下就从任务栏里消失"，
 *    只有 `hide()` 会把任务栏按钮一起收掉。`setSkipTaskbar(true)` 是第二道
 *    保险：窗口处于某些中间状态（刚最小化、正在动画）时按钮偶尔赖着不走。
 *
 * 🔴 **托盘那一路绝不能拿 `isFocused()` 当条件。** 点托盘图标那一下，焦点
 *    已经被系统外壳拿走了，回调里主窗口永远是"没聚焦"，于是永远只会 show，
 *    第二下点下去什么都不会发生 —— 症状正是"切不回去"。
 *    快捷键那一路相反，**必须**看焦点：窗口开着但你正在别的软件里干活时
 *    按下唤起键，想要的是把它叫到面前，不是把它收起来。
 *
 * @returns true = 这一下把窗口收起来了（调用方不该再往窗口里发消息）
 */
function toggleWindow(mode: 'tray' | 'hotkey'): boolean {
  if (!win || win.isDestroyed()) {
    showWindow();
    return false;
  }
  const shown = win.isVisible() && !win.isMinimized();
  const shouldHide = mode === 'tray' ? shown : shown && win.isFocused();
  if (shouldHide) {
    if (process.platform !== 'darwin') win.setSkipTaskbar(true);
    win.hide();
    return true;
  }
  showWindow();
  return false;
}

function wireWindowEvents(w: BrowserWindow): void {
  const push = () => {
    if (w.isDestroyed()) return;
    w.webContents.send(IPC.windowStateChanged, {
      isMaximized: w.isMaximized(),
      isFullScreen: w.isFullScreen(),
    });
  };
  w.on('maximize', push);
  w.on('unmaximize', push);
  w.on('enter-full-screen', push);
  w.on('leave-full-screen', push);
}

function broadcast(channel: string, payload: unknown): void {
  for (const w of BrowserWindow.getAllWindows()) {
    if (!w.isDestroyed()) w.webContents.send(channel, payload);
  }
}

// ── E4 剪贴板哨兵 ────────────────────────────────────────

function startClipboard(): void {
  clip = new ClipboardWatcher({
    onEntry: (e) => {
      broadcast(IPC.clipCaptured, e);
      // N7 随手研究：复制到一段文字就在屏幕角落浮出三条最相关的。
      // **只对纯文本触发，不对链接** —— 复制链接多半是要发给别人，
      // 那时候弹一个"这个链接讲什么"没有帮助，只是打扰。
      // 密钥类内容在 ClipboardWatcher 里已经被静默丢弃，走不到这儿
      if (settings.clipboardPeek && e.kind === 'text') {
        peek?.show(e.content);
      }
      // A8：复制了一张图也弹浮窗，走以图搜图那一路。
      // **和文字分成两条通道**而不是复用 `show(content)` ——
      // 图片的 content 是一个几百 KB 的 data URL，
      // 当查询词塞进去会被当成文本去分词，症状是浮窗永远查不到东西
      if (settings.clipboardPeek && e.kind === 'image') {
        peek?.showImage(e.content, e.preview);
      }
    },
    onAutoArchive: (e) => void archiveClip(e),
  });
  clip.setAutoArchiveLinks(settings.clipboardAutoArchiveLinks);
  applyClipboardSetting();
}

/** N7 浮窗按当前设置启停。关掉时**销毁窗口**而不是只隐藏 —— 用户关掉它
 *  是不想要它存在，留一个隐藏的窗口在那儿占内存说不过去 */
function applyPeekSetting(): void {
  if (settings.clipboardPeek) {
    peek ??= new PeekWindow();
    peek.setOptions({
      allowNetwork: settings.allowNetwork ?? true,
      peekWeb: settings.clipboardPeekWeb ?? false,
    });
  } else {
    peek?.destroy();
    peek = null;
  }
}

/** 开关拨到哪就真的启停到哪。关掉时连内存里攒的也清空。 */
function applyClipboardSetting(): void {
  if (!clip) return;
  if (settings.clipboardSentinel) {
    clip.start();
  } else {
    clip.stop();
    clip.clear();
    broadcast(IPC.clipCaptured, null);
  }
}

/** 把一条剪贴板内容真正送进引擎入库 */
async function archiveClip(e: ClipEntry): Promise<boolean> {
  const port = engine?.getState().port;
  if (!port) return false;
  const body = e.kind === 'link'
    ? { targets: [e.content], source: 'link' as const, recursive: false }
    : { targets: [e.content], source: 'clipboard' as const, recursive: false, inline: true };
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) return false;
    clip?.markArchived(e.id);
    return true;
  } catch {
    return false;
  }
}

// ── 引擎 ────────────────────────────────────────────────────

/**
 * 引擎要的那张 key 表 = 明文端点（自建 SearXNG 地址）+ 加密存的 API Key。
 *
 * 两类东西合成一张表交给引擎，是因为引擎侧的 `keys` 参数本来就是
 * 「这家引擎的那个字符串配置」——SearXNG 要的是地址、Brave 要的是 Key，
 * 形状一样。**但存的地方必须分开**：地址不是秘密，加密存它只会
 * 让用户想改的时候找不到地方改。
 */
function collectWebKeys(): Record<string, string> {
  return { ...(settings.webEndpoints ?? {}), ...loadEngineKeys() };
}

function startEngine(): void {
  engine = new EngineManager({
    dataDir: settings.dataDir,
    modelDir: settings.modelDir,
    concurrency: settings.concurrency,
    allowCloud: settings.cloud.enabled,
    enableGpuAcceleration: settings.enableGpuAcceleration ?? false,
    enableImageDescription: settings.enableImageDescription,
    enableFaceClustering: settings.enableFaceClustering,
    // `?? true` 兜底：老 settings.json 升级上来没有这个字段时按"默认开"处理，
    // 不能被当成 false——那样升级完这道安全闸会静默消失
    sensitiveGuardEnabled: settings.sensitiveGuardEnabled ?? true,
    // B6：老 settings.json 升级上来没有这个字段时按"默认开"处理——同一条纪律
    backgroundIndexingLowPriority: settings.backgroundIndexingLowPriority ?? true,
    lanPairingEnabled: settings.lanPairingEnabled,
    pairingToken: settings.pairingToken,
    // 整库加密口令。存在 safeStorage 里，走环境变量传给子进程（不进 argv）
    dbKey: loadDbKey() ?? '',
    // 联网搜索这一路（E12/U9 · S1 · V5）。`?? true` 是给老 settings.json
    // 兜底 —— 升级上来的用户配置里没有这个字段，读出来是 undefined，
    // 不兜底的话会被当成 false，用户升级完发现联网功能整个消失了
    allowNetwork: settings.allowNetwork ?? true,
    webLineupSize: settings.webLineupSize ?? 0,
    verifyLevel: settings.verifyLevel ?? 'counter',
    webEngines: settings.webEngines ?? [],
    trustProfile: settings.trustProfile ? JSON.stringify(settings.trustProfile) : '',
    webKeys: collectWebKeys(),
  });

  engine.onStateChange((s: EngineProcessState) => {
    broadcast(IPC.engineStateChanged, s);
    tray?.setEngineState(s);
    // 引擎每次就绪（含重启）都要重新推一遍云端配置 —— 引擎侧密钥只存内存，
    // 重启就没了，不重推的话用户会以为"设置好了怎么又失效了"
    if (s.lifecycle === 'ready') {
      void pushCloudConfig();
      // 监听的目录同理：引擎侧的 watcher 也是纯内存状态，重启就空了，
      // 每次就绪都要把当前设置里的列表重新推一遍
      void pushWatchedFolders();
    }
  });

  engine.onEngineEvent((ev) => {
    broadcast(IPC.engineEvent, ev);
  });

  void engine.start();
}

/** 把当前设置 + 解密出来的 Key 推给引擎。引擎没就绪时静默跳过，下次就绪会自动补推。 */
async function pushCloudConfig(): Promise<void> {
  const port = engine?.getState().port;
  if (!port) return;
  const apiKey = settings.cloud.enabled ? (loadCloudKey() ?? '') : '';
  const provider = settings.cloud.enabled ? settings.cloud.provider : 'none';
  try {
    await fetch(`http://127.0.0.1:${port}/api/cloud/configure`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        provider,
        apiKey,
        baseUrl: settings.cloud.baseUrl ?? '',
        chatModel: settings.cloud.chatModel ?? '',
      }),
    });
  } catch (err) {
    console.warn('[cloud] 推送配置到引擎失败：', err);
  }
}

/**
 * 把"监听的目录"整份列表推给引擎——全量替换，不是增量。引擎自己 diff
 * 出该新开哪些监听、该撤销哪些（见 watcher.py），这边不用关心上次
 * 推的是什么。引擎没就绪时静默跳过，下次就绪（含首次启动）会自动补推。
 */
async function pushWatchedFolders(): Promise<void> {
  const port = engine?.getState().port;
  if (!port) return;
  try {
    await fetch(`http://127.0.0.1:${port}/api/watch/folders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folders: settings.watchedFolders }),
    });
  } catch (err) {
    console.warn('[watch] 推送监听目录到引擎失败：', err);
  }
}

// ── U 组 应用自更新 ──────────────────────────────────────────

function startUpdater(): void {
  updater = new UpdateManager(settings.skippedUpdateVersion ?? null);
  updater.onChange((s) => {
    broadcast(IPC.updateStateChanged, s);
    // 托盘常驻是默认行为，多数时候主窗口是关着的 —— 只广播给渲染层的话，
    // 那些时候查到的更新一个人也看不到
    tray?.setUpdateState(s);
  });

  // 启动就查会和引擎启动、模型加载抢带宽和 CPU，而更新这件事一点都不急。
  // 延后 20 秒，等首屏和引擎都稳定了再悄悄查一次。
  if (settings.autoCheckUpdate ?? true) {
    setTimeout(() => void updater?.check(true), 20_000).unref?.();
  }
}

// ── IPC 发送方校验 ───────────────────────────────────────────

/**
 * 🔴 **每条 IPC 都要问一句"你是谁"。**
 *
 * `ipcMain.handle` 不区分发送方：只要是这个应用里的任何一个 webContents
 * （包括 8.5 渲染代理那两个正在加载**任意公网页面**的隐藏窗口、
 * PDF 打印窗口、以后任何一个 iframe）都能调到全部 46 个通道 ——
 * 里面有 `sys:open-path`（起进程）、`cloud:*`（碰密钥）、
 * `settings:patch`（改隐私围栏）。任何一个页面被攻破，这些就全是它的。
 *
 * 判据只有一条：**发起这次调用的那个 frame，它的 URL 是不是我们自己的页面。**
 *   · 开发模式：放行 vite dev server（`ELECTRON_RENDERER_URL`）和 devtools
 *   · 打包之后：只放行 `file://`，而且文件必须落在应用自己的目录里
 *     （`app.getAppPath()`，打包后是 app.asar 内部）
 * 远程页面永远是 `https://…`，一条都过不了。
 */
function senderFrameUrl(e: Electron.IpcMainInvokeEvent): string | null {
  try {
    // frame 可能已经被销毁（页面在 await 期间跳走了），读 .url 会直接抛
    return e.senderFrame?.url ?? null;
  } catch {
    return null;
  }
}

function isTrustedSender(e: Electron.IpcMainInvokeEvent): boolean {
  const url = senderFrameUrl(e);
  if (!url) return false;

  if (!app.isPackaged) {
    const dev = process.env.ELECTRON_RENDERER_URL;
    if (dev && url.startsWith(dev)) return true;
    if (url.startsWith('devtools://')) return true;
  }

  if (!url.startsWith('file://')) return false;
  try {
    // 转成本地路径再比，不能拿 URL 字符串前缀比 ——
    // `file:///C:/app.asar/../../evil/index.html` 这种字符串前缀是对的，
    // 解析出来的路径却在应用外面
    const filePath = resolvePath(fileURLToPath(url));
    const root = resolvePath(app.getAppPath());
    return filePath === root || filePath.startsWith(root + (process.platform === 'win32' ? '\\' : '/'));
  } catch {
    return false;
  }
}

/**
 * 包一层再挂上去。所有 `ipcMain.handle` 都走这个，不要直接用 `ipcMain.handle`。
 *
 * 拒绝时**抛异常而不是静默返回 undefined**：静默返回会让调用方拿到一个
 * 看起来正常的空结果，出事了没人知道；抛出去至少在渲染层是一个明确的失败。
 *
 * （本文件目前没有 `ipcMain.on`。以后要加的话同样不能裸用，
 *   照这个写一个 `on()` 包装 —— `on` 那条路连返回值都没有，
 *   不校验就是完全静默地执行。）
 */
function handle<A extends unknown[], R>(
  channel: string,
  fn: (e: Electron.IpcMainInvokeEvent, ...args: A) => R,
): void {
  ipcMain.handle(channel, (e, ...args) => {
    if (!isTrustedSender(e)) {
      console.warn(`[ipc] 拒绝 ${channel}：发送方不是本应用页面（${senderFrameUrl(e) ?? '未知'}）`);
      throw new Error(`拒绝执行 ${channel}：这个请求不是从本应用的界面发出来的`);
    }
    return fn(e, ...(args as A));
  });
}

// ── 本地路径校验（sys:open-path / sys:reveal） ────────────────

/**
 * 🔴 **UNC 路径必须拦。**
 *
 * `shell.openPath('\\\\攻击者\\share\\x.exe')` 在 Windows 上会做两件事：
 *   ① 为了访问那个共享，系统**自动把当前用户的 NTLM 哈希发过去**
 *      —— 用户什么都没点，凭证已经泄了，可以拿去离线爆破或中继；
 *   ② 那个 exe 从远程共享上直接跑起来。
 * 一条渲染层过来的字符串就能触发这两件事，是这个应用里最短的一条攻击路径。
 *
 * 挡的是 UNC 和网络位置，**不是"打开本地文件"**：正常的
 * `D:\资料\报告.pdf` 一个字都没变，那是这个功能的全部意义。
 *
 * @returns 规范化后的本地绝对路径；不合格返回 null
 */
function safeLocalPath(input: unknown): string | null {
  const raw = typeof input === 'string' ? input.trim() : '';
  if (!raw) return null;
  // `file:///...`、`http://...`、`ms-settings:`、`shell:startup` 这类协议串
  // 一律不认 —— 这个接口的语义是"本机上的一个文件/目录"，不是"一个地址"。
  // 盘符（`C:\`）长得也像协议，所以先把它单独放过
  const isDriveLetter = /^[a-zA-Z]:[\\/]/.test(raw);
  if (!isDriveLetter && /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) return null;
  // UNC：`\\server\share`、`//server/share`、`\\?\UNC\...`、设备路径 `\\.\`
  if (/^[\\/]{2}/.test(raw)) return null;
  if (!isAbsolute(raw)) return null;

  const norm = resolvePath(raw);
  // 规范化之后再查一遍：`C:\a\..\..\..\\\\server\share` 这种在 resolve
  // 之后才会露出 UNC 的原形
  if (/^[\\/]{2}/.test(norm)) return null;
  if (process.platform === 'win32' && !/^[a-zA-Z]:[\\/]/.test(norm)) return null;
  return norm;
}

// ── IPC ─────────────────────────────────────────────────────

function registerIpc(): void {
  // 窗口
  handle(IPC.windowMinimize, () => win?.minimize());
  handle(IPC.windowMaximizeToggle, () => {
    if (!win) return;
    win.isMaximized() ? win.unmaximize() : win.maximize();
  });
  handle(IPC.windowClose, () => win?.close());
  handle(IPC.windowIsMaximized, () => win?.isMaximized() ?? false);

  /**
   * 界面整体缩放。
   *
   * 🔴 **每个窗口都要设，不能只设主窗口。** 浮窗（随手研究）和渲染代理
   *    也是 BrowserWindow —— 只设主窗口的话，用户放大到 150% 之后
   *    浮窗还是原大小，看起来像"这个窗口没跟上"。
   *
   * 🔴 夹在 0.5~3 之间。setZoomFactor 收到 0 或负数会直接抛，
   *    而调用方（渲染层）传什么完全取决于设置文件，设置文件是可以被手改的。
   */
  handle(IPC.windowSetZoom, (_e, factor: number) => {
    const f = Math.min(3, Math.max(0.5, Number(factor) || 1));
    for (const w of BrowserWindow.getAllWindows()) {
      if (!w.isDestroyed()) w.webContents.setZoomFactor(f);
    }
  });

  // ── 资料库整库加密 ────────────────────────────────────────

  handle(IPC.dbEncryptStatus, async () => {
    const port = engine?.getState().port;
    let cipherAvailable = false;
    let encrypted = false;
    if (port) {
      try {
        const r = await fetch(`http://127.0.0.1:${port}/api/security/db`);
        if (r.ok) {
          const j = (await r.json()) as { cipherAvailable?: boolean; encrypted?: boolean };
          cipherAvailable = !!j.cipherAvailable;
          encrypted = !!j.encrypted;
        }
      } catch {
        /* 引擎没起来就照实说"不知道"，见下面的 engineReady */
      }
    }
    return { engineReady: !!port, cipherAvailable, encrypted, keyStored: hasDbKey() };
  });

  /**
   * 开启加密。
   *
   * 顺序很重要：**先让引擎把库转成加密的，成了再存口令、再重启**。
   * 反过来（先存口令再转换）的话，转换失败时下次启动会拿着一个口令
   * 去开一个明文库 —— 引擎直接起不来，而用户完全不知道发生了什么。
   */
  handle(IPC.dbEncryptEnable, async (_e, passphrase: string) => {
    const pw = String(passphrase ?? '');
    if (pw.length < 8) return { ok: false, error: '口令至少 8 位。这是解开整个资料库的唯一钥匙。' };
    const port = engine?.getState().port;
    if (!port) return { ok: false, error: '引擎还没就绪，等它起来再试' };
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/security/db/encrypt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase: pw }),
      });
      const j = (await r.json()) as { ok?: boolean; error?: string };
      if (!r.ok || !j.ok) return { ok: false, error: j.error ?? `引擎返回 ${r.status}` };
    } catch (err) {
      return { ok: false, error: `转换失败：${String(err)}` };
    }
    if (!saveDbKey(pw)) {
      return {
        ok: false,
        error:
          '库已经加密了，但这台机器上存不住口令（系统密钥库不可用）。' +
          '下次启动要手动输入 —— 请务必确认你已经把口令记在别处。',
      };
    }
    await requestEngineRestart();
    return { ok: true };
  });

  handle(IPC.dbEncryptDisable, async (_e, passphrase: string) => {
    const port = engine?.getState().port;
    if (!port) return { ok: false, error: '引擎还没就绪，等它起来再试' };
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/security/db/decrypt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase: String(passphrase ?? '') }),
      });
      const j = (await r.json()) as { ok?: boolean; error?: string };
      if (!r.ok || !j.ok) return { ok: false, error: j.error ?? `引擎返回 ${r.status}` };
    } catch (err) {
      return { ok: false, error: `转换失败：${String(err)}` };
    }
    clearDbKey();
    await requestEngineRestart();
    return { ok: true };
  });

  // 设置
  handle(IPC.settingsGet, () => settings);
  handle(IPC.settingsPatch, (_e, patch: Partial<AppSettings>) => applyPatch(patch));

  // ── 多库支持 ─────────────────────────────────────────────
  // 全是在操作 settings.libraries 这份注册表，"切库"复用 applyPatch——
  // 传的 patch 里带 dataDir，会自然触发下面那段"dataDir 变了就重启引擎"的逻辑。
  handle(IPC.libraryList, () => settings.libraries);

  handle(IPC.libraryCreate, (_e, name: string, dataDir?: string) => {
    const trimmedName = String(name ?? '').trim() || '未命名库';
    const id = randomUUID();
    // 没传目录：在 userData 下自动生成一个专属目录，不和任何已有库共用
    const dir = dataDir && String(dataDir).trim() ? String(dataDir).trim() : join(app.getPath('userData'), 'libraries', id);
    mkdirSync(dir, { recursive: true });
    const entry: LibraryEntry = { id, name: trimmedName, dataDir: dir, createdAt: new Date().toISOString() };
    // 只登记，不切换——用户自己决定要不要马上切过去
    void applyPatch({ libraries: [...settings.libraries, entry] });
    return entry;
  });

  handle(IPC.librarySwitch, async (_e, id: string) => {
    const target = settings.libraries.find((l) => l.id === id);
    if (!target) return { ok: false, error: '找不到这个库' };
    if (target.id === settings.activeLibraryId) return { ok: true, settings };
    // dataDir 一起改掉，触发下面的"dataDir 变了就重启引擎"逻辑——
    // 这就是"切库"的全部实现：不是引擎同时管理多个库，是换一个库重启一次。
    // 🔴 必须 await：不等的话这个 handler 会在旧引擎还没停、新引擎还没起
    // 之前就把"已切换"回给界面，界面显示库 C 已激活，实际处理请求的
    // 还是库 B 的进程——这就是本轮审计抓到的那个真 bug
    const next = await applyPatch({ activeLibraryId: id, dataDir: target.dataDir });
    return { ok: true, settings: next };
  });

  handle(IPC.libraryRename, (_e, id: string, name: string) => {
    const trimmed = String(name ?? '').trim();
    if (!trimmed) return settings;
    const libraries = settings.libraries.map((l) => (l.id === id ? { ...l, name: trimmed } : l));
    return applyPatch({ libraries });
  });

  handle(IPC.libraryRemove, (_e, id: string) => {
    // 只从注册表移除，不碰硬盘上的数据——跟这个项目"删除只删索引记录不碰
    // 原文件"的一贯原则一致。数据还在，用户改主意了随时能把目录重新加回来。
    if (id === settings.activeLibraryId) {
      return { ok: false, error: '不能移除当前激活的库，请先切换到别的库再移除' };
    }
    if (settings.libraries.length <= 1) {
      return { ok: false, error: '至少要保留一个库' };
    }
    const libraries = settings.libraries.filter((l) => l.id !== id);
    void applyPatch({ libraries });
    return { ok: true };
  });

  async function applyPatch(patch: Partial<AppSettings>): Promise<AppSettings> {
    const before = settings;
    settings = patchSettings(patch);
    ensureDataDirs(settings);

    if (before.launchAtLogin !== settings.launchAtLogin) {
      setLaunchAtLogin(settings.launchAtLogin);
    }

    /**
     * 🔴 **托盘常驻改了要当场生效，不能等重启。**
     *
     * 原来这里根本没处理 runInTray，两个方向都是坏的：
     *   · 打开它 → 托盘图标不出现。而 `window-all-closed` 已经开始按
     *     runInTray=true 走了（不退出），于是用户关掉窗口之后
     *     **既没有窗口也没有托盘图标** —— 又一个隐形进程。
     *   · 关掉它 → 图标赖着不走，点它还能唤出窗口，看着像没关掉。
     * 两种都不报错，都只能靠重启应用"自己好了"。
     */
    if (before.runInTray !== settings.runInTray) {
      if (settings.runInTray) {
        tray?.create(settings.clipboardSentinel);
        if (engine) tray?.setEngineState(engine.getState());
      } else {
        tray?.destroy();
      }
    }
    if (before.clipboardSentinel !== settings.clipboardSentinel) {
      tray?.setClipboardEnabled(settings.clipboardSentinel);
      applyClipboardSetting();
    }
    if (before.clipboardAutoArchiveLinks !== settings.clipboardAutoArchiveLinks) {
      clip?.setAutoArchiveLinks(settings.clipboardAutoArchiveLinks);
    }
    if (
      before.clipboardPeek !== settings.clipboardPeek ||
      before.clipboardPeekWeb !== settings.clipboardPeekWeb ||
      before.allowNetwork !== settings.allowNetwork
    ) {
      applyPeekSetting();
    }
    if (JSON.stringify(before.cloud) !== JSON.stringify(settings.cloud)) {
      void pushCloudConfig();
    }
    if (JSON.stringify(before.watchedFolders) !== JSON.stringify(settings.watchedFolders)) {
      void pushWatchedFolders();
    }
    // 数据目录 / 并发度 / 隐私围栏开关变了要重启引擎才生效——
    // allowCloud / enableImageDescription / enableFaceClustering 都是启动时
    // 传给 Python 进程的命令行参数（EngineConfig 的字段，不像云端 Key 那样
    // 能在引擎跑着的时候用 /api/cloud/configure 热更新），只改 settings.json
    // 不重启引擎的话，界面上的开关和后端实际生效的状态会对不上。
    if (
      before.dataDir !== settings.dataDir ||
      before.modelDir !== settings.modelDir ||
      before.concurrency !== settings.concurrency ||
      before.cloud.enabled !== settings.cloud.enabled ||
      before.enableImageDescription !== settings.enableImageDescription ||
      before.enableFaceClustering !== settings.enableFaceClustering ||
      before.lanPairingEnabled !== settings.lanPairingEnabled ||
      before.pairingToken !== settings.pairingToken ||
      // 联网这一路同理，全是启动参数。
      // 🔴 `allowNetwork` 尤其不能漏 —— 用户在隐私围栏里点「一键全断网」，
      // 如果引擎不重启，它照样能出网，而界面显示的是已断网。
      // 那是最坏的一种半成品：**看起来生效了，实际没有**
      before.allowNetwork !== settings.allowNetwork ||
      before.webLineupSize !== settings.webLineupSize ||
      before.verifyLevel !== settings.verifyLevel ||
      JSON.stringify(before.webEngines) !== JSON.stringify(settings.webEngines) ||
      JSON.stringify(before.webEndpoints) !== JSON.stringify(settings.webEndpoints) ||
      JSON.stringify(before.trustProfile) !== JSON.stringify(settings.trustProfile)
    ) {
      // 等重启真正完成（旧进程已退出、新进程已启动）再往下走——调用方
      // （比如"切库"）靠这个 await 才能保证它返回给界面"已切换"时，
      // 服务请求的确实已经是新库的引擎，而不是还在悄悄读旧库
      await requestEngineRestart();
    }

    broadcast(IPC.settingsChanged, settings);
    return settings;
  }

  // 引擎
  handle(IPC.engineGetState, () => engine?.getState() ?? null);
  // 🔴 必须走 requestEngineRestart()，不能直接 engine.restart()：
  //    理由见文件顶上 engineRestartChain 那段——绕过串行链的重启会漏下
  //    没人管的 Python 子进程，它继续锁着上一个库的索引文件
  handle(IPC.engineRestart, () => requestEngineRestart());

  /**
   * 首次运行自举（锚点 2「可以自动配置需要的工具与内容」）。
   *
   * 🔴 **绝不自动触发** —— 它会在用户机器上建目录、装包，属于要先问的动作。
   * 只有用户在引导页上点了那个按钮才跑。
   * 装完直接重启引擎，不让用户再手动点一次"重试"。
   */
  handle(IPC.engineBootstrap, async () => {
    const { bootstrapEngine } = await import('./bootstrap.js');
    const r = await bootstrapEngine((p) => broadcast(IPC.engineBootstrapProgress, p));
    if (r.ok) {
      // 让引擎下次启动直接用自举出来的解释器 —— 它已经在
      // `pythonCandidates()` 的候选里（userData/engine-venv），不用额外接线
      await engine?.stop();
      startEngine();
      return { ok: true };
    }
    broadcast(IPC.engineBootstrapProgress, { step: 'error', message: r.error });
    return { ok: false, error: r.error };
  });

  // 系统集成
  handle(IPC.pickFolders, async () => {
    if (!win) return [];
    const r = await dialog.showOpenDialog(win, {
      properties: ['openDirectory', 'multiSelections'],
      title: '选择要索引的文件夹',
      buttonLabel: '加入索引',
    });
    return r.canceled ? [] : r.filePaths;
  });

  handle(IPC.pickFiles, async () => {
    if (!win) return [];
    const r = await dialog.showOpenDialog(win, {
      properties: ['openFile', 'multiSelections'],
      title: '选择要分析的文件',
      buttonLabel: '开始分析',
    });
    return r.canceled ? [] : r.filePaths;
  });

  handle(IPC.revealInExplorer, (_e, p: string) => {
    const safe = safeLocalPath(p);
    if (!safe) {
      console.warn(`[sys] 拒绝在文件管理器里定位：${String(p)}（不是本机本地路径）`);
      return;
    }
    shell.showItemInFolder(safe);
  });
  handle(IPC.openPath, async (_e, p: string) => {
    const safe = safeLocalPath(p);
    // openPath 的约定是"返回空串 = 成功，返回字符串 = 失败原因"，
    // 拒绝时照这个约定给原因，界面上的错误提示不用改
    if (!safe) return '只能打开这台电脑上的本地文件或文件夹（网络共享路径 \\\\… 已被拒绝）';
    return shell.openPath(safe);
  });
  handle(IPC.openExternal, (_e, url: string) => {
    if (!/^https?:\/\//i.test(url)) return;
    return shell.openExternal(url);
  });

  // A16：安卓配对页要显示"手机该填哪个 IP"，列出这台机器所有局域网 IPv4 地址
  // （虚拟网卡、VPN 会插进来好几个，全列出来让用户自己认——猜哪个是"真的"猜错的代价
  // 比多列几行 UI 更大）
  handle(IPC.sysGetLanAddresses, () => {
    const nets = require('node:os').networkInterfaces() as Record<
      string,
      Array<{ address: string; family: string; internal: boolean }> | undefined
    >;
    const out: string[] = [];
    for (const list of Object.values(nets)) {
      for (const info of list ?? []) {
        // Node 18 起 family 统一成字符串 IPv4，老版本给数字 4。
        // 用 String() 抹平：直接写两个相等比较会被 TS 判成两个类型没有交集
        if (!info.internal && String(info.family) === 'IPv4') {
          out.push(info.address);
        }
      }
    }
    return out;
  });

  // E4 剪贴板哨兵
  handle(IPC.clipList, () => clip?.list() ?? []);
  handle(IPC.clipArchive, (_e, id: string) => {
    const entry = clip?.list().find((x) => x.id === id);
    return entry ? archiveClip(entry) : false;
  });
  handle(IPC.clipDismiss, (_e, id: string) => clip?.remove(id));
  handle(IPC.peekClose, () => peek?.hide());

  // F7：把**真实**注册结果交给界面。设置页显示的必须是实际生效的键，
  // 不是我们希望生效的那个 —— 显示错的比不显示更糟
  handle(IPC.hotkeyReport, () => hotkeyReport);

  /**
   * 改键。**先真的注册一次再落盘。**
   *
   * 🔴 只把新键写进设置的话，用户看到"保存成功"，按下去却没反应 ——
   *    而他没有任何线索。这里试注册失败就原样回滚并把失败原因报回界面。
   */
  handle(
    IPC.hotkeySet,
    async (_e, id: string, accelerator: string): Promise<{ ok: boolean; error?: string }> => {
      const key = id === 'focus-search' ? 'focusSearch' : id === 'screenshot-search' ? 'screenshot' : null;
      if (!key) return { ok: false, error: `不认识的快捷键项：${id}` };

      const before = settings.hotkeys ?? {};
      const next = { ...before, [key]: accelerator.trim() };
      settings = patchSettings({ hotkeys: next });
      applyHotkeys();

      const row = hotkeyReport.find((r) => r.id === id);
      // 用了备选键 = 首选没抢到。对"我刚指定了这个键"来说，那就是失败
      if (!row?.active || (accelerator.trim() && row.usedFallback)) {
        settings = patchSettings({ hotkeys: before });
        applyHotkeys();
        broadcast(IPC.settingsChanged, settings);
        return {
          ok: false,
          error: `${accelerator} 抢不到，多半被别的程序占着（输入法、截图工具、录屏软件最常见）。已经保持原样。`,
        };
      }
      broadcast(IPC.settingsChanged, settings);
      return { ok: true };
    },
  );
  // A4：命令面板里也能触发截图，不是只有快捷键那一条路
  handle(IPC.screenshotCapture, () => launchScreenCapture());

  // E5：引用可点的 PDF。渲染层把引擎生成的 single-html 交过来，
  // 这边用 Chromium 自己的 PDF 后端打印 —— 只有它会保留 <a> 的链接注解
  handle(IPC.saveText, (_e, req: { content: string; name: string; ext: string }) =>
    saveText(req?.content ?? '', req?.name ?? '文稿', req?.ext ?? 'md'),
  );
  handle(IPC.exportPdf, (_e, req: { html: string; name: string }) =>
    exportPdf(req?.html ?? '', req?.name ?? '研究简报'),
  );
  handle(IPC.clipClear, () => {
    clip?.clear();
    // ⚠️ 必须广播，否则界面自己那份状态不会跟着清 —— 用户点了「全部清掉」，
    //    主进程空了，界面上的条目却还在，而且从此和内存对不上。实测抓到过。
    //    null 沿用「哨兵被关掉」那条约定：收到就把列表清空。
    broadcast(IPC.clipCaptured, null);
  });

  // 主题
  handle(IPC.themeGetSystem, () => (nativeTheme.shouldUseDarkColors ? 'dark' : 'light'));
  nativeTheme.on('updated', () => {
    broadcast(IPC.themeSystemChanged, nativeTheme.shouldUseDarkColors ? 'dark' : 'light');
  });

  // R8 云端简报：Key 走 safeStorage，settings.json 里只留一个"设没设"的布尔值
  handle(IPC.cloudHasKey, () => hasCloudKey());
  handle(IPC.cloudSetKey, (_e, apiKey: string) => {
    const ok = saveCloudKey(apiKey);
    if (ok) void pushCloudConfig();
    return ok;
  });
  handle(IPC.cloudClearKey, () => {
    clearCloudKey();
    void pushCloudConfig();
  });

  // S3 联网搜索引擎的 Key。
  //
  // 🔴 **改完必须重启引擎**，和 webEndpoints 那一路同理：这些 Key 是
  // 启动时通过 `--web-key id=值` 传给 Python 进程的命令行参数，
  // 不像云端 Key 那样有热更新接口。不重启的话，用户填完 Key 会看到
  // 引擎照旧报"没有配置 API Key" —— 又是一次"看起来生效了，实际没有"。
  handle(IPC.engineKeyStatus, () => engineKeyStatus());
  handle(IPC.engineKeySet, (_e, id: string, value: string) => {
    const key = String(id || '').trim();
    if (!key) return false;
    const all = loadEngineKeys();
    const next = String(value ?? '').trim();
    if (next) all[key] = next;
    else delete all[key];
    const ok = saveEngineKeys(all);
    if (ok) void requestEngineRestart();
    return ok;
  });
  // U 组 应用自更新。**下载和安装永远是用户点出来的**，
  // 这里没有任何一条路径会自己走到 quitAndInstall
  handle(IPC.updateGetState, () => updater?.getState() ?? null);
  handle(IPC.updateCheck, () => updater?.check(false));
  handle(IPC.updateDownload, () => updater?.download());
  handle(IPC.updateInstall, () => {
    // 让引擎先干净退出，再让安装器接管。不这么做的话 Python 进程
    // 还占着 data 目录的文件句柄，NSIS 覆盖安装会撞上"文件被占用"
    (app as AppRef).isQuitting = true;
    updater?.install();
  });
  handle(IPC.updateSkip, (_e, version: string) => {
    settings = patchSettings({ skippedUpdateVersion: version });
    updater?.setSkippedVersion(version);
    broadcast(IPC.settingsChanged, settings);
  });

  handle(
    IPC.cloudTest,
    async (
      _e,
      draft: { provider: string; baseUrl: string; chatModel: string; apiKey: string },
    ) => {
      const port = engine?.getState().port;
      if (!port) return { ok: false, error: '引擎还没就绪' };
      try {
        /**
         * 🔴 **草稿里没带 Key 时，草稿里的地址一律不作数。**
         *
         * 原来这里是 `apiKey = draft.apiKey || loadCloudKey()`，而
         * `baseUrl` 整个由渲染层给。于是一次
         * `cloud.test({ baseUrl:'https://攻击者/v1', apiKey:'' })`
         * 就让主进程**替调用方**把付费 Key 从 safeStorage 解出来，
         * 送到攻击者的服务器上 —— 调用方自己根本不需要知道 Key 是什么。
         *
         * 两个候选修法里选了这一个（另一个是"baseUrl 必须 https + 厂商白名单
         * + 自建端点要显式开关"）：白名单挡不住自建端点这个正当需求，
         * 一旦留了开关，攻击面又回来了；而"要用已存的 Key，就只能用已存的
         * 那套配置"是一条不需要维护任何名单的硬规则，也不改变正常用法 ——
         * 用户在设置页改地址时那个改动本来就已经存进 settings 了
         * （SettingsPage 的 patchCloud 是立刻落盘的），
         * 所以"用已保存的配置去测"测的正是他刚填的地址。
         *
         * 只有用户在输入框里**当场敲了一把新 Key**时，才允许连草稿里的
         * 地址一起用 —— 那把 Key 是他自己刚输入的，不是我们替他解密的。
         */
        const typed = String(draft?.apiKey ?? '').trim();
        const target = typed
          ? {
              provider: String(draft?.provider ?? 'none'),
              baseUrl: String(draft?.baseUrl ?? ''),
              chatModel: String(draft?.chatModel ?? ''),
              apiKey: typed,
            }
          : {
              provider: settings.cloud.enabled ? settings.cloud.provider : 'none',
              baseUrl: settings.cloud.baseUrl ?? '',
              chatModel: settings.cloud.chatModel ?? '',
              apiKey: loadCloudKey() ?? '',
            };
        // 当场敲的那把 Key 也不能往任意协议上送：`baseUrl` 只认 http/https，
        // 空串交给引擎用它自己的默认地址
        if (target.baseUrl && !/^https?:\/\//i.test(target.baseUrl)) {
          return { ok: false, error: '接口地址必须以 http:// 或 https:// 开头' };
        }
        await fetch(`http://127.0.0.1:${port}/api/cloud/configure`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(target),
        });
        const r = await fetch(`http://127.0.0.1:${port}/api/cloud/test`, { method: 'POST' });
        const body = (await r.json().catch(() => ({}))) as { detail?: string; reply?: string };
        if (!r.ok) return { ok: false, error: body.detail ?? `HTTP ${r.status}` };
        return { ok: true, reply: body.reply };
      } catch (err) {
        return { ok: false, error: err instanceof Error ? err.message : String(err) };
      } finally {
        // 测试用的草稿别留在引擎里——把真正保存过的配置推回去，
        // 没保存过就是 none，不能让一次"测试"意外地让云端功能变成可用状态
        void pushCloudConfig();
      }
    },
  );
}

/**
 * 按当前设置（重新）注册全局快捷键。
 *
 * 🔴 **先注销再注册。** `globalShortcut.register()` 对一个已经被**自己**
 *    占着的组合会直接失败 —— 改键时不先注销，就会出现"改成 A 成功了，
 *    再改回原来那个键却说抢不到"这种莫名其妙的现象。
 *
 * 🔴 **注册结果必须留下来给界面看。** register() 抢不到时返回 false 而不抛，
 *    失败是静默的 —— 用户按了没反应，日志干干净净，他唯一能得出的结论
 *    是"这功能坏了"。
 */
function applyHotkeys(): void {
  unregisterAllHotkeys();
  const custom = settings.hotkeys ?? {};
  hotkeyReport = registerHotkeys([
    {
      id: 'focus-search',
      label: '任何时候唤起搜索',
      accelerator: custom.focusSearch?.trim() || 'Alt+Space',
      fallbacks: ['CommandOrControl+Alt+Space', 'CommandOrControl+Shift+Space'],
      run: () => {
        // 🔴 按一下唤起，**再按一下收回去**。原来这里只有 showWindow()：
        //    窗口已经在眼前了还按同一个键，什么都不会发生，用户只能去点关闭。
        //    收起来的判断在 toggleWindow 里（要看焦点，理由见那儿）。
        if (toggleWindow('hotkey')) return;
        win?.webContents.send(IPC.engineEvent, { type: 'ui.focus-search' });
      },
    },
    {
      id: 'screenshot-search',
      label: '截图直搜',
      accelerator: custom.screenshot?.trim() || 'CommandOrControl+Alt+S',
      fallbacks: ['CommandOrControl+Shift+Alt+S'],
      run: () => {
        void launchScreenCapture();
      },
    },
  ]);
  // 托盘菜单里那行「快速搜索…」要印真正抢到的键，不能印我们希望抢到的
  tray?.setSearchAccelerator(hotkeyReport.find((r) => r.id === 'focus-search')?.active ?? null);
  for (const r of hotkeyReport) {
    if (!r.active) {
      console.warn(`[hotkey] 「${r.label}」一个都没抢到，试过：${r.tried.join(' / ')}`);
    } else if (r.usedFallback) {
      console.warn(`[hotkey] 「${r.label}」退到了 ${r.active}（首选被别的软件占了）`);
    }
  }
}

// ── 生命周期 ─────────────────────────────────────────────────

app.whenReady().then(() => {
  ensureDataDirs(settings);
  registerIpc();

  tray = new TrayController({
    onShow: () => showWindow(),
    onToggle: () => void toggleWindow('tray'),
    onSearch: () => {
      showWindow();
      win?.webContents.send(IPC.engineEvent, { type: 'ui.focus-search' });
    },
    onQuit: () => {
      (app as AppRef).isQuitting = true;
      app.quit();
    },
    // 同 IPC.engineRestart：托盘菜单这一路以前也绕过了串行链
    onRestartEngine: () => void requestEngineRestart(),
    onToggleClipboard: (enabled) => {
      settings = patchSettings({ clipboardSentinel: enabled });
      applyClipboardSetting();
      broadcast(IPC.settingsChanged, settings);
    },
    onOpenSettings: () => {
      showWindow();
      win?.webContents.send(IPC.engineEvent, { type: 'ui.open-settings' });
    },
  });

  // --tray-only 是开机自启带的参数：这一趟不弹窗口。
  const trayOnly = process.argv.includes('--tray-only');

  /**
   * 🔴 **静默进托盘时必须有托盘图标，哪怕 runInTray 是关的。**
   *
   * 原来的条件是 `if (settings.runInTray)`。于是「开机自启开着 + 托盘常驻关着」
   * 这个组合下，开机后：`--tray-only` 让窗口不弹，`runInTray` 为假让托盘不建 ——
   * 结果是一个**既看不见窗口、也看不见图标**的进程在后台跑。
   * 用户看到的现象是"开机自启根本没生效"，实际它生效了，只是没有任何入口能回到它。
   * 任务管理器里能看到 Synorive.exe，但没人会去那儿找。
   *
   * 规则改成：**只要这一趟不弹窗口，就一定有托盘图标。** 界面永远要有一条回来的路。
   */
  if (settings.runInTray || trayOnly) {
    tray.create(settings.clipboardSentinel);
    console.log(
      `[tray] 已创建托盘图标（托盘常驻=${settings.runInTray} 静默启动=${trayOnly}）`,
    );
  } else {
    console.log('[tray] 不创建托盘图标：托盘常驻关着，且这一趟会弹出窗口');
  }
  setLaunchAtLogin(settings.launchAtLogin);

  startEngine();
  startClipboard();
  applyPeekSetting();
  startUpdater();

  // F7 全局唤起 + A4 截图直搜。**注册结果要留下来**：
  // 界面上要显示"你想要的 Alt+空格被别的软件占了，现在用的是 Ctrl+Alt+空格"，
  // 不然用户按了没反应，永远查不出为什么
  applyHotkeys();

  if (!trayOnly) showWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) showWindow();
  });
});

app.on('window-all-closed', () => {
  // 托盘常驻时不退出 —— 剪贴板哨兵、目录监听、订阅监控都靠进程活着
  if (!settings.runInTray && process.platform !== 'darwin') {
    (app as AppRef).isQuitting = true;
    app.quit();
  }
});

app.on('before-quit', () => {
  (app as AppRef).isQuitting = true;
});

app.on('will-quit', (e) => {
  globalShortcut.unregisterAll();
  teardownRenderer(); // 隐藏窗口和渲染代理的 HTTP 服务不该活过主进程
  if (engine) {
    e.preventDefault();
    const eng = engine;
    engine = null;
    void eng.stop().finally(() => {
      tray?.destroy();
      app.exit(0);
    });
  }
});
