/**
 * 全局状态（zustand）
 * ============================================================
 * 只放"跨界面共享且变化不频繁"的东西：设置、引擎状态、主题、当前页面。
 * 搜索结果这类高频数据不放这里 —— 它们走各自的 hook，
 * 避免一次结果更新触发整棵组件树重渲染（那是掉帧的头号来源）。
 */

import { create } from 'zustand';
import type { AppSettings, Density } from '@synorive/shared-types';
import type { EngineProcessState } from '../../electron/shared/ipc-contract';

export type PageId =
  | 'search'
  | 'library'
  | 'analyze'
  | 'timeline'
  | 'graph'
  | 'research'
  | 'settings';

/** 界面主标题 —— 用户点名要「更大的宋体」的就是这一批文字 */
export const PAGE_TITLES: Record<PageId, string> = {
  search: '搜索',
  library: '文件管理器',
  analyze: '分析中心',
  timeline: '时间轴',
  graph: '知识图谱',
  research: '研究工作台',
  settings: '设置',
};

export type ResolvedTheme = 'light' | 'dark';

interface AppState {
  ready: boolean;
  settings: AppSettings | null;
  engine: EngineProcessState | null;
  systemTheme: ResolvedTheme;
  page: PageId;
  sideBarCollapsed: boolean;
  commandPaletteOpen: boolean;
  /** 搜索框要不要抢焦点（托盘快捷键唤起时置 true） */
  searchFocusNonce: number;

  setReady: (v: boolean) => void;
  setSettings: (s: AppSettings) => void;
  setEngine: (s: EngineProcessState | null) => void;
  setSystemTheme: (t: ResolvedTheme) => void;
  setPage: (p: PageId) => void;
  toggleSideBar: () => void;
  setCommandPaletteOpen: (v: boolean) => void;
  focusSearch: () => void;
}

export const useApp = create<AppState>((set) => ({
  ready: false,
  settings: null,
  engine: null,
  systemTheme: 'light',
  page: 'search',
  sideBarCollapsed: false,
  commandPaletteOpen: false,
  searchFocusNonce: 0,

  setReady: (v) => set({ ready: v }),
  setSettings: (s) => set({ settings: s }),
  setEngine: (s) => set({ engine: s }),
  setSystemTheme: (t) => set({ systemTheme: t }),
  setPage: (p) => set({ page: p }),
  toggleSideBar: () => set((s) => ({ sideBarCollapsed: !s.sideBarCollapsed })),
  setCommandPaletteOpen: (v) => set({ commandPaletteOpen: v }),
  focusSearch: () => set((s) => ({ page: 'search', searchFocusNonce: s.searchFocusNonce + 1 })),
}));

/** 实际生效的主题：设置里选 system 时跟随系统 */
export function useResolvedTheme(): ResolvedTheme {
  const settings = useApp((s) => s.settings);
  const systemTheme = useApp((s) => s.systemTheme);
  if (!settings || settings.theme === 'system') return systemTheme;
  return settings.theme;
}

export function useDensity(): Density {
  return useApp((s) => s.settings?.density ?? 'standard');
}
