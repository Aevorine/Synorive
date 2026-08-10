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

  // ── 多库支持 ─────────────────────────────────────────────
  // 引擎是"一个进程绑死一个数据目录"的架构，没法同时管理多个库——
  // 这几个 IPC 全是在操作 `AppSettings.libraries` 这份注册表，
  // 真正的"切库"落地成"把 dataDir 换掉，让已有的重启逻辑触发一次"。
  /** 列出所有库 */
  libraryList: 'library:list',
  /** 新建一条库记录。不传目录就在 userData 下自动生成一个。**不会自动切换过去** */
  libraryCreate: 'library:create',
  /** 切到另一个库——会重启引擎，调用方之前要先告诉用户这一点 */
  librarySwitch: 'library:switch',
  libraryRename: 'library:rename',
  /** 只从注册表移除，不删硬盘上的数据目录。移除当前激活的库会被拒绝 */
  libraryRemove: 'library:remove',

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

  /**
   * A4 一键成稿：把一段纯文本（Markdown / 纯文字 / BibTeX）另存为文件。
   *
   * 单开一条而不是复用 exportPdf：那条要起一个渲染窗口打印，
   * 而这里只是写个文本文件 —— 为写一行字启动一个 BrowserWindow
   * 既慢又多一堆可能失败的环节。
   */
  saveText: 'export:text',

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

  // ── S3 联网搜索引擎的 Key：同样走 safeStorage，不进 settings.json ──
  //
  // 🔴 这三条以前是**缺的**。`cloud-keys.ts` 里 `saveEngineKeys` /
  // `engineKeyStatus` 早就写好了，却没有任何 IPC、没有 preload、没有界面
  // 调用它们 —— 于是引擎端撞上 429 时提示"去设置里填一个 Key"，
  // 而那个入口根本不存在。**一句让用户去做一件做不到的事的提示，
  // 比不给提示更糟**：他会以为是自己没找到。
  /** 回「哪几家配了 Key」，只回布尔，**永不回明文** */
  engineKeyStatus: 'engine-key:status',
  /** 存一家的 Key。空串 = 删掉这一家 */
  engineKeySet: 'engine-key:set',

  // ── U 组 应用自更新 ─────────────────────────────────────
  /** 当前更新状态快照（界面挂载时先拉一次，之后靠事件推） */
  updateGetState: 'update:get-state',
  /** 主动查一次。返回的是"发起成功没有"，结果走 updateStateChanged 推 */
  updateCheck: 'update:check',
  /** 开始下载（只有 checked 到新版本才有意义） */
  updateDownload: 'update:download',
  /** 退出并安装。**会立刻关掉应用**，调用方必须先确认过 */
  updateInstall: 'update:install',
  /** 「以后别提醒这个版本」 */
  updateSkip: 'update:skip',
  /** 状态变化推送（检查中/有新版/下载进度/已就绪/出错） */
  updateStateChanged: 'update:state-changed',
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

// ── U 组 应用自更新 ────────────────────────────────────────

/**
 * 更新器的生命周期。
 *
 * `unsupported` 是**必须存在**的一档：便携版（portable exe）和开发模式下
 * electron-updater 根本没法原地替换自己。把这种情况混进 `error` 里，
 * 用户会以为"更新坏了"并反复重试；实际上他要做的是去下载页手动换一个包。
 */
export type UpdateLifecycle =
  | 'idle'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'downloaded'
  | 'up-to-date'
  | 'error'
  | 'unsupported';

export interface UpdateState {
  lifecycle: UpdateLifecycle;
  /** 正在跑的这个版本 */
  currentVersion: string;
  /** 查到的新版本号，没查到就是 null */
  latestVersion: string | null;
  /** 更新说明（Release body），可能是 Markdown 原文 */
  releaseNotes: string | null;
  releaseUrl: string | null;
  /** 0~100，只有 downloading 阶段有意义 */
  progressPercent: number;
  /** 每秒字节数，用来估剩余时间 */
  bytesPerSecond: number;
  transferredBytes: number;
  totalBytes: number;
  /** 上次检查完成的时间（ISO），从没查过是 null */
  lastCheckedAt: string | null;
  error: string | null;
  /** `unsupported` 时说明为什么，以及用户该怎么办 */
  unsupportedReason: string | null;
  /** 用户跳过的版本号——界面据此不再挂角标 */
  skippedVersion: string | null;
}
