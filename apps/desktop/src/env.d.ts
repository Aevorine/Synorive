/// <reference types="vite/client" />

import type { AppSettings } from '@synorive/shared-types';
import type {
  ClipEntry,
  EngineProcessState,
  UpdateState,
  WindowState,
} from '../electron/shared/ipc-contract';

type Unsubscribe = () => void;

export interface SynoriveApi {
  window: {
    minimize: () => Promise<void>;
    maximizeToggle: () => Promise<void>;
    close: () => Promise<void>;
    isMaximized: () => Promise<boolean>;
    onStateChanged: (cb: (s: WindowState) => void) => Unsubscribe;
  };
  settings: {
    get: () => Promise<AppSettings>;
    patch: (patch: Partial<AppSettings>) => Promise<AppSettings>;
    onChanged: (cb: (s: AppSettings) => void) => Unsubscribe;
  };
  engine: {
    getState: () => Promise<EngineProcessState | null>;
    restart: () => Promise<void>;
    onStateChanged: (cb: (s: EngineProcessState) => void) => Unsubscribe;
    onEvent: (cb: (e: unknown) => void) => Unsubscribe;
    /** 引擎起不来时自己配好环境 */
    bootstrap: () => Promise<{ ok: boolean; error?: string }>;
    onBootstrapProgress: (
      cb: (p: { step: string; message: string; ratio?: number }) => void,
    ) => Unsubscribe;
  };
  sys: {
    pickFolders: () => Promise<string[]>;
    pickFiles: () => Promise<string[]>;
    reveal: (p: string) => Promise<void>;
    openPath: (p: string) => Promise<string>;
    openExternal: (url: string) => Promise<void>;
    pathForFile: (file: File) => string;
    getLanAddresses: () => Promise<string[]>;
  };
  clip: {
    list: () => Promise<ClipEntry[]>;
    archive: (id: string) => Promise<boolean>;
    dismiss: (id: string) => Promise<void>;
    clear: () => Promise<void>;
    /** payload 为 null 表示哨兵被关掉、列表已清空 */
    onCaptured: (cb: (e: ClipEntry | null) => void) => Unsubscribe;
  };
  /** N7 随手研究浮窗。只有浮窗那个渲染进程用得到这几个 */
  peek: {
    onQuery: (cb: (p: { query: string; web: boolean }) => void) => Unsubscribe;
    /** A8 复制了一张图 —— 和 onQuery 分开，因为图走的是另一条检索路径 */
    onImage: (cb: (p: { image: string; preview: string; web: boolean }) => void) => Unsubscribe;
    close: () => Promise<void>;
  };
  /** F7 全局快捷键 ｜ A4 截图直搜 */
  hotkeys: {
    /** 真实注册结果：`active` 可能是备选键，也可能是 null（一个都没抢到） */
    report: () => Promise<
      { id: string; label: string; active: string | null; usedFallback: boolean; tried: string[] }[]
    >;
    screenshot: () => Promise<{ ok: boolean; note: string }>;
  };
  /** E5 引用可点的 PDF。`ok:false` + 无 `error` = 用户取消了保存，不是失败 */
  doc: {
    exportPdf: (
      html: string,
      name: string,
    ) => Promise<{ ok: boolean; path?: string; error?: string }>;
    /** A4 另存为文本文件。`ok:false` + 无 `error` 同样表示用户取消了 */
    saveText: (
      content: string,
      name: string,
      ext: string,
    ) => Promise<{ ok: boolean; path?: string; error?: string }>;
  };
  theme: {
    getSystem: () => Promise<'light' | 'dark'>;
    onSystemChanged: (cb: (t: 'light' | 'dark') => void) => Unsubscribe;
  };
  /**
   * U 组 应用自更新。
   * `install()` 会**立刻关掉应用** —— 只能在用户明确确认后调用。
   * `getState()` 返回 null 表示主进程那边还没初始化完（正常只在极早期出现）。
   */
  updater: {
    getState: () => Promise<UpdateState | null>;
    check: () => Promise<void>;
    download: () => Promise<void>;
    install: () => Promise<void>;
    skip: (version: string) => Promise<void>;
    onStateChanged: (cb: (s: UpdateState) => void) => Unsubscribe;
  };
  cloud: {
    hasKey: () => Promise<boolean>;
    setKey: (apiKey: string) => Promise<boolean>;
    clearKey: () => Promise<void>;
    test: (draft: {
      provider: string;
      baseUrl: string;
      chatModel: string;
      apiKey: string;
    }) => Promise<{ ok: boolean; reply?: string; error?: string }>;
  };
  /**
   * S3 联网搜索引擎的 Key。`status()` 只回「哪几家配了」，
   * **拿不到明文** —— 和 `cloud` 那组一样，明文只存在于主进程和 safeStorage 里。
   * `set(id, '')` 表示删掉这一家。存完主进程会自己重启引擎让 Key 生效。
   */
  engineKeys: {
    status: () => Promise<Record<string, boolean>>;
    set: (id: string, value: string) => Promise<boolean>;
  };
}

declare global {
  interface Window {
    synorive: SynoriveApi;
  }
}

export {};
