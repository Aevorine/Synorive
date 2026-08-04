/**
 * A3 Ask 模式状态
 * ============================================================
 * 和 useSearch 分开的两条理由：
 *
 * ① **节奏完全不同。** 搜索是敲一个字就发一次（140ms 防抖），
 *    Ask 是**按回车才发**——一个问题打到一半时发出去，摘出来的答案
 *    是对着半句话答的，而它会先于正确答案显示出来。
 *
 * ② **失败语义不同。** 搜索"没结果"是空列表；Ask"没结果"是一个带
 *    原因和建议的对象。混在一个 store 里会让两边的 error/empty 判断互相污染。
 *
 * 两者共用同一个输入框（B1 主舞台）—— 切模式时**输入内容永不丢失**，
 * 这是刻意的：用户常常先当搜索词打进去，发现要的是答案才切过来。
 */

import { create } from 'zustand';
import type { AskAnswer, SearchFilters, SearchHit } from '@synorive/shared-types';
import { api } from './api';
import { recordAsk } from './perf';

interface AskState {
  /** 最后一次真正提交的问题（不是输入框里正在打的字） */
  asked: string;
  answer: AskAnswer | null;
  /** 引擎读过的那几条，摊在答案下面让用户能自己核对 */
  hits: SearchHit[];
  elapsedMs: number;
  loading: boolean;
  error: string | null;

  run: (question: string, filters?: SearchFilters) => void;
  clear: () => void;
}

/**
 * 只保留最后一次请求。
 *
 * 🔴 不中止上一次的话，会出现**旧答案盖住新答案**：用户改了问题重问，
 *    第一次请求因为要跑精排慢了 0.8 秒，回来时把第二次的正确答案覆盖掉。
 *    这类错误在界面上看不出是竞态 —— 只会显得"它答非所问"。
 */
let inflight: AbortController | null = null;

export const useAsk = create<AskState>((set) => ({
  asked: '',
  answer: null,
  hits: [],
  elapsedMs: 0,
  loading: false,
  error: null,

  run: (question, filters) => {
    const q = question.trim();
    if (!q) return;

    inflight?.abort();
    const ctrl = new AbortController();
    inflight = ctrl;

    set({ loading: true, error: null, asked: q });

    void api
      .ask({ query: q, filters }, ctrl.signal)
      .then((r) => {
        // 已经被更新的一次请求取代了 —— 直接丢弃，别写 state
        if (ctrl.signal.aborted) return;
        // C6：记引擎端耗时而不是往返总时长 —— 往返里含 IPC 和 JSON 解析，
        // 那两项和"检索快不快"无关，混进去会让指标指向错误的优化方向
        recordAsk(r.elapsedMs ?? 0);
        set({
          answer: r.ask,
          hits: r.hits ?? [],
          elapsedMs: r.elapsedMs ?? 0,
          loading: false,
        });
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        // AbortError 不是错误，是我们自己取消的
        if (e instanceof DOMException && e.name === 'AbortError') return;
        set({ loading: false, error: e instanceof Error ? e.message : String(e) });
      });
  },

  clear: () => {
    inflight?.abort();
    inflight = null;
    set({ asked: '', answer: null, hits: [], elapsedMs: 0, loading: false, error: null });
  },
}));
