/**
 * preload —— 渲染层能碰到的全部能力就这一张表
 * ============================================================
 * contextIsolation 开着，渲染层拿不到 Node、拿不到 ipcRenderer 本体，
 * 只能调这里白名单里的方法。这是安全边界，不是写着好看的。
 */

import { contextBridge, ipcRenderer, webUtils } from 'electron';
import { IPC, type ClipEntry } from '../shared/ipc-contract.js';

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
    /** 引擎起不来时，让它自己找 Python、建环境、装引擎（锚点 2） */
    bootstrap: (): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.engineBootstrap),
    onBootstrapProgress: (
      cb: (p: { step: string; message: string; ratio?: number }) => void,
    ) => on(IPC.engineBootstrapProgress, cb),
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
    /** A16 安卓配对面板：这台机器所有局域网 IPv4 地址 */
    getLanAddresses: (): Promise<string[]> => ipcRenderer.invoke(IPC.sysGetLanAddresses),
  },

  /** E4 剪贴板哨兵。内容只在主进程内存里，这里拿到的是快照。 */
  clip: {
    list: (): Promise<ClipEntry[]> => ipcRenderer.invoke(IPC.clipList),
    /** 真正入库。返回 false 表示引擎没就绪或写失败。 */
    archive: (id: string): Promise<boolean> => ipcRenderer.invoke(IPC.clipArchive, id),
    dismiss: (id: string): Promise<void> => ipcRenderer.invoke(IPC.clipDismiss, id),
    clear: (): Promise<void> => ipcRenderer.invoke(IPC.clipClear),
    /** payload 为 null 表示哨兵被关掉、列表已清空 */
    onCaptured: (cb: (e: ClipEntry | null) => void) => on(IPC.clipCaptured, cb),
  },

  /**
   * N7 随手研究浮窗。**只有浮窗那个渲染进程会用到这两个** ——
   * 主窗口也能调，但它拿不到任何东西（主进程只往浮窗推 peekQuery）。
   */
  peek: {
    onQuery: (cb: (p: { query: string; web: boolean }) => void) => on(IPC.peekQuery, cb),
    /** A8 复制了一张图 —— 和 onQuery 分开，因为图走的是完全另一条检索路径 */
    onImage: (cb: (p: { image: string; preview: string; web: boolean }) => void) =>
      on(IPC.peekImage, cb),
    close: (): Promise<void> => ipcRenderer.invoke(IPC.peekClose),
  },

  /** F7 全局快捷键 ｜ A4 截图直搜 */
  hotkeys: {
    /**
     * 真实注册结果。**`active` 可能和你设的键不一样，也可能是 null** ——
     * 设置页必须照实显示，不能显示"我们想注册的那个"
     */
    report: (): Promise<
      { id: string; label: string; active: string | null; usedFallback: boolean; tried: string[] }[]
    > => ipcRenderer.invoke(IPC.hotkeyReport),
    /** A4 手动拉起系统截图。截完的图由剪贴板哨兵接住 */
    screenshot: (): Promise<{ ok: boolean; note: string }> =>
      ipcRenderer.invoke(IPC.screenshotCapture),
  },

  /**
   * E5 打印成引用可点的 PDF。
   * `ok:false` 且 `error` 为空 = 用户在保存对话框点了取消，**不是错误**，
   * 界面不该为此弹红字
   */
  doc: {
    exportPdf: (
      html: string,
      name: string,
    ): Promise<{ ok: boolean; path?: string; error?: string }> =>
      ipcRenderer.invoke(IPC.exportPdf, { html, name }),
  },

  theme: {
    getSystem: (): Promise<'light' | 'dark'> => ipcRenderer.invoke(IPC.themeGetSystem),
    onSystemChanged: (cb: (t: 'light' | 'dark') => void) => on(IPC.themeSystemChanged, cb),
  },

  /** R8 云端简报：Key 只经这几个方法进出，渲染层拿不到明文、拿不到文件路径 */
  cloud: {
    hasKey: (): Promise<boolean> => ipcRenderer.invoke(IPC.cloudHasKey),
    setKey: (apiKey: string): Promise<boolean> => ipcRenderer.invoke(IPC.cloudSetKey, apiKey),
    clearKey: (): Promise<void> => ipcRenderer.invoke(IPC.cloudClearKey),
    test: (draft: {
      provider: string;
      baseUrl: string;
      chatModel: string;
      apiKey: string;
    }): Promise<{ ok: boolean; reply?: string; error?: string }> =>
      ipcRenderer.invoke(IPC.cloudTest, draft),
  },
} as const;

export type SynoriveApi = typeof api;

contextBridge.exposeInMainWorld('synorive', api);
