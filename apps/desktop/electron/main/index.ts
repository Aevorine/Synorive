/**
 * Synorive 桌面端 · 主进程入口
 */

import { BrowserWindow, app, dialog, globalShortcut, ipcMain, nativeTheme, shell } from 'electron';
import type { AppSettings } from '@synorive/shared-types';
import { IPC, type EngineProcessState } from '../shared/ipc-contract.js';
import { EngineManager } from './engine.js';
import { ensureDataDirs, loadSettings, patchSettings } from './settings.js';
import { TrayController, setLaunchAtLogin } from './tray.js';
import { createMainWindow } from './window.js';

interface AppRef {
  isQuitting?: boolean;
}

let win: BrowserWindow | null = null;
let tray: TrayController | null = null;
let engine: EngineManager | null = null;
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

// ── 引擎 ────────────────────────────────────────────────────

function startEngine(): void {
  engine = new EngineManager(settings.dataDir, settings.modelDir, settings.concurrency);

  engine.onStateChange((s: EngineProcessState) => {
    broadcast(IPC.engineStateChanged, s);
    tray?.setEngineState(s);
  });

  engine.onEngineEvent((ev) => {
    broadcast(IPC.engineEvent, ev);
  });

  void engine.start();
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
    }
    // 数据目录 / 并发度变了要重启引擎才生效
    if (
      before.dataDir !== settings.dataDir ||
      before.modelDir !== settings.modelDir ||
      before.concurrency !== settings.concurrency
    ) {
      void engine?.stop().then(() => startEngine());
    }

    broadcast(IPC.settingsChanged, settings);
    return settings;
  });

  // 引擎
  ipcMain.handle(IPC.engineGetState, () => engine?.getState() ?? null);
  ipcMain.handle(IPC.engineRestart, () => engine?.restart());

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

  // 主题
  ipcMain.handle(IPC.themeGetSystem, () => (nativeTheme.shouldUseDarkColors ? 'dark' : 'light'));
  nativeTheme.on('updated', () => {
    broadcast(IPC.themeSystemChanged, nativeTheme.shouldUseDarkColors ? 'dark' : 'light');
  });
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
