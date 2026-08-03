/**
 * 找到引擎并跟它说话
 * ============================================================
 * 引擎的端口是每次启动动态挑的，所以不能写死地址。发现顺序：
 *   ① 环境变量 SYNORIVE_ENGINE_URL（用户显式指定）
 *   ② data 目录下的 engine.json（桌面端拉起的那个引擎写的）
 *   ③ 都没有 → 自己起一个（Claude Code 单独用、桌面端没开的情况）
 *
 * ② 优先于 ③ 是有意的：桌面端已经开着引擎时再起一个，
 * 两个进程会抢同一个 SQLite 文件，虽然 WAL 撑得住，但白白多占几百 MB 内存。
 */

import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

interface Endpoint {
  port: number;
  host: string;
  pid: number;
  dataDir: string;
  startedAt: number;
}

/** 候选的 data 目录。顺序 = 优先级。 */
function dataDirCandidates(): string[] {
  const out: string[] = [];
  if (process.env.SYNORIVE_DATA_DIR) out.push(process.env.SYNORIVE_DATA_DIR);
  // 开发时：仓库根的 data/
  out.push(resolve(__dirname, '..', '..', 'data'));
  // 安装后：用户数据目录
  out.push(join(homedir(), 'AppData', 'Roaming', 'Synorive', 'data'));
  out.push(join(homedir(), '.synorive', 'data'));
  return out;
}

function readEndpoint(): Endpoint | null {
  for (const dir of dataDirCandidates()) {
    const f = join(dir, 'engine.json');
    if (!existsSync(f)) continue;
    try {
      return JSON.parse(readFileSync(f, 'utf8')) as Endpoint;
    } catch {
      /* 文件写了一半或损坏，试下一个 */
    }
  }
  return null;
}

async function isAlive(url: string, timeoutMs = 2000): Promise<boolean> {
  try {
    const r = await fetch(`${url}/health`, { signal: AbortSignal.timeout(timeoutMs) });
    return r.ok;
  } catch {
    return false;
  }
}

export class EngineClient {
  private baseUrl: string | null = null;
  private child: ChildProcess | null = null;
  private connecting: Promise<string> | null = null;

  /** 拿到可用的 base url。幂等，多次调用只连一次。 */
  async url(): Promise<string> {
    if (this.baseUrl && (await isAlive(this.baseUrl))) return this.baseUrl;
    this.baseUrl = null;
    if (!this.connecting) {
      this.connecting = this.connect().finally(() => {
        this.connecting = null;
      });
    }
    return this.connecting;
  }

  private async connect(): Promise<string> {
    // ① 用户显式指定
    const explicit = process.env.SYNORIVE_ENGINE_URL;
    if (explicit) {
      if (await isAlive(explicit)) {
        this.baseUrl = explicit.replace(/\/$/, '');
        return this.baseUrl;
      }
      throw new Error(`SYNORIVE_ENGINE_URL=${explicit} 连不上`);
    }

    // ② 桌面端已经拉起来的引擎
    const ep = readEndpoint();
    if (ep) {
      const url = `http://${ep.host || '127.0.0.1'}:${ep.port}`;
      if (await isAlive(url)) {
        this.baseUrl = url;
        return url;
      }
    }

    // ③ 自己起一个
    return this.spawnEngine();
  }

  private async spawnEngine(): Promise<string> {
    const repoRoot = resolve(__dirname, '..', '..');
    const engineDir = join(repoRoot, 'engine');
    const py =
      process.env.SYNORIVE_PYTHON ||
      (existsSync(join(engineDir, '.venv', 'Scripts', 'python.exe'))
        ? join(engineDir, '.venv', 'Scripts', 'python.exe')
        : existsSync(join(engineDir, '.venv', 'bin', 'python'))
          ? join(engineDir, '.venv', 'bin', 'python')
          : process.platform === 'win32'
            ? 'python.exe'
            : 'python3');

    const dataDir = process.env.SYNORIVE_DATA_DIR || join(repoRoot, 'data');
    const port = 8700 + Math.floor(Math.random() * 800);

    const child = spawn(
      py,
      [
        '-m', 'synorive.main',
        '--host', '127.0.0.1',
        '--port', String(port),
        '--data-dir', dataDir,
      ],
      {
        cwd: engineDir,
        stdio: ['ignore', 'ignore', 'pipe'],
        env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
        windowsHide: true,
      },
    );
    this.child = child;

    // 引擎的日志不能走 stdout —— MCP 的 stdio 传输占着 stdout，
    // 混进去一行日志整个协议就废了。所以引擎 stdout 直接丢弃，
    // 只把 stderr 转到我们自己的 stderr 供排查。
    let stderrTail = '';
    child.stderr?.on('data', (b: Buffer) => {
      const s = b.toString('utf8');
      stderrTail = (stderrTail + s).slice(-2000);
      process.stderr.write(`[engine] ${s}`);
    });

    const url = `http://127.0.0.1:${port}`;
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      if (await isAlive(url, 1000)) {
        this.baseUrl = url;
        return url;
      }
      if (child.exitCode !== null) {
        throw new Error(
          `引擎启动失败（退出码 ${child.exitCode}）。最后的错误输出：\n${stderrTail.slice(-600)}`,
        );
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    throw new Error('引擎 60 秒内没就绪。用 SYNORIVE_ENGINE_URL 指定一个已在运行的引擎试试');
  }

  /**
   * `timeoutMs` 必须能按调用点单独给。
   * 本地检索几十毫秒就回来了，而联网研究要搜五家再抓五篇正文，
   * 一个 120 秒的固定值对前者太松、对后者又不够 ——
   * 不够的那一头症状是"Claude Code 那边报超时，而引擎其实还在正常跑"。
   */
  async call<T>(path: string, init?: RequestInit, timeoutMs = 120_000): Promise<T> {
    const base = await this.url();
    const r = await fetch(`${base}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!r.ok) {
      const text = await r.text().catch(() => '');
      throw new Error(`引擎返回 ${r.status}：${text.slice(0, 400)}`);
    }
    return (await r.json()) as T;
  }

  post<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
    return this.call<T>(path, { method: 'POST', body: JSON.stringify(body) }, timeoutMs);
  }

  get<T>(path: string): Promise<T> {
    return this.call<T>(path);
  }

  /** 只有我们自己起的引擎才关掉；桌面端的那个不能碰。 */
  dispose(): void {
    if (this.child && this.child.exitCode === null) {
      this.child.kill();
    }
    this.child = null;
  }
}
