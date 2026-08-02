/**
 * Synorive 共享类型 —— 引擎 / 桌面端 / 安卓端 / MCP 四方通信契约
 * ============================================================
 * 这里是 API 形状的唯一真相源。Python 端有对应的 pydantic 模型
 * （engine/synorive/api/schemas.py），两边字段名必须一致。
 * 改这里就要同步改那里，CI 有一致性检查脚本。
 */

// ──────────────────────────────────────────────────────────────
// 一、内容项：库里的一条记录
// ──────────────────────────────────────────────────────────────

/** 模态：一条记录的主类型 */
export type Modality = 'text' | 'image' | 'video' | 'audio' | 'link' | 'message';

/** 来源：这条记录是怎么进来的 */
export type SourceKind =
  | 'file'          // 本机文件（监听目录或手动投喂）
  | 'link'          // 网页链接
  | 'clipboard'     // 剪贴板哨兵 E4
  | 'chat-export'   // 手动导出的聊天记录
  | 'mail'          // IMAP 只读
  | 'mobile'        // 安卓端上传
  | 'api';          // HTTP API / MCP 投喂

/** 处理状态 */
export type ItemStatus =
  | 'queued'      // 排队中
  | 'analyzing'   // 分析中
  | 'ready'       // 可检索
  | 'partial'     // 部分完成（比如向量好了但 OCR 失败）
  | 'failed'      // 失败
  | 'skipped';    // 被隐私围栏或规则跳过

/** 库里的一条内容 */
export interface ContentItem {
  id: string;
  /** 内容指纹（SHA-256 前 16 字节），用于去重与断点续跑 */
  fingerprint: string;
  modality: Modality;
  source: SourceKind;
  status: ItemStatus;

  title: string;
  /** 绝对路径 / URL / 消息定位符 */
  locator: string;
  /** 供列表展示的摘要（可能来自 C9 自动摘要） */
  snippet?: string;
  /** MIME 类型 */
  mime?: string;
  /** 字节数；目录或链接为 null */
  sizeBytes?: number | null;

  /** 内容自身的时间（拍摄时间 / 发布时间 / 消息时间），优先于文件时间 */
  contentTime?: string | null;
  createdAt: string;
  updatedAt: string;
  /** 最后一次被打开的时间，供 E11 热度学习 */
  lastOpenedAt?: string | null;
  openCount: number;

  tags: string[];
  /** 缩略图相对路径（相对于 data/thumbs） */
  thumbPath?: string | null;

  /** 各模态特有的元数据 */
  meta?: ItemMeta;
}

export interface ImageMeta {
  width: number;
  height: number;
  /** 感知哈希，用于 E9 近重复检测 */
  phash?: string;
  exifTime?: string | null;
  cameraModel?: string | null;
  gps?: { lat: number; lon: number } | null;
  /** C6：是否判定为截图 */
  isScreenshot?: boolean;
  dominantColors?: string[];
  /** C2 OCR 提取的文字 */
  ocrText?: string;
}

export interface VideoMeta {
  durationSec: number;
  width: number;
  height: number;
  fps?: number;
  /** C14 场景切分结果 */
  scenes?: VideoScene[];
  hasTranscript: boolean;
}

/** 视频场景 —— E2 片段级定位的基础 */
export interface VideoScene {
  index: number;
  startSec: number;
  endSec: number;
  /** 关键帧缩略图相对路径 */
  keyframePath?: string;
  /** 该场景内的语音转写 */
  transcript?: string;
}

export interface DocumentMeta {
  pageCount?: number;
  wordCount?: number;
  language?: string;
  author?: string | null;
  /** 分块数量 */
  chunkCount: number;
}

export interface LinkMeta {
  url: string;
  domain: string;
  fetchedAt: string;
  /** C11 正文存档相对路径 */
  archivePath?: string;
  /** C12 整页截图 / PDF 相对路径 */
  snapshotPath?: string;
  httpStatus?: number;
  /** 原站是否已失效 */
  isDead?: boolean;
}

export interface MessageMeta {
  platform: string;
  conversation: string;
  sender?: string;
  /** 同一会话内的顺序 */
  seq?: number;
}

export type ItemMeta =
  | ({ kind: 'image' } & ImageMeta)
  | ({ kind: 'video' } & VideoMeta)
  | ({ kind: 'document' } & DocumentMeta)
  | ({ kind: 'link' } & LinkMeta)
  | ({ kind: 'message' } & MessageMeta);

// ──────────────────────────────────────────────────────────────
// 二、检索：请求与响应
// ──────────────────────────────────────────────────────────────

/**
 * D4 多指标排序权重 —— 用户原话「功能有更多可选择的指标」
 * 每一项 0~1，界面上是滑块。总和不必为 1，内部会归一化。
 */
export interface RankingWeights {
  /** 语义相关度（向量相似） */
  semantic: number;
  /** 关键词精确匹配（BM25） */
  keyword: number;
  /** 时间新鲜度：越新越靠前 */
  recency: number;
  /** 来源权重：你标为"重要"的目录/域名加分 */
  sourceTrust: number;
  /** 热度：你打开得越多越靠前（E11） */
  popularity: number;
  /** 标题命中额外加权 */
  titleBoost: number;
}

export const DEFAULT_WEIGHTS: RankingWeights = {
  semantic: 1.0,
  keyword: 1.0,
  recency: 0.3,
  sourceTrust: 0.2,
  popularity: 0.2,
  titleBoost: 0.5,
};

/** 排序预设：一键切换整套权重 */
export type RankingPreset = 'balanced' | 'precise' | 'semantic' | 'recent' | 'custom';

/** D5 结构化筛选 */
export interface SearchFilters {
  modalities?: Modality[];
  sources?: SourceKind[];
  tags?: string[];
  /** ISO 日期，闭区间 */
  timeFrom?: string | null;
  timeTo?: string | null;
  sizeMinBytes?: number | null;
  sizeMaxBytes?: number | null;
  /** 只在这些目录/域名里搜 */
  scopes?: string[];
  /** 排除这些目录/域名 */
  excludeScopes?: string[];
  /** 语言过滤 */
  languages?: string[];
}

export interface SearchRequest {
  query: string;
  /** 以图搜图 / 以文件搜：传内容指纹或临时上传 id */
  byContentId?: string;
  filters?: SearchFilters;
  weights?: Partial<RankingWeights>;
  preset?: RankingPreset;
  limit?: number;
  offset?: number;
  /** D7 是否启用精排（+150~300ms） */
  rerank?: boolean;
  /** 是否要 D6 可解释信息（略微增加响应体积） */
  explain?: boolean;
  /** D8 是否同时生成秒答卡 */
  answer?: boolean;
}

/** 检索命中的具体位置 —— 支撑 E2 视频片段定位与文档定位 */
export interface HitLocation {
  /** 文档：第几块 / 第几页 */
  chunkIndex?: number;
  page?: number;
  /** 视频/音频：命中的时间点 */
  startSec?: number;
  endSec?: number;
  /** 图片：命中区域（OCR 文字框），归一化坐标 0~1 */
  bbox?: { x: number; y: number; w: number; h: number };
}

/** D6 可解释：为什么这条能匹配上 */
export interface MatchExplain {
  /** 各通道原始得分 */
  scores: {
    semantic?: number;
    keyword?: number;
    recency?: number;
    sourceTrust?: number;
    popularity?: number;
    rerank?: number;
  };
  /** 命中的关键词，用于高亮 */
  matchedTerms: string[];
  /** 命中来自哪个模态通道 */
  matchedVia: ('title' | 'body' | 'ocr' | 'transcript' | 'vector' | 'tag' | 'filename')[];
  /** 一句话人话解释 */
  reason: string;
}

export interface SearchHit {
  item: ContentItem;
  /** 融合后的最终分，0~1 */
  score: number;
  /** 命中片段，已带高亮标记 <em>…</em> */
  highlight?: string;
  location?: HitLocation;
  explain?: MatchExplain;
}

/**
 * D2 三级瀑布：同一次搜索会推送多个 stage 的结果。
 * 前端按 stage 平滑替换，不清屏、不跳动。
 */
export type SearchStage = 'instant' | 'keyword' | 'semantic' | 'reranked';

export interface SearchResponse {
  queryId: string;
  stage: SearchStage;
  /** 该 stage 是否为本次搜索的最后一波 */
  final: boolean;
  hits: SearchHit[];
  totalEstimate: number;
  /** 本 stage 服务端耗时（毫秒） */
  elapsedMs: number;
  /** D8 秒答卡 */
  answer?: InstantAnswer;
  /** D9 零结果补救建议 */
  suggestions?: SearchSuggestion[];
}

/** D8 秒答卡：从你自己的资料里答，每句都带出处 */
export interface InstantAnswer {
  text: string;
  citations: { itemId: string; title: string; location?: HitLocation }[];
  /** 生成来源：本地模型还是云端 */
  generatedBy: 'local' | 'cloud';
  confidence: number;
}

/** D9 零结果补救 */
export interface SearchSuggestion {
  kind: 'relaxed' | 'synonym' | 'cross-modal' | 'spelling' | 'scope';
  label: string;
  /** 点一下就执行的替代查询 */
  request: SearchRequest;
}

// ──────────────────────────────────────────────────────────────
// 三、摄取与分析流水线
// ──────────────────────────────────────────────────────────────

/** 分析阶段：DAG 的节点 */
export type AnalysisStage =
  | 'probe'        // 探测类型、算指纹、去重
  | 'extract'      // 抽正文 / 抽帧 / 抽音轨
  | 'ocr'          // C2
  | 'transcribe'   // C14 语音转文字
  | 'chunk'        // C8 语义分块
  | 'embed'        // 向量化
  | 'enrich'       // C9 摘要 / C10 实体 / C4 图片描述
  | 'thumbnail'    // 缩略图
  | 'index';       // 写库

export type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export interface IngestRequest {
  /** 文件路径 / URL / 目录 */
  targets: string[];
  source: SourceKind;
  /** 目录时是否递归 */
  recursive?: boolean;
  /** 优先级：你正在看的东西插队 */
  priority?: 'high' | 'normal' | 'low';
  tags?: string[];
  /** 是否允许把内容送到云端（受 E12 隐私围栏二次约束） */
  allowCloud?: boolean;
}

export interface IngestJob {
  jobId: string;
  createdAt: string;
  totalItems: number;
  doneItems: number;
  failedItems: number;
  skippedItems: number;
  status: 'queued' | 'running' | 'paused' | 'done' | 'failed' | 'cancelled';
  /** 各阶段实时吞吐，供 E14 驾驶舱展示 */
  stageStats: Record<AnalysisStage, { done: number; failed: number; itemsPerSec: number }>;
  etaSec?: number | null;
}

/** 单条内容的分析进度 */
export interface ItemProgress {
  itemId: string;
  jobId: string;
  stages: Record<AnalysisStage, StageStatus>;
  error?: string | null;
}

// ──────────────────────────────────────────────────────────────
// 四、引擎状态与并发控制
// ──────────────────────────────────────────────────────────────

export interface EngineStatus {
  version: string;
  uptimeSec: number;
  /** M5：当前并发度，可 1~16 调 */
  concurrency: number;
  cpuPercent: number;
  memoryMb: number;
  queueDepth: number;
  activeJobs: number;
  indexedItems: number;
  dbSizeMb: number;
  /** 当前推理执行器：cpu / dml（核显） */
  executionProvider: string;
  /** 云端是否已配置可用 */
  cloudReady: boolean;
  /** 已就绪的本地模型 */
  modelsReady: string[];
  modelsMissing: string[];
}

// ──────────────────────────────────────────────────────────────
// 五、E3 依赖医生
// ──────────────────────────────────────────────────────────────

export type DependencyKind = 'runtime' | 'binary' | 'python-pkg' | 'model' | 'font';

export interface DependencySpec {
  id: string;
  kind: DependencyKind;
  name: string;
  /** 为什么需要它 —— 界面上要说人话 */
  purpose: string;
  requiredBy: string[];
  /** 缺了它会失去哪些功能，而不是整个不能用 */
  degradesTo?: string;
  optional: boolean;
  sizeBytes?: number;
  version?: string;
}

export type DependencyState =
  | 'ok'
  | 'missing'
  | 'outdated'
  | 'downloading'
  | 'installing'
  | 'failed';

export interface DependencyStatus {
  spec: DependencySpec;
  state: DependencyState;
  installedVersion?: string | null;
  /** 下载进度 0~1 */
  progress?: number;
  downloadedBytes?: number;
  speedBps?: number;
  error?: string | null;
}

// ──────────────────────────────────────────────────────────────
// 六、E12 隐私围栏
// ──────────────────────────────────────────────────────────────

export type FenceAction = 'never-index' | 'index-no-cloud' | 'allow-all';

export interface PrivacyFence {
  id: string;
  /** 目录绝对路径 或 域名 或 glob */
  pattern: string;
  action: FenceAction;
  note?: string;
  createdAt: string;
}

/** H2 出站审计：什么内容在什么时候被发到了哪里 */
export interface OutboundRecord {
  id: string;
  at: string;
  provider: string;
  endpoint: string;
  itemId?: string;
  /** 只记摘要不记原文 */
  contentSummary: string;
  bytesSent: number;
  purpose: string;
}

// ──────────────────────────────────────────────────────────────
// 七、E7 搜索配方 / E8 订阅监控
// ──────────────────────────────────────────────────────────────

export interface SearchRecipe {
  id: string;
  name: string;
  request: SearchRequest;
  createdAt: string;
  lastRunAt?: string | null;
  /** cron 表达式，null 表示不定时 */
  schedule?: string | null;
  pinned: boolean;
}

export interface Watch {
  id: string;
  name: string;
  request: SearchRequest;
  createdAt: string;
  lastCheckedAt?: string | null;
  /** 已通知过的 itemId，避免重复打扰 */
  notifiedItemIds: string[];
  enabled: boolean;
}

// ──────────────────────────────────────────────────────────────
// 八、E6 实体知识图谱
// ──────────────────────────────────────────────────────────────

export type EntityKind = 'person' | 'place' | 'org' | 'product' | 'event' | 'concept' | 'time';

export interface Entity {
  id: string;
  kind: EntityKind;
  name: string;
  aliases: string[];
  mentionCount: number;
}

export interface EntityEdge {
  from: string;
  to: string;
  /** 共现次数 */
  weight: number;
  relation?: string;
}

export interface GraphSlice {
  entities: Entity[];
  edges: EntityEdge[];
}

// ──────────────────────────────────────────────────────────────
// 九、E5 语义时间轴
// ──────────────────────────────────────────────────────────────

export type TimelineBucket = 'hour' | 'day' | 'week' | 'month' | 'year';

export interface TimelinePoint {
  /** 桶起始时间 */
  at: string;
  count: number;
  /** 按模态拆分 */
  byModality: Partial<Record<Modality, number>>;
  /** 该桶内是否有当前搜索命中 */
  hitCount?: number;
}

// ──────────────────────────────────────────────────────────────
// 十、实时事件：引擎 → 界面的 WebSocket 推送
// ──────────────────────────────────────────────────────────────

export type EngineEvent =
  | { type: 'search.stage'; payload: SearchResponse }
  | { type: 'ingest.job'; payload: IngestJob }
  | { type: 'ingest.item'; payload: ItemProgress }
  | { type: 'engine.status'; payload: EngineStatus }
  | { type: 'dependency.status'; payload: DependencyStatus }
  | { type: 'watch.hit'; payload: { watchId: string; items: ContentItem[] } }
  | { type: 'toast'; payload: { level: 'info' | 'success' | 'warn' | 'error'; message: string } };

// ──────────────────────────────────────────────────────────────
// 十一、设置
// ──────────────────────────────────────────────────────────────

/** 结果列表信息密度 —— 不同任务需要不同密度（F11） */
export type Density = 'compact' | 'standard' | 'comfortable';

/** F1 字体方案：a=全宋体 b=正文宋体+标题思源（默认）c=全思源 */
export type FontScheme = 'a' | 'b' | 'c';

/** E16 护眼色温档位 */
export type EyeComfortLevel = 'off' | 'low' | 'medium' | 'high';

export interface AppSettings {
  theme: 'light' | 'dark' | 'system';
  fontScheme: FontScheme;
  eyeComfort: EyeComfortLevel;
  eyeReminderMinutes: number;
  density: Density;
  /** M5 并发度 1~16 */
  concurrency: number;
  /** 托盘常驻 */
  runInTray: boolean;
  launchAtLogin: boolean;
  /** E4 剪贴板哨兵：盯着剪贴板，攒在内存里等你决定要不要存 */
  clipboardSentinel: boolean;
  /**
   * 纯链接自动归档。
   * 只对「整段内容就是一个 http(s) 链接」生效 —— 链接里不会夹带密码或验证码，
   * 所以它是唯一一类可以自动落盘还不出事的剪贴板内容。默认关。
   */
  clipboardAutoArchiveLinks: boolean;
  /** 监听索引的目录 */
  watchedFolders: string[];
  /** 数据与模型位置 */
  dataDir: string;
  modelDir: string;
  /** 云端配置 */
  cloud: CloudConfig;
  /** C5 人脸聚类 / C13 登录态抓取：隐私敏感，默认关 */
  enableFaceClustering: boolean;
  enableAuthenticatedFetch: boolean;
  /** 是否启用核显加速（DirectML） */
  enableGpuAcceleration: boolean;
}

export interface CloudConfig {
  enabled: boolean;
  /** 'openai-compatible' 可接通义/智谱/DeepSeek/Kimi/本地 Ollama */
  provider: 'none' | 'openai-compatible' | 'anthropic';
  baseUrl?: string;
  /** 密钥不存这里，存系统凭据管理器（H3），这里只存一个引用键 */
  credentialKey?: string;
  chatModel?: string;
  visionModel?: string;
  /** 每日花费上限（元），超了自动停用 */
  dailyBudget?: number | null;
}

// ──────────────────────────────────────────────────────────────
// 十二、MCP 工具入参（G2）
// ──────────────────────────────────────────────────────────────

export interface McpToolMap {
  synorive_search: { input: SearchRequest; output: SearchResponse };
  synorive_ingest: { input: IngestRequest; output: IngestJob };
  synorive_analyze: { input: { target: string; deep?: boolean }; output: ContentItem };
  synorive_get_content: { input: { itemId: string; maxChars?: number }; output: { text: string; item: ContentItem } };
  synorive_similar: { input: { itemId: string; limit?: number }; output: SearchHit[] };
  synorive_timeline: { input: { bucket: TimelineBucket; from?: string; to?: string }; output: TimelinePoint[] };
  synorive_graph: { input: { entityId?: string; kind?: EntityKind; limit?: number }; output: GraphSlice };
  synorive_status: { input: Record<string, never>; output: EngineStatus };
}

export type McpToolName = keyof McpToolMap;
