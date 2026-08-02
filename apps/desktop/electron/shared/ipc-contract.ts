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

  // ── E4 剪贴板哨兵 ────────────────────────────────────────
  clipList: 'clip:list',
  clipArchive: 'clip:archive',
  clipDismiss: 'clip:dismiss',
  clipClear: 'clip:clear',
  /** 攒到新内容时主进程推过来 */
  clipCaptured: 'clip:captured',

  // ── 主题 ────────────────────────────────────────────────
  themeGetSystem: 'theme:get-system',
  themeSystemChanged: 'theme:system-changed',
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
