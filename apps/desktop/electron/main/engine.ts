/**
 * Python 引擎进程托管
 * ============================================================
 * 「使用时不卡顿」这条要求的根本实现就在这里：所有分析计算跑在
 * **另一个进程**里，Electron 主线程和渲染线程一帧都不参与。
 * 引擎再忙，界面该 60fps 还是 60fps —— 这不是优化出来的，是架构决定的。
 *
 * 职责：
 *   ① 找到 Python（开发用 venv，打包后用随包的运行时）
 *   ② 挑一个空闲端口起 FastAPI
 *   ③ 健康检查等就绪，超时算失败
 *   ④ 崩了自动重启（指数退避 + 次数上限，绝不无限重试）
 *   ⑤ 用 WebSocket 接引擎推来的实时事件，转发给渲染层
 */

import { app } from 'electron';
import { type ChildProcess, spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type { EngineProcessState } from '../shared/ipc-contract.js';
import { startRegistering, stopRegistering } from './render.js';

const isDev = !app.isPackaged;

/** 启动超时：本机冷启动含模型探测，给足 45 秒 */
const BOOT_TIMEOUT_MS = 45_000;
/** 健康检查轮询间隔 */
const HEALTH_POLL_MS = 100;
/** 最多自动重启几次 —— 到顶就停手报错，不无限重试 */
const MAX_RESTARTS = 5;

type Listener = (state: EngineProcessState) => void;
type EventListener = (event: unknown) => void;

export interface EngineLaunchOptions {
  dataDir: string;
  modelDir: string;
  concurrency: number;
  /** 隐私围栏：允许把内容送云端（云端简报生成 R8、图片描述 C4 都受它管） */
  allowCloud: boolean;
  /** C4：图片详细描述——还要 allowCloud 为真且设置页配好了视觉模型才会真的调用 */
  enableImageDescription: boolean;
  /** C5：本地人脸检测与聚类，默认关 */
  enableFaceClustering: boolean;
  /** A16：开了就把引擎的监听地址从 127.0.0.1 换成 0.0.0.0，局域网里的安卓端才连得上 */
  lanPairingEnabled: boolean;
  /** 局域网配对令牌，非本机请求必须带这个（见 --pairing-token） */
  pairingToken: string;
  /**
   * E12/U9 联网搜索总闸。**和 allowCloud 是两回事，别合并** ——
   * 这个发出去的是查询词（"我在查什么"），那个发出去的是资料原文（"我有什么"）。
   * 关掉后引擎不建 MetaSearch，整条联网链路从根上断掉，
   * 而不是靠界面藏按钮 —— 藏按钮挡不住 MCP 和 CLI
   */
  allowNetwork: boolean;
  /** S1 每轮最多派几家引擎。0 = 全派 */
  webLineupSize: number;
  /** V 组核查档位：annotate / counter / claim */
  verifyLevel: string;
  /** 引擎的非密钥配置（自建 SearXNG 地址等）+ 从 safeStorage 解出来的 API Key */
  webKeys: Record<string, string>;
  /** 启用哪几家引擎。空 = 用各家默认开关 */
  webEngines: string[];
  /** V5 可信度权重（JSON 串，为空则用默认档） */
  trustProfile: string;
}

export class EngineManager {
  private child: ChildProcess | null = null;
  private ws: WebSocket | null = null;
  private state: EngineProcessState = {
    lifecycle: 'stopped',
    pid: null,
    port: null,
    bootMs: null,
    restartCount: 0,
    lastError: null,
    detail: null,
  };

  private stateListeners = new Set<Listener>();
  private eventListeners = new Set<EventListener>();
  private stopping = false;
  private restartTimer: NodeJS.Timeout | null = null;

  constructor(private readonly opts: EngineLaunchOptions) {}

  // ── 对外 ────────────────────────────────────────────────

  getState(): EngineProcessState {
    return { ...this.state };
  }

  onStateChange(fn: Listener): () => void {
    this.stateListeners.add(fn);
    return () => this.stateListeners.delete(fn);
  }

  onEngineEvent(fn: EventListener): () => void {
    this.eventListeners.add(fn);
    return () => this.eventListeners.delete(fn);
  }

  async start(): Promise<void> {
    if (this.child) return;
    this.stopping = false;
    await this.spawnOnce();
  }

  async restart(): Promise<void> {
    this.state.restartCount = 0;
    await this.stop();
    await this.start();
  }

  async stop(): Promise<void> {
    this.stopping = true;
    stopRegistering();
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    this.ws?.close();
    this.ws = null;

    const child = this.child;
    this.child = null;
    if (!child) {
      this.patch({ lifecycle: 'stopped', pid: null, port: null });
      return;
    }

    // 先好好说，3 秒不走再强杀
    child.kill('SIGTERM');
    await new Promise<void>((res) => {
      const t = setTimeout(() => {
        try {
          child.kill('SIGKILL');
        } catch {
          /* 已经没了 */
        }
        res();
      }, 3000);
      child.once('exit', () => {
        clearTimeout(t);
        res();
      });
    });

    this.patch({ lifecycle: 'stopped', pid: null, port: null, detail: null });
  }

  // ── 内部 ────────────────────────────────────────────────

  private patch(p: Partial<EngineProcessState>): void {
    this.state = { ...this.state, ...p };
    const snapshot = this.getState();
    for (const fn of this.stateListeners) fn(snapshot);
  }

  private pythonPath(): string {
    if (process.env.SYNORIVE_PYTHON) return process.env.SYNORIVE_PYTHON;

    if (isDev) {
      const venv = resolve(app.getAppPath(), '..', '..', 'engine', '.venv', 'Scripts', 'python.exe');
      if (existsSync(venv)) return venv;
      const venvNix = resolve(app.getAppPath(), '..', '..', 'engine', '.venv', 'bin', 'python');
      if (existsSync(venvNix)) return venvNix;
    }

    // 打包后：随应用一起分发的运行时
    const bundled = join(process.resourcesPath, 'engine', 'python.exe');
    if (existsSync(bundled)) return bundled;

    return process.platform === 'win32' ? 'python.exe' : 'python3';
  }

  private engineCwd(): string {
    return isDev
      ? resolve(app.getAppPath(), '..', '..', 'engine')
      : join(process.resourcesPath, 'engine');
  }

  private async freePort(): Promise<number> {
    return new Promise((res, rej) => {
      const srv = createServer();
      srv.once('error', rej);
      srv.listen(0, '127.0.0.1', () => {
        const addr = srv.address();
        const port = typeof addr === 'object' && addr ? addr.port : 0;
        srv.close(() => (port ? res(port) : rej(new Error('拿不到空闲端口'))));
      });
    });
  }

  private async spawnOnce(): Promise<void> {
    const t0 = Date.now();
    this.patch({ lifecycle: 'starting', lastError: null });

    let port: number;
    try {
      port = await this.freePort();
    } catch (err) {
      this.fail(`挑端口失败：${String(err)}`);
      return;
    }

    const py = this.pythonPath();
    const cwd = this.engineCwd();
    // A16：局域网配对没开就只听本机，安卓端连不上也碰不到——这是默认状态；
    // 开了才换成 0.0.0.0，同时必须带 --pairing-token，见下面的中间件校验
    const host = this.opts.lanPairingEnabled ? '0.0.0.0' : '127.0.0.1';
    const args = [
      '-m',
      'synorive.main',
      '--host',
      host,
      '--port',
      String(port),
      '--data-dir',
      this.opts.dataDir,
      '--model-dir',
      this.opts.modelDir,
      '--concurrency',
      String(this.opts.concurrency),
    ];
    // 🔴 之前这几个开关只存在于 settings.json 里，从没被真正传给引擎——
    // 用户在设置页打开"云端增强"或"人脸检测与聚类"，实际上什么都不会发生，
    // 因为 EngineConfig 对应的字段永远是构造函数默认值 False。
    // 界面上的开关变了却没有连到后端，是最容易被忽略的一类"半成品功能"。
    if (this.opts.allowCloud) args.push('--allow-cloud');
    if (this.opts.enableImageDescription) args.push('--enable-image-description');
    if (this.opts.enableFaceClustering) args.push('--enable-face-clustering');
    if (this.opts.lanPairingEnabled) args.push('--pairing-token', this.opts.pairingToken);

    // 联网这一路：**关掉时显式传 --no-network**，而不是"什么都不传靠默认值"。
    // 引擎侧的默认是开着的（这是它的主要用途之一），
    // 靠默认值意味着以后任何一处默认值变动都会把这道隐私闸悄悄打开
    if (!this.opts.allowNetwork) args.push('--no-network');
    if (this.opts.webLineupSize > 0) args.push('--web-lineup', String(this.opts.webLineupSize));
    if (this.opts.verifyLevel) args.push('--verify-level', this.opts.verifyLevel);
    if (this.opts.webEngines.length) args.push('--web-engines', this.opts.webEngines.join(','));
    if (this.opts.trustProfile) args.push('--trust-profile', this.opts.trustProfile);
    for (const [id, v] of Object.entries(this.opts.webKeys)) {
      if (v) args.push('--web-key', `${id}=${v}`);
    }

    // 令牌和 API Key 都是密钥，日志里必须打码 ——
    // 照抄 args 会把它们明文写进控制台和日志文件
    const loggedArgs = args.map((a, i) =>
      args[i - 1] === '--pairing-token' || args[i - 1] === '--web-key' ? '***' : a,
    );
    console.log(`[engine] 启动 ${py} ${loggedArgs.join(' ')}  (cwd=${cwd})`);

    const child = spawn(py, args, {
      cwd,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
        // 别让 BLAS 抢线程 —— 并发度由我们自己的进程池控制
        OMP_NUM_THREADS: '1',
      },
      windowsHide: true,
    });

    this.child = child;
    this.patch({ pid: child.pid ?? null, port });

    child.stdout?.on('data', (b: Buffer) => {
      for (const line of b.toString('utf8').split('\n')) {
        if (line.trim()) console.log(`[engine] ${line.trimEnd()}`);
      }
    });
    child.stderr?.on('data', (b: Buffer) => {
      for (const line of b.toString('utf8').split('\n')) {
        if (line.trim()) console.warn(`[engine!] ${line.trimEnd()}`);
      }
    });

    child.once('exit', (code, signal) => {
      console.warn(`[engine] 进程退出 code=${code} signal=${signal}`);
      this.ws?.close();
      this.ws = null;
      stopRegistering(); // 旧端口已经死了，别再对着它心跳
      if (this.child === child) this.child = null;
      if (!this.stopping) this.scheduleRestart(`引擎退出（code=${code}）`);
    });

    child.once('error', (err) => {
      this.fail(`启动失败：${err.message}`);
    });

    const ok = await this.waitHealthy(port);
    if (!ok) {
      this.fail(`启动超时（${BOOT_TIMEOUT_MS / 1000}s 内没就绪）`);
      try {
        child.kill('SIGKILL');
      } catch {
        /* noop */
      }
      return;
    }

    this.patch({ lifecycle: 'ready', bootMs: Date.now() - t0 });
    console.log(`[engine] 就绪，耗时 ${Date.now() - t0}ms，端口 ${port}`);
    this.connectEvents(port);
    // 端口每次启动都会变，必须重新告诉引擎渲染服务在哪 ——
    // 这一步失败不影响引擎其余功能，Google/Yandex 会老实报"渲染不可用"
    startRegistering(port);
  }

  private async waitHealthy(port: number): Promise<boolean> {
    const deadline = Date.now() + BOOT_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (this.stopping) return false;
      try {
        const r = await fetch(`http://127.0.0.1:${port}/health`, {
          signal: AbortSignal.timeout(1500),
        });
        if (r.ok) {
          this.patch({ detail: await r.json().catch(() => null) });
          return true;
        }
      } catch {
        /* 还没起来，继续等 */
      }
      await new Promise((r) => setTimeout(r, HEALTH_POLL_MS));
    }
    return false;
  }

  /** 用 WebSocket 接引擎推来的实时事件（Node 22+ 自带 WebSocket，不用装 ws） */
  private connectEvents(port: number): void {
    try {
      const ws = new WebSocket(`ws://127.0.0.1:${port}/events`);
      this.ws = ws;

      ws.addEventListener('message', (ev) => {
        try {
          const parsed = JSON.parse(String(ev.data)) as { type?: string; payload?: unknown };

          // 引擎每 2 秒推一次状态，合进 detail 里让状态栏保持实时。
          // 不这么做的话状态栏永远显示引擎启动那一刻的快照 ——
          // 索引了 19 条还写着「已索引 1 条」，用户会以为索引没生效。
          if (parsed?.type === 'engine.status' && parsed.payload) {
            this.patch({ detail: parsed.payload });
          }

          for (const fn of this.eventListeners) fn(parsed);
        } catch {
          /* 非 JSON 就丢掉，不该发生 */
        }
      });

      ws.addEventListener('close', () => {
        if (this.ws === ws) this.ws = null;
        // 引擎还活着但事件通道断了 → 降级，不重启整个进程
        if (!this.stopping && this.state.lifecycle === 'ready') {
          this.patch({ lifecycle: 'degraded', lastError: '事件通道断开' });
          setTimeout(() => {
            if (!this.stopping && this.child) this.connectEvents(port);
          }, 2000);
        }
      });

      ws.addEventListener('open', () => {
        if (this.state.lifecycle === 'degraded') {
          this.patch({ lifecycle: 'ready', lastError: null });
        }
      });
    } catch (err) {
      console.warn('[engine] 事件通道连接失败：', err);
    }
  }

  private scheduleRestart(reason: string): void {
    if (this.state.restartCount >= MAX_RESTARTS) {
      this.fail(`${reason}；已重启 ${MAX_RESTARTS} 次仍不稳定，停止自动重启`);
      return;
    }
    const n = this.state.restartCount + 1;
    // 指数退避：1s, 2s, 4s, 8s, 16s
    const delay = Math.min(16_000, 1000 * 2 ** (n - 1));
    this.patch({ lifecycle: 'restarting', restartCount: n, lastError: reason });
    console.warn(`[engine] ${reason}，${delay}ms 后第 ${n} 次重启`);

    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      if (!this.stopping) void this.spawnOnce();
    }, delay);
  }

  private fail(msg: string): void {
    console.error(`[engine] ${msg}`);
    this.patch({ lifecycle: 'failed', lastError: msg, pid: null });
  }
}
