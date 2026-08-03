/**
 * 搜索状态
 * ============================================================
 * 单独一个 store，不放进全局 useApp —— 搜索结果变化频率极高，
 * 混进全局状态会让整棵组件树跟着重渲染，那是掉帧的头号来源。
 */

import { create } from 'zustand';
import type { RecoveryPlan } from '../components/Recovery';
import { useApp } from './store';
import type {
  RankingWeights,
  SearchFilters,
  SearchHit,
  SearchStage,
} from '@synorive/shared-types';
import { DEFAULT_WEIGHTS } from '@synorive/shared-types';
import { createWaterfallSearch } from './api';
import { history } from './undo';

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
  /**
   * D10 / L3-plus：我把你这句话理解成了什么。
   *
   * 🔴 引擎一直在算这个（`parsedQuery`），但**以前界面根本没读**。
   * 后果是：用户写 `section:方法 注意力`，结果集悄悄少了一大半，
   * 而他完全不知道是那条指令干的 —— 他只会觉得"这库里东西怎么这么少"。
   * 把理解结果显示出来，才谈得上"可以点掉它"。
   */
  parsed: { text: string; filters: string[]; unknown: string[] } | null;

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
      set({ hits: [], stage: null, total: 0, loading: false, searched: false, error: null, recovery: null, weakMatch: false, parsed: null });
      return;
    }

    set({ loading: true, error: null });

    // F3 闪回：每次真正发起搜索存一张**界面状态**快照。
    //
    // 🔴 **这是闪回唯一的数据来源。** 在补上这行之前，`history.snapshot()`
    // 全项目一次都没被调用过 —— 闪回按钮永远是禁用的，撤销栈永远是空的。
    // F3 的引擎写得好好的，却一个生产者都没有：界面渲染正常、不报错、
    // 点上去也没有任何异常，它就是**永远什么都不做**。
    //
    // 存查询词和筛选，**不存结果** —— 回到那一刻是重新查一遍，
    // 所以看到的一定是现在库里的样子。连打字时 `History.snapshot()`
    // 自己有 15 秒去重，这里不用再防抖。
    history.snapshot('search', query.trim().slice(0, 40), { query, filters, preset });

    void waterfall.run(
      {
        query,
        weights,
        preset: preset === 'custom' ? undefined : preset,
        filters,
        explain,
        // 从设置里读 —— 精排是全局偏好，不是每次搜索的临时选项
        rerank: useApp.getState().settings?.rerankResults ?? false,
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
          // 引擎只在「解析出了指令 或 有看不懂的指令」时才给 parsedQuery。
          // 没给就置 null，免得上一次的解析标签残留在这一次的结果上
          parsed:
            (r as { parsedQuery?: { text: string; filters: string[]; unknown: string[] } })
              .parsedQuery ?? null,
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
    parsed: null,
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
      set({ query: '', hits: [], stage: null, total: 0, searched: false, error: null, recovery: null, weakMatch: false, parsed: null });
    },
  };
});
