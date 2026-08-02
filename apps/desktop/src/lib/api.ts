/**
 * 引擎 HTTP 客户端
 * ============================================================
 * 端口是引擎启动时动态分配的，主进程通过 engine 状态告诉渲染层。
 * 所以这里不能写死地址，得从 store 里取当前端口。
 */

import type {
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

async function call<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
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

  ingest: (req: IngestRequest) =>
    call<{ jobId: string }>('/api/ingest', { method: 'POST', body: JSON.stringify(req) }),

  item: (id: string) => call<ContentItem>(`/api/items/${id}`),

  content: (id: string, maxChars = 20000) =>
    call<{ text: string; item: ContentItem }>(`/api/items/${id}/content?maxChars=${maxChars}`),

  recordOpen: (id: string) => call<{ ok: boolean }>(`/api/items/${id}/open`, { method: 'POST' }),

  stats: () => call<{ items: number; ready: number; failed: number; chunks: number }>('/api/stats'),

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
};

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
        emit({ ...full, final: true });
      } catch (e) {
        if ((e as Error).name === 'AbortError') return;
        onError?.(e as Error);
      }
    },
  };
}
