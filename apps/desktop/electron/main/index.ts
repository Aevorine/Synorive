/**
 * Synorive 桌面端 · 主进程入口
 */

import { BrowserWindow, app, dialog, globalShortcut, ipcMain, nativeTheme, shell } from 'electron';
import type { AppSettings } from '@synorive/shared-types';
import { IPC, type ClipEntry, type EngineProcessState } from '../shared/ipc-contract.js';
import { ClipboardWatcher } from './clipboard.js';
import { PeekWindow } from './peek.js';
import {
  clearCloudKey,
  hasCloudKey,
  loadCloudKey,
  loadEngineKeys,
  saveCloudKey,
} from './cloud-keys.js';
import { EngineManager } from './engine.js';
import { teardown as teardownRenderer } from './render.js';
import { ensureDataDirs, loadSettings, patchSettings } from './settings.js';
import { TrayController, setLaunchAtLogin } from './tray.js';
import { createMainWindow } from './window.js';

interface AppRef {
  isQuitting?: boolean;
}

let win: BrowserWindow | null = null;
let tray: TrayController | null = null;
let engine: EngineManager | null = null;
let clip: ClipboardWatcher | null = null;
/** N7 随手研究浮窗。默认关，所以默认是 null —— 开了才建 */
let peek: PeekWindow | null = null;
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
    enableImageDescription: settings.enableImageDescription,
    enableFaceClustering: settings.enableFaceClustering,
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
    if (s.lifecycle === 'ready') void pushCloudConfig();
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
  ipcMain.handle(IPC.settingsPatch, (_e, patch: Partial<AppSettings>) => {
    const before = settings;
    settings = patchSettings(patch);
    ensureDataDirs(settings);

    if (before.launchAtLogin !== settings.launchAtLogin) {
      setLaunchAtLogin(settings.launchAtLogin);
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
      void engine?.stop().then(() => startEngine());
    }

    broadcast(IPC.settingsChanged, settings);
    return settings;
  });

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

  if (settings.runInTray) tray.create(settings.clipboardSentinel);
  setLaunchAtLogin(settings.launchAtLogin);

  startEngine();
  startClipboard();
  applyPeekSetting();

  // 全局快捷键：任何时候唤起搜索
  globalShortcut.register('CommandOrControl+Alt+Space', () => {
    showWindow();
    win?.webContents.send(IPC.engineEvent, { type: 'ui.focus-search' });
  });

  // --tray-only 是开机自启时带的，静默进托盘不弹窗口
  const trayOnly = process.argv.includes('--tray-only');
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
