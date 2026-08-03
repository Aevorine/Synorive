/**
 * 主进程 ↔ 渲染进程 IPC 契约
 * ============================================================
 * 渲染层永远不直接碰 Node API。所有能力都通过 preload 里
 * contextBridge 暴露的这张表走，通道名集中在这里定义，
 * 两边引同一个文件，改名字就会两边一起编译报错。
 */

export const IPC = {
  // ── 窗口 ────────────────────────────────────────────────
  windowMinimize: 'window:minimize',
  windowMaximizeToggle: 'window:maximize-toggle',
  windowClose: 'window:close',
  windowIsMaximized: 'window:is-maximized',
  windowStateChanged: 'window:state-changed',

  // ── 设置 ────────────────────────────────────────────────
  settingsGet: 'settings:get',
  settingsPatch: 'settings:patch',
  settingsChanged: 'settings:changed',

  // ── 引擎进程 ─────────────────────────────────────────────
  engineGetState: 'engine:get-state',
  engineRestart: 'engine:restart',
  engineStateChanged: 'engine:state-changed',
  /** 引擎推来的实时事件（搜索分级结果 / 摄取进度 / 依赖状态…） */
  engineEvent: 'engine:event',

  // ── 系统集成 ─────────────────────────────────────────────
  pickFolders: 'sys:pick-folders',
  pickFiles: 'sys:pick-files',
  revealInExplorer: 'sys:reveal',
  openExternal: 'sys:open-external',
  openPath: 'sys:open-path',
  readDroppedPaths: 'sys:read-dropped-paths',
  /** A16 安卓配对：列出本机所有局域网 IPv4 地址，给配对面板显示 */
  sysGetLanAddresses: 'sys:get-lan-addresses',

  // ── E4 剪贴板哨兵 ────────────────────────────────────────
  clipList: 'clip:list',
  clipArchive: 'clip:archive',
  clipDismiss: 'clip:dismiss',
  clipClear: 'clip:clear',
  /** 攒到新内容时主进程推过来 */
  clipCaptured: 'clip:captured',
  /** N7 随手研究：主进程把"刚复制的这段话"推给浮窗渲染层 */
  peekQuery: 'peek:query',
  /** 浮窗自己请求关闭（点了叉、或按 Esc） */
  peekClose: 'peek:close',
  /** A8 复制了一张图 → 浮窗按图搜（和 peekQuery 分开：一个给文字一个给图） */
  peekImage: 'peek:image',

  /** F7 全局快捷键的**真实**注册结果。抢不到首选键时界面要照实显示 */
  hotkeyReport: 'hotkey:report',
  /** A4 手动触发一次截图直搜（快捷键之外，命令面板里也能点） */
  screenshotCapture: 'hotkey:screenshot',

  /**
   * E5 把一段 HTML 打成 PDF，且**保留可点的引用锚点**。
   * 必须走主进程：渲染层的 `window.print()` 走的是系统打印驱动，
   * 那条路会把 `<a href>` 拍成纯文字，导出的 PDF 里点引用号毫无反应。
   */
  exportPdf: 'export:pdf',

  /** 首次运行自举：自己找 Python、建 venv、装引擎 */
  engineBootstrap: 'engine:bootstrap',
  /** 自举进度（每一步都推，装依赖要一两分钟，不能只给一个转圈） */
  engineBootstrapProgress: 'engine:bootstrap-progress',

  // ── 主题 ────────────────────────────────────────────────
  themeGetSystem: 'theme:get-system',
  themeSystemChanged: 'theme:system-changed',

  // ── R8 云端简报生成：Key 走 safeStorage，不进 settings.json ──
  cloudSetKey: 'cloud:set-key',
  cloudHasKey: 'cloud:has-key',
  cloudClearKey: 'cloud:clear-key',
  cloudTest: 'cloud:test',
} as const;

export type IpcChannel = (typeof IPC)[keyof typeof IPC];

/** 引擎进程的生命周期状态 —— 界面状态栏直接显示它 */
export type EngineLifecycle =
  | 'stopped'
  | 'starting'
  | 'ready'
  | 'degraded'
  | 'restarting'
  | 'failed';

export interface EngineProcessState {
  lifecycle: EngineLifecycle;
  pid: number | null;
  port: number | null;
  /** 启动到就绪耗时，毫秒 */
  bootMs: number | null;
  restartCount: number;
  lastError: string | null;
  /** 引擎自报的详细状态（EngineStatus），未就绪时为 null */
  detail: unknown | null;
}

/** E4 剪贴板哨兵攒下来的一条 */
export interface ClipEntry {
  id: string;
  kind: 'text' | 'link' | 'image';
  content: string;
  preview: string;
  width?: number;
  height?: number;
  capturedAt: string;
  archived: boolean;
}

export interface WindowState {
  isMaximized: boolean;
  isFullScreen: boolean;
}
