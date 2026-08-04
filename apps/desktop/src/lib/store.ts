/**
 * 全局状态（zustand）
 * ============================================================
 * 只放"跨界面共享且变化不频繁"的东西：设置、引擎状态、主题、当前页面。
 * 搜索结果这类高频数据不放这里 —— 它们走各自的 hook，
 * 避免一次结果更新触发整棵组件树重渲染（那是掉帧的头号来源）。
 */

import { create } from 'zustand';
import type { AppSettings, Density, InputMode } from '@synorive/shared-types';
import type { EngineProcessState } from '../../electron/shared/ipc-contract';

export type PageId =
  | 'today'
  | 'search'
  | 'library'
  | 'analyze'
  | 'timeline'
  | 'graph'
  | 'research'
  | 'settings';

/** 界面主标题 —— 用户点名要「更大的宋体」的就是这一批文字 */
export const PAGE_TITLES: Record<PageId, string> = {
  today: '今日',
  search: '搜索',
  library: '文件管理器',
  analyze: '分析中心',
  timeline: '时间轴',
  graph: '知识图谱',
  research: '研究工作台',
  settings: '设置',
};

/**
 * 实际写到 <html data-theme> 上的值。
 * 加了 'paper' 之后**不许再写 `t === 'dark' ? A : B`** —— paper 会掉进 else
 * 拿到浅色那一套，而且不报错，只是颜色悄悄不对。
 */
export type ResolvedTheme = 'light' | 'dark' | 'paper';

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

  /**
   * B1 主舞台是否展开成大输入区。
   *
   * 放全局而不是放 useSearch，是因为**从任何一页按 Ctrl+K 都要能把它拉起来**——
   * 挂在搜索页里的话，人在图谱页时那个状态根本没被挂载。
   *
   * 初值 true：没搜过东西时就该是大的。第一次拿到结果后自动收窄（见 useSearch）。
   */
  stageExpanded: boolean;
  /** A3 当前输入意图：问一句话 / 找东西 */
  inputMode: InputMode;

  /**
   * A5 当前项目的**名字**（id 存在设置里）。
   *
   * 单独放一份而不是每处现拉：项目名要出现在成稿标题、监控标签、
   * 今日页标题上 —— 三处各发一次请求，就为拿同一个字符串，
   * 而且启动那一瞬间三处会先后闪一下才填上。
   * null = 没选项目，或名字还没拉回来（两种都按"无项目"渲染）。
   */
  activeProjectName: string | null;

  setReady: (v: boolean) => void;
  setSettings: (s: AppSettings) => void;
  setEngine: (s: EngineProcessState | null) => void;
  setSystemTheme: (t: ResolvedTheme) => void;
  setPage: (p: PageId) => void;
  toggleSideBar: () => void;
  setCommandPaletteOpen: (v: boolean) => void;
  focusSearch: () => void;
  setStageExpanded: (v: boolean) => void;
  setInputMode: (m: InputMode) => void;
  setActiveProjectName: (n: string | null) => void;
  /** 展开舞台并抢焦点（Ctrl+K / 侧栏「问一句」/ 空态引导都走它） */
  openStage: () => void;
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
  stageExpanded: true,
  inputMode: 'ask',
  activeProjectName: null,

  setReady: (v) => set({ ready: v }),
  setSettings: (s) => set({ settings: s }),
  setEngine: (s) => set({ engine: s }),
  setSystemTheme: (t) => set({ systemTheme: t }),
  setPage: (p) => set({ page: p }),
  toggleSideBar: () => set((s) => ({ sideBarCollapsed: !s.sideBarCollapsed })),
  setCommandPaletteOpen: (v) => set({ commandPaletteOpen: v }),
  focusSearch: () => set((s) => ({ page: 'search', searchFocusNonce: s.searchFocusNonce + 1 })),
  setStageExpanded: (v) => set({ stageExpanded: v }),
  setInputMode: (m) => set({ inputMode: m }),
  setActiveProjectName: (n) => set({ activeProjectName: n }),
  // 三件事一起做：跳到搜索页 + 展开舞台 + 抢焦点。
  // 少任何一件都会出现"按了快捷键但光标不在框里"或"框展开了却在别的页"
  openStage: () =>
    set((s) => ({
      page: 'search',
      stageExpanded: true,
      searchFocusNonce: s.searchFocusNonce + 1,
    })),
}));

/**
 * 实际生效的主题：设置里选 system 时跟随系统。
 * 'paper' 是显式选择，**永远不会被系统偏好覆盖** —— 用户选纸感是为了看得舒服，
 * 系统一进夜间就把它顶掉的话，这个选项等于没有。
 */
export function useResolvedTheme(): ResolvedTheme {
  const settings = useApp((s) => s.settings);
  const systemTheme = useApp((s) => s.systemTheme);
  if (!settings || settings.theme === 'system') return systemTheme;
  return settings.theme;
}

export function useDensity(): Density {
  return useApp((s) => s.settings?.density ?? 'standard');
}
