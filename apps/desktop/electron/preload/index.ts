/**
 * preload —— 渲染层能碰到的全部能力就这一张表
 * ============================================================
 * contextIsolation 开着，渲染层拿不到 Node、拿不到 ipcRenderer 本体，
 * 只能调这里白名单里的方法。这是安全边界，不是写着好看的。
 */

import { contextBridge, ipcRenderer, webUtils } from 'electron';
import type { AppSettings, LibraryEntry } from '@synorive/shared-types';
import { IPC, type ClipEntry, type UpdateState } from '../shared/ipc-contract.js';

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
    /** 界面整体缩放。1 = 100%。所有窗口一起设 */
    setZoom: (factor: number): Promise<void> => ipcRenderer.invoke(IPC.windowSetZoom, factor),
    onStateChanged: (cb: (s: { isMaximized: boolean; isFullScreen: boolean }) => void) =>
      on(IPC.windowStateChanged, cb),
  },

  settings: {
    get: () => ipcRenderer.invoke(IPC.settingsGet),
    patch: (patch: unknown) => ipcRenderer.invoke(IPC.settingsPatch, patch),
    onChanged: (cb: (s: unknown) => void) => on(IPC.settingsChanged, cb),
  },

  /**
   * 多库支持。切库落地成"换个 dataDir 重启引擎"——`switchTo` 会重启引擎，
   * 界面调用前要先告诉用户"切换后当前搜索状态会清空"这件不符合直觉的事。
   */
  library: {
    list: (): Promise<LibraryEntry[]> => ipcRenderer.invoke(IPC.libraryList),
    /** 不传 dataDir 就在 userData 下自动生成一个专属目录。创建后不会自动切换过去 */
    create: (name: string, dataDir?: string): Promise<LibraryEntry> =>
      ipcRenderer.invoke(IPC.libraryCreate, name, dataDir),
    switchTo: (id: string): Promise<{ ok: boolean; error?: string; settings?: AppSettings }> =>
      ipcRenderer.invoke(IPC.librarySwitch, id),
    rename: (id: string, name: string): Promise<AppSettings> =>
      ipcRenderer.invoke(IPC.libraryRename, id, name),
    /** 只从注册表移除，不删硬盘上的数据。移除当前激活的库会被拒绝 */
    remove: (id: string): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.libraryRemove, id),
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

  /**
   * 资料库整库加密。
   * 🔴 口令丢了整个库永远打不开 —— 界面开启前必须让用户明确确认这一点。
   */
  db: {
    encryptStatus: (): Promise<{
      engineReady: boolean;
      cipherAvailable: boolean;
      encrypted: boolean;
      keyStored: boolean;
    }> => ipcRenderer.invoke(IPC.dbEncryptStatus),
    encryptEnable: (passphrase: string): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.dbEncryptEnable, passphrase),
    encryptDisable: (passphrase: string): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.dbEncryptDisable, passphrase),
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
    /**
     * 改键。**先真的注册一次再落盘** —— 只写设置的话用户会看到"保存成功"
     * 而按下去没反应，且没有任何线索。抢不到时回滚并把原因说清楚。
     */
    set: (id: string, accelerator: string): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.hotkeySet, id, accelerator),
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

    /** A4 另存为文本文件（Markdown / 纯文字 / BibTeX）。同样：取消 ≠ 失败 */
    saveText: (
      content: string,
      name: string,
      ext: string,
    ): Promise<{ ok: boolean; path?: string; error?: string }> =>
      ipcRenderer.invoke(IPC.saveText, { content, name, ext }),
  },

  theme: {
    getSystem: (): Promise<'light' | 'dark'> => ipcRenderer.invoke(IPC.themeGetSystem),
    onSystemChanged: (cb: (t: 'light' | 'dark') => void) => on(IPC.themeSystemChanged, cb),
  },

  /**
   * U 组 应用自更新。
   * `install()` **会立刻关掉应用** —— 界面上必须在用户明确点了
   * 「重启并安装」之后才调，不能挂在任何自动流程里。
   */
  updater: {
    getState: (): Promise<UpdateState | null> => ipcRenderer.invoke(IPC.updateGetState),
    check: (): Promise<void> => ipcRenderer.invoke(IPC.updateCheck),
    download: (): Promise<void> => ipcRenderer.invoke(IPC.updateDownload),
    install: (): Promise<void> => ipcRenderer.invoke(IPC.updateInstall),
    skip: (version: string): Promise<void> => ipcRenderer.invoke(IPC.updateSkip, version),
    onStateChanged: (cb: (s: UpdateState) => void) => on(IPC.updateStateChanged, cb),
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

  /**
   * S3 联网搜索引擎的 Key（Serper / Brave / Tavily / Exa / Semantic Scholar…）。
   *
   * 和 `cloud` 那组一样：**渲染层只能写和查有没有，永远读不到明文**。
   * `set(id, '')` 就是删掉这一家。存完主进程会自己重启引擎让 Key 生效。
   */
  engineKeys: {
    status: (): Promise<Record<string, boolean>> => ipcRenderer.invoke(IPC.engineKeyStatus),
    set: (id: string, value: string): Promise<boolean> =>
      ipcRenderer.invoke(IPC.engineKeySet, id, value),
  },
} as const;

export type SynoriveApi = typeof api;

contextBridge.exposeInMainWorld('synorive', api);
