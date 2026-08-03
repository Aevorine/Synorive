/**
 * 第四轮新增能力的 HTTP 客户端（B/C/D/E/A 五组）
 * ============================================================
 * **单独成文件而不是往 `webApi.ts` 里塞**：那个文件已经 540 行，
 * 而这一批在概念上是另一层 —— webApi 是「搜」，这里是「搜完之后
 * 拿这批结果再做点什么」（聚类、抽表、核对、记住）。
 *
 * 一条贯穿全文件的约定：**所有返回里的 `note` 字段都要显示给用户看**。
 * 引擎那边每个功能都写了一句能力边界（"这是等分的"、"没去正文里猜"、
 * "单一来源不代表是假的"），把它吞掉等于把最要紧的限定条件删掉了。
 */

import { call } from './api';

/**
 * E17/6.5 —— 同步状态。
 *
 * 🔴 `cryptoAvailable: false` 表示**同步整个不可用**，不是"降级成明文同步"。
 * 界面必须照这个说，含糊其辞会让用户以为同步在跑只是没加密 ——
 * 而实际上一条都推不出去。
 */
export interface SyncStatus {
  deviceId: string;
  lamport: number;
  queued: number;
  pending: number;
  entities: number;
  tombstones: number;
  cryptoAvailable: boolean;
  note: string;
}

/**
 * C12/C13 —— 整页截图归档的结果。
 *
 * 🔴 `cookieFailures` 非空时**必须显眼地提示**：少了关键的那个 session cookie，
 * 截到的是**登录页而不是内容页**，而字节数、宽高、HTTP 200 全都正常 ——
 * 这是这个功能唯一一种"看起来完全成功"的失败。
 */
export interface ArchiveShot {
  ok: boolean;
  shot: string;
  path: string;
  bytes: number;
  width?: number;
  height?: number;
  truncated: boolean;
  usedCookies: boolean;
  cookieFailures?: string[];
  warning?: string;
  ingest?: string;
}

/**
 * E15 —— 模型状态。
 *
 * 🔴 `installed` 和 `loaded` **是两回事**：模型是懒加载的，
 * 「装了但还没加载」是正常状态不是故障。混着显示会让用户
 * 在装完之后看到"未加载"，以为装失败了跑去重装一遍。
 *
 * 🔴 `hotSwappable: false` 的那个（文本向量模型）**不能在线换成别的模型** ——
 * 库里的向量都是它算的，换了之后搜索**不会报错**，只会开始返回不相干的东西。
 */
export interface ModelStatus {
  textEmbedder: {
    id: string;
    installed: boolean;
    loaded: boolean;
    provider: string | null;
    dim: number | null;
    hotSwappable: boolean;
    why: string;
  };
  reranker: {
    id: string;
    installed: boolean;
    loaded: boolean;
    hotSwappable: boolean;
    why: string;
  };
  preferGpu: boolean;
  note: string;
}

/**
 * E9 —— 全库近重复扫描。
 *
 * 🔴 `suggestKeep` 只是**建议**（按分辨率×体积排的第一名），不是决定。
 * 界面必须允许用户改选 —— 他可能要留有 EXIF 的那张、或者路径在正式目录里的那张。
 */
export interface DupSweep {
  groups: {
    phash: string;
    count: number;
    wastedBytes: number;
    members: {
      id: string;
      title: string;
      locator: string;
      sizeBytes: number;
      createdAt: string;
      width: number;
      height: number;
      suggestKeep: boolean;
    }[];
  }[];
  groupCount: number;
  truncated: boolean;
  scannedImages: number;
  wastedBytes: number;
  note: string;
}

/** 🔴 `dryRun:true` = 一条都没删。界面拿它做二次确认，别当成删成功了 */
export interface DeleteResult {
  dryRun: boolean;
  wouldDelete?: number;
  deleted?: number;
  missing: string[];
  titles?: string[];
  failed?: { id: string; error: string }[];
  note: string;
}

/**
 * B2 —— 三家反查并发的结果。
 *
 * 🔴 **每家的 `error` 必须原样显示。** 里面区分了「解析不出条目」
 * 和「被人机验证挡下」—— 折叠掉的话，用户会拿一次失败的查询
 * 当成"这张图是原创的"证据。那是这个功能能造成的最大误导。
 */
export interface ReverseMulti {
  totalPages: number;
  note: string;
  providers: Record<
    string,
    {
      pagesIncluding?: { title: string; pageUrl: string }[];
      visualSimilar?: { title: string; pageUrl: string }[];
      bestGuess?: string | null;
      error?: string | null;
    }
  >;
}

/**
 * A3 —— 一张图四路并发的结果。
 *
 * 🔴 每一路都可能单独带 `error` 而其他三路完全正常。界面必须
 * **按路显示错误**，不能因为反查那一路挂了就整块显示"分析失败" ——
 * 反查最容易挂（要联网、会被限流），而它挂掉时 OCR 和本地相似图
 * 仍然是有效结果。
 */
export interface ImageLanes {
  path: string;
  note: string;
  lanes: {
    similar?: { hits?: unknown[]; error?: string; [k: string]: unknown };
    ocr?: { text?: string; charCount?: number; hits?: unknown[]; note?: string; error?: string };
    /**
     * 🔴 字段名是 `pagesIncluding` / `visualSimilar`（`ReverseImageResult.to_dict()` 定的），
     * **不是 `results`**。这里曾经写成 `results?: unknown[]`，
     * 于是这一路永远显示「0 处出现」—— 接口是通的、没有报错、数据也真的回来了，
     * 只是界面读的那个 key 不存在。**跨语言的字段名对不上是这类静默失败的头号来源。**
     */
    reverse?: {
      pagesIncluding?: { title: string; pageUrl: string }[];
      visualSimilar?: { title: string; pageUrl: string }[];
      bestGuess?: string | null;
      note?: string;
      error?: string | null;
    };
    tamper?: { suspicion?: number; note?: string; signals?: unknown[]; error?: string; [k: string]: unknown };
  };
}

/**
 * A2 —— 秒开预览的结果。
 *
 * 🔴 `thumbs` 是**等距抽的**，不是场景切分结果。`note` 里写死了这句话，
 * 界面必须原样显示 —— 不说的话用户会以为这 12 格就是这个视频的 12 个镜头，
 * 然后奇怪为什么正式分析出来的镜头数对不上。
 */
export interface MediaPreview {
  ok: boolean;
  path?: string;
  durationSec: number;
  width?: number;
  height?: number;
  hasAudio?: boolean;
  thumbs: { sec: number; dataUrl: string }[];
  /** 0~1 的音量包络，已按峰值归一化。空数组 = 没画出来，原因在 note 里 */
  waveform: number[];
  elapsedMs: number;
  note: string;
}

/**
 * F2 —— 一个摄取任务的实时状态。
 *
 * 🔴 `items` **只含失败和跳过的**，不含成功的。成功的那几万条对用户
 * 毫无信息量，却能把一次响应撑到几十兆。所以驾驶舱的"全部"标签页
 * 展示的是这份异常清单加上一行汇总数字，不是逐个文件的流水。
 *
 * 🔴 `itemsTruncated` 为真时**必须显眼地说出来**：清单封顶 500 条，
 * 不说的话用户以为一共就失败了 500 个。
 */
export interface IngestJob {
  jobId: string;
  status: 'running' | 'done' | 'failed' | 'cancelled';
  total: number;
  done: number;
  failed: number;
  skipped: number;
  current: string | null;
  startedAt: number;
  paused: boolean;
  items: { path: string; status: 'failed' | 'skipped'; error: string }[];
  itemsTruncated: boolean;
  error?: string | null;
}

// ── B1 首字节竞速（SSE 流式）─────────────────────────────

export interface StreamEngines {
  kind: 'engines';
  pending: string[];
  skipped: { id: string; error: string }[];
  appliedPreset?: { id: string; label: string; caveat: string };
  effectiveQuery?: string;
}

export interface StreamPartial {
  kind: 'partial';
  engine: string;
  outcome: string;
  count: number;
  elapsedMs: number;
  totalMs: number;
  results: unknown[];
  waiting: string[];
}

export interface StreamFinal {
  kind: 'final';
  result: Record<string, unknown>;
  fromCache?: boolean;
}

export interface StreamError {
  kind: 'error';
  error: string;
}

export type StreamEvent = StreamEngines | StreamPartial | StreamFinal | StreamError;

/**
 * B1 —— 订阅流式搜索。哪家引擎先回哪家先画。
 *
 * 🔴 **用 fetch + ReadableStream 而不是 `EventSource`**：
 * `EventSource` 只能发 GET，而搜索请求体里有引擎列表、预设、时间范围
 * 这一堆参数，塞进 query string 会碰到 URL 长度限制，而且没法带
 * `AbortSignal`（用户改一个字就该取消上一次流，否则两次结果会互相覆盖）。
 *
 * 🔴 **必须自己处理粘包**：SSE 事件以 `\n\n` 分隔，但一次 `read()`
 * 可能拿到半个事件、也可能拿到三个。不缓冲的话症状是偶发的
 * "JSON 解析失败"，而且只在网络慢的时候出现 —— 最难查的那种。
 */
export async function streamSearch(
  req: {
    query: string;
    limit?: number;
    engines?: string[];
    lang?: string;
    timeRange?: string | null;
    preset?: string | null;
    intent?: boolean;
  },
  onEvent: (ev: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const { enginePort } = await import('./api');
  const port = enginePort();
  if (port == null) throw new Error('引擎还没就绪');

  const resp = await fetch(`http://127.0.0.1:${port}/api/web/search/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`${resp.status} ${resp.statusText}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let idx: number;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = chunk.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as StreamEvent);
      } catch {
        // 单个事件解析失败**不中断整条流** —— 后面的事件多半是好的，
        // 为一个坏包丢掉整次搜索是过度反应
      }
    }
  }
}

// ── B5 意图 ｜ B4 缓存 ｜ B7 独立性 ─────────────────────────

export interface IntentInfo {
  kind: string;
  confidence: number;
  hits: string[];
  engines: string[];
  preset: string | null;
  timeRange: string | null;
  why: string;
}

export interface IndependenceItem {
  url: string;
  title: string;
  sites: string[];
  siteCount: number;
  owners: string[];
  ownerCount: number;
  syndicated: number;
  verdict: string;
}

export interface FarmInfo {
  site: string;
  flags: string[];
  reasons: string[];
  interest: string[];
  interestLabels: string[];
  penalty: number;
  trusted: boolean;
}

// ── D 组 ────────────────────────────────────────────────

export interface NumberCheckItem {
  raw: string;
  value: number | null;
  unit: string;
  isPercent: boolean;
  status: 'ok' | 'mismatch' | 'unverified';
  sourceUrl: string;
  context: string;
  near: string[];
  note: string;
}

export interface NumberCheckResult {
  checks: NumberCheckItem[];
  total: number;
  ok: number;
  mismatch: number;
  unverified: number;
  verdict: string;
  note: string;
}

export interface TimelineConflictItem {
  aUrl: string;
  bUrl: string;
  aSite: string;
  bSite: string;
  aDate: string;
  bDate: string;
  gapDays: number;
  aContext: string;
  bContext: string;
}

export interface ControversyInfo {
  score: number;
  support: number;
  refute: number;
  neutral: number;
  independentSites: number;
  level: 'high' | 'medium' | 'low' | 'unknown';
  note: string;
  signals: string[];
}

// ── C 组 ────────────────────────────────────────────────

export interface ScholarCluster {
  id: number;
  label: string;
  keywords: string[];
  members: number[];
  size: number;
  representative: Record<string, unknown> | null;
  yearSpan: string;
}

export interface ReviewSection {
  heading: string;
  keywords: string[];
  paperCount: number;
  yearSpan: string;
  quotes: { text: string; ref: number; title: string; url: string; year: string; stance: string }[];
  disputes: { a: { text: string; ref: number }; b: { text: string; ref: number } }[];
}

export interface AlignTable {
  columns: string[];
  rows: {
    title: string;
    url: string;
    year: string;
    cells: Record<string, { value: string; unit: string; raw: string } | null>;
  }[];
  filled: number;
  coverage: number;
  note: string;
}

export interface PaperNode {
  id: string;
  title: string;
  year: string;
  url: string;
  venue: string;
  citations: number;
  coCited: number;
  citesSeeds: number;
  role: string;
}

export interface HarvestPlan {
  downloadable: { title: string; pdfUrl: string }[];
  skipped: { title: string; reason: string }[];
  count: number;
  estimatedMb: number;
  note: string;
  dryRun?: boolean;
}

export interface HarvestResult {
  downloaded: number;
  ingested: number;
  failed: number;
  elapsedMs: number;
  items: { title: string; status: string; reason: string; path: string }[];
  outDir: string;
  note: string;
}

// ── E 组 ────────────────────────────────────────────────

export interface MemoryRecall {
  topic: string;
  known: boolean;
  runCount: number;
  lastSeen: string;
  controversy: number | null;
  facts: {
    text: string;
    url: string;
    title: string;
    site: string;
    seen_count: number;
    first_seen: string;
  }[];
  note: string;
}

export interface RunDiff {
  added: { url: string; title: string; site: string }[];
  gone: { url: string; title: string; site: string }[];
  newNumbers: string[];
  controversyBefore: number | null;
  controversyAfter: number | null;
  controversyDelta: number | null;
  summary: string;
  note: string;
}

// ── A 组 ────────────────────────────────────────────────

export interface CompareResult {
  kind?: 'text' | 'image' | 'video' | 'binary';
  error?: string;
  verdict?: string;
  note?: string;
  similarity?: number;
  added?: number;
  removed?: number;
  distance?: number;
  aSize?: number[];
  bSize?: number[];
  hunks?: { tag: string; aStart: number; aLines: string[]; bStart: number; bLines: string[] }[];
  segments?: { aTimecode: string; bTimecode: string; frames: number }[];
}

export interface ChapterItem {
  index: number;
  startSec: number;
  endSec: number;
  durationSec: number;
  title: string;
  summary: string;
  titleSource: 'transcript' | 'hint' | 'timecode';
  sceneCount: number;
  keyframe: string;
  timecode: string;
}

export interface ChaptersResult {
  chapters: ChapterItem[];
  method: 'pause+scene' | 'pause' | 'scene' | 'equal' | 'none';
  count: number;
  note: string;
}

export interface TamperReport {
  path: string;
  suspicion: number;
  signals: string[];
  reasons: string[];
  width: number;
  height: number;
  format: string;
  note: string;
}

export interface WatchItem {
  id: string;
  query: string;
  label: string;
  engines: string[];
  intervalHours: number;
  lastRun: number;
  seenCount: number;
  autoIngest: boolean;
  enabled: boolean;
}

const post = <T,>(path: string, body: unknown, signal?: AbortSignal) =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body) }, signal);

export const labApi = {
  // B
  intent: (query: string, signal?: AbortSignal) =>
    post<IntentInfo>('/api/web/intent', { query }, signal),
  intents: () => call<{ intents: Record<string, unknown>[] }>('/api/web/intent/describe'),
  cacheStats: () =>
    call<{ entries: number; ttlSeconds: number; oldestAgeSeconds: number; capacity: number }>(
      '/api/web/cache',
    ),
  prewarm: (queries: string[]) => post<{ warmed: number }>('/api/web/prewarm', { queries }),
  independence: (results: unknown[], signal?: AbortSignal) =>
    post<{ items: IndependenceItem[]; loneSourceCount: number; note: string }>(
      '/api/web/independence',
      { results },
      signal,
    ),
  ingestResults: (urls: string[], tags?: string[]) =>
    post<{ ok: number; failed: number; note: string }>('/api/web/ingest-results', { urls, tags }),

  // D
  farm: (results: unknown[]) =>
    post<{ items: { farm: FarmInfo }[]; summary: { flagged: number; note: string } }>(
      '/api/web/farm',
      { results },
    ),
  timelineConflicts: (results: unknown[], texts: Record<string, string>) =>
    post<{ conflicts: TimelineConflictItem[]; verdict: string; note: string }>(
      '/api/web/timeline-conflicts',
      { results, texts },
    ),
  aiStyle: (results: unknown[], texts: Record<string, string>) =>
    post<{ analyzed: number; skipped: number; note: string }>('/api/web/ai-style', {
      results,
      texts,
    }),
  numbers: (briefing: unknown, texts: Record<string, string>) =>
    post<NumberCheckResult>('/api/web/numbers', { briefing, texts }),
  controversy: (verification: unknown) =>
    post<Record<string, unknown>>('/api/web/controversy', { verification }),

  // C
  cluster: (entries: unknown[], maxClusters = 8) =>
    post<{ clusters: ScholarCluster[]; outliers: number[]; note: string }>('/api/scholar/cluster', {
      entries,
      maxClusters,
    }),
  review: (entries: unknown[], topic = '') =>
    post<{ sections: ReviewSection[]; references: Record<string, unknown>[]; note: string }>(
      '/api/scholar/review',
      { entries, topic },
    ),
  table: (entries: unknown[], metrics?: string[]) =>
    post<AlignTable>('/api/scholar/table', { entries, metrics: metrics ?? null, format: 'json' }),
  citations: (entries: unknown[], direction: 'back' | 'forward' | 'both' = 'both') =>
    post<{
      foundations: PaperNode[];
      followups: PaperNode[];
      resolved: number;
      requested: number;
      note: string;
    }>('/api/scholar/citations', { entries, direction }),
  mergePreprints: (entries: unknown[]) =>
    post<{ entries: Record<string, unknown>[]; merged: number; note: string }>(
      '/api/scholar/merge-preprints',
      { entries },
    ),
  harvestPlan: (entries: unknown[], limit = 20) =>
    post<HarvestPlan>('/api/scholar/harvest', { entries, apply: false, limit }),
  harvest: (entries: unknown[], limit = 20, tags?: string[]) =>
    post<HarvestResult>('/api/scholar/harvest', { entries, apply: true, limit, tags, ingest: true }),

  // C7 订阅
  watches: () => call<{ watches: WatchItem[]; due: string[] }>('/api/watches'),
  addWatch: (req: {
    query: string;
    label?: string;
    engines?: string[];
    intervalHours?: number;
    autoIngest?: boolean;
  }) => post<WatchItem>('/api/watches', req),
  removeWatch: (id: string) =>
    call<{ deleted: boolean }>(`/api/watches/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  runWatch: (id: string) =>
    post<{ freshCount: number; note: string }>(`/api/watches/${encodeURIComponent(id)}/run`, {}),

  // E
  remember: (topic: string, briefing: unknown, clusters: unknown[], controversy?: number) =>
    post<{ new: number; repeated: number; note: string }>('/api/memory/remember', {
      topic,
      briefing,
      clusters,
      controversy: controversy ?? null,
    }),
  recall: (topic: string, limit = 30) =>
    call<MemoryRecall>(`/api/memory/recall?topic=${encodeURIComponent(topic)}&limit=${limit}`),
  memoryStats: () => call<{ facts: number; sources: number; topics: number }>('/api/memory/stats'),
  forget: (topic: string) =>
    call<{ deleted: number }>(`/api/memory/topic?topic=${encodeURIComponent(topic)}`, {
      method: 'DELETE',
    }),
  diffRuns: (oldRun: unknown, newRun: unknown) =>
    post<RunDiff>('/api/memory/diff', { old: oldRun, new: newRun }),
  saveToLibrary: (payload: unknown, title?: string, template = 'points') =>
    post<{ itemId: string; path: string; note: string }>('/api/research/save-to-library', {
      payload,
      title,
      template,
    }),

  // A
  compareFiles: (a: string, b: string) => post<CompareResult>('/api/compare/files', { a, b }),
  compareVideos: (aItemId: string, bItemId: string) =>
    post<CompareResult>('/api/compare/videos', { aItemId, bItemId }),
  chapters: (itemId: string, maxChapters = 30) =>
    call<ChaptersResult>(
      `/api/items/${encodeURIComponent(itemId)}/chapters?maxChapters=${maxChapters}`,
    ),
  tamper: (paths: string[], earliestSeen = '') =>
    post<TamperReport | { reports: TamperReport[]; flagged: number; note: string }>(
      '/api/images/tamper',
      { paths, earliestSeen },
    ),

  // E9 近重复清理
  dupSweep: (limit = 200) => call<DupSweep>(`/api/duplicates/sweep?limit=${limit}`),
  deleteItems: (ids: string[], confirm: boolean) =>
    post<DeleteResult>('/api/items/delete', { ids, confirm }),

  // B2 三家反查并发
  reverseMulti: (path: string, providers: string[] = ['bing', 'yandex', 'lens'], limit = 20) =>
    post<ReverseMulti>('/api/web/reverse-image/multi', { path, providers, limit }),

  // A3 一图四路
  imageLanes: (path: string, limit = 12, web = true) =>
    post<ImageLanes>('/api/image/lanes', { path, limit, web }),

  // E17 端到端加密同步 ｜ 6.5 离线队列
  syncStatus: () => call<SyncStatus>('/api/sync/status'),
  syncPair: (passphrase: string, salt?: string) =>
    post<{ salt: string; fingerprint: string; deviceId: string; note: string }>(
      '/api/sync/pair',
      { passphrase, salt },
    ),
  syncPull: (limit = 200) =>
    post<{ envelope: unknown; opIds: string[]; count: number; deviceId: string; note: string }>(
      '/api/sync/pull',
      { limit },
    ),
  syncPurge: () => post<{ purgedSent: number; purgedTombstones: number }>('/api/sync/purge', {}),

  // C12 整页截图归档 ｜ C13 登录态抓取
  archiveShot: (req: {
    url: string;
    cookies?: { name: string; value: string; domain?: string; path?: string }[];
    ingest?: boolean;
    tags?: string[];
  }) => post<ArchiveShot>('/api/web/archive-shot', req),

  // E15 模型热插拔
  modelStatus: () => call<ModelStatus>('/api/models/status'),
  reloadModels: (preferGpu?: boolean) =>
    post<{ ok: boolean; changed: string[]; status: ModelStatus; note: string }>(
      '/api/models/reload',
      { preferGpu },
    ),

  // A2 秒开预览
  previewMedia: (path: string) => post<MediaPreview>('/api/preview/media', { path }),

  // F2 批量驾驶舱
  ingestJob: (jobId: string) => call<IngestJob>(`/api/ingest/${encodeURIComponent(jobId)}`),
  controlIngest: (jobId: string, action: 'pause' | 'resume' | 'cancel') =>
    post<{ ok: boolean; paused?: boolean; cancelled?: boolean; note: string; status?: string }>(
      `/api/ingest/${encodeURIComponent(jobId)}/${action}`,
      {},
    ),

  // G
  budgets: () =>
    call<{
      budgets: { id: string; label: string; target: string; how: string; hasBench: boolean }[];
      observed: Record<string, unknown>;
      note: string;
    }>('/api/metrics/budgets'),
};
