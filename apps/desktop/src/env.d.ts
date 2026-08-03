/// <reference types="vite/client" />

import type { AppSettings } from '@synorive/shared-types';
import type { ClipEntry, EngineProcessState, WindowState } from '../electron/shared/ipc-contract';

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
  /** N7 随手研究浮窗。只有浮窗那个渲染进程用得到这两个 */
  peek: {
    onQuery: (cb: (p: { query: string; web: boolean }) => void) => Unsubscribe;
    close: () => Promise<void>;
  };
  theme: {
    getSystem: () => Promise<'light' | 'dark'>;
    onSystemChanged: (cb: (t: 'light' | 'dark') => void) => Unsubscribe;
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
}

declare global {
  interface Window {
    synorive: SynoriveApi;
  }
}

export {};
