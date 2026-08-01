/// <reference types="vite/client" />

import type { AppSettings } from '@synorive/shared-types';
import type { EngineProcessState, WindowState } from '../electron/shared/ipc-contract';

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
  };
  sys: {
    pickFolders: () => Promise<string[]>;
    pickFiles: () => Promise<string[]>;
    reveal: (p: string) => Promise<void>;
    openPath: (p: string) => Promise<string>;
    openExternal: (url: string) => Promise<void>;
    pathForFile: (file: File) => string;
  };
  theme: {
    getSystem: () => Promise<'light' | 'dark'>;
    onSystemChanged: (cb: (t: 'light' | 'dark') => void) => Unsubscribe;
  };
}

declare global {
  interface Window {
    synorive: SynoriveApi;
  }
}

export {};
