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
      const [h, s, deps] = await Promise.all([
        engine.get<Record<string, unknown>>('/health'),
        engine.get<{ items: number; ready: number; failed: number; chunks: number }>('/api/stats'),
        engine
          .get<{ name: string; state: string; optional: boolean; degradesTo: string }[]>(
            '/api/doctor',
          )
          .catch(() => []),
      ]);

      const missing = deps.filter((d) => d.state !== 'ok');
      const lines = [
        `引擎版本 ${h.version}　已运行 ${Math.round(Number(h.uptimeSec) / 60)} 分钟`,
        `索引内容 ${s.items} 条（可搜 ${s.ready}，失败 ${s.failed}），文本块 ${s.chunks} 个`,
        `库文件 ${h.dbSizeMb} MB　并发度 ${h.concurrency}　推理执行器 ${h.executionProvider}`,
        `内存占用 ${h.memoryMb} MB　进行中的任务 ${h.activeJobs} 个`,
      ];
      if (missing.length) {
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
