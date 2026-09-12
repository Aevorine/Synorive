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
  /** 引擎开了 --lan-tls 时是 "https" —— 那时候连回环口也是 HTTPS */
  scheme?: 'http' | 'https';
  /** 自签证书路径（https 时才有）。当 CA 用，不是用来跳过校验的 */
  cert?: string;
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

/**
 * 自签证书下的 fetch —— 只给本机引擎用。
 *
 * 🔴 **为什么不能直接用全局 fetch。**
 *    引擎开了 `--lan-tls` 之后整个监听口都是 HTTPS，回环也不例外，而证书是
 *    自签的。Node 的 fetch（undici）不接受 `ca` 选项，也没有不引新依赖就能
 *    塞 CA 的口子 —— `NODE_EXTRA_CA_CERTS` 只在进程启动那一刻读一次。
 *    所以 https 这一路改走 node:https，把 engine.json 里给出的证书当 CA 传进去。
 *
 * 🔴 **不用 `rejectUnauthorized: false`。** 那样等于谁的证书都认，
 *    而这道 TLS 加上来本来就是为了防中间人。证书的 SAN 里有 127.0.0.1
 *    和 localhost（见 engine/synorive/lan_tls.py），按 CA 正常校验就能过。
 *
 * 只实现调用点真正用到的那几个成员：ok / status / text() / json()。
 */
/** 读 engine.json 指出来的自签证书，读不到就返回 undefined（那时走明文那一路） */
function readCert(ep: Endpoint | null): string | undefined {
  if (!ep?.cert) return undefined;
  try {
    return readFileSync(ep.cert, 'utf8');
  } catch {
    return undefined;
  }
}

async function engineFetch(
  url: string,
  init: RequestInit | undefined,
  ca: string | undefined,
): Promise<{ ok: boolean; status: number; text(): Promise<string>; json(): Promise<unknown> }> {
  if (!url.startsWith('https:') || !ca) {
    return fetch(url, init);
  }
  const { request } = await import('node:https');
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const headers: Record<string, string> = {};
    const h = init?.headers as Record<string, string> | undefined;
    if (h) for (const [k, v] of Object.entries(h)) headers[k] = v;
    const body = init?.body as string | undefined;
    if (body !== undefined) headers['Content-Length'] = String(Buffer.byteLength(body));
    const req = request(
      {
        hostname: u.hostname,
        port: u.port,
        path: u.pathname + u.search,
        method: (init?.method as string) || 'GET',
        headers,
        ca,
        // 证书 CN 是 "Synorive LAN"，匹配靠 SAN。servername 给 localhost，
        // 因为 SAN 里 DNS 项就是它（IP 项另有 127.0.0.1，两条都在）
        servername: 'localhost',
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c: Buffer) => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          const status = res.statusCode ?? 0;
          resolve({
            ok: status >= 200 && status < 300,
            status,
            text: async () => text,
            json: async () => JSON.parse(text) as unknown,
          });
        });
      },
    );
    req.on('error', reject);
    const signal = init?.signal;
    if (signal) signal.addEventListener('abort', () => req.destroy(new Error('超时')), { once: true });
    if (body !== undefined) req.write(body);
    req.end();
  });
}

async function isAlive(url: string, timeoutMs = 2000, ca?: string): Promise<boolean> {
  try {
    const r = await engineFetch(
      `${url}/health`,
      { signal: AbortSignal.timeout(timeoutMs) },
      ca,
    );
    return r.ok;
  } catch {
    return false;
  }
}

export class EngineClient {
  private baseUrl: string | null = null;
  /** 自签证书内容（https 时才有）。发现引擎那一刻一起记下来 */
  private ca: string | undefined;
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
      // 协议由引擎写在 engine.json 里 —— 写死 http 的话，
      // 开了局域网配对之后这里永远连不上桌面端已经起好的那个引擎，
      // 于是每次都去另起一个，两个进程抢同一个库文件
      const url = `${ep.scheme ?? 'http'}://${ep.host || '127.0.0.1'}:${ep.port}`;
      const ca = readCert(ep);
      if (await isAlive(url, 2000, ca)) {
        this.baseUrl = url;
        this.ca = ca;
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

  async call<T>(path: string, init?: RequestInit): Promise<T> {
    const base = await this.url();
    const r = await engineFetch(
      `${base}${path}`,
      {
        ...init,
        headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
        signal: AbortSignal.timeout(120_000),
      },
      this.ca,
    );
    if (!r.ok) {
      const text = await r.text().catch(() => '');
      throw new Error(`引擎返回 ${r.status}：${text.slice(0, 400)}`);
    }
    return (await r.json()) as T;
  }

  post<T>(path: string, body: unknown): Promise<T> {
    return this.call<T>(path, { method: 'POST', body: JSON.stringify(body) });
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
