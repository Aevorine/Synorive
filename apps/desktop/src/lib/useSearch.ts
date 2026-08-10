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
import { recordSearch } from './perf';
import { history } from './undo';

const waterfall = createWaterfallSearch();

/** 敲字防抖。太短会白发很多请求，太长会有卡顿感。 */
const DEBOUNCE_MS = 140;

/**
 * 'deep' = 深读一份（关掉多样性，允许同一份资料铺满整屏）。
 * 'auto' = 自适应：引擎按查询内容自己判断该用哪套权重，不用你去猜
 * "这次该选求准还是求全"。默认档就是它——手动预设/滑块永远是可以
 * 随时切回去的后备，不是必须先学会才能用的门槛。
 */
export type Preset = 'auto' | 'balanced' | 'precise' | 'semantic' | 'recent' | 'deep' | 'custom';

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
  /**
   * D-adaptive：preset 是 'auto' 时，引擎这次实际判定成了哪一类
   * （precise/explore/factcheck/compare/balanced），给界面显示一句
   * "自动识别为：xxx"用。preset 不是 'auto' 时始终是 null。
   */
  autoIntent: string | null;

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
        // 🔴 之前这里不管 preset 是什么都把 weights 塞进请求——引擎侧
        // "weights 非空就整体覆盖 preset 查出来的值"（这条逻辑本身没错，
        // 显式权重理应优先于预设），后果是点 精确/求全/最近/深读 这些
        // 预设 chip 时，实际生效的还是上一次滑块停留的数值，除非那组数值
        // 刚好和预设一致。只有 preset==='custom'（用户真的拖过滑块）时
        // 才该把 weights 带上；选中命名预设（含 'auto'）时只传 preset，
        // 让引擎自己用预设表/自动分类的值，不被这里的陈旧滑块状态覆盖
        weights: preset === 'custom' ? weights : undefined,
        preset: preset === 'custom' ? undefined : preset,
        filters,
        explain,
        // 从设置里读 —— 精排是全局偏好，不是每次搜索的临时选项
        rerank: useApp.getState().settings?.rerankResults ?? false,
        limit: 60,
      },
      (r) => {
        // C6：只记**最终那一轮**的耗时。
        // 首屏关键词那一波天然只要几十毫秒，把它也记进去会让 P95 好看得失真 ——
        // 而用户体感的"搜完了"是最后一波，不是第一波
        if (r.final) recordSearch(r.elapsedMs);
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
          // 只有这次请求真的带了 preset:'auto' 才会有值，其它情况引擎
          // 不返回这个字段，这里显式置 null 免得上一次自动档的结果残留
          autoIntent: (r as { autoIntent?: string }).autoIntent ?? null,
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
    autoIntent: null,
    weights: { ...DEFAULT_WEIGHTS },
    preset: 'auto',
    filters: {},
    explain: true,

    setQuery: (q) => {
      set({ query: q });
      // C1 / A3：**「问一句」模式下打字不发请求。**
      //
      // 原来是敲一个字就发一次（140ms 防抖）。搜索场景下这是对的 ——
      // 边打边看结果收窄很有用。但问句场景下它是纯浪费而且有害：
      //   · 一句 30 字的问题会打出七八次请求，每次都跑一遍向量召回
      //   · 更糟的是**对着半句话摘出来的答案会先显示出来**，
      //     用户读到的第一版答案是错的，而它看起来和正确答案一模一样
      // 所以问答模式一律等回车（AskStage 的 submit 走 useAsk.run）。
      if (useApp.getState().inputMode === 'ask') return;
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
    // C1：立刻搜之前**必须先掐掉排队中的那次防抖**。
    // 不掐的话，"打字 → 140ms 内按回车"会发出两次一模一样的检索：
    // rerun 一次、防抖计时器到点再一次。两次结果相同所以界面上完全看不出来，
    // 只是每次回车都白跑一遍全量召回。
    rerun: () => {
      if (debounceTimer) {
        clearTimeout(debounceTimer);
        debounceTimer = null;
      }
      fire();
    },
    clear: () => {
      waterfall.cancel();
      set({ query: '', hits: [], stage: null, total: 0, searched: false, error: null, recovery: null, weakMatch: false, parsed: null });
    },
  };
});
