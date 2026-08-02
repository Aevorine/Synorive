#!/usr/bin/env node
/**
 * synorive 命令行
 * ============================================================
 * 给脚本、定时任务、以及不走 MCP 的其它工具用。和 MCP 服务器共用
 * 同一套引擎发现逻辑（读 data/engine.json），所以桌面端开着的时候
 * 它连的是同一个引擎、同一个库，不会各起一个抢文件。
 *
 * 用法：
 *   synorive search "中文分词" [-n 10] [--json]
 *   synorive search "type:pdf date:最近7天 预算"
 *   synorive add <路径...> [--tag 重要]
 *   synorive status
 *   synorive doctor [--install <id>]
 *   synorive open <id>
 *   synorive timeline [--bucket month]
 */

import { EngineClient } from './engine-client.js';

const engine = new EngineClient();

// ── 输出小工具 ──────────────────────────────────────────────

const C = {
  dim: (s: string) => `\x1b[2m${s}\x1b[0m`,
  bold: (s: string) => `\x1b[1m${s}\x1b[0m`,
  cyan: (s: string) => `\x1b[36m${s}\x1b[0m`,
  green: (s: string) => `\x1b[32m${s}\x1b[0m`,
  yellow: (s: string) => `\x1b[33m${s}\x1b[0m`,
  red: (s: string) => `\x1b[31m${s}\x1b[0m`,
};

// 管道输出（synorive search x | grep y）时不要带颜色码
const tty = process.stdout.isTTY;
const c = tty ? C : new Proxy(C, { get: () => (s: string) => s });

function fmtSize(n?: number | null): string {
  if (!n) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)}KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)}MB`;
  return `${(n / 1024 ** 3).toFixed(2)}GB`;
}

function fmtTime(sec: number): string {
  return `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
}

function die(msg: string): never {
  process.stderr.write(c.red(`✗ ${msg}\n`));
  process.exit(1);
}

// ── 参数解析（不引第三方库，就这几个 flag）──────────────────

interface Args {
  _: string[];
  flags: Record<string, string | boolean>;
}

function parseArgs(argv: string[]): Args {
  const out: Args = { _: [], flags: {} };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith('-')) {
        out.flags[key] = next;
        i++;
      } else {
        out.flags[key] = true;
      }
    } else if (a.startsWith('-') && a.length === 2) {
      const key = a.slice(1);
      const next = argv[i + 1];
      if (next && !next.startsWith('-')) {
        out.flags[key] = next;
        i++;
      } else {
        out.flags[key] = true;
      }
    } else {
      out._.push(a);
    }
  }
  return out;
}

// ── 命令 ────────────────────────────────────────────────────

interface Hit {
  item: {
    id: string;
    title: string;
    locator: string;
    modality: string;
    sizeBytes?: number | null;
    contentTime?: string | null;
  };
  score: number;
  highlight?: string;
  location?: { page?: number; startSec?: number };
  explain?: { reason?: string };
}

async function cmdSearch(args: Args): Promise<void> {
  const query = args._.join(' ');
  if (!query) die('要搜什么？用法：synorive search "中文分词"');

  const limit = Number(args.flags.n ?? args.flags.limit ?? 10);
  const r = await engine.post<{
    hits: Hit[];
    totalEstimate: number;
    elapsedMs: number;
    parsedQuery?: { text: string; filters: string[] };
  }>('/api/search', {
    query,
    limit,
    explain: true,
    preset: String(args.flags.preset ?? 'balanced'),
  });

  if (args.flags.json) {
    process.stdout.write(JSON.stringify(r, null, 2) + '\n');
    return;
  }

  if (r.parsedQuery?.filters?.length) {
    process.stdout.write(c.dim(`筛选：${r.parsedQuery.filters.join('；')}\n`));
  }
  process.stdout.write(c.dim(`${r.totalEstimate} 条，${r.elapsedMs}ms\n\n`));

  if (!r.hits.length) {
    process.stdout.write(
      c.yellow('没搜到。试试换个说法，或者用 --preset semantic 提高语义权重。\n'),
    );
    return;
  }

  for (const [i, h] of r.hits.entries()) {
    const bits: string[] = [];
    if (h.location?.page != null) bits.push(`第${h.location.page}页`);
    if (h.location?.startSec != null) bits.push(fmtTime(h.location.startSec));
    const size = fmtSize(h.item.sizeBytes);
    if (size) bits.push(size);
    if (h.item.contentTime) bits.push(h.item.contentTime.slice(0, 10));

    process.stdout.write(
      `${c.dim(String(i + 1).padStart(2))} ${c.bold(h.item.title)}` +
        (bits.length ? c.dim(`  [${bits.join(' · ')}]`) : '') +
        c.dim(`  ${h.score.toFixed(4)}\n`),
    );
    process.stdout.write(`   ${c.cyan(h.item.locator)}\n`);
    // 摘要里可能带换行 —— 不折叠的话后续行会顶到最左边，把缩进对齐全毁掉
    const snip = (h.highlight ?? '')
      .replace(/<em>/g, tty ? '\x1b[33m' : '')
      .replace(/<\/em>/g, tty ? '\x1b[0m' : '')
      .replace(/\s+/g, ' ')
      .trim();
    if (snip) process.stdout.write(`   ${snip.slice(0, 200)}\n`);
    if (h.explain?.reason) process.stdout.write(c.dim(`   ${h.explain.reason}\n`));
    process.stdout.write(c.dim(`   id ${h.item.id}\n\n`));
  }
}

async function cmdAdd(args: Args): Promise<void> {
  if (!args._.length) die('要加什么？用法：synorive add D:\\项目\\文档');
  const tags = args.flags.tag ? String(args.flags.tag).split(',') : undefined;
  const r = await engine.post<{ jobId: string }>('/api/ingest', {
    targets: args._,
    recursive: args.flags['no-recursive'] ? false : true,
    tags,
    source: 'api',
  });
  process.stdout.write(c.green(`✓ 已开始索引 ${args._.length} 个目标\n`));
  process.stdout.write(c.dim(`  任务号 ${r.jobId}，用 synorive status 看进度\n`));
}

async function cmdStatus(args: Args): Promise<void> {
  const [h, s] = await Promise.all([
    engine.get<Record<string, unknown>>('/health'),
    engine.get<{ items: number; ready: number; failed: number; chunks: number }>('/api/stats'),
  ]);
  if (args.flags.json) {
    process.stdout.write(JSON.stringify({ health: h, stats: s }, null, 2) + '\n');
    return;
  }
  process.stdout.write(
    `${c.bold('Synorive')} ${h.version}  ${c.dim(`已运行 ${Math.round(Number(h.uptimeSec) / 60)} 分钟`)}\n\n` +
      `  索引内容   ${c.bold(String(s.items))} 条  ${c.dim(`(可搜 ${s.ready}，失败 ${s.failed})`)}\n` +
      `  文本块     ${s.chunks}\n` +
      `  库文件     ${h.dbSizeMb} MB\n` +
      `  并发度     ${h.concurrency}\n` +
      `  推理执行器 ${h.executionProvider}\n` +
      `  内存占用   ${h.memoryMb} MB\n` +
      `  进行中任务 ${h.activeJobs}\n`,
  );
}

async function cmdDoctor(args: Args): Promise<void> {
  if (args.flags.install) {
    const id = String(args.flags.install);
    await engine.post(`/api/doctor/${id}/install`, {});
    process.stdout.write(c.green(`✓ 已开始安装 ${id}（后台进行，再跑一次 doctor 看结果）\n`));
    return;
  }
  const deps = await engine.get<
    { id: string; name: string; state: string; optional: boolean; degradesTo: string }[]
  >('/api/doctor');
  if (args.flags.json) {
    process.stdout.write(JSON.stringify(deps, null, 2) + '\n');
    return;
  }
  for (const d of deps) {
    const mark =
      d.state === 'ok' ? c.green('✓') : d.state === 'failed' ? c.red('✗') : c.dim('·');
    process.stdout.write(
      `${mark} ${d.name.padEnd(42)} ${c.dim(d.state)}${d.optional ? '' : c.yellow('  必需')}\n`,
    );
    if (d.state !== 'ok') {
      process.stdout.write(c.dim(`    缺了会：${d.degradesTo}\n`));
      process.stdout.write(c.dim(`    装它：synorive doctor --install ${d.id}\n`));
    }
  }
}

async function cmdOpen(args: Args): Promise<void> {
  const id = args._[0];
  if (!id) die('要打开哪条？用法：synorive open <id>');
  const item = await engine.get<{ locator: string; title: string }>(`/api/items/${id}`);
  await engine.post(`/api/items/${id}/open`, {});
  const { spawn } = await import('node:child_process');
  const cmd = process.platform === 'win32' ? 'explorer' : 'xdg-open';
  spawn(cmd, [item.locator], { detached: true, stdio: 'ignore' }).unref();
  process.stdout.write(c.green(`✓ 已打开 ${item.title}\n`));
}

async function cmdTimeline(args: Args): Promise<void> {
  const bucket = String(args.flags.bucket ?? 'month');
  const rows = await engine.get<{ at: string; count: number }[]>(
    `/api/timeline?bucket=${bucket}&limit=48`,
  );
  if (!rows.length) {
    process.stdout.write('库里还没有带时间的内容。\n');
    return;
  }
  const max = Math.max(...rows.map((r) => r.count));
  for (const r of rows) {
    const bar = '█'.repeat(Math.max(1, Math.round((r.count / max) * 32)));
    process.stdout.write(
      `${r.at.padEnd(12)} ${String(r.count).padStart(6)}  ${c.cyan(bar)}\n`,
    );
  }
}

function usage(): void {
  process.stdout.write(`${c.bold('synorive')} —— 本地内容库命令行

  ${c.cyan('synorive search')} <查询词> [-n 10] [--preset semantic] [--json]
      搜索。查询词里可以直接写筛选指令：
        type:pdf   date:>2026-01   date:最近7天   size:>10mb
        in:D:\\项目   tag:重要   src:link   -排除词   "精确短语"

  ${c.cyan('synorive add')} <路径...> [--tag 重要] [--no-recursive]
      把文件或目录加入索引

  ${c.cyan('synorive status')} [--json]        看引擎状态与索引进度
  ${c.cyan('synorive doctor')} [--install <id>] 看缺什么依赖、一键装
  ${c.cyan('synorive open')} <id>             用系统默认程序打开某条内容
  ${c.cyan('synorive timeline')} [--bucket month]  按时间看内容分布

  ${c.dim('引擎地址会自动发现（读 data/engine.json）。')}
  ${c.dim('桌面端开着时连的是同一个引擎，没开就自己起一个。')}
  ${c.dim('也可以用 SYNORIVE_ENGINE_URL 显式指定。')}
`);
}

// ── 入口 ────────────────────────────────────────────────────

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  const cmd = argv[0];
  const args = parseArgs(argv.slice(1));

  if (!cmd || cmd === 'help' || cmd === '--help' || cmd === '-h') {
    usage();
    return;
  }

  try {
    switch (cmd) {
      case 'search':
      case 's':
        await cmdSearch(args);
        break;
      case 'add':
      case 'a':
        await cmdAdd(args);
        break;
      case 'status':
        await cmdStatus(args);
        break;
      case 'doctor':
        await cmdDoctor(args);
        break;
      case 'open':
        await cmdOpen(args);
        break;
      case 'timeline':
        await cmdTimeline(args);
        break;
      default:
        die(`不认识的命令 "${cmd}"。跑 synorive help 看用法`);
    }
  } catch (e) {
    die(e instanceof Error ? e.message : String(e));
  } finally {
    engine.dispose();
  }
}

void main();
