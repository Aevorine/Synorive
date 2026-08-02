/**
 * 搜索状态
 * ============================================================
 * 单独一个 store，不放进全局 useApp —— 搜索结果变化频率极高，
 * 混进全局状态会让整棵组件树跟着重渲染，那是掉帧的头号来源。
 */

import { create } from 'zustand';
import type { RecoveryPlan } from '../components/Recovery';
import type {
  RankingWeights,
  SearchFilters,
  SearchHit,
  SearchStage,
} from '@synorive/shared-types';
import { DEFAULT_WEIGHTS } from '@synorive/shared-types';
import { createWaterfallSearch } from './api';

const waterfall = createWaterfallSearch();

/** 敲字防抖。太短会白发很多请求，太长会有卡顿感。 */
const DEBOUNCE_MS = 140;

export type Preset = 'balanced' | 'precise' | 'semantic' | 'recent' | 'custom';

interface SearchState {
  query: string;
  hits: SearchHit[];
  stage: SearchStage | null;
  total: number;
  elapsedMs: number;
  loading: boolean;
  error: string | null;
  /** 已经搜过一次了吗 —— 用来区分"空库"和"还没搜" */
  searched: boolean;
  /** D9：搜不到、或只搜到弱匹配时，引擎给的补救方案 */
  recovery: RecoveryPlan | null;
  /** 有结果但都只是"最接近的几条"，没有一条真正匹配上 */
  weakMatch: boolean;

  weights: RankingWeights;
  preset: Preset;
  filters: SearchFilters;
  explain: boolean;

  setQuery: (q: string) => void;
  setWeights: (w: Partial<RankingWeights>) => void;
  setPreset: (p: Preset) => void;
  setFilters: (f: SearchFilters) => void;
  toggleExplain: () => void;
  rerun: () => void;
  clear: () => void;
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null;

export const useSearch = create<SearchState>((set, get) => {
  const fire = () => {
    const { query, weights, filters, preset, explain } = get();
    if (!query.trim()) {
      waterfall.cancel();
      set({ hits: [], stage: null, total: 0, loading: false, searched: false, error: null, recovery: null, weakMatch: false });
      return;
    }

    set({ loading: true, error: null });
    void waterfall.run(
      {
        query,
        weights,
        preset: preset === 'custom' ? undefined : preset,
        filters,
        explain,
        limit: 60,
      },
      (r) => {
        set({
          hits: r.hits,
          stage: r.stage,
          total: r.totalEstimate,
          elapsedMs: r.elapsedMs,
          loading: !r.final,
          searched: true,
          // 引擎只在最终那一轮才给 recovery；首屏没有就置空，
          // 免得上一次搜索的补救建议残留在这一次的结果上
          recovery: (r as { recovery?: RecoveryPlan }).recovery ?? null,
          weakMatch: (r as { weakMatch?: boolean }).weakMatch ?? false,
        });
      },
      (e) => set({ loading: false, error: e.message, searched: true }),
    );
  };

  const schedule = () => {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(fire, DEBOUNCE_MS);
  };

  return {
    query: '',
    hits: [],
    stage: null,
    total: 0,
    elapsedMs: 0,
    loading: false,
    error: null,
    searched: false,
    recovery: null,
    weakMatch: false,
    weights: { ...DEFAULT_WEIGHTS },
    preset: 'balanced',
    filters: {},
    explain: true,

    setQuery: (q) => {
      set({ query: q });
      schedule();
    },
    setWeights: (w) => {
      set((s) => ({ weights: { ...s.weights, ...w }, preset: 'custom' }));
      schedule();
    },
    setPreset: (p) => {
      set({ preset: p });
      schedule();
    },
    setFilters: (f) => {
      set({ filters: f });
      schedule();
    },
    toggleExplain: () => {
      set((s) => ({ explain: !s.explain }));
      schedule();
    },
    rerun: fire,
    clear: () => {
      waterfall.cancel();
      set({ query: '', hits: [], stage: null, total: 0, searched: false, error: null, recovery: null, weakMatch: false });
    },
  };
});
