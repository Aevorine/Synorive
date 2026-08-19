/**
 * 引擎 HTTP 客户端
 * ============================================================
 * 端口是引擎启动时动态分配的，主进程通过 engine 状态告诉渲染层。
 * 所以这里不能写死地址，得从 store 里取当前端口。
 */

import type {
  AskResponse,
  ContentItem,
  GraphSlice,
  IngestRequest,
  SearchRequest,
  SearchResponse,
  TimelineBucket,
  TimelinePoint,
} from '@synorive/shared-types';

let basePort: number | null = null;

export function setEnginePort(port: number | null): void {
  basePort = port;
}

export function enginePort(): number | null {
  return basePort;
}

class EngineUnavailable extends Error {
  constructor() {
    super('引擎还没就绪');
    this.name = 'EngineUnavailable';
  }
}

// 导出给 webApi.ts 复用——联网搜索/云端简报是独立的一批类型（见那边的注释），
// 但走的是同一个"引擎端口从哪拿""怎么拼错误信息"的逻辑，不用另写一份
export async function call<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  if (basePort == null) throw new EngineUnavailable();
  const r = await fetch(`http://127.0.0.1:${basePort}${path}`, {
    ...init,
    signal,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}${text ? `：${text.slice(0, 200)}` : ''}`);
  }
  return (await r.json()) as T;
}

export const api = {
  search: (req: SearchRequest & { stage?: string }, signal?: AbortSignal) =>
    call<SearchResponse>('/api/search', { method: 'POST', body: JSON.stringify(req) }, signal),

  /**
   * A3 问一句话，拿一段带出处的答案。
   *
   * ⚠️ 它**不会**因为"库里没有答案"而 reject —— 那是正常业务结果，
   *    走 `ask.enough === false` 分支，不是 catch 分支。
   *    把它当错误处理会让"没搜到"和"引擎挂了"长得一模一样。
   */
  ask: (req: { query: string; filters?: SearchRequest['filters']; rerank?: boolean }, signal?: AbortSignal) =>
    call<AskResponse>('/api/ask', { method: 'POST', body: JSON.stringify(req) }, signal),

  ingest: (req: IngestRequest) =>
    call<{ jobId: string }>('/api/ingest', { method: 'POST', body: JSON.stringify(req) }),

  item: (id: string) => call<ContentItem>(`/api/items/${id}`),

  content: (id: string, maxChars = 20000) =>
    call<{ text: string; item: ContentItem }>(`/api/items/${id}/content?maxChars=${maxChars}`),

  /**
   * 记一次打开。带上当前查询词的话，引擎会顺便学一条"搜这几个词时你点了这个"。
   * 不传 query 也完全正常 —— 那时候只更新全局热度。
   */
  recordOpen: (id: string, query?: string) =>
    call<{ ok: boolean }>(
      `/api/items/${id}/open${query && query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ''}`,
      { method: 'POST' },
    ),
  /** 清空"搜这几个词时你点了什么"的记录。返回清掉多少条 */
  clearClicks: () =>
    call<{ ok: boolean; cleared: number }>('/api/personalization/clicks', { method: 'DELETE' }),
  clickStats: () => call<{ count: number }>('/api/personalization/clicks'),

  /**
   * 自定义同义词。双向 —— 搜 a 命中 b，搜 b 也命中 a。
   * 内置词表不可能知道"小李"指的是谁，这张表是用户自己的黑话和缩写。
   */
  synonyms: {
    list: () => call<{ items: { a: string; b: string; at: string }[] }>('/api/synonyms'),
    add: (a: string, b: string) =>
      call<{ ok: boolean; items: { a: string; b: string; at: string }[] }>('/api/synonyms', {
        method: 'POST',
        body: JSON.stringify({ a, b }),
      }),
    remove: (a: string, b: string) =>
      call<{ ok: boolean; items: { a: string; b: string; at: string }[] }>(
        `/api/synonyms?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
        { method: 'DELETE' },
      ),
  },

  stats: () => call<{ items: number; ready: number; failed: number; chunks: number }>('/api/stats'),

  /** C6 性能看板：引擎侧的目标值 + 运行期采样 */
  metricsBudgets: () =>
    call<{
      budgets: { id: string; label: string; target: string; how: string; hasBench: boolean }[];
      ingestBudgets: { id: string; label: string; target: string; how: string; hasBench: boolean }[];
      observed: Record<string, unknown>;
    }>('/api/metrics/budgets'),

  doctor: () => call<DoctorEntry[]>('/api/doctor'),

  installDep: (id: string) =>
    call<{ ok: boolean }>(`/api/doctor/${id}/install`, { method: 'POST' }),

  timeline: (bucket: TimelineBucket, limit = 200) =>
    call<TimelinePoint[]>(`/api/timeline?bucket=${bucket}&limit=${limit}`),

  graph: (opts: { entityId?: string; kind?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.entityId) q.set('entityId', opts.entityId);
    if (opts.kind) q.set('kind', opts.kind);
    q.set('limit', String(opts.limit ?? 60));
    return call<GraphSlice>(`/api/graph?${q}`);
  },

  scenes: (itemId: string) =>
    call<SceneRow[]>(`/api/items/${itemId}/scenes`),

  duplicates: (itemId: string) => call<ContentItem[]>(`/api/items/${itemId}/duplicates`),

  byImage: (req: { itemId?: string; path?: string; limit?: number; includeScenes?: boolean }) =>
    call<SearchResponse>('/api/search/by-image', { method: 'POST', body: JSON.stringify(req) }),

  /**
   * N6：这篇能回答哪些问题。
   *
   * 搜索的前提是你已经知道要问什么，而一篇四十页的 PDF 躺在库里，
   * 难的恰恰是"我该问它什么"。这条接口反过来做 ——
   * 从原文里读出它能回答的问题，每条都指向一个具体的段落。
   */
  questions: (itemId: string, limit = 20) =>
    call<ItemQuestions>(`/api/items/${itemId}/questions?limit=${limit}`),
};

export interface ItemQuestions {
  itemId: string;
  title: string;
  questions: {
    question: string;
    /** section 来自章节标题（最可靠）｜define/finding/method/compare/number 来自句式 */
    kind: string;
    chunkRowid: number;
    section?: string;
    page?: number;
    preview: string;
  }[];
  chunkCount: number;
  note: string;
}

export interface SceneRow {
  index: number;
  startSec: number;
  endSec: number;
  keyframePath: string | null;
  transcript: string;
}

export interface DoctorEntry {
  id: string;
  kind: string;
  name: string;
  purpose: string;
  requiredBy: string[];
  degradesTo: string;
  optional: boolean;
  state: 'ok' | 'missing' | 'failed' | 'installing' | 'downloading';
  error?: string | null;
  note?: string;
  sizeBytes?: number;
  installedVersion?: string | null;
  path?: string;
}

/**
 * D2 三级瀑布：同一次搜索先要快的、再要全的。
 *
 * 关键是 **不清屏**：keyword 结果先铺上去，semantic 回来后平滑替换。
 * 用户看到的是结果在"长出来"，而不是转圈等 260ms 再一次性弹出来。
 *
 * 每次新查询会 abort 上一次未完成的请求 —— 敲字很快时不这么做的话，
 * 早发出的慢请求会后到，把新结果覆盖掉（经典的竞态）。
 */
export function createWaterfallSearch() {
  let controller: AbortController | null = null;
  let seq = 0;

  return {
    cancel() {
      controller?.abort();
      controller = null;
    },

    async run(
      req: SearchRequest,
      onStage: (r: SearchResponse) => void,
      onError?: (e: Error) => void,
    ): Promise<void> {
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;
      const mySeq = ++seq;

      const emit = (r: SearchResponse) => {
        // 只认最新一次查询的结果，晚到的旧结果直接丢
        if (mySeq === seq && !signal.aborted) onStage(r);
      };

      try {
        // 第一波：只跑关键词和文件名子串，实测 P50 45ms @ 10 万块
        const fast = await api.search({ ...req, stage: 'keyword' }, signal);
        emit({ ...fast, final: false });

        // 第二波：加上向量语义，实测 P50 260ms @ 10 万块
        const full = await api.search({ ...req, stage: 'semantic' }, signal);
        // 还要不要第三波？要的话这一波就不是 final，否则界面会先停掉转圈再重新转
        const wantRerank = !!req.rerank;
        emit({ ...full, final: !wantRerank });

        // 第三波：交叉编码器精排。
        //
        // 🔴 它**必须**是独立的一波，不能塞进第二波里。
        //    BGE-reranker-base 是 278M 参数的交叉编码器，12 条候选实测
        //    P95 823ms —— 合进第二波就直接顶破 A3 的「完整检索 P95 ≤500ms」。
        //    拆成第三波之后：语义结果 P95 36ms 就上屏，精排结果晚到再悄悄重排，
        //    用户全程没有等待感，而 Top1 准确率实测 +3 题（94 → 97）。
        //
        // 失败不报错：精排是锦上添花，挂了就保持第二波的顺序。
        if (wantRerank) {
          try {
            const rr = await api.search({ ...req, stage: 'semantic', rerank: true }, signal);
            emit({ ...rr, final: true });
          } catch (e) {
            if ((e as Error).name === 'AbortError') return;
            emit({ ...full, final: true });
          }
        }
      } catch (e) {
        if ((e as Error).name === 'AbortError') return;
        onError?.(e as Error);
      }
    },
  };
}
