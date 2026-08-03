/**
 * 联网搜索 / 研究简报 / 学术文献 / 云端生成 —— 引擎 HTTP 客户端
 * ============================================================
 * 类型定义故意**不放进 `@synorive/shared-types`**：那个包是
 * ContentItem/SearchHit 这些贯穿本地检索全链路（桌面端/CLI/MCP 都要用）
 * 的核心契约；而联网搜索这批 MCP 那边（`mcp/src/index.ts`）已经是
 * 本地内联类型的写法（`WebHit`/`EvidenceOut`），这里跟着同一个先例走，
 * 不強行拉进共享包多背一次 `tsc -p` 构建步骤。
 */

import { call } from './api';

export interface TrustInfo {
  tier: string;
  tierLabel: string;
  score: number;
  independentSources: number;
  reasons: string[];
  farmFlags?: string[];
  ageDays?: number;
  aiSuspect?: boolean;
  /** V3：具体命中了哪几条判据。只说"疑似 AI"用户没法判断要不要信 */
  aiFlags?: string[];
  hide?: boolean;
}

export interface WebResultItem {
  title: string;
  url: string;
  snippet?: string;
  site?: string;
  engine?: string;
  engines?: string[];
  engineCount?: number;
  siteCount?: number;
  published?: string;
  score: number;
  finalScore?: number;
  trust?: TrustInfo;
  alsoAt?: string[];
}

export interface EngineReplyInfo {
  id: string;
  outcome: 'ok' | 'empty' | 'challenged' | 'broken';
  count: number;
  elapsedMs: number;
  error?: string;
}

export interface WebSearchResponse {
  query: string;
  results: WebResultItem[];
  excluded: WebResultItem[];
  engines: EngineReplyInfo[];
  elapsedMs: number;
  fromCache: boolean;
  partial: boolean;
  trustSummary?: { shown: number; excluded: number; multiSourced: number; note: string };
}

export interface Evidence {
  text: string;
  url: string;
  title: string;
  site: string;
  trustScore: number;
  tier: string;
  published?: string;
}

export interface ConsensusTopic {
  topic: string;
  independentSites: number;
  evidence: Evidence[];
}

export interface DisputeTopic {
  topic: string;
  conflicts: { a: Evidence; b: Evidence }[];
}

export interface Briefing {
  query: string;
  kind: 'extract';
  consensus: ConsensusTopic[];
  disputes: DisputeTopic[];
  timeline: { published?: string; title?: string; url?: string; site?: string }[];
  numbers: { value: string; sentence: string; url?: string; title?: string; site?: string }[];
  openQuestions: string[];
  docCount: number;
  siteCount: number;
  /** V2 一致性矩阵：横轴来源、纵轴话题、格子是态度 */
  matrix?: ConsistencyMatrix;
}

/**
 * V2 一致性矩阵。
 *
 * `silent` 和 `mixed` 是**两回事**：前者是这个站压根没提这个话题，
 * 后者是提了但态度不明。合成一个值会让"三个站都没提"看起来像
 * "三个站都保持中立"。
 */
export interface ConsistencyMatrix {
  sites: string[];
  topics: string[];
  cells: {
    stance: 'positive' | 'negative' | 'mixed' | 'silent';
    text?: string;
    url?: string;
    trustScore?: number;
  }[][];
  disagreements?: number;
  note: string;
}

/** V6 反向检索找到的一条反面材料 / V1 核查里的一条表态 */
export interface StanceItem {
  url: string;
  title: string;
  site: string;
  snippet: string;
  stance: 'support' | 'refute' | 'neutral';
  trustScore: number;
  tier: string;
  published?: string;
}

/** V1 一条断言的核查结论。**给数不给判决** */
export interface ClaimVerdict {
  claim: string;
  sourceUrl: string;
  verdict: 'supported' | 'disputed' | 'weak' | 'unverified';
  supportCount: number;
  refuteCount: number;
  neutralCount: number;
  support: StanceItem[];
  refute: StanceItem[];
  note: string;
}

/** V4 溯源链路 */
export interface OriginTrace {
  earliest?: {
    ageDays: number;
    published?: string;
    title?: string;
    url?: string;
    site?: string;
    tier?: string;
  } | null;
  chain: {
    ageDays: number;
    published?: string;
    title?: string;
    url?: string;
    site?: string;
    tier?: string;
  }[];
  undated: number;
  /** burst = 转载爆发 ｜ weak-origin = 源头不可靠 ｜ unknown = 排不出来 ｜ ok */
  verdict: 'burst' | 'weak-origin' | 'unknown' | 'ok' | '';
  note: string;
}

export interface Verification {
  level: 'annotate' | 'counter' | 'claim';
  query?: string;
  counterEvidence?: StanceItem[];
  retracted?: Record<string, { doi: string; title?: string; year?: number; citedBy?: number; reason: string }>;
  origin?: OriginTrace;
  claims?: ClaimVerdict[];
  claimSummary?: {
    total: number;
    supported: number;
    disputed: number;
    weak: number;
    unverified: number;
  };
  note?: string;
}

/** S4 查询变体 */
export interface QueryVariant {
  text: string;
  lang: string;
  kind: 'original' | 'translated' | 'trimmed' | 'scoped' | 'term';
  why: string;
  weight: number;
}

/** S5 每一轮问了什么、为什么问 */
export interface RoundTrace {
  round: number;
  queries: QueryVariant[];
  newResults?: number;
  skipped?: string;
}

export interface ResearchResponse {
  query: string;
  results: WebResultItem[];
  excluded: WebResultItem[];
  trustSummary?: { note: string };
  briefing: Briefing;
  verification?: Verification;
  rounds?: RoundTrace[];
  fetched: number;
  fetchFailed: number;
  engines: EngineReplyInfo[];
  elapsedMs: number;
}

export interface ScholarPaper {
  title: string;
  url: string;
  snippet?: string;
  sources: string[];
  sourceCount: number;
  published?: string;
  meta?: {
    doi?: string;
    authors?: string[];
    year?: string;
    venue?: string;
    citations?: number;
    pdf?: string;
    openAccess?: boolean;
  };
}

export interface ScholarResponse {
  query: string;
  papers: ScholarPaper[];
  sources: EngineReplyInfo[];
  totalBeforeMerge: number;
  mergedCount: number;
  elapsedMs: number;
}

export interface EngineDescriptor {
  id: string;
  label: string;
  group: 'web' | 'scholar';
  kind: 'api' | 'html';
  needsKey: boolean;
  needsBrowser: boolean;
  defaultOn: boolean;
  note: string;
}

/**
 * S1 引擎健康仪表盘的一行。
 *
 * `verdict` 是一句人话 —— 用户不该需要理解「0.73」是什么意思
 * 才知道这家今天能不能用。
 */
export interface EngineHealthRow {
  id: string;
  label: string;
  kind: 'api' | 'html';
  group: 'web' | 'scholar';
  needsKey: boolean;
  needsBrowser: boolean;
  note: string;
  score: number;
  verdict: string;
  samples: number;
  okRate?: number | null;
  avgMs?: number | null;
  lastTried?: number | null;
  total?: number;
  totalOk?: number;
  recent?: string[];
}

export interface EnginesResponse {
  engines: EngineDescriptor[];
  health: {
    enabled: string[];
    lineupSize?: number;
    breaker: Record<string, { fails: number; openFor: number }>;
    rendererAvailable: boolean;
    table?: EngineHealthRow[];
  };
}

/** S8 定向源预设 */
export interface SourcePreset {
  id: string;
  label: string;
  sites: string[];
  why: string;
  /** 开了它就搜不到别的东西 —— 这一点必须让用户看见 */
  caveat: string;
  preferEngines: string[];
}

/** P4 研究项目 */
export interface ResearchProject {
  id: string;
  title: string;
  query: string;
  created_at: string;
  updated_at: string;
  status: 'open' | 'done' | 'archived';
  notes?: string | null;
  settings: Record<string, unknown>;
  runCount: number;
  sourceCount: number;
}

export interface ResearchRun {
  id: string;
  project_id: string;
  query: string;
  mode: 'quick' | 'deep' | 'scholar';
  created_at: string;
  elapsed_ms?: number;
  payload?: ResearchResponse;
}

export interface ProjectSource {
  project_id: string;
  url: string;
  title?: string;
  site?: string;
  tier?: string;
  trust_score?: number;
  first_seen: string;
  pinned: number;
  note?: string | null;
}

export interface ResumeContext {
  project: ResearchProject;
  lastBriefing?: Briefing | null;
  lastVerification?: Verification | null;
  pinnedSources: ProjectSource[];
  /** 已经搜过的词 —— 续做时最不该做的就是把它们再搜一遍 */
  askedQueries: string[];
  sourceCount: number;
}

/** P5 本地 × 网上并排 */
export interface UnifiedResponse {
  query: string;
  local: { results?: unknown[]; answer?: unknown; error?: string };
  web: { results?: WebResultItem[]; excluded?: WebResultItem[]; error?: string; unavailable?: string };
  conflicts: {
    local: { itemId?: string; title?: string; text: string };
    web: { url?: string; site?: string; title?: string; text: string };
  }[];
  note: string;
}

/** N4 顺藤摸瓜：它引用了谁 + 谁在讨论它 */
export interface Trail {
  url: string;
  outlinks: { url: string; site: string; text: string; tier: string; tierLabel: string }[];
  /** 按来源等级分组的计数 —— 「引用了 3 个官方文档」和「引用了 12 个不认识的站」一眼可辨 */
  byTier: Record<string, number>;
  backlinks: { url: string; title?: string; site?: string; snippet?: string }[];
  note: string;
}

export interface ReadUrlResponse {
  url: string;
  finalUrl: string;
  title: string;
  site: string;
  author?: string | null;
  published?: string | null;
  lang?: string | null;
  text: string;
  truncated: boolean;
  chars: number;
  warnings: string[];
  trust: { tier: string; tierLabel: string };
  trail?: Trail;
}

export interface ExportResult {
  filename: string;
  mime: string;
  encoding: 'utf-8' | 'base64';
  content: string;
}

export interface GeneratedBriefing {
  text: string;
  citations: { n: number; url: string; title: string; site: string; used: boolean }[];
  model?: string;
  kind: 'generated';
  warning?: string;
}

export const webApi = {
  engines: (signal?: AbortSignal) => call<EnginesResponse>('/api/web/engines', undefined, signal),

  search: (
    req: {
      query: string;
      limit?: number;
      engines?: string[];
      lang?: string;
      timeRange?: string;
      /** S8 只搜这一组权威站 */
      preset?: string | null;
      /** S4 自动扩写。快搜默认关——它要多花一个维基往返，而快搜要的就是快 */
      expand?: boolean;
    },
    signal?: AbortSignal,
  ) => call<WebSearchResponse>('/api/web/search', { method: 'POST', body: JSON.stringify(req) }, signal),

  research: (
    req: {
      query: string;
      fetch?: number;
      limit?: number;
      lang?: string;
      /** S5 挖几轮。1 = 搜一次就出简报；2 = 读完第一轮再自动追问一轮 */
      rounds?: number;
      expand?: boolean;
      preset?: string | null;
      /** V 组档位。不传就用设置里的默认 */
      verifyLevel?: 'annotate' | 'counter' | 'claim';
    },
    signal?: AbortSignal,
  ) =>
    call<ResearchResponse>('/api/web/research', { method: 'POST', body: JSON.stringify(req) }, signal),

  /**
   * N4 读一个链接，并可选「顺藤摸瓜」：它引用了谁 + 谁在讨论它。
   *
   * 出链按来源等级分组是关键 —— 一篇文章挂 40 条外链是常态，
   * 其中 35 条是导航和分享按钮，平铺出来看不出重点。
   * 而「一条站外链接都没有」本身就是最有力的判据之一。
   */
  read: (
    req: { url: string; maxChars?: number; trail?: boolean; backlinks?: boolean },
    signal?: AbortSignal,
  ) => call<ReadUrlResponse>('/api/web/read', { method: 'POST', body: JSON.stringify(req) }, signal),

  /** S8 定向源预设清单 */
  presets: (signal?: AbortSignal) =>
    call<{ presets: SourcePreset[] }>('/api/web/presets', undefined, signal),

  /**
   * V 组单独入口：不做完整深挖，只核查一个说法。
   * 深挖要十几秒，这条两三秒就有结果 —— 「我看到一句话，想知道有没有人反驳过」
   * 是个高频且独立的需求，不该逼用户跑一整轮深挖。
   */
  verify: (
    req: { query: string; claims?: string[]; engines?: string[]; level?: string; dois?: string[] },
    signal?: AbortSignal,
  ) => call<Verification>('/api/web/verify', { method: 'POST', body: JSON.stringify(req) }, signal),

  /** P5 本地库 × 网上，一次查完并排 */
  unified: (
    req: { query: string; limit?: number; local?: boolean; web?: boolean; preset?: string | null },
    signal?: AbortSignal,
  ) =>
    call<UnifiedResponse>('/api/unified/search', { method: 'POST', body: JSON.stringify(req) }, signal),

  scholar: (req: { query: string; sources?: string[]; limit?: number }, signal?: AbortSignal) =>
    call<ScholarResponse>('/api/web/scholar', { method: 'POST', body: JSON.stringify(req) }, signal),

  synthesize: (req: { query: string; briefing: Briefing }, signal?: AbortSignal) =>
    call<GeneratedBriefing>('/api/cloud/synthesize', { method: 'POST', body: JSON.stringify(req) }, signal),

  cloudStatus: () => call<{ provider: string; configured: boolean }>('/api/cloud/status'),
};

/**
 * P4 研究项目：关掉窗口再打开能接着挖。
 *
 * **存不存由界面显式决定**，不是每次搜索都自动存 —— 自动存会让项目里
 * 堆满随手搜的东西，真正要接着挖的那次反而找不到。
 */
export const projectApi = {
  list: (status?: string) =>
    call<{ projects: ResearchProject[] }>(
      `/api/research/projects${status ? `?status=${encodeURIComponent(status)}` : ''}`,
    ),

  create: (req: { query: string; title?: string; settings?: Record<string, unknown> }) =>
    call<ResearchProject>('/api/research/projects', {
      method: 'POST',
      body: JSON.stringify(req),
    }),

  get: (id: string) => call<ResearchProject>(`/api/research/projects/${id}`),

  update: (id: string, patch: Partial<Pick<ResearchProject, 'title' | 'status' | 'notes' | 'query'>>) =>
    call<ResearchProject>(`/api/research/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  remove: (id: string) =>
    call<{ ok: boolean }>(`/api/research/projects/${id}`, { method: 'DELETE' }),

  /** 续做：上次的简报 + 钉住的来源 + **已经搜过哪些词** */
  resume: (id: string) => call<ResumeContext>(`/api/research/projects/${id}/resume`),

  runs: (id: string, limit = 20) =>
    call<{ runs: ResearchRun[] }>(`/api/research/projects/${id}/runs?limit=${limit}`),

  run: (runId: string) => call<ResearchRun>(`/api/research/runs/${runId}`),

  saveRun: (id: string, req: { query: string; mode: string; payload: unknown }) =>
    call<{ ok: boolean; runId: string; project: ResearchProject }>(
      `/api/research/projects/${id}/runs`,
      { method: 'POST', body: JSON.stringify(req) },
    ),

  sources: (id: string, pinnedOnly = false) =>
    call<{ sources: ProjectSource[] }>(
      `/api/research/projects/${id}/sources?pinnedOnly=${pinnedOnly}`,
    ),

  pin: (id: string, req: { url: string; pinned: boolean; note?: string }) =>
    call<{ ok: boolean }>(`/api/research/projects/${id}/sources/pin`, {
      method: 'POST',
      body: JSON.stringify(req),
    }),

  /**
   * P3 导出。PDF 不走这里 —— 引擎出 HTML，桌面端用 Chromium 的
   * printToPDF 打印，那边的排版和中文字体都是现成的。
   */
  export: (req: {
    payload?: unknown;
    runId?: string;
    projectId?: string;
    format: 'markdown' | 'html' | 'json' | 'docx';
    title?: string;
    includeExcluded?: boolean;
  }) =>
    call<ExportResult>('/api/research/export', { method: 'POST', body: JSON.stringify(req) }),
};
