/**
 * preload —— 渲染层能碰到的全部能力就这一张表
 * ============================================================
 * contextIsolation 开着，渲染层拿不到 Node、拿不到 ipcRenderer 本体，
 * 只能调这里白名单里的方法。这是安全边界，不是写着好看的。
 */

import { contextBridge, ipcRenderer, webUtils } from 'electron';
import { IPC } from '../shared/ipc-contract.js';

type Unsubscribe = () => void;

function on<T>(channel: string, cb: (payload: T) => void): Unsubscribe {
  const handler = (_e: unknown, payload: T) => cb(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

const api = {
  window: {
    minimize: () => ipcRenderer.invoke(IPC.windowMinimize),
    maximizeToggle: () => ipcRenderer.invoke(IPC.windowMaximizeToggle),
    close: () => ipcRenderer.invoke(IPC.windowClose),
    isMaximized: () => ipcRenderer.invoke(IPC.windowIsMaximized),
    onStateChanged: (cb: (s: { isMaximized: boolean; isFullScreen: boolean }) => void) =>
      on(IPC.windowStateChanged, cb),
  },

  settings: {
    get: () => ipcRenderer.invoke(IPC.settingsGet),
    patch: (patch: unknown) => ipcRenderer.invoke(IPC.settingsPatch, patch),
    onChanged: (cb: (s: unknown) => void) => on(IPC.settingsChanged, cb),
  },

  engine: {
    getState: () => ipcRenderer.invoke(IPC.engineGetState),
    restart: () => ipcRenderer.invoke(IPC.engineRestart),
    onStateChanged: (cb: (s: unknown) => void) => on(IPC.engineStateChanged, cb),
    onEvent: (cb: (e: unknown) => void) => on(IPC.engineEvent, cb),
  },

  sys: {
    pickFolders: (): Promise<string[]> => ipcRenderer.invoke(IPC.pickFolders),
    pickFiles: (): Promise<string[]> => ipcRenderer.invoke(IPC.pickFiles),
    reveal: (p: string) => ipcRenderer.invoke(IPC.revealInExplorer, p),
    openPath: (p: string) => ipcRenderer.invoke(IPC.openPath, p),
    openExternal: (url: string) => ipcRenderer.invoke(IPC.openExternal, url),
    /**
     * 拖进来的文件拿真实路径。
     * Electron 32 起 File.path 被移除了，必须走 webUtils.getPathForFile，
     * 而它只能在 preload 里调 —— 这是「投喂即搜 E1」的地基。
     */
    pathForFile: (file: File): string => webUtils.getPathForFile(file),
  },

  theme: {
    getSystem: (): Promise<'light' | 'dark'> => ipcRenderer.invoke(IPC.themeGetSystem),
    onSystemChanged: (cb: (t: 'light' | 'dark') => void) => on(IPC.themeSystemChanged, cb),
  },
} as const;

export type SynoriveApi = typeof api;

contextBridge.exposeInMainWorld('synorive', api);
