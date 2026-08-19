/**
 * Synorive 桌面端 · 主进程入口
 */

import { BrowserWindow, app, dialog, globalShortcut, ipcMain, nativeTheme, shell } from 'electron';
import { randomUUID } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { AppSettings, LibraryEntry } from '@synorive/shared-types';
import { IPC, type ClipEntry, type EngineProcessState } from '../shared/ipc-contract.js';
import { ClipboardWatcher } from './clipboard.js';
import {
  launchScreenCapture,
  registerHotkeys,
  type HotkeyReport,
} from './hotkeys.js';
import { PeekWindow } from './peek.js';
import {
  clearCloudKey,
  hasCloudKey,
  loadCloudKey,
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

// 后台不节流。和 window.ts 里的 backgroundThrottling: false 是一对 ——
// 那个管渲染进程，这几个管 Chromium 自己的遮挡检测和定时器降频。
// 少任何一半，窗口被挡住时状态栏就会停止更新。
app.commandLine.appendSwitch('disable-background-timer-throttling');
app.commandLine.appendSwitch('disable-renderer-backgrounding');
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');

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
  win.show();
  win.focus();
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
    lanPairingEnabled: settings.lanPairingEnabled,
    pairingToken: settings.pairingToken,
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

// ── IPC ─────────────────────────────────────────────────────

function registerIpc(): void {
  // 窗口
  ipcMain.handle(IPC.windowMinimize, () => win?.minimize());
  ipcMain.handle(IPC.windowMaximizeToggle, () => {
    if (!win) return;
    win.isMaximized() ? win.unmaximize() : win.maximize();
  });
  ipcMain.handle(IPC.windowClose, () => win?.close());
  ipcMain.handle(IPC.windowIsMaximized, () => win?.isMaximized() ?? false);

  // 设置
  ipcMain.handle(IPC.settingsGet, () => settings);
  ipcMain.handle(IPC.settingsPatch, (_e, patch: Partial<AppSettings>) => applyPatch(patch));

  // ── 多库支持 ─────────────────────────────────────────────
  // 全是在操作 settings.libraries 这份注册表，"切库"复用 applyPatch——
  // 传的 patch 里带 dataDir，会自然触发下面那段"dataDir 变了就重启引擎"的逻辑。
  ipcMain.handle(IPC.libraryList, () => settings.libraries);

  ipcMain.handle(IPC.libraryCreate, (_e, name: string, dataDir?: string) => {
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

  ipcMain.handle(IPC.librarySwitch, async (_e, id: string) => {
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

  ipcMain.handle(IPC.libraryRename, (_e, id: string, name: string) => {
    const trimmed = String(name ?? '').trim();
    if (!trimmed) return settings;
    const libraries = settings.libraries.map((l) => (l.id === id ? { ...l, name: trimmed } : l));
    return applyPatch({ libraries });
  });

  ipcMain.handle(IPC.libraryRemove, (_e, id: string) => {
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
  ipcMain.handle(IPC.engineGetState, () => engine?.getState() ?? null);
  ipcMain.handle(IPC.engineRestart, () => engine?.restart());

  /**
   * 首次运行自举（锚点 2「可以自动配置需要的工具与内容」）。
   *
   * 🔴 **绝不自动触发** —— 它会在用户机器上建目录、装包，属于要先问的动作。
   * 只有用户在引导页上点了那个按钮才跑。
   * 装完直接重启引擎，不让用户再手动点一次"重试"。
   */
  ipcMain.handle(IPC.engineBootstrap, async () => {
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
  ipcMain.handle(IPC.pickFolders, async () => {
    if (!win) return [];
    const r = await dialog.showOpenDialog(win, {
      properties: ['openDirectory', 'multiSelections'],
      title: '选择要索引的文件夹',
      buttonLabel: '加入索引',
    });
    return r.canceled ? [] : r.filePaths;
  });

  ipcMain.handle(IPC.pickFiles, async () => {
    if (!win) return [];
    const r = await dialog.showOpenDialog(win, {
      properties: ['openFile', 'multiSelections'],
      title: '选择要分析的文件',
      buttonLabel: '开始分析',
    });
    return r.canceled ? [] : r.filePaths;
  });

  ipcMain.handle(IPC.revealInExplorer, (_e, p: string) => shell.showItemInFolder(p));
  ipcMain.handle(IPC.openPath, (_e, p: string) => shell.openPath(p));
  ipcMain.handle(IPC.openExternal, (_e, url: string) => {
    if (!/^https?:\/\//i.test(url)) return;
    return shell.openExternal(url);
  });

  // A16：安卓配对页要显示"手机该填哪个 IP"，列出这台机器所有局域网 IPv4 地址
  // （虚拟网卡、VPN 会插进来好几个，全列出来让用户自己认——猜哪个是"真的"猜错的代价
  // 比多列几行 UI 更大）
  ipcMain.handle(IPC.sysGetLanAddresses, () => {
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
  ipcMain.handle(IPC.clipList, () => clip?.list() ?? []);
  ipcMain.handle(IPC.clipArchive, (_e, id: string) => {
    const entry = clip?.list().find((x) => x.id === id);
    return entry ? archiveClip(entry) : false;
  });
  ipcMain.handle(IPC.clipDismiss, (_e, id: string) => clip?.remove(id));
  ipcMain.handle(IPC.peekClose, () => peek?.hide());

  // F7：把**真实**注册结果交给界面。设置页显示的必须是实际生效的键，
  // 不是我们希望生效的那个 —— 显示错的比不显示更糟
  ipcMain.handle(IPC.hotkeyReport, () => hotkeyReport);
  // A4：命令面板里也能触发截图，不是只有快捷键那一条路
  ipcMain.handle(IPC.screenshotCapture, () => launchScreenCapture());

  // E5：引用可点的 PDF。渲染层把引擎生成的 single-html 交过来，
  // 这边用 Chromium 自己的 PDF 后端打印 —— 只有它会保留 <a> 的链接注解
  ipcMain.handle(IPC.saveText, (_e, req: { content: string; name: string; ext: string }) =>
    saveText(req?.content ?? '', req?.name ?? '文稿', req?.ext ?? 'md'),
  );
  ipcMain.handle(IPC.exportPdf, (_e, req: { html: string; name: string }) =>
    exportPdf(req?.html ?? '', req?.name ?? '研究简报'),
  );
  ipcMain.handle(IPC.clipClear, () => {
    clip?.clear();
    // ⚠️ 必须广播，否则界面自己那份状态不会跟着清 —— 用户点了「全部清掉」，
    //    主进程空了，界面上的条目却还在，而且从此和内存对不上。实测抓到过。
    //    null 沿用「哨兵被关掉」那条约定：收到就把列表清空。
    broadcast(IPC.clipCaptured, null);
  });

  // 主题
  ipcMain.handle(IPC.themeGetSystem, () => (nativeTheme.shouldUseDarkColors ? 'dark' : 'light'));
  nativeTheme.on('updated', () => {
    broadcast(IPC.themeSystemChanged, nativeTheme.shouldUseDarkColors ? 'dark' : 'light');
  });

  // R8 云端简报：Key 走 safeStorage，settings.json 里只留一个"设没设"的布尔值
  ipcMain.handle(IPC.cloudHasKey, () => hasCloudKey());
  ipcMain.handle(IPC.cloudSetKey, (_e, apiKey: string) => {
    const ok = saveCloudKey(apiKey);
    if (ok) void pushCloudConfig();
    return ok;
  });
  ipcMain.handle(IPC.cloudClearKey, () => {
    clearCloudKey();
    void pushCloudConfig();
  });

  // S3 联网搜索引擎的 Key。
  //
  // 🔴 **改完必须重启引擎**，和 webEndpoints 那一路同理：这些 Key 是
  // 启动时通过 `--web-key id=值` 传给 Python 进程的命令行参数，
  // 不像云端 Key 那样有热更新接口。不重启的话，用户填完 Key 会看到
  // 引擎照旧报"没有配置 API Key" —— 又是一次"看起来生效了，实际没有"。
  ipcMain.handle(IPC.engineKeyStatus, () => engineKeyStatus());
  ipcMain.handle(IPC.engineKeySet, (_e, id: string, value: string) => {
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
  ipcMain.handle(IPC.updateGetState, () => updater?.getState() ?? null);
  ipcMain.handle(IPC.updateCheck, () => updater?.check(false));
  ipcMain.handle(IPC.updateDownload, () => updater?.download());
  ipcMain.handle(IPC.updateInstall, () => {
    // 让引擎先干净退出，再让安装器接管。不这么做的话 Python 进程
    // 还占着 data 目录的文件句柄，NSIS 覆盖安装会撞上"文件被占用"
    (app as AppRef).isQuitting = true;
    updater?.install();
  });
  ipcMain.handle(IPC.updateSkip, (_e, version: string) => {
    settings = patchSettings({ skippedUpdateVersion: version });
    updater?.setSkippedVersion(version);
    broadcast(IPC.settingsChanged, settings);
  });

  ipcMain.handle(
    IPC.cloudTest,
    async (
      _e,
      draft: { provider: string; baseUrl: string; chatModel: string; apiKey: string },
    ) => {
      const port = engine?.getState().port;
      if (!port) return { ok: false, error: '引擎还没就绪' };
      try {
        // 输入框里的草稿为空，说明用户测的是"已经保存过的那把 Key"，
        // 不是没填——这时候要从 safeStorage 读出真实值，不能拿空串去配置
        // （拿空串配的话，已保存过 Key 的用户点"测试连接"必然失败，
        //  他会以为自己保存出了问题，而实际上问题出在这个 IPC 处理逻辑没接上）
        const apiKey = draft.apiKey || loadCloudKey() || '';
        await fetch(`http://127.0.0.1:${port}/api/cloud/configure`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...draft, apiKey }),
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

// ── 生命周期 ─────────────────────────────────────────────────

app.whenReady().then(() => {
  ensureDataDirs(settings);
  registerIpc();

  tray = new TrayController({
    onShow: () => showWindow(),
    onSearch: () => {
      showWindow();
      win?.webContents.send(IPC.engineEvent, { type: 'ui.focus-search' });
    },
    onQuit: () => {
      (app as AppRef).isQuitting = true;
      app.quit();
    },
    onRestartEngine: () => void engine?.restart(),
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
  hotkeyReport = registerHotkeys([
    {
      id: 'focus-search',
      label: '任何时候唤起搜索',
      accelerator: 'Alt+Space',
      fallbacks: ['CommandOrControl+Alt+Space', 'CommandOrControl+Shift+Space'],
      run: () => {
        showWindow();
        win?.webContents.send(IPC.engineEvent, { type: 'ui.focus-search' });
      },
    },
    {
      id: 'screenshot-search',
      label: '截图直搜',
      accelerator: 'CommandOrControl+Alt+S',
      fallbacks: ['CommandOrControl+Shift+Alt+S'],
      run: () => {
        void launchScreenCapture();
      },
    },
  ]);
  for (const r of hotkeyReport) {
    if (!r.active) {
      console.warn(`[hotkey] 「${r.label}」一个键都没抢到，试过：${r.tried.join(' / ')}`);
    } else if (r.usedFallback) {
      console.warn(`[hotkey] 「${r.label}」退到了 ${r.active}（首选被别的软件占了）`);
    }
  }

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
