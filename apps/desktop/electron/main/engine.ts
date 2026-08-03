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
  /**
   * E15 优先用核显跑推理。
   * 🔴 这个字段以前**没有传给引擎的路径** —— 设置里存着，但既不在
   * `EngineOptions` 里，也没有对应的命令行参数，于是开了等于没开
   */
  enableGpuAcceleration: boolean;
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

  /**
   * 找 Python 解释器 —— 「引擎总是启动失败」的根因就在这个函数的旧版本里。
   *
   * 🔴 **旧版本是必然失败的，不是偶发**，链条很具体：
   *   ① 打包版**没有**自带 Python 运行时（台账明确决定：打进去会让安装包
   *      从 101 MB 涨到约 700 MB，所以走依赖医生按需装）
   *   ② 于是 `resourcesPath/engine/python.exe` 这一步必然找不到
   *   ③ 于是落到最后一行兜底 `return 'python.exe'`，即"用系统 PATH 里的"
   *   ④ 而这台机器 PATH 里的是 **Python 3.9.4**，引擎要 ≥3.11
   *      （`from datetime import UTC` 在 3.9 上直接 ImportError）
   *   ⑤ 就算版本够，那个解释器里也**没装 synorive 包**——包只装在 engine/.venv
   *   → 打包版 100% 起不来，报 `spawn python.exe ENOENT` 或 ModuleNotFoundError
   *
   * 另一半问题在 dev 侧：旧版本写死 `appPath/../../engine/.venv`，
   * 而 `app.getAppPath()` 在**三种启动方式下解析到不同层级**
   * （打包 exe / electron-vite dev / `electron <目录>`，台账坑 45）。
   * 层数写死就意味着换一种启动方式就断。
   *
   * 新版本：**不区分 dev 和打包**，从多个基准点各往上找几层，
   * 命中哪个用哪个。多探几次的成本是几十次 `existsSync`（微秒级），
   * 换来的是"换任何一种启动方式都能找到"。
   */
  private pythonCandidates(): string[] {
    const win = process.platform === 'win32';
    const rel = win ? ['Scripts', 'python.exe'] : ['bin', 'python'];
    const out: string[] = [];
    const push = (p: string) => {
      if (p && !out.includes(p)) out.push(p);
    };

    // ① 显式指定优先级最高 —— 用户/CI 想用哪个就是哪个
    if (process.env.SYNORIVE_PYTHON) push(process.env.SYNORIVE_PYTHON);

    // ② 打包时随应用分发的运行时。**这一档现在是主路径，不是备胎。**
    //    `scripts/bundle-python.mjs` 在构建时把一份 embeddable Python
    //    连同全部核心依赖放进 resources/pyruntime，装完即用 ——
    //    不找系统 Python、不建 venv、不 pip、不联网。
    //
    //    🔴 顺序必须排在仓库 venv 前面：打包版里那些 `resolve(..)` 上溯路径
    //    有可能碰巧命中开发机上的目录（比如从源码目录直接跑打包产物），
    //    那会让"打包版能跑"变成一个只在这台机器上成立的假象
    if (process.resourcesPath) {
      push(join(process.resourcesPath, 'pyruntime', win ? 'python.exe' : 'bin/python3'));
      // 旧布局与手工放置的运行时，留着兼容
      push(join(process.resourcesPath, 'engine', win ? 'python.exe' : 'python3'));
      push(join(process.resourcesPath, 'engine', '.venv', ...rel));
    }

    // ③ 仓库里的 venv。**从四个基准点各往上找四层** ——
    //    哪个基准点有效取决于启动方式，全试一遍最省心
    const bases = [
      app.getAppPath(),
      __dirname,
      process.cwd(),
      app.getPath('exe'),
    ];
    for (const base of bases) {
      for (let up = 0; up <= 4; up++) {
        const prefix = Array<string>(up).fill('..');
        push(resolve(base, ...prefix, 'engine', '.venv', ...rel));
      }
    }

    // ④ 用户数据目录下的 venv —— 打包版首次运行时依赖医生会建在这儿
    push(join(app.getPath('userData'), 'engine-venv', ...rel));

    return out;
  }

  private pythonPath(): string {
    for (const p of this.pythonCandidates()) {
      if (existsSync(p)) return p;
    }
    // 🔴 兜底仍然返回 PATH 里的 python，但**这已经是已知会失败的路径**。
    // 之所以还返回而不是直接抛：万一用户就是全局装了 3.13 + synorive，
    // 那它是能跑的。失败时由 `diagnose()` 说清楚到底卡在哪一环，
    // 而不是甩一个 ENOENT 让人猜。
    return process.platform === 'win32' ? 'python.exe' : 'python3';
  }

  /**
   * 引擎工作目录。跟着解释器走 —— 找到的是哪个 venv，cwd 就该是它的上一级。
   * 两者用不同的解析逻辑，就会出现"解释器找到了但 cwd 指向别处"这种
   * 极难查的状态（症状是 `python -m synorive.main` 报找不到模块）。
   */
  private engineCwd(): string {
    const py = this.pythonPath();
    // …/engine/.venv/Scripts/python.exe → …/engine
    const m = py.replace(/\\/g, '/').match(/^(.*)\/\.venv\/(Scripts|bin)\//);
    if (m?.[1] && existsSync(m[1])) return m[1];

    if (process.resourcesPath && existsSync(join(process.resourcesPath, 'engine'))) {
      return join(process.resourcesPath, 'engine');
    }
    for (const base of [app.getAppPath(), __dirname, process.cwd()]) {
      for (let up = 0; up <= 4; up++) {
        const c = resolve(base, ...Array<string>(up).fill('..'), 'engine');
        if (existsSync(join(c, 'synorive'))) return c;
      }
    }
    return isDev ? resolve(app.getAppPath(), '..', '..', 'engine') : process.cwd();
  }

  /**
   * 启动失败时跑一次诊断，把「到底卡在哪一环」分清楚。
   *
   * **为什么不在启动前就查**：查一次要 spawn 一个 Python 进程读版本、
   * 再 spawn 一次试 import，加起来几百毫秒 —— 加在每次冷启动上，
   * 直接顶在 A1「≤2.0s 可搜索」的头上。而正常情况下这几百毫秒是白花的。
   * 所以只在**真的失败之后**才跑，那时候用户已经在等了，多两秒无所谓，
   * 而一句说得清的原因值得这两秒。
   */
  private diagnose(py: string, cwd: string): string {
    const lines: string[] = [];
    lines.push(`解释器：${py}`);
    lines.push(`工作目录：${cwd}`);

    if (!existsSync(py) && !/[\\/]/.test(py)) {
      lines.push(`❌ 没有在任何已知位置找到 Python，只能退回系统 PATH 里的 "${py}"。`);
    } else if (!existsSync(py)) {
      lines.push(`❌ 这个路径不存在。`);
    }
    if (!existsSync(join(cwd, 'synorive'))) {
      lines.push(`❌ 工作目录下没有 synorive 包 —— 这个目录不是引擎源码目录。`);
    }

    // 版本与包，各花一次同步 spawn。失败时值这个钱
    try {
      const { execFileSync } = require('node:child_process') as typeof import('node:child_process');
      const ver = execFileSync(py, ['-c', 'import sys;print("%d.%d"%sys.version_info[:2])'], {
        encoding: 'utf8',
        timeout: 8000,
        cwd,
      }).trim();
      lines.push(`版本：Python ${ver}`);
      // 解释器坏掉时 execFileSync 可能回一个空串，split 出来就是 undefined。
      // TS 提醒得对：这不是理论风险，正是"引擎起不来"时最可能的状态
      const parts = ver.split('.').map(Number);
      const maj = parts[0] ?? 0;
      const min = parts[1] ?? 0;
      if (maj === 3 && min < 11) {
        lines.push(
          `❌ **版本太低**。引擎要 Python ≥3.11（3.10 及以下没有 datetime.UTC 等 API）。`,
        );
      }
      try {
        execFileSync(py, ['-c', 'import synorive'], { timeout: 15000, cwd, stdio: 'ignore' });
        lines.push('✅ synorive 包能导入。');
      } catch {
        lines.push(
          '❌ **这个解释器里没装 synorive 包**。包只装在 engine/.venv 里，' +
            '全局 Python 是拿不到的。',
        );
      }
    } catch (e) {
      lines.push(`❌ 连版本都读不出来：${(e as Error).message.split('\n')[0]}`);
    }

    lines.push('');
    lines.push('怎么修（在仓库根目录跑）：');
    lines.push('  py -3.13 -m venv engine/.venv');
    lines.push('  engine/.venv/Scripts/python.exe -m pip install -e engine');
    lines.push('或者用环境变量直接指定：set SYNORIVE_PYTHON=<你的python.exe完整路径>');
    return lines.join('\n');
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
    // E15：以前这个设置**根本没传给引擎** —— 设置页开了核显加速，
    // 引擎侧却没有任何地方读它，推理照样在 CPU 上跑。开关只换了
    // onnxruntime 的包，没换实际的执行器，而且完全没有迹象
    if (this.opts.enableGpuAcceleration) args.push('--prefer-gpu');
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
      // 🔴 `spawn python.exe ENOENT` 这五个字对用户毫无意义 ——
      // 他不知道是没装 Python、装了但版本太低、还是装了但没装包。
      // 这三种情况的处置完全不同，必须分清楚了再报
      this.fail(`引擎起不来：${err.message}

${this.diagnose(py, cwd)}`);
    });

    const ok = await this.waitHealthy(port);
    if (!ok) {
      // 超时和 spawn 失败是两种病：spawn 失败是"根本没跑起来"，
      // 超时是"跑起来了但没就绪"（多半是缺依赖或模型在下载）。
      // 但诊断信息对两者都有用，所以一并给
      this.fail(
        `启动超时（${BOOT_TIMEOUT_MS / 1000}s 内没就绪）

${this.diagnose(py, cwd)}`,
      );
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
