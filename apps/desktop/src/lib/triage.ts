/**
 * 秒级预判 —— N2
 * ============================================================
 * **要治的病**：你把一个东西丢进来，然后盯着一个转圈的图标，
 * 不知道它在干什么、要等多久、会不会做错。这种等待里最难受的
 * 不是"慢"，是"不知道"。
 *
 * 所以这一层做的事只有一件：**在 200 毫秒内告诉你三件事** ——
 * 这是什么、我打算怎么处理、大概几秒。然后你可以当场改主意。
 *
 * 🔴 **它必须是纯本地、纯同步的**。一旦这里发一个请求，
 * "秒级预判"自己就变成了要等的东西，整件事就没意义了。
 * 所以这里只做能靠正则和扩展名判出来的事，判不准就**说判不准**，
 * 绝不为了显得聪明去猜。
 */

export type InputKind =
  | 'url'
  | 'image'
  | 'video'
  | 'audio'
  | 'document'
  | 'code'
  | 'question'
  | 'keywords'
  | 'longtext'
  | 'doi'
  | 'unknown';

export interface Triage {
  kind: InputKind;
  /** 一句话：这是什么 */
  what: string;
  /** 一句话：我打算怎么办 */
  plan: string;
  /** 预计秒数区间。给区间不给单个数——单个数一定是错的，区间才诚实 */
  etaS: [number, number];
  /** 建议走哪条路。界面据此高亮默认按钮 */
  route: 'quick' | 'research' | 'scholar' | 'ingest' | 'reverse-image' | 'read-url' | 'unified';
  /** 可选的其它路线，让用户一键改主意 */
  alternatives: { route: Triage['route']; label: string }[];
  /** 判不准时说出来。**不猜** */
  uncertain?: string;
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|tiff?|heic|avif)$/i;
const VIDEO_EXT = /\.(mp4|mkv|mov|avi|webm|flv|wmv|m4v|ts)$/i;
const AUDIO_EXT = /\.(mp3|wav|flac|aac|m4a|ogg|opus)$/i;
const DOC_EXT = /\.(pdf|docx?|xlsx?|pptx?|epub|txt|md|rtf|csv|html?|json)$/i;
const CODE_EXT = /\.(py|ts|tsx|js|jsx|java|c|cc|cpp|h|hpp|cs|go|rs|rb|php|swift|kt|sql|sh|ps1|yml|yaml|toml)$/i;

const URL_RE = /^https?:\/\/\S+$/i;
const DOI_RE = /^(https?:\/\/(dx\.)?doi\.org\/)?10\.\d{4,9}\/\S+$/i;
/** 中英疑问句式。判「这是个问题」而不是「这是几个关键词」 */
const QUESTION_RE =
  /(吗[？?]?$|呢[？?]?$|[？?]$|^(如何|怎么|怎样|为什么|为啥|是什么|什么是|哪个|哪些|多少|能不能|可不可以|有没有)|^(how|what|why|when|where|which|who|can|does|is|are|should)\b)/i;

/** 一秒内能判完的、纯本地的输入分诊 */
export function triage(raw: string): Triage {
  const s = (raw || '').trim();
  if (!s) {
    return {
      kind: 'unknown',
      what: '还没有输入',
      plan: '把图片、视频、文件拖进来，或者直接打字',
      etaS: [0, 0],
      route: 'unified',
      alternatives: [],
    };
  }

  if (DOI_RE.test(s)) {
    return {
      kind: 'doi',
      what: '一个 DOI（论文的永久编号）',
      plan: '去五家学术源查这篇论文的元数据、被引数和 PDF，顺带查它有没有被撤稿',
      etaS: [3, 8],
      route: 'scholar',
      alternatives: [{ route: 'read-url', label: '直接读这个页面' }],
    };
  }

  if (URL_RE.test(s)) {
    return {
      kind: 'url',
      what: '一个网址',
      plan: '抓正文 + 判来源等级 + 顺藤摸瓜找它引用了谁、谁引用了它',
      etaS: [2, 6],
      route: 'read-url',
      alternatives: [
        { route: 'research', label: '以这篇为起点深挖这个话题' },
        { route: 'ingest', label: '存进我的库' },
      ],
    };
  }

  // 本机路径（拖进来的文件通常是这个形状）
  const looksPath = /^[a-zA-Z]:[\\/]|^[\\/]{1,2}/.test(s) || /[\\/]/.test(s);
  if (looksPath) {
    if (IMAGE_EXT.test(s)) {
      return {
        kind: 'image',
        what: '一张图片',
        plan: '三路同时跑：认出图里的字 ／ 到网上反查它的出处 ／ 在你自己的库里找相似图',
        etaS: [3, 10],
        route: 'reverse-image',
        alternatives: [{ route: 'ingest', label: '只存进库不联网' }],
      };
    }
    if (VIDEO_EXT.test(s)) {
      return {
        kind: 'video',
        what: '一段视频',
        plan: '先切镜头抽关键帧（很快，马上能按画面搜），语音转写在后台慢慢补',
        etaS: [5, 30],
        route: 'ingest',
        alternatives: [{ route: 'reverse-image', label: '拿关键帧去网上反查来源' }],
      };
    }
    if (AUDIO_EXT.test(s)) {
      return {
        kind: 'audio',
        what: '一段音频',
        plan: '语音转写后入库，转完就能按台词搜到具体是第几分几秒',
        etaS: [10, 60],
        route: 'ingest',
        alternatives: [],
      };
    }
    if (CODE_EXT.test(s)) {
      return {
        kind: 'code',
        what: '一个代码文件',
        plan: '走代码专用路径：摘要取文件头注释，关键词取真实定义的类名/函数名',
        etaS: [1, 3],
        route: 'ingest',
        alternatives: [],
      };
    }
    if (DOC_EXT.test(s)) {
      return {
        kind: 'document',
        what: '一个文档',
        plan: '解析 → 语义分块 → 向量化入库；论文类还会按 Abstract/Method/Results 分节',
        etaS: [2, 15],
        route: 'ingest',
        alternatives: [{ route: 'research', label: '读完顺便查一遍它讲的话对不对' }],
      };
    }
    return {
      kind: 'unknown',
      what: '一个文件，但扩展名不认识',
      plan: '按纯文本试着解析，解析不了会明确告诉你，不会静默失败',
      etaS: [1, 5],
      route: 'ingest',
      alternatives: [],
      uncertain: '认不出这个类型 —— 我不猜，直接按文本试，失败会说清楚为什么',
    };
  }

  if (s.length > 200) {
    return {
      kind: 'longtext',
      what: `一段长文（${s.length} 字）`,
      plan: '拆成可核查的断言逐条查证，同时在你自己的库里找相关资料并排对照',
      etaS: [8, 30],
      route: 'research',
      alternatives: [
        { route: 'ingest', label: '当成一条笔记存起来' },
        { route: 'unified', label: '只在本地库找相关的' },
      ],
    };
  }

  if (QUESTION_RE.test(s)) {
    return {
      kind: 'question',
      what: '一个问题',
      plan: '多引擎并发搜 → 读完第一轮自己想出该追问什么 → 再搜一轮 → 出带出处的简报',
      etaS: [8, 25],
      route: 'research',
      alternatives: [
        { route: 'quick', label: '只要快，先给我一屏结果' },
        { route: 'unified', label: '同时看我自己的资料怎么说' },
      ],
    };
  }

  return {
    kind: 'keywords',
    what: '几个关键词',
    plan: '多引擎并发搜，按来源等级和交叉印证排序，可疑的当场标出来',
    etaS: [2, 5],
    route: 'quick',
    alternatives: [
      { route: 'research', label: '深挖：多搜一轮再出简报' },
      { route: 'unified', label: '连我自己的库一起搜' },
      { route: 'scholar', label: '只查学术文献' },
    ],
  };
}

/** 从 DataTransfer 里拿到"用户到底丢了什么"。图片/文件/纯文本/网址各一路 */
export function triageDrop(dt: DataTransfer): { text: string; files: File[] } {
  const files = Array.from(dt.files || []);
  const text = dt.getData('text/uri-list') || dt.getData('text/plain') || '';
  return { text: text.trim(), files };
}

const ROUTE_LABELS: Record<Triage['route'], string> = {
  quick: '快搜',
  research: '深挖',
  scholar: '文献',
  ingest: '存进我的库',
  'reverse-image': '图片反查',
  'read-url': '读这个链接',
  unified: '本地 + 网上一起搜',
};

export function routeLabel(r: Triage['route']): string {
  return ROUTE_LABELS[r] ?? r;
}
