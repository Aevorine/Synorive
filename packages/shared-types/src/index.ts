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
  /**
   * D1 结果多样性：**同一个目录 / 同一个域名**下的第 2、3 条依次降权。
   * 0 = 允许一个目录铺满整屏（已经知道东西在哪个文件夹时要的就是这个）。
   *
   * ⚠️ 不是"同一份资料的第几段" —— 召回和融合都已按 item 去重，
   *    同一份资料最多出现一次，按段降权是进不去的死分支。
   */
  diversity: number;
  /** D1 长度惩罚：很短的片段（目录行、页眉、单句标题）降权 */
  lengthPenalty: number;
}

export const DEFAULT_WEIGHTS: RankingWeights = {
  semantic: 1.0,
  keyword: 1.0,
  recency: 0.3,
  sourceTrust: 0.2,
  popularity: 0.2,
  titleBoost: 0.5,
  diversity: 0.5,
  lengthPenalty: 0.3,
};

/** 排序预设：一键切换整套权重。'deep' = 深读一份（关掉多样性） */
export type RankingPreset =
  | 'balanced'
  | 'precise'
  | 'semantic'
  | 'recent'
  | 'deep'
  | 'custom';

/**
 * D1 用户自己存的权重组合。
 * 存在设置里，跟人走 —— 调好一套"找代码用的权重"却只活到关窗口为止，
 * 等于每次都要重调，那这些滑块就白给了。
 */
export interface SavedRankingPreset {
  id: string;
  name: string;
  weights: RankingWeights;
}

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
  /** D10 `type:pdf` 的扩展名过滤，带点（`.pdf`）。OR 关系 */
  extensions?: string[];
  /**
   * L3-plus `section:方法` —— 只搜论文的这些章节。
   *
   * 🔴 **子串匹配不是精确相等**：真实章节标题长这样
   * `3.2 Experimental Method`、`4. Results and Discussion`，
   * 精确相等的话一条都命中不了，而且是**静默**命中不了
   * （返回空结果，看起来就像"库里没有相关内容"）。
   */
  sections?: string[];
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
  /** L3：PDF 论文章节（Abstract/Method/Results…），非论文内容不存在这个字段 */
  section?: string;
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

/**
 * A3 Ask 模式的一段逐字摘录
 *
 * 🔴 `text` **一定能在原文里逐字找到**。界面不许对它做任何"顺一下语句"的
 *    处理（去空格、补标点、截断加省略号都算），否则"点回原文"会定位不到，
 *    而定位不到的引用等于没有引用。
 */
export interface AskPassage {
  text: string;
  itemId: string;
  title: string;
  locator: string;
  /** 0~1，这一段命中了问题里多少实词。仅用于排序和弱化显示 */
  coverage: number;
  /** 命中的词，界面拿它做高亮 —— 让用户看见"它凭什么被选中" */
  matched: string[];
  /** 永远是 'extract'。留字段是为了将来真接了生成式时能区分，而不是现在就有 */
  kind: 'extract';
  page?: number;
  startSec?: number;
}

/** A3 一次问答的完整结果。**答不上时也返回它**，靠 enough 区分 */
export interface AskAnswer {
  question: string;
  passages: AskPassage[];
  sources: { itemId: string; title: string; locator: string }[];
  /** false = 依据不足。此时 why/suggest 一定有值，界面必须显示它们 */
  enough: boolean;
  coverage: number;
  kind: 'extract';
  /** 为什么答不上（enough=false 时） */
  why?: string;
  /** 具体到可以直接照做的建议，不是「换个说法试试」这种废话 */
  suggest?: string[];
}

export interface AskResponse {
  ask: AskAnswer;
  hits: SearchHit[];
  elapsedMs: number;
  weakMatch: boolean;
  recovery?: unknown;
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

/**
 * A3 主输入模式 —— 同一个输入框，两种意图
 *   ask  = 问一句话，要的是**带出处的答案**
 *   find = 找东西，要的是**文件列表**
 * 两者共用一套检索管线，差别只在最后一层怎么组织输出。
 */
export type InputMode = 'ask' | 'find';

/** A2 启动落地页 */
export type StartPage = 'today' | 'search';

/**
 * 多库支持：一个独立的索引库 —— 自己的 `.db`（items/chunks/FTS/向量索引都在里面）、
 * 自己的监听目录、自己的隐私策略、自己的排序预设。库之间数据完全隔离。
 *
 * **模型文件不属于这里** —— `modelDir` 全局共享一份（体积以 GB 计，
 * 没有谁会想每个库各拷贝一份），只有 `dataDir` 按库区分。
 *
 * 引擎进程是"一个进程绑死一个 dataDir"的架构（`Runtime` 构造时一次性绑定，
 * 生命周期内不可更换），所以"切库"的实现是：把 `AppSettings.dataDir` 换成
 * 这条记录的 `dataDir`，触发已有的"dataDir 变了就重启引擎"逻辑——
 * 不是引擎同时管理多个库，是**换一个库就重启一次引擎**。
 */
export interface LibraryEntry {
  id: string;
  name: string;
  /** 这个库的 items/chunks/FTS/向量索引所在目录，即引擎的 `dataDir` */
  dataDir: string;
  createdAt: string;
}

export interface AppSettings {
  /**
   * paper = 纸感（B4 第三档，长时间阅读）。
   * ⚠️ 加了这一档以后，所有 `theme === 'dark' ? A : B` 的二元判断都变成了
   *    **静默错误**：paper 会走进 else 分支拿到浅色那套。全仓判断一律要写成
   *    显式三分支，不许用二元三目。
   */
  theme: 'light' | 'dark' | 'paper' | 'system';
  fontScheme: FontScheme;
  eyeComfort: EyeComfortLevel;
  eyeReminderMinutes: number;
  density: Density;
  /**
   * A2 打开软件先落在哪一页。默认 today ——
   * 「今日」页存在的全部理由就是"打开就有东西看"，
   * 而它如果不是启动页，用户永远不会主动点进去看。
   */
  startPage: StartPage;
  /** A3 主输入框默认意图。默认 ask */
  defaultInputMode: InputMode;
  /**
   * B7 钉在侧栏顶部的入口 id（PageId 或 `project:<id>` / `watch:<id>`）。
   * 顺序即显示顺序。
   */
  pinnedNav: string[];
  /** D1 用户存下来的排序权重组合。空数组 = 还没存过 */
  savedPresets: SavedRankingPreset[];
  /**
   * A5 当前工作在哪个项目下。null = 不归属任何项目（默认）。
   *
   * 存在设置里而不是内存里：**项目是跨会话的工作上下文**，
   * 关掉窗口第二天打开还该在同一个项目里 ——
   * 每次启动都退回"没有项目"，等于逼用户每天早上重新选一次。
   */
  activeProjectId: string | null;
  /**
   * C3 把高开销渲染（高亮分词、差异比对、图谱布局）放进 Worker。
   * 默认开；留开关是因为**极老的机器上创建 Worker 本身有开销**，
   * 而且 Worker 挂掉时要能一键退回主线程路径排查。
   */
  offloadHeavyWork: boolean;
  /** M5 并发度 1~16 */
  concurrency: number;
  /** 托盘常驻 */
  runInTray: boolean;
  launchAtLogin: boolean;
  /**
   * D7 精排：搜完之后再用交叉编码器把前 12 条重排一遍。
   * 准一点（实测 Top1 +3/100），但要多等约 0.8 秒 —— 它是**瀑布第三级**，
   * 不挡首屏，结果先出来、晚一点自己重排。默认关：模型 279MB，多数人不需要。
   */
  rerankResults: boolean;
  /** E4 剪贴板哨兵：盯着剪贴板，攒在内存里等你决定要不要存 */
  clipboardSentinel: boolean;
  /**
   * 纯链接自动归档。
   * 只对「整段内容就是一个 http(s) 链接」生效 —— 链接里不会夹带密码或验证码，
   * 所以它是唯一一类可以自动落盘还不出事的剪贴板内容。默认关。
   */
  clipboardAutoArchiveLinks: boolean;
  /**
   * N7 随手研究浮窗：复制一段文字后，在屏幕角落浮出三条最相关的。
   *
   * 默认关。**每次复制都弹窗是一种非常具体的敌意** —— 用户得先明确说要它。
   * 浮窗不可聚焦（`focusable: false`），永远不会把键盘从你手里抢走 ——
   * 你复制东西十有八九是为了马上粘贴，抢焦点等于毁掉你正在做的事。
   */
  clipboardPeek: boolean;
  /**
   * 浮窗要不要也查网上。默认关，**和上面那个是两个开关**：
   * 只查本地是几十毫秒、不出网、不花钱；联网要几秒还会把
   * "我在查什么"发出去。和隐私围栏里那条分开原则一致。
   */
  clipboardPeekWeb: boolean;
  /**
   * 多库支持：库注册表 + 当前激活的库。
   *
   * 这两个字段是"全局"的——它们描述的是库本身这份名单，不随切库变化
   * （切库变的是 `dataDir` 指向哪一条）。空数组只会出现在老用户刚升级上来
   * 那一刻，桌面端 `loadSettings()` 会立刻补一条「默认库」，界面不会看到
   * 空列表的状态。
   *
   * 下面这些字段**每个库各自一份**（存在 `<该库 dataDir>/library-settings.json`，
   * 不在这份主设置文件里），随 `activeLibraryId` 切换自动换成对应库的值：
   * `watchedFolders` `savedPresets` `allowNetwork` `webLineupSize` `verifyLevel`
   * `webEndpoints` `webEngines` `trustProfile` `enableFaceClustering`
   * `enableAuthenticatedFetch` `enableImageDescription` `clipboardAutoArchiveLinks`
   * `sensitiveGuardEnabled` `activeProjectId`——它们在类型定义上仍然是
   * `AppSettings` 的平铺字段（不拆嵌套对象），只是**运行时的值来源**变成了
   * "当前激活的库"，桌面端 `settings.ts` 负责这层合并，其余读 `settings.xxx`
   * 的代码不用感知这个变化。
   */
  libraries: LibraryEntry[];
  activeLibraryId: string;
  /** 监听索引的目录 */
  watchedFolders: string[];
  /** 数据与模型位置。`dataDir` 就是当前激活库的 `LibraryEntry.dataDir` */
  dataDir: string;
  /** 模型目录全局共享，不随库切换——体积以 GB 计，没有谁想每个库拷一份 */
  modelDir: string;
  /** 云端配置 */
  cloud: CloudConfig;
  /** C5 人脸聚类 / C13 登录态抓取：隐私敏感，默认关 */
  enableFaceClustering: boolean;
  enableAuthenticatedFetch: boolean;
  /** C4 图片详细描述：调云端视觉模型给图片生成一段描述并入索引。
   *  依赖 cloud.enabled 且配好了 cloud.visionModel，默认关（会把图片发去云端） */
  enableImageDescription: boolean;
  /** 是否启用核显加速（DirectML） */
  enableGpuAcceleration: boolean;
  /**
   * 投喂目录时自动跳过看起来像密钥/凭据的文件（.env、id_rsa、
   * credentials.json……），不索引进搜索库。默认开——这类文件本身就是
   * 纯文本/JSON，能被正常解析写进索引甚至发去云端，用户投喂一个项目
   * 目录时几百个文件混在一起，肉眼很难逐个排查。
   */
  sensitiveGuardEnabled: boolean;
  /**
   * A16 安卓配对：打开后引擎从只听 127.0.0.1 改成监听 0.0.0.0，
   * 局域网里的手机才连得上。默认关——这会让同一局域网内的其他设备
   * 看得到这台机器在跑这个服务。开着的时候所有非本机请求都要带
   * `pairingToken`（`X-Synorive-Token` 头）才放行，本机（桌面端自己/
   * MCP/CLI）不受影响，永远直连 127.0.0.1。
   */
  lanPairingEnabled: boolean;
  /** 配对令牌，首次启动随机生成一次；界面上会显示出来给手机端手动输入 */
  pairingToken: string;

  // ── E12 隐私围栏 / 联网搜索（U9 · S1 · V5）───────────────
  /**
   * 联网搜索总闸。**和 `cloud.enabled` 是两个开关，绝不能合并** ——
   * 联网搜索发出去的是**查询词**（泄露"我在查什么"），
   * 云端推理发出去的是**你的资料原文**（泄露"我有什么"）。
   * 很多人愿意接受前者而绝不接受后者，合成一个开关就是逼他们二选一。
   * 关掉之后整个研究工作台停用，本地检索完全不受影响。
   */
  allowNetwork: boolean;
  /**
   * S1 每轮最多派几家引擎（按最近表现排班 + 一个探索位）。
   * 0 = 全部派出（默认，也是老行为）。设成 5 之后，
   * 一家最近老失败的引擎不会每轮都白等它一次。
   */
  webLineupSize: number;
  /**
   * V 组核查档位。
   * annotate 只标注不出网 ｜ counter 反向检索+溯源+撤稿（默认）｜
   * claim 再加断言级逐句核查（慢很多）
   */
  verifyLevel: 'annotate' | 'counter' | 'claim';
  /**
   * 引擎的非密钥类配置，比如自建 SearXNG 的地址。
   * **API Key 不存这里** —— 那些走 Electron safeStorage（DPAPI）加密存放，
   * 和云端 Key 同一条路，绝不落进 settings.json 明文。
   */
  webEndpoints: Record<string, string>;
  /** 启用哪几家引擎。空数组 = 用各家自带的默认开关 */
  webEngines: string[];
  /**
   * V5 可信度权重。这套权重里没有一个"客观正确"的值 ——
   * 查学术的人要官方文档压倒一切，查产品体验的人恰恰需要社区博客。
   * 默认值只是一个中位取舍，不是真理。
   */
  trustProfile?: TrustProfileConfig;

  // ── U 组 应用自更新 ──────────────────────────────────────
  /**
   * 启动后自动去 GitHub Releases 查一次有没有新版。
   *
   * **只查、不装** —— 查到了在设置页和状态栏挂一个角标，
   * 下载和安装永远要用户自己点。默认开：不查的话用户根本不知道有更新，
   * 而"自动装"会在用户正在干活时重启应用，那是另一种敌意。
   *
   * ⚠️ 它**不受 `allowNetwork` 管**：那个开关管的是"把我的查询词发出去"，
   * 这里发出去的只有一个"最新版本号是多少"的请求，不含任何用户内容。
   * 但反过来，关掉这个开关就是真的一个字节都不发。
   */
  autoCheckUpdate: boolean;
  /**
   * 用户按「以后别再提醒这个版本」跳过的版本号。
   * 只跳过这一个版本，再出更新的还会提示 —— 永久静音那种开关
   * 是让用户永远停在旧版的最快办法。
   */
  skippedUpdateVersion?: string;
}

/** V5 可信度模型的可调参数。字段全可选，缺的用引擎侧默认值 */
export interface TrustProfileConfig {
  /** 六档来源权重：official / academic / mainstream / community / unknown / low */
  tierWeights?: Record<string, number>;
  multiSourceBonus?: number;
  loneSourcePenalty?: number;
  farmPenalty?: number;
  aiPenalty?: number;
  staleDays?: number;
  stalePenalty?: number;
  /** 可信度在最终排序里占的比重，剩下的是相关性 */
  rankWeight?: number;
  /** 用户自定义的域名分级覆盖 {域名: 档位} */
  overrides?: Record<string, string>;
  /** 用户自己的屏蔽名单。被屏蔽的仍然进「已排除」抽屉，随时能看能放回 */
  blocklist?: string[];
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
