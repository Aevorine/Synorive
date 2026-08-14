#!/usr/bin/env node
/**
 * Synorive MCP 服务器 —— 让 Claude Code 直接检索你的本地内容库
 * ============================================================
 * 装：claude mcp add synorive -- node <这个文件的绝对路径>
 *
 * 两条硬规矩：
 *   ① **stdout 只能走 MCP 协议**。任何一行 console.log 都会把协议搞坏，
 *      症状是 Claude Code 那边报"服务器无响应"而不是报错。
 *      所有日志一律走 stderr。
 *   ② **返回给 Claude 的是给人读的文本，不是 JSON 转储**。
 *      把 60 条结果的完整 JSON 塞回去只会烧掉上下文，
 *      而 Claude 真正需要的是"有哪些、在哪、大概讲什么"。
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { EngineClient } from './engine-client.js';

const engine = new EngineClient();

// ── 给 Claude 看的格式化 ────────────────────────────────────

interface Item {
  id: string;
  title: string;
  locator: string;
  modality: string;
  source: string;
  sizeBytes?: number | null;
  contentTime?: string | null;
  snippet?: string | null;
  tags?: string[];
}

interface Hit {
  item: Item;
  score: number;
  highlight?: string;
  location?: { page?: number; startSec?: number; endSec?: number };
  explain?: { reason?: string; matchedVia?: string[] };
}

function fmtTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function fmtSize(n?: number | null): string {
  if (!n) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)}KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)}MB`;
  return `${(n / 1024 ** 3).toFixed(2)}GB`;
}

function fmtHits(hits: Hit[], opts: { showId?: boolean } = {}): string {
  if (!hits.length) return '（没有结果）';
  return hits
    .map((h, i) => {
      const bits: string[] = [];
      if (h.location?.page != null) bits.push(`第${h.location.page}页`);
      if (h.location?.startSec != null) bits.push(fmtTime(h.location.startSec));
      const size = fmtSize(h.item.sizeBytes);
      if (size) bits.push(size);
      if (h.item.contentTime) bits.push(h.item.contentTime.slice(0, 10));

      const head =
        `${i + 1}. ${h.item.title}` +
        (bits.length ? `  [${bits.join(' · ')}]` : '') +
        `  相关度 ${h.score.toFixed(4)}`;

      const lines = [head, `   路径：${h.item.locator}`];
      if (opts.showId) lines.push(`   id：${h.item.id}`);
      // 高亮标记是给界面用的，给 Claude 看要去掉
      const snip = (h.highlight || h.item.snippet || '').replace(/<\/?em>/g, '').trim();
      if (snip) lines.push(`   ${snip.slice(0, 240)}`);
      if (h.explain?.reason) lines.push(`   命中原因：${h.explain.reason}`);
      return lines.join('\n');
    })
    .join('\n\n');
}

function text(s: string) {
  return { content: [{ type: 'text' as const, text: s }] };
}

function fail(e: unknown) {
  const msg = e instanceof Error ? e.message : String(e);
  return { content: [{ type: 'text' as const, text: `出错了：${msg}` }], isError: true };
}

// ── 服务器 ──────────────────────────────────────────────────

const server = new McpServer({
  name: 'synorive',
  version: '0.1.0',
});

// ① 检索
server.registerTool(
  'synorive_search',
  {
    title: '搜索本地内容库',
    description:
      '在用户的本地内容库里搜索（文档、代码、图片里的文字、网页存档、视频台词）。' +
      '支持中文语义检索——描述内容也能搜到，不用记文件名。' +
      '查询串里可以直接写筛选指令：type:pdf、date:>2026-01、date:最近7天、' +
      'size:>10mb、in:D:\\项目、tag:重要、src:link、-排除词、"精确短语"。' +
      '论文可以按章节过滤：section:方法 / section:结果 / section:结论（中英文都认，' +
      '按子串匹配所以「3.2 Experimental Method」这种带编号的标题也能命中）。' +
      '视频和音频的结果会带上时间点，可以直接跳到那一秒。',
    inputSchema: {
      query: z.string().describe('查询词，可含筛选指令'),
      limit: z.number().int().min(1).max(50).default(10).describe('返回多少条'),
      preset: z
        .enum(['balanced', 'precise', 'semantic', 'recent'])
        .default('balanced')
        .describe('balanced=均衡，precise=认准原词，semantic=理解意思，recent=偏新的'),
      explain: z.boolean().default(true).describe('是否附带命中原因'),
    },
  },
  async ({ query, limit, preset, explain }) => {
    try {
      const r = await engine.post<{
        hits: Hit[];
        totalEstimate: number;
        elapsedMs: number;
        parsedQuery?: { text: string; filters: string[]; unknown: string[] };
      }>('/api/search', { query, limit, preset, explain, stage: 'semantic' });

      const head: string[] = [`共 ${r.totalEstimate} 条，用时 ${r.elapsedMs}ms`];
      if (r.parsedQuery?.filters?.length) {
        head.push(`识别到的筛选：${r.parsedQuery.filters.join('；')}`);
        head.push(`实际检索词：「${r.parsedQuery.text || '（空，只按筛选列出）'}」`);
      }
      if (r.parsedQuery?.unknown?.length) {
        head.push(`没看懂的指令（已当普通词处理）：${r.parsedQuery.unknown.join(' ')}`);
      }
      return text(`${head.join('\n')}\n\n${fmtHits(r.hits, { showId: true })}`);
    } catch (e) {
      return fail(e);
    }
  },
);

// ② 投喂
server.registerTool(
  'synorive_ingest',
  {
    title: '把文件或目录加入索引',
    description:
      '把本机的文件或整个目录加入索引，之后就能搜到。支持文档、代码、图片、视频、音频、网页存档。' +
      '这是后台任务，会立刻返回一个 jobId，用 synorive_status 看进度。' +
      '重复投喂同一份内容会自动跳过（按内容指纹去重），不会重复占空间。',
    inputSchema: {
      targets: z.array(z.string()).min(1).describe('文件或目录的绝对路径'),
      recursive: z.boolean().default(true).describe('目录是否递归'),
      tags: z.array(z.string()).optional().describe('给这批内容打的标签'),
    },
  },
  async ({ targets, recursive, tags }) => {
    try {
      const r = await engine.post<{ jobId: string }>('/api/ingest', {
        targets,
        recursive,
        tags,
        source: 'api',
      });
      return text(
        `已开始索引 ${targets.length} 个目标，任务号 ${r.jobId}。\n` +
          `这是后台任务，用 synorive_status 看进度。\n` +
          `注：图片的文字识别和视频的语音转写是延后补跑的，会晚一些才能搜到。`,
      );
    } catch (e) {
      return fail(e);
    }
  },
);

// ③ 分析单个文件
server.registerTool(
  'synorive_analyze',
  {
    title: '分析一个文件并立刻返回结果',
    description:
      '分析单个文件并同步等结果返回（和 synorive_ingest 的区别是这个会等完成）。' +
      '适合"我想知道这个文件里有什么"的场景。大文件会比较慢。',
    inputSchema: {
      target: z.string().describe('文件的绝对路径'),
      maxChars: z.number().int().min(200).max(50000).default(4000).describe('返回多少字正文'),
    },
  },
  async ({ target, maxChars }) => {
    try {
      await engine.post('/api/ingest', { targets: [target], source: 'api' });
      // 轮询等它进库
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 500));
        const s = await engine.post<{ hits: Hit[] }>('/api/search', {
          query: '',
          filters: { scopes: [target] },
          limit: 1,
        });
        const hit = s.hits[0];
        if (hit) {
          const c = await engine.get<{ text: string; item: Item }>(
            `/api/items/${hit.item.id}/content?maxChars=${maxChars}`,
          );
          return text(
            `${c.item.title}\n路径：${c.item.locator}\n` +
              `类型：${c.item.modality}　大小：${fmtSize(c.item.sizeBytes)}\n` +
              `标签：${(c.item.tags || []).join(' ') || '（无）'}\n\n${c.text}`,
          );
        }
      }
      return text('分析已提交，但 20 秒内还没完成。大文件需要更久，稍后用 synorive_search 找它。');
    } catch (e) {
      return fail(e);
    }
  },
);

// ④ 取正文
server.registerTool(
  'synorive_get_content',
  {
    title: '取某条内容的全文',
    description:
      '按 id 取一条内容的完整正文。id 从 synorive_search 的结果里拿。' +
      '想读一个文件的具体内容时用这个，比让用户复制粘贴省事。',
    inputSchema: {
      itemId: z.string().describe('内容 id'),
      maxChars: z.number().int().min(200).max(100000).default(20000),
    },
  },
  async ({ itemId, maxChars }) => {
    try {
      const c = await engine.get<{ text: string; item: Item }>(
        `/api/items/${itemId}/content?maxChars=${maxChars}`,
      );
      return text(`${c.item.title}\n路径：${c.item.locator}\n\n${c.text}`);
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑤ 相似内容
server.registerTool(
  'synorive_similar',
  {
    title: '找相似内容',
    description:
      '给一条内容的 id，找库里和它相似的其它内容。' +
      '如果那条是图片，还能找出视频里相似的**镜头**并给出时间点。',
    inputSchema: {
      itemId: z.string().describe('作为基准的内容 id'),
      limit: z.number().int().min(1).max(30).default(10),
      byImage: z.boolean().default(false).describe('true=按画面相似（图片专用），false=按文字相似'),
    },
  },
  async ({ itemId, limit, byImage }) => {
    try {
      if (byImage) {
        const r = await engine.post<{ hits: Hit[]; totalEstimate: number }>(
          '/api/search/by-image',
          { itemId, limit },
        );
        return text(`按画面找到 ${r.totalEstimate} 条：\n\n${fmtHits(r.hits, { showId: true })}`);
      }
      const hits = await engine.get<Hit[]>(`/api/similar/${itemId}?limit=${limit}`);
      return text(`按内容找到 ${hits.length} 条：\n\n${fmtHits(hits, { showId: true })}`);
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑥ 时间轴
server.registerTool(
  'synorive_timeline',
  {
    title: '按时间看内容分布',
    description:
      '按时间桶统计库里的内容分布。回答"我那段时间在忙什么""某个月存了些什么"这类问题。',
    inputSchema: {
      bucket: z.enum(['hour', 'day', 'week', 'month', 'year']).default('month'),
      limit: z.number().int().min(1).max(200).default(36),
    },
  },
  async ({ bucket, limit }) => {
    try {
      const rows = await engine.get<
        { at: string; count: number; byModality: Record<string, number> }[]
      >(`/api/timeline?bucket=${bucket}&limit=${limit}`);
      if (!rows.length) return text('库里还没有带时间的内容。');
      const max = Math.max(...rows.map((r) => r.count));
      const lines = rows.map((r) => {
        const bar = '█'.repeat(Math.max(1, Math.round((r.count / max) * 28)));
        const kinds = Object.entries(r.byModality)
          .map(([k, v]) => `${k}${v}`)
          .join(' ');
        return `${r.at.padEnd(12)} ${String(r.count).padStart(5)} ${bar}  ${kinds}`;
      });
      return text(`按${bucket}统计（共 ${rows.length} 个时间段）：\n\n${lines.join('\n')}`);
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑦ 知识图谱
server.registerTool(
  'synorive_graph',
  {
    title: '看内容里的实体与关系',
    description:
      '看库里自动抽取的实体（邮箱、链接、金额、日期、版本号、机构名）以及它们的共现关系。' +
      '用来顺藤摸瓜：从一个实体找出所有提到它的内容。',
    inputSchema: {
      entityId: z.string().optional().describe('看某个实体的邻居；不给就看全局最热的'),
      kind: z
        .enum(['person', 'place', 'org', 'product', 'event', 'concept', 'time', 'contact', 'link', 'money', 'version'])
        .optional(),
      limit: z.number().int().min(1).max(120).default(40),
    },
  },
  async ({ entityId, kind, limit }) => {
    try {
      const q = new URLSearchParams({ limit: String(limit) });
      if (entityId) q.set('entityId', entityId);
      if (kind) q.set('kind', kind);
      const g = await engine.get<{
        entities: { id: string; kind: string; name: string; mentionCount: number }[];
        edges: { from: string; to: string; weight: number }[];
      }>(`/api/graph?${q}`);
      if (!g.entities.length) return text('还没有抽取到实体。');

      const byId = new Map(g.entities.map((e) => [e.id, e]));
      const lines = g.entities.map(
        (e) => `[${e.kind.padEnd(8)}] ${e.name}  ×${e.mentionCount}  (id ${e.id})`,
      );
      const edgeLines = g.edges
        .slice(0, 24)
        .map((e) => {
          const a = byId.get(e.from)?.name ?? e.from;
          const b = byId.get(e.to)?.name ?? e.to;
          return `  ${a} ── ${b}  （共现 ${e.weight} 次）`;
        });
      return text(
        `实体 ${g.entities.length} 个：\n${lines.join('\n')}` +
          (edgeLines.length ? `\n\n共现关系（前 ${edgeLines.length} 条）：\n${edgeLines.join('\n')}` : ''),
      );
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑧ 状态
server.registerTool(
  'synorive_status',
  {
    title: '看引擎状态与索引进度',
    description:
      '看引擎跑没跑、索引了多少内容、有没有正在跑的任务、哪些能力还缺依赖。' +
      '搜不到东西时先查这个——很可能是还没索引，或者缺模型。',
    inputSchema: {},
  },
  async () => {
    try {
      // 🔴 doctor 探测失败（网络/超时/500）不能和"探测成功、正好没有缺失
      // 依赖"混为一谈——原来两种情况都变成 []，Claude 看到的报告里
      // "还没装的能力"那一段直接消失，跟"全部依赖健康"长得一模一样，
      // 而实际上探测本身根本没跑成，缺失依赖有没有还是未知数
      let depsFailed = false;
      const [h, s, deps] = await Promise.all([
        engine.get<Record<string, unknown>>('/health'),
        engine.get<{ items: number; ready: number; failed: number; chunks: number }>('/api/stats'),
        engine
          .get<{ name: string; state: string; optional: boolean; degradesTo: string }[]>(
            '/api/doctor',
          )
          .catch(() => {
            depsFailed = true;
            return [];
          }),
      ]);

      const missing = deps.filter((d) => d.state !== 'ok');
      const lines = [
        `引擎版本 ${h.version}　已运行 ${Math.round(Number(h.uptimeSec) / 60)} 分钟`,
        `索引内容 ${s.items} 条（可搜 ${s.ready}，失败 ${s.failed}），文本块 ${s.chunks} 个`,
        `库文件 ${h.dbSizeMb} MB　并发度 ${h.concurrency}　推理执行器 ${h.executionProvider}`,
        `内存占用 ${h.memoryMb} MB　进行中的任务 ${h.activeJobs} 个`,
      ];
      if (depsFailed) {
        lines.push('', '⚠️ 依赖探测（/api/doctor）本次请求失败，缺不缺依赖这次没查到，不代表都装好了。');
      } else if (missing.length) {
        lines.push('', '还没装的能力：');
        for (const d of missing) {
          lines.push(
            `  · ${d.name}${d.optional ? '（可选）' : '（必需）'} → ${d.degradesTo || '不可用'}`,
          );
        }
        lines.push('在 Synorive 应用的设置里可以一键安装。');
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ══════════════════════════════════════════════════════════════
// 联网搜索与研究（W / R / L）
//
// 上面八个工具搜的是**用户自己的库**，下面五个搜的是**全网**。
// 给 Claude 的返回里必须始终带着可信度分项和出处 ——
// 不带的话 Claude 会把内容农场的说法和官方文档等同看待，
// 然后用同样自信的语气转述给用户。
// ══════════════════════════════════════════════════════════════

interface WebHit {
  title: string;
  url: string;
  snippet?: string;
  site?: string;
  engines?: string[];
  published?: string;
  trust?: {
    tierLabel?: string;
    score?: number;
    independentSources?: number;
    reasons?: string[];
    farmFlags?: string[];
    aiSuspect?: boolean;
    ageDays?: number;
  };
}

function fmtWebHits(hits: WebHit[]): string {
  if (!hits.length) return '（没有结果）';
  return hits
    .map((h, i) => {
      const t = h.trust || {};
      const badges: string[] = [];
      if (t.tierLabel) badges.push(t.tierLabel);
      if ((t.independentSources ?? 1) >= 3) badges.push(`${t.independentSources}个独立来源`);
      else if ((t.independentSources ?? 1) === 1) badges.push('孤证');
      if (t.aiSuspect) badges.push('疑似批量生成');
      if (t.farmFlags?.length) badges.push('内容农场特征');
      if (t.ageDays != null && t.ageDays > 1095)
        badges.push(`${Math.floor(t.ageDays / 365)}年前`);

      const lines = [
        `${i + 1}. ${h.title}` + (badges.length ? `  [${badges.join(' · ')}]` : ''),
        `   ${h.url}`,
      ];
      if (h.snippet) lines.push(`   ${h.snippet.slice(0, 200)}`);
      if (h.published) lines.push(`   发布：${h.published.slice(0, 10)}`);
      return lines.join('\n');
    })
    .join('\n\n');
}

// ⑨ 联网搜索
server.registerTool(
  'synorive_web_search',
  {
    title: '多引擎联网搜索（带可信度标注）',
    description:
      '同时问多家搜索引擎（Bing/百度/360/Mojeek 等），去重折叠后按相关度+可信度排序。' +
      '每条结果都带来源分级（官方/学术/主流媒体/社区/未收录/低信誉）、' +
      '有几个独立来源印证、是不是内容农场、内容有多旧。' +
      '⚠️ 这些标注是**基于来源和统计特征**的，判断不了"这句话本身是不是事实"——' +
      '转述给用户时请连同来源一起给出，不要把低信誉来源的说法当定论。',
    inputSchema: {
      query: z.string().describe('查询词'),
      limit: z.number().int().min(1).max(30).default(10).describe('返回多少条'),
      engines: z.array(z.string()).optional().describe('指定引擎，不传就用用户设置里启用的'),
      lang: z.string().default('zh').describe('语言，zh 或 en'),
      timeRange: z
        .enum(['day', 'week', 'month', 'year'])
        .optional()
        .describe('只要这段时间内发布的'),
    },
  },
  async ({ query, limit, engines, lang, timeRange }) => {
    try {
      const r = await engine.post<{
        results: WebHit[];
        excluded: WebHit[];
        trustSummary?: { note?: string };
        engines: { id: string; outcome: string; count: number; error?: string }[];
        elapsedMs: number;
        fromCache?: boolean;
      }>('/api/web/search', { query, limit, engines, lang, timeRange });

      const head = [
        `${r.trustSummary?.note || `${r.results.length} 条结果`}　用时 ${r.elapsedMs}ms` +
          (r.fromCache ? '（10 分钟内的缓存）' : ''),
      ];
      // 哪些引擎没成功必须说出来 —— 不说的话 Claude 会把
      // "只有一家引擎返回的结果"当成"全网就这些"
      const bad = r.engines.filter((e) => e.outcome !== 'ok' && e.outcome !== 'empty');
      if (bad.length) {
        head.push(`没跑成的引擎：${bad.map((e) => `${e.id}(${e.outcome})`).join('、')}`);
      }
      if (r.excluded?.length) {
        head.push(`另有 ${r.excluded.length} 条被判为低信誉已折叠（用户可在界面里查看和放回）`);
      }
      return text(`${head.join('\n')}\n\n${fmtWebHits(r.results)}`);
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑩ 联网研究（搜 + 抓正文 + 出摘录简报）
server.registerTool(
  'synorive_research',
  {
    title: '联网研究：搜索 + 抓正文 + 出带出处的简报',
    description:
      '一次调用走完：多引擎搜索 → 判可信度 → 抓取靠前几篇的正文 → 输出结构化简报。' +
      '简报分五块：共识（多个独立站点都在说的）、分歧（互相矛盾的说法**并排列出**）、' +
      '时间线、关键数据、还没查清的。' +
      '🔴 简报里每一句都是**某篇原文里逐字存在的句子**，不是改写和总结，后面都挂着出处链接。' +
      '遇到分歧时它不会替你选一个——请把两种说法都转述给用户。' +
      '比 synorive_web_search 慢（要抓正文），适合"帮我查清楚某件事"而不是"找几个链接"。',
    inputSchema: {
      query: z.string().describe('要查清楚的问题'),
      fetch: z.number().int().min(1).max(10).default(5).describe('抓几篇正文来做简报'),
      limit: z.number().int().min(1).max(30).default(15).describe('搜多少条'),
      lang: z.string().default('zh'),
      rounds: z
        .number()
        .int()
        .min(1)
        .max(3)
        .default(2)
        .describe(
          '挖几轮。2 = 读完第一轮之后**自己想出该追问什么**再搜一轮（默认）；' +
            '1 = 只搜一次，快但只能回答你已经会问的问题',
        ),
      preset: z
        .string()
        .optional()
        .describe(
          '只搜某一类权威来源：official-docs / academic / gov / code / cn-media / factcheck。' +
            '⚠️ 开了就搜不到这些站之外的东西',
        ),
      verifyLevel: z
        .enum(['annotate', 'counter', 'claim'])
        .default('counter')
        .describe(
          'annotate=只静态标注不出网；counter=主动去搜辟谣/质疑 + 溯源 + 撤稿检查（默认）；' +
            'claim=每条断言单独搜一轮核查（很慢）',
        ),
    },
  },
  async ({ query, fetch, limit, lang, rounds, preset, verifyLevel }) => {
    try {
      const r = await engine.post<{
        results: WebHit[];
        fetched: number;
        fetchFailed: number;
        trustSummary?: { note?: string };
        rounds?: { round: number; queries: { text: string; why: string }[]; newResults?: number }[];
        verification?: {
          level: string;
          note?: string;
          counterEvidence?: { title: string; url: string; site: string; snippet: string }[];
          retracted?: Record<string, { title?: string; reason: string }>;
          origin?: { verdict: string; note: string };
          claims?: {
            claim: string;
            verdict: string;
            supportCount: number;
            refuteCount: number;
            note: string;
          }[];
        };
        briefing: {
          consensus: { topic: string; independentSites: number; evidence: EvidenceOut[] }[];
          disputes: { topic: string; conflicts: { a: EvidenceOut; b: EvidenceOut }[] }[];
          timeline: { published?: string; title?: string; site?: string }[];
          numbers: { value: string; sentence: string; site?: string }[];
          openQuestions: string[];
        };
      }>(
        '/api/web/research',
        { query, fetch, limit, lang, rounds, preset, verifyLevel },
        240_000,
      );

      const b = r.briefing;
      const out: string[] = [
        `【${query}】`,
        `${r.trustSummary?.note || ''}　抓到正文 ${r.fetched} 篇` +
          (r.fetchFailed ? `（${r.fetchFailed} 篇抓取失败）` : ''),
        '',
        '⚠️ 以下每句都是原文摘录，不是总结改写。括号里是出处。',
      ];

      if (b.consensus.length) {
        out.push('', '── 多个独立来源都在说的 ──');
        for (const c of b.consensus) {
          out.push(`◆ ${c.topic}（${c.independentSites} 个独立站点）`);
          for (const e of c.evidence.slice(0, 2)) {
            out.push(`   「${e.text}」`, `     ↳ ${e.site} ${e.url}`);
          }
        }
      }
      if (b.disputes.length) {
        out.push('', '── 存在分歧（**没有替你选一个**）──');
        for (const d of b.disputes) {
          out.push(`◆ ${d.topic}`);
          for (const p of d.conflicts.slice(0, 2)) {
            out.push(`   甲：「${p.a.text}」  ↳ ${p.a.site}`);
            out.push(`   乙：「${p.b.text}」  ↳ ${p.b.site}`);
          }
        }
      }
      if (b.numbers.length) {
        out.push('', '── 关键数据 ──');
        for (const n of b.numbers.slice(0, 8)) {
          out.push(`   ${n.value}：${n.sentence.slice(0, 100)}  ↳ ${n.site}`);
        }
      }
      if (b.timeline.length) {
        out.push('', '── 时间线 ──');
        for (const t of b.timeline.slice(0, 6)) {
          out.push(`   ${(t.published || '').slice(0, 10)}  ${t.title}  ↳ ${t.site}`);
        }
      }
      if (b.openQuestions.length) {
        out.push('', '── 还没查清 ──');
        for (const q of b.openQuestions) out.push(`   · ${q}`);
      }

      // 多轮：把「第二轮为什么问这些」如实交代。
      // 不交代的话，结果里会冒出一批用户没搜过的东西，只会让人困惑
      if (r.rounds && r.rounds.length > 1) {
        out.push('', '── 检索过程（第二轮起的问题是读完前一轮之后自己想出来的）──');
        for (const rd of r.rounds) {
          out.push(`   第 ${rd.round} 轮${rd.newResults != null ? `（新增 ${rd.newResults} 条）` : ''}`);
          for (const q of rd.queries) out.push(`     · ${q.text}　— ${q.why}`);
        }
      }

      // 🔴 核查结论必须原样带给 Claude，且**措辞不能变成"这是假的"** ——
      // 找到反驳源不等于原说法就错了。不这么写的话，Claude 会把
      // "有人反驳过" 转述成 "这是谣言"，那是我们给不出的结论
      const v = r.verification;
      if (v && v.level !== 'annotate') {
        out.push('', '── 主动核查 ──', `   档位：${v.level}　${v.note || ''}`);
        for (const c of v.counterEvidence || []) {
          out.push(`   ⚠️ 反面材料：${c.title}  ↳ ${c.site} ${c.url}`);
        }
        for (const [doi, info] of Object.entries(v.retracted || {})) {
          out.push(`   🔴 已撤稿：${info.title || doi}（DOI ${doi}）`);
        }
        if (v.origin?.note) out.push(`   溯源：${v.origin.note}`);
        for (const cl of v.claims || []) {
          out.push(
            `   断言「${cl.claim.slice(0, 60)}」→ ${cl.verdict}` +
              `（支持 ${cl.supportCount}／反驳 ${cl.refuteCount}）${cl.note}`,
          );
        }
        out.push(
          '   ⚠️ 「有人反驳」不等于原说法就是假的，也可能是反驳的人错了。' +
            '请把两边的材料都转述给用户，不要替他下真假结论。',
        );
      }

      out.push('', '── 用到的来源 ──', fmtWebHits(r.results.slice(0, 8)));
      return text(out.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

interface EvidenceOut {
  text: string;
  url: string;
  title: string;
  site: string;
  trustScore?: number;
  tier?: string;
}

// ⑬b 这篇能回答哪些问题（N6）
server.registerTool(
  'synorive_questions',
  {
    title: '这篇文档能回答哪些问题',
    description:
      '给一个库里的条目 id，返回**这篇文档能回答的问题清单**，每条都指向原文里一个具体段落。' +
      '和搜索是相反的方向：搜索的前提是你已经知道要问什么，' +
      '而面对一篇四十页的 PDF，难的恰恰是"该问它什么"。' +
      '拿到清单之后可以用 synorive_get_content 读对应段落。' +
      '🔴 这些问题是**从原文里读出来的**（章节标题 + 定义/结论/数字句式），不是模型生成的 —— ' +
      '所以它们一定对应真实存在的段落，但也因此覆盖不全：' +
      '一篇没有明显结构的散文可能一条都读不出来，那不代表内容有问题。',
    inputSchema: {
      itemId: z.string().describe('条目 id（搜索结果里带）'),
      limit: z.number().int().min(1).max(50).default(20),
    },
  },
  async ({ itemId, limit }) => {
    try {
      const r = await engine.get<{
        title: string;
        note: string;
        chunkCount: number;
        questions: {
          question: string;
          kind: string;
          chunkRowid: number;
          section?: string;
          page?: number;
          preview: string;
        }[];
      }>(`/api/items/${encodeURIComponent(itemId)}/questions?limit=${limit}`);

      const lines = [`【${r.title}】`, r.note, ''];
      for (const q of r.questions) {
        const where = [q.section, q.page != null ? `第 ${q.page} 页` : ''].filter(Boolean).join(' · ');
        lines.push(`· ${q.question}${where ? `　（${where}）` : ''}`);
        lines.push(`    ${q.preview}`);
      }
      if (!r.questions.length) {
        lines.push('（读不出问题。这篇共 ' + r.chunkCount + ' 块正文，多半是没有明显结构的散文。）');
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑭ 单说法核查（V6/V1）
server.registerTool(
  'synorive_verify',
  {
    title: '核查一个说法：主动去找反驳材料',
    description:
      '给一句话（或几句），主动去搜「辟谣 / 质疑 / 争议 / debunked」，把找到的反面材料带回来。' +
      '比 synorive_research 快得多（两三秒），适合"我看到一句话，想知道有没有人反驳过"。' +
      '🔴 **能力边界，转述时必须一起说**：找到反驳源**不等于**这个说法是假的（也可能是反驳的人错了）；' +
      '没找到反驳源**更不等于**它是真的（只说明没人公开反驳过）。' +
      '这个工具给的是「支持 N ／ 反驳 M」这两个数和各自的出处，**它判断不了一句话本身是不是事实**。' +
      '请把两边的材料都转述给用户，不要替他下真假结论。',
    inputSchema: {
      query: z.string().describe('话题，或者要核查的那句话'),
      claims: z
        .array(z.string())
        .max(8)
        .optional()
        .describe('要逐条核查的具体说法。给了这个就走断言级核查，每条单独搜一轮，慢但更准'),
      dois: z.array(z.string()).max(20).optional().describe('要检查有没有被撤稿的 DOI'),
    },
  },
  async ({ query, claims, dois }) => {
    try {
      const r = await engine.post<{
        counterEvidence?: { title: string; url: string; site: string; snippet: string }[];
        retracted?: Record<string, { title?: string; reason: string }>;
        claims?: {
          claim: string;
          verdict: string;
          supportCount: number;
          refuteCount: number;
          note: string;
          refute?: { title: string; url: string; site: string }[];
        }[];
        note?: string;
      }>(
        '/api/web/verify',
        { query, claims, dois, level: claims?.length ? 'claim' : 'counter' },
        180_000,
      );

      const out: string[] = [`【核查：${query}】`, r.note || ''];
      for (const c of r.counterEvidence || []) {
        out.push(`⚠️ ${c.title}  ↳ ${c.site} ${c.url}`, `   「${c.snippet.slice(0, 160)}」`);
      }
      for (const [doi, info] of Object.entries(r.retracted || {})) {
        out.push(`🔴 已撤稿：${info.title || doi}（DOI ${doi}）`);
      }
      for (const cl of r.claims || []) {
        out.push(
          '',
          `断言：${cl.claim}`,
          `  结论：${cl.verdict}（支持 ${cl.supportCount}／反驳 ${cl.refuteCount}）`,
          `  ${cl.note}`,
        );
        for (const s of cl.refute || []) out.push(`  反驳：${s.title} ↳ ${s.site} ${s.url}`);
      }
      out.push(
        '',
        '⚠️ 转述时请保留这条：有反驳材料 ≠ 原说法是假的；没有反驳材料 ≠ 原说法是真的。',
      );
      return text(out.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑮ 本地 × 网上并排（P5）
server.registerTool(
  'synorive_unified_search',
  {
    title: '同时搜「我自己的资料」和「网上」，冲突高亮',
    description:
      '一次调用同时查本地库和联网搜索，两边结果并排返回，并标出**互相矛盾的地方**。' +
      '适合"我记得我存过这个，但不确定还是不是最新的"这类问题 —— ' +
      '本地资料可能过时，网上说法可能不适用于用户的具体情况，两边都给用户才谈得上判断。' +
      '🔴 冲突标记只说明两边说法对不上，**没有判断哪边对**。',
    inputSchema: {
      query: z.string(),
      limit: z.number().int().min(1).max(30).default(10),
      local: z.boolean().default(true).describe('要不要搜本地库'),
      web: z.boolean().default(true).describe('要不要联网搜'),
    },
  },
  async ({ query, limit, local, web }) => {
    try {
      const r = await engine.post<{
        local: { results?: { title?: string; snippet?: string; itemId?: string }[]; error?: string };
        web: { results?: WebHit[]; error?: string; unavailable?: string };
        conflicts: {
          local: { title?: string; text: string };
          web: { site?: string; url?: string; text: string };
        }[];
        note: string;
      }>('/api/unified/search', { query, limit, local, web }, 180_000);

      const out: string[] = [`【${query}】`, r.note, ''];
      const lr = r.local.results || [];
      out.push('── 你自己的资料 ──');
      if (r.local.error) out.push(`   （本地检索失败：${r.local.error}）`);
      else if (!lr.length) out.push('   （库里没有相关内容）');
      for (const h of lr.slice(0, 6)) {
        out.push(`   ${h.title || '(无标题)'}  [${h.itemId || ''}]`, `     ${(h.snippet || '').slice(0, 120)}`);
      }

      out.push('', '── 网上说的 ──');
      if (r.web.unavailable) out.push(`   （${r.web.unavailable}）`);
      else if (r.web.error) out.push(`   （联网检索失败：${r.web.error}）`);
      else out.push(fmtWebHits((r.web.results || []).slice(0, 6)));

      if (r.conflicts.length) {
        out.push('', '── ⚠️ 这几处对不上（没有判断哪边对）──');
        for (const c of r.conflicts) {
          out.push(`   你的资料：「${c.local.text.slice(0, 120)}」`);
          out.push(`   网上说的：「${c.web.text.slice(0, 120)}」 ↳ ${c.web.site || c.web.url}`);
          out.push('');
        }
      }
      return text(out.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑪ 文献检索
server.registerTool(
  'synorive_scholar',
  {
    title: '学术文献检索（arXiv/Crossref/OpenAlex/DOAJ/PubMed）',
    description:
      '同时查五家学术源，按 DOI 合并去重，返回带作者、年份、期刊、被引数、PDF 直链的清单。' +
      '排序先看被几家收录再看被引数——**不单纯按被引数排**，' +
      '否则永远是老论文在前面，而查一个新方向时最需要的恰恰是近两年的。' +
      '拿到结果后可以用 synorive_ingest 把感兴趣的 PDF 存进用户的本地库。',
    inputSchema: {
      query: z.string().describe('检索式，用英文效果明显更好'),
      limit: z.number().int().min(1).max(50).default(20),
      sources: z
        .array(z.enum(['arxiv', 'crossref', 'openalex', 'doaj', 'pubmed']))
        .optional()
        .describe('指定学术源，不传就五家全问'),
    },
  },
  async ({ query, limit, sources }) => {
    try {
      const r = await engine.post<{
        papers: {
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
        }[];
        sources: { id: string; outcome: string; count: number; error?: string }[];
        totalBeforeMerge: number;
        mergedCount: number;
        elapsedMs: number;
      }>('/api/web/scholar', { query, limit, sources }, 120_000);

      const bad = r.sources.filter((s) => s.outcome !== 'ok' && s.outcome !== 'empty');
      const head = [
        `${r.totalBeforeMerge} 条 → 按 DOI 合并为 ${r.mergedCount} 篇 → 返回 ${r.papers.length} 篇` +
          `　用时 ${r.elapsedMs}ms`,
      ];
      if (bad.length) head.push(`没跑成的源：${bad.map((s) => `${s.id}(${s.outcome})`).join('、')}`);

      const body = r.papers
        .map((p, i) => {
          const m = p.meta || {};
          const bits = [
            m.year,
            m.venue,
            m.citations != null ? `被引 ${m.citations}` : '',
            m.openAccess ? '开放获取' : '',
            `收录于 ${p.sources.join('+')}`,
          ].filter(Boolean);
          const lines = [`${i + 1}. ${p.title}`, `   ${bits.join(' · ')}`];
          if (m.authors?.length)
            lines.push(`   ${m.authors.slice(0, 5).join(', ')}${m.authors.length > 5 ? ' 等' : ''}`);
          if (m.doi) lines.push(`   DOI: ${m.doi}`);
          if (m.pdf) lines.push(`   PDF: ${m.pdf}`);
          else if (p.url) lines.push(`   ${p.url}`);
          if (p.snippet) lines.push(`   ${p.snippet.slice(0, 220)}`);
          return lines.join('\n');
        })
        .join('\n\n');
      return text(`${head.join('\n')}\n\n${body || '（没有结果）'}`);
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑫ 链接秒析
server.registerTool(
  'synorive_read_url',
  {
    title: '抓取一个网页并提取正文',
    description:
      '给一个网址，返回它的标题、作者、发布时间和**去掉导航广告后的正文**。' +
      '内网地址会被拒绝（防止误访问路由器后台之类）。' +
      '如果只是想让用户以后能搜到这个页面，用 synorive_ingest 传 URL 会连快照一起存档。',
    inputSchema: {
      url: z.string().describe('网址'),
      maxChars: z.number().int().min(200).max(50000).default(6000).describe('返回多少字正文'),
      trail: z
        .boolean()
        .default(false)
        .describe(
          '顺藤摸瓜：额外返回**它引用了谁**（出链，按来源等级分组）和**谁在讨论它**（反链）。' +
            '判断一篇文章可不可信时这两项常常比正文本身更有用 —— ' +
            '一篇声称"研究表明"却一条站外链接都没有的文章，这件事本身就是判据',
        ),
    },
  },
  async ({ url, maxChars, trail }) => {
    try {
      const r = await engine.post<{
        results: WebHit[];
        briefing?: unknown;
      } & Record<string, unknown>>('/api/web/read', { url, maxChars, trail }, 120_000);
      const p = r as unknown as {
        title: string;
        site: string;
        published?: string;
        author?: string;
        text: string;
        warnings?: string[];
        trust?: WebHit['trust'];
        trail?: {
          note: string;
          byTier: Record<string, number>;
          outlinks: { url: string; site: string; tierLabel: string; text: string }[];
          backlinks: { url: string; title?: string; site?: string }[];
        };
      };
      const lines = [
        p.title,
        `站点：${p.site}` +
          (p.author ? `　作者：${p.author}` : '') +
          (p.published ? `　发布：${p.published.slice(0, 10)}` : '　发布时间：抓不到'),
      ];
      if (p.trust?.tierLabel) lines.push(`来源分级：${p.trust.tierLabel}`);
      if (p.warnings?.length) lines.push(`注意：${p.warnings.join('；')}`);

      if (p.trail) {
        lines.push('', '── 顺藤摸瓜 ──', p.trail.note);
        const tiers = Object.entries(p.trail.byTier);
        if (tiers.length) {
          lines.push('出链分布：' + tiers.map(([k, n]) => `${k} ${n}`).join('　'));
        }
        for (const o of p.trail.outlinks.slice(0, 10)) {
          lines.push(`  → [${o.tierLabel}] ${o.site}　${o.text || o.url}`);
        }
        for (const b of p.trail.backlinks.slice(0, 6)) {
          lines.push(`  ← ${b.title || b.url}　↳ ${b.site}`);
        }
      }

      lines.push('', p.text);
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑬ 引擎可用性
server.registerTool(
  'synorive_web_engines',
  {
    title: '看哪些搜索引擎当前可用',
    description:
      '列出所有搜索引擎和学术源、各自是否启用、要不要 Key、要不要浏览器渲染、有没有被熔断。' +
      '联网搜索结果很少或很偏时先查这个——很可能是某几家被限流了。',
    inputSchema: {},
  },
  async () => {
    try {
      const r = await engine.get<{
        engines: {
          id: string;
          label: string;
          group: string;
          kind: string;
          needsKey: boolean;
          needsBrowser: boolean;
          defaultOn: boolean;
          note: string;
        }[];
        health: {
          enabled: string[];
          breaker: Record<string, { fails: number; openFor: number }>;
          rendererAvailable?: boolean;
        };
      }>('/api/web/engines');

      const lines: string[] = [
        `当前启用：${r.health.enabled.join('、') || '（无）'}`,
        `浏览器渲染（Google/Yandex 需要它）：${r.health.rendererAvailable ? '可用（已连接桌面端）' : '不可用（桌面端没开，或还没连上）'}`,
        '',
      ];
      for (const g of ['web', 'scholar']) {
        lines.push(g === 'web' ? '── 网页搜索 ──' : '── 学术文献源 ──');
        for (const e of r.engines.filter((x) => x.group === g)) {
          const st: string[] = [];
          if (r.health.enabled.includes(e.id)) st.push('已启用');
          else st.push('未启用');
          if (e.needsKey) st.push('要 Key');
          if (e.needsBrowser) st.push('要浏览器渲染');
          const br = r.health.breaker[e.id];
          if (br?.openFor) st.push(`熔断中，${Math.ceil(br.openFor / 60)} 分钟后重试`);
          lines.push(`  · ${e.label}（${e.id}）[${st.join('、')}]`);
          if (e.note) lines.push(`      ${e.note}`);
        }
        lines.push('');
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑭ 以图搜图（W5）
server.registerTool(
  'synorive_reverse_image',
  {
    title: '以图搜图——找一张图在网上的出处',
    description:
      '给一张图片（库里已有的条目 id，或者本机文件路径），反查它在网上还出现在哪些地方——' +
      '找原始出处、找更清晰的版本、看是不是被搬运过。走的是 Bing 的反向图片搜索，' +
      '没有官方文档，是逆向出来的协议，可能会突然失效——失效时会明确报错，不会装作查到了。',
    inputSchema: {
      itemId: z.string().optional().describe('库里已有内容的 id，和 path 二选一'),
      path: z.string().optional().describe('本机图片文件的绝对路径，和 itemId 二选一'),
      limit: z.number().int().min(1).max(50).default(20),
    },
  },
  async ({ itemId, path, limit }) => {
    if (!itemId && !path) return fail(new Error('itemId 和 path 至少给一个'));
    try {
      const r = await engine.post<{
        pagesIncluding: { title: string; pageUrl: string; thumbnailUrl: string }[];
        visualSimilar: { title: string; pageUrl: string; thumbnailUrl: string }[];
        bestGuess?: string;
        error?: string;
      }>('/api/web/reverse-image', { itemId, path, limit }, 60_000);

      if (r.error) return text(`反查没成功：${r.error}`);
      const lines: string[] = [];
      if (r.bestGuess) lines.push(`Bing 猜这是：${r.bestGuess}`, '');
      if (r.pagesIncluding.length) {
        lines.push('── 出现在这些网页里（大概率是出处或转载）──');
        for (const [i, p] of r.pagesIncluding.slice(0, 10).entries()) {
          lines.push(`${i + 1}. ${p.title || '（无标题）'}`, `   ${p.pageUrl}`);
        }
      }
      if (r.visualSimilar.length) {
        lines.push('', '── 视觉相似（不一定是同一张图，仅供参考）──');
        for (const [i, p] of r.visualSimilar.slice(0, 5).entries()) {
          lines.push(`${i + 1}. ${p.title || '（无标题）'}  ${p.pageUrl}`);
        }
      }
      return text(lines.join('\n') || '没查到什么结果。');
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑮ 视频反查（W6）
server.registerTool(
  'synorive_reverse_video',
  {
    title: '视频反查——找一段视频的原始来源',
    description:
      '给库里一条视频内容的 id，均匀抽几个已经提取好的关键帧分别以图搜图，' +
      '按"被几帧同时命中同一个网页"聚合排序——命中帧数越多，越可能是真正的原始来源' +
      '（单一帧命中可能只是巧合的画面相似）。视频要先被索引过、跑完场景检测才有关键帧可用。',
    inputSchema: {
      itemId: z.string().describe('库里视频内容的 id'),
      maxFrames: z.number().int().min(1).max(15).default(5),
    },
  },
  async ({ itemId, maxFrames }) => {
    try {
      const r = await engine.post<{
        candidates: { pageUrl: string; title: string; matchedKeyframes: number }[];
        framesTried: number;
        error?: string;
      }>('/api/web/reverse-video', { itemId, maxFrames }, 90_000);

      if (r.error) return text(`反查没成功：${r.error}`);
      if (!r.candidates.length) return text(`抽了 ${r.framesTried} 帧，没查到像样的来源候选。`);
      const lines = [`抽了 ${r.framesTried} 帧，候选来源（按命中帧数排序）：`, ''];
      for (const [i, c] of r.candidates.slice(0, 8).entries()) {
        lines.push(`${i + 1}. ${c.title || '（无标题）'}  命中 ${c.matchedKeyframes} 帧`, `   ${c.pageUrl}`);
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ════════════════════════════════════════════════════════════
// 第四轮新增的八个工具（C/D/E/A 组）
// ════════════════════════════════════════════════════════════
// 这一批全部遵守同一条：**能力边界写进 description**。
// 不写的话 Claude 会把「按词面相似度分堆」当成「按语义理解分类」，
// 把「从摘要里抽的数字」当成「核对过全文」，然后用同样自信的语气转述。

// ⑰ 文献综述（C4）
server.registerTool(
  'synorive_scholar_review',
  {
    title: '把一批文献整理成分主题的综述（只摘录，不改写）',
    description:
      '给一批 synorive_scholar 搜到的文献，按主题聚成几簇，每簇一段，' +
      '**每句都是从某篇摘要里逐字摘出来的**，句尾 [n] 指回那一篇。\n' +
      '🔴 它不改写、不概括、不做任何原文没说的推断 —— 所以读起来不如人写的连贯，' +
      '这是刻意的代价。有分歧的地方会并排摆出，不替谁判断哪边对。\n' +
      '🔴 分主题用的是**标题和摘要的词面相似度，不是语义模型** —— ' +
      '换了说法但讲同一件事的，可能被分到两堆里。',
    inputSchema: {
      entries: z.array(z.record(z.any())).describe('synorive_scholar 返回的文献条目'),
      topic: z.string().default('').describe('主题名，只用于标题'),
      maxSections: z.number().int().min(2).max(12).default(6),
    },
  },
  async ({ entries, topic, maxSections }) => {
    try {
      const r = await engine.post<{
        sections: {
          heading: string; paperCount: number; yearSpan: string;
          quotes: { text: string; ref: number; year: string }[];
          disputes: { a: { text: string; ref: number }; b: { text: string; ref: number } }[];
        }[];
        references: { n: number; title: string; url: string; year: string }[];
        note: string;
      }>('/api/scholar/review', { entries, topic, maxSections });

      const lines: string[] = [r.note, ''];
      for (const s of r.sections) {
        lines.push(`## ${s.heading}　（${s.paperCount} 篇${s.yearSpan ? ` · ${s.yearSpan}` : ''}）`);
        for (const q of s.quotes) lines.push(`  · ${q.text} [${q.ref}]`);
        for (const d of s.disputes) {
          lines.push(`  ⚠️ 这里有分歧，两边都摆出来：`);
          lines.push(`     A [${d.a.ref}] ${d.a.text}`);
          lines.push(`     B [${d.b.ref}] ${d.b.text}`);
        }
        lines.push('');
      }
      lines.push('参考文献：');
      for (const ref of r.references) lines.push(`  [${ref.n}] ${ref.title}（${ref.year}）${ref.url}`);
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑱ 多篇对齐抽表（C5）
server.registerTool(
  'synorive_scholar_table',
  {
    title: '同一个指标在 N 篇论文里各是多少，抽成一张表',
    description:
      '把一批文献里的准确率/F1/样本量/参数量/耗时等指标抽成表格，方便横向比。\n' +
      '🔴 **只抽摘要里明确写了的**。摘要没写就是空格 —— 不去正文里猜，' +
      '也不做单位换算（92% 和 0.92 保持原样并排放）。' +
      '要更全得先把 PDF 下下来入库再对全文抽一次。\n' +
      '返回里的 coverage 是填充率，很低就说明这批文献的摘要普遍没写指标，那不是抽取失败。',
    inputSchema: {
      entries: z.array(z.record(z.any())),
      metrics: z.array(z.string()).optional().describe('只要哪几列，不传就全要'),
    },
  },
  async ({ entries, metrics }) => {
    try {
      const r = await engine.post<{
        columns: string[];
        rows: { title: string; year: string; cells: Record<string, { value: string; unit: string } | null> }[];
        coverage: number; note: string;
      }>('/api/scholar/table', { entries, metrics: metrics ?? null, format: 'json' });

      const cols = r.columns.filter((c) => c !== '文献' && c !== '年份');
      const lines = [r.note, '', ['文献', '年份', ...cols].join(' | '), '---'];
      for (const row of r.rows) {
        const vals = cols.map((c) => {
          const cell = row.cells[c];
          return cell ? `${cell.value}${cell.unit}` : '—';
        });
        lines.push([row.title.slice(0, 40), row.year || '—', ...vals].join(' | '));
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑲ 引用网络（C3）
server.registerTool(
  'synorive_citations',
  {
    title: '找一个领域绕不过去的那几篇论文',
    description:
      '从一批文献出发，往回展开参考文献、往前展开被引，然后**数共被引次数**。' +
      '被这批文献共同引用最多的那几篇，基本就是这个领域的奠基工作。\n' +
      '🔴 这个数是**自己数出来的**，不是谁的推荐榜 —— 但它只反映"你给的这批文献在引谁"，' +
      '你给的那批要是本身就偏，数出来的奠基论文也会跟着偏。\n' +
      '🔴 只展开一层。数据来自 OpenAlex，**预印本和中文文献常常查不到**，' +
      '返回里的 resolved/requested 会告诉你有几篇没解析出来。',
    inputSchema: {
      entries: z.array(z.record(z.any())),
      direction: z.enum(['back', 'forward', 'both']).default('both'),
      topN: z.number().int().min(3).max(40).default(15),
    },
  },
  async ({ entries, direction, topN }) => {
    try {
      const r = await engine.post<{
        foundations: { title: string; year: string; coCited: number; citations: number; url: string }[];
        followups: { title: string; year: string; citesSeeds: number; citations: number; url: string }[];
        resolved: number; requested: number; note: string;
      }>('/api/scholar/citations', { entries, direction, topN }, 60_000);

      const lines = [r.note, '', `【奠基论文】被这批文献共同引用最多的`];
      for (const f of r.foundations) {
        lines.push(`  · ${f.title}（${f.year}）— 被这批里 ${f.coCited} 篇引用，总被引 ${f.citations}`);
        lines.push(`    ${f.url}`);
      }
      if (r.followups.length) {
        lines.push('', '【后续工作】引用了这批文献的');
        for (const f of r.followups) {
          lines.push(`  · ${f.title}（${f.year}）— 引了这批里 ${f.citesSeeds} 篇，总被引 ${f.citations}`);
        }
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ⑳ 批量下载 PDF（C2）
server.registerTool(
  'synorive_harvest',
  {
    title: '把搜到的开放获取论文批量下下来并入库',
    description:
      '**默认干跑**：不传 apply=true 时只告诉你打算下几篇、大概多大，不真下。\n' +
      '🔴 只下各家源明确标了公开全文的那些。**绝不去猜出版商的下载地址** —— ' +
      '那既下不到（会拿到登录页）也不合规。付费墙后面的会被跳过并说明原因。\n' +
      '🔴 单次上限 50 篇、4 并发。学术站点对批量抓取很敏感，打太狠会让整个 IP 被封。\n' +
      '下完自动入库并走分节索引，之后用 synorive_search 就能搜到全文。',
    inputSchema: {
      entries: z.array(z.record(z.any())),
      apply: z.boolean().default(false).describe('true 才真下；默认干跑'),
      limit: z.number().int().min(1).max(50).default(20),
    },
  },
  async ({ entries, apply, limit }) => {
    try {
      const r = await engine.post<{
        dryRun?: boolean; count?: number; estimatedMb?: number;
        downloaded?: number; ingested?: number; failed?: number;
        items?: { title: string; status: string; reason: string }[];
        note: string;
      }>('/api/scholar/harvest', { entries, apply, limit, ingest: true }, 300_000);

      if (r.dryRun) {
        return text(`${r.note}\n\n要真下的话，把 apply 设成 true 再调一次。`);
      }
      const lines = [r.note, ''];
      for (const it of (r.items ?? []).filter((x) => x.status !== 'ok').slice(0, 15)) {
        lines.push(`  ✗ ${it.title.slice(0, 50)} —— ${it.reason}`);
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ㉑ 长期记忆（E2）
server.registerTool(
  'synorive_memory',
  {
    title: '这个话题我以前查过什么',
    description:
      '开始查一个话题之前先问一句"以前看过什么"。返回的是**逐字摘录 + 出处**，' +
      '按"被反复见到的次数"排序 —— 反复出现的更可能是这个话题的骨干信息。\n' +
      '🔴 记忆库里**只存摘录，不存任何总结** —— 存总结等于把一次可能出错的提炼' +
      '固化成"记忆"，之后每次都在这个可能错的基础上继续，错误会累积且追不回源头。\n' +
      '🔴 「以前没查过」不代表这个话题没被研究过，只代表这台机器上没有记录。',
    inputSchema: {
      topic: z.string().describe('话题（就用查询词即可）'),
      limit: z.number().int().min(1).max(100).default(30),
    },
  },
  async ({ topic, limit }) => {
    try {
      const r = await engine.get<{
        known: boolean; runCount: number; lastSeen: string; controversy: number | null;
        facts: { text: string; url: string; site: string; seen_count: number }[];
        note: string;
      }>(`/api/memory/recall?topic=${encodeURIComponent(topic)}&limit=${limit}`);

      if (!r.known) return text(`${r.note}。这是第一次查这个话题。`);
      const lines = [
        `${r.note}，最近一次 ${r.lastSeen}` +
          (r.controversy != null ? `，上次的争议度 ${r.controversy}/100` : ''),
        '',
      ];
      for (const f of r.facts) {
        lines.push(`· ${f.text}`);
        lines.push(`  —— ${f.site}（见过 ${f.seen_count} 次）${f.url}`);
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ㉒ 文件比对（A5）
server.registerTool(
  'synorive_compare',
  {
    title: '两个文件哪里不一样',
    description:
      '文本/代码走行级 diff，图片走感知哈希，二进制只能比到字节层面。' +
      '两边类型不同时**不硬比**（硬比会得到一个看起来像结论、实际毫无意义的 0%）。\n' +
      '🔴 只报差异，**不判断哪个版本更好** —— 那是用户的事。\n' +
      '🔴 图片那一路看的是构图和明暗分布，**看不出局部小改动**：' +
      '改掉照片里一个小物件，感知哈希距离可能仍然是 0。',
    inputSchema: {
      a: z.string().describe('第一个文件的绝对路径'),
      b: z.string().describe('第二个文件的绝对路径'),
    },
  },
  async ({ a, b }) => {
    try {
      const r = await engine.post<{
        kind?: string; verdict?: string; error?: string; note?: string;
        similarity?: number; added?: number; removed?: number;
        hunks?: { tag: string; aStart: number; aLines: string[]; bLines: string[] }[];
      }>('/api/compare/files', { a, b }, 120_000);

      if (r.error) return text(`比不了：${r.error}`);
      const lines = [r.verdict ?? '', r.note ?? '', ''];
      if (r.kind === 'text') {
        lines.push(`新增 ${r.added} 行，删除 ${r.removed} 行`, '');
        for (const h of (r.hunks ?? []).slice(0, 12)) {
          lines.push(`@@ 第 ${h.aStart} 行 (${h.tag})`);
          for (const l of h.aLines.slice(0, 6)) lines.push(`- ${l}`);
          for (const l of h.bLines.slice(0, 6)) lines.push(`+ ${l}`);
        }
      }
      return text(lines.filter(Boolean).join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ㉓ 视频章节（A6）
server.registerTool(
  'synorive_chapters',
  {
    title: '给一个视频/音频出章节目录',
    description:
      '按语音停顿和画面切换把长视频切成有标题的章节，每章带时间码，可直接跳。\n' +
      '🔴 返回里的 method 一定要看：**method="equal" 表示这是等分的** —— ' +
      '没有转写也没有足够的场景数据，章节边界不代表内容真的在这里换了话题。\n' +
      '🔴 章节标题是从转写原文里**挑**出来的，不是生成的。挑不出来就用时间码。',
    inputSchema: {
      itemId: z.string(),
      maxChapters: z.number().int().min(2).max(60).default(30),
    },
  },
  async ({ itemId, maxChapters }) => {
    try {
      const r = await engine.get<{
        chapters: { index: number; timecode: string; title: string; summary: string; titleSource: string }[];
        method: string; note: string;
      }>(`/api/items/${encodeURIComponent(itemId)}/chapters?maxChapters=${maxChapters}`);

      const lines = [r.note, ''];
      for (const c of r.chapters) {
        lines.push(`${c.timecode}　${c.title}${c.titleSource === 'timecode' ? '（没挑出标题）' : ''}`);
        if (c.summary) lines.push(`        ${c.summary.slice(0, 100)}`);
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ㉔ 数字回原文校对（D5）
server.registerTool(
  'synorive_check_numbers',
  {
    title: '把一份简报里的数字逐个回原文核对',
    description:
      '简报里出现的每个数字，回到它挂的那条出处原文里找一遍。找不到就标出来。' +
      '这是整条链路上**错得最多、又最没人查**的一环 —— ' +
      '「增长 23%」和「增长 32%」在版面上长得一模一样。\n' +
      '🔴 结论只有三档：ok（原文里找到了）/ unverified（这条出处的正文没拿到，没法查）/ ' +
      'mismatch（正文拿到了但里面没这个数）。**只有 mismatch 值得警惕**。\n' +
      '🔴 mismatch 也不等于写错了 —— 可能原文换了单位或表述。返回里会给出' +
      '"最接近的是哪几个数"，那才是真正能拿去改的信息。\n' +
      '要传 texts（{url: 正文}），可以先用 synorive_read_url 抓。',
    inputSchema: {
      briefing: z.record(z.any()).describe('synorive_research 返回里的 briefing'),
      texts: z.record(z.string()).describe('{出处url: 正文全文}'),
    },
  },
  async ({ briefing, texts }) => {
    try {
      const r = await engine.post<{
        total: number; ok: number; mismatch: number; unverified: number;
        verdict: string; note: string;
        checks: { raw: string; status: string; note: string; sourceUrl: string; context: string }[];
      }>('/api/web/numbers', { briefing, texts });

      const lines = [
        `${r.verdict}（共 ${r.total} 个数字：对上 ${r.ok}、对不上 ${r.mismatch}、没法查 ${r.unverified}）`,
        r.note, '',
      ];
      for (const c of r.checks.filter((x) => x.status === 'mismatch')) {
        lines.push(`⚠️ ${c.raw} —— ${c.note}`);
        lines.push(`    上下文：${c.context}`);
        lines.push(`    出处：${c.sourceUrl}`);
      }
      return text(lines.join('\n'));
    } catch (e) {
      return fail(e);
    }
  },
);

// ── 启动 ────────────────────────────────────────────────────

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // 只能写 stderr：stdout 被 MCP 协议占着
  process.stderr.write('[synorive-mcp] 已就绪\n');
}

for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.on(sig, () => {
    engine.dispose();
    process.exit(0);
  });
}

main().catch((e) => {
  process.stderr.write(`[synorive-mcp] 启动失败：${e}\n`);
  process.exit(1);
});
