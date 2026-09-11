/**
 * U 组 · 桌面端应用自更新
 * ====================================================================
 * 走 electron-updater + GitHub Releases。设计上只有三条硬规矩：
 *
 * 1. **只查不装。** `autoDownload = false` + `autoInstallOnAppQuit = false`。
 *    检查是自动的，下载和重启安装永远要用户点。在用户干活的时候
 *    悄悄下 100MB 然后重启应用，是这类功能最常见也最招人恨的做法。
 *
 * 2. **便携版要明说不支持，而不是报错。** portable exe 是单文件自解压，
 *    运行时的自己在临时目录里，electron-updater 原地替换不了。
 *    把这种情况归进 `error`，用户会一直点重试；归进 `unsupported`
 *    并给出下载页链接，他两秒钟就知道该干什么。
 *
 * 3. **失败不能是静默的。** 网络不通、仓库还是私有的、Release 里
 *    没有 latest.yml —— 这三种在日志里长得都像 404。全都要把原文
 *    塞进 `state.error` 让界面显示出来，否则就是"点了检查更新没反应"。
 *
 * ── 这条链路的安全边界，说清楚而不是含糊过去 ──────────────
 *
 * **保护它的是三样东西**：① 全程 HTTPS 拿 GitHub 的 latest.yml 和安装包；
 * ② electron-updater 用 latest.yml 里的 sha512 校验下载下来的 exe，对不上就拒绝装；
 * ③ 更新源写死在代码里（`setFeedURL`）+ 下载地址白名单 —— 安装目录里那份
 *    明文 `app-update.yml` 被改掉也劫持不了这条链路。见下面 FEED 的注释。
 *
 * **它没有的是代码签名。** 我们 `signExecutable: false`（没有代码签名证书），
 * 于是 `publisherName` 是空的，electron-updater 的 Authenticode 校验那一步
 * **会被跳过**。后果有两条，都要如实知道：
 *   · 用户装的时候 Windows SmartScreen 会弹"未知发布者"警告，这是正常的
 *   · 能改写 HTTPS 内容的攻击者（拿到 GitHub 账号 / 或 CA 层被攻破）可以
 *     推一个恶意版本，我们这边**没有第二道闸**能拦住
 * 要补这个洞只有一条路：买代码签名证书，然后在这里配上 `publisherName`。
 * 那是花钱的事，得你点头 —— 在此之前，不要在文档里把它说成"安全更新"。
 *
 * （安卓那边的第二道闸是天然有的：签名对不上的 APK 系统根本不让覆盖安装。）
 *
 * ⚠️ 打包侧的配套（缺一个更新链路就是死的）：
 *    · `electron-builder.yml` 的 `publish:` 必须指向真实仓库
 *    · 仓库必须是**公开**的，或者应用里内嵌 token（我们选前者）
 *    · NSIS 目标会生成 `latest.yml`，**这个文件必须一起传到 Release**
 *      —— 只传 exe 不传 latest.yml 的话，更新器永远查不到新版，
 *      而且它报的是"已是最新"，不是报错。
 */

import { app } from 'electron';
import electronUpdater from 'electron-updater';
import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { UpdateState } from '../shared/ipc-contract.js';

// electron-updater 是 CJS 包，而主进程打成 ESM。
// 具名导入（import { autoUpdater } from …）在这个组合下拿到的是 undefined，
// 且**不报错**——症状是运行时 "Cannot read properties of undefined"。
// 必须先默认导入整个模块再解构。
const { autoUpdater } = electronUpdater;

type Listener = (s: UpdateState) => void;

/**
 * 🔴 **更新源写死在代码里，不读 `app-update.yml`。**
 *
 * `resources/app-update.yml` 是 asar **外面**的一个明文文件，而
 * `electron-builder.yml` 里 `perMachine: false` —— 装到的是
 * `%LOCALAPPDATA%\Programs\Synorive`，那个目录当前用户可写。
 * 任何以这个用户身份跑起来的程序（包括他自己下的一个小工具）
 * 把那份 yml 改成 `provider: generic / url: https://攻击者/feed/`，
 * 用户下次点「检查更新」装上的就是攻击者的 exe。
 * 而我们 `signExecutable: false`，没有 Authenticode 这道兜底闸。
 *
 * `setFeedURL()` 的优先级高于 app-update.yml，写死之后那份文件
 * 被改成什么样都不再影响这条链路。
 *
 * ⚠️ owner/repo 必须和 `electron-builder.yml` 的 `publish:` 一致，
 *    也和下面 `releaseUrlFor()` / `DOWNLOAD_PAGE` 一致。三处改一处就是坏的。
 */
const FEED = { provider: 'github' as const, owner: 'Aevorine', repo: 'Synorive' };

/**
 * 允许从哪些主机下载安装包。
 *
 * GitHub Releases 的实际下载会从 `github.com` 302 到
 * `objects.githubusercontent.com` / `release-assets.githubusercontent.com`，
 * 所以两类都要放行；除此之外一律拒绝。
 * 这是第二道闸：万一 feed 那一层还是被绕过去了（比如 electron-updater
 * 自身某天改了优先级），下载地址仍然出不了 GitHub。
 */
const ALLOWED_UPDATE_HOSTS: RegExp[] = [
  /^github\.com$/i,
  /^api\.github\.com$/i,
  /^(?:[a-z0-9-]+\.)*githubusercontent\.com$/i,
];

/**
 * 检查一个下载地址是否可信。
 * @returns null = 可信；否则是**说得出口的**拒绝原因（会进 state.error）
 */
function rejectUpdateUrl(raw: string): string | null {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    // 解析不出来说明它是相对路径（latest.yml 里的 `url:` 多数是文件名），
    // 那它会拼在我们钉死的 feed 上，本身不构成新的信任来源
    return null;
  }
  if (u.protocol !== 'https:') {
    return `更新包地址不是 HTTPS（${u.protocol}//${u.host}），已拒绝。`;
  }
  if (!ALLOWED_UPDATE_HOSTS.some((re) => re.test(u.hostname))) {
    return `更新包地址指向 ${u.hostname}，不在允许的下载源里（只认 github.com / *.githubusercontent.com），已拒绝。`;
  }
  return null;
}

/**
 * 更新日志。
 *
 * 🔴 原来是 `autoUpdater.logger = null` —— 托盘常驻是默认行为，
 *    这时候自动检查失败连界面都没有，日志再关掉就**事后完全查不了**。
 *    换成写进应用自己的日志文件；写不进去（磁盘满、目录只读）也不能
 *    让更新流程炸掉，所以每一处都吞异常。
 */
function updateLogPath(): string {
  return join(app.getPath('logs'), 'updater.log');
}

function writeLog(level: string, args: unknown[]): void {
  const line =
    `[${new Date().toISOString()}] ${level} ` +
    args.map((a) => (a instanceof Error ? `${a.message}\n${a.stack ?? ''}` : String(a))).join(' ');
  try {
    mkdirSync(app.getPath('logs'), { recursive: true });
    appendFileSync(updateLogPath(), line + '\n', 'utf8');
  } catch {
    /* 日志写不进去不该让更新失败 */
  }
  if (level === 'ERROR' || level === 'WARN') console.warn(`[updater] ${line}`);
}

const fileLogger = {
  info: (...a: unknown[]) => writeLog('INFO', a),
  warn: (...a: unknown[]) => writeLog('WARN', a),
  error: (...a: unknown[]) => writeLog('ERROR', a),
  debug: (...a: unknown[]) => writeLog('DEBUG', a),
};

/** 便携版由 electron-builder 在运行时注入这个环境变量 */
function portableDir(): string | undefined {
  return process.env.PORTABLE_EXECUTABLE_DIR;
}

function detectUnsupported(): string | null {
  if (!app.isPackaged) {
    return '开发模式下不检查更新（跑的是源码，没有可替换的安装包）。';
  }
  if (portableDir()) {
    return '这是便携版（portable exe），它没法替换正在运行的自己。' +
      '点下面的「打开下载页」下载新的便携版，替换掉当前这个文件即可——你的数据和设置都在别处，不会丢。';
  }
  return null;
}

export class UpdateManager {
  private state: UpdateState;
  private listeners = new Set<Listener>();
  /** 防止重复点「检查更新」时并发发起 */
  private inFlight = false;
  /**
   * 这一版的下载地址被白名单拒了的原因。非空时 `download()` 直接不干活 ——
   * 光在界面上写一行红字是不够的，按钮还能点就还是会有人点。
   */
  private blockedReason: string | null = null;

  constructor(skippedVersion: string | null) {
    const unsupportedReason = detectUnsupported();
    this.state = {
      lifecycle: unsupportedReason ? 'unsupported' : 'idle',
      currentVersion: app.getVersion(),
      latestVersion: null,
      releaseNotes: null,
      releaseUrl: null,
      progressPercent: 0,
      bytesPerSecond: 0,
      transferredBytes: 0,
      totalBytes: 0,
      lastCheckedAt: null,
      error: null,
      unsupportedReason,
      skippedVersion,
    };

    if (!unsupportedReason) this.wire();
  }

  // ── 对外 ────────────────────────────────────────────────

  getState(): UpdateState {
    return { ...this.state };
  }

  onChange(cb: Listener): void {
    this.listeners.add(cb);
  }

  setSkippedVersion(v: string | null): void {
    this.patch({ skippedVersion: v });
  }

  /**
   * 查一次。
   * @param silent true = 启动时的自动检查，失败不弹任何东西、只记进状态
   */
  async check(silent = false): Promise<void> {
    if (this.state.lifecycle === 'unsupported') return;
    // 已经下好了就别再查了——再查一次不会有任何新信息，
    // 反而会把 lifecycle 从 downloaded 打回 checking，界面上那个
    // 「重启安装」按钮会凭空消失
    if (this.state.lifecycle === 'downloading' || this.state.lifecycle === 'downloaded') return;
    if (this.inFlight) return;

    this.inFlight = true;
    this.patch({ lifecycle: 'checking', error: null });
    try {
      const r = await autoUpdater.checkForUpdates();
      // checkForUpdates() 在"已是最新"时也返回一个结果对象，
      // 真正判定靠的是 update-available / update-not-available 事件（在 wire() 里）。
      // 这里只兜底一种情况：事件一个都没来（网络层直接挂了但没抛异常）
      if (!r && this.state.lifecycle === 'checking') {
        this.patch({ lifecycle: 'up-to-date', lastCheckedAt: new Date().toISOString() });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.patch({
        lifecycle: 'error',
        error: silent ? `自动检查更新失败：${msg}` : msg,
        lastCheckedAt: new Date().toISOString(),
      });
    } finally {
      this.inFlight = false;
    }
  }

  async download(): Promise<void> {
    if (this.state.lifecycle !== 'available') return;
    // 白名单拒绝过的版本，这里是最后一道闸：不下载、把原因原样留在界面上
    if (this.blockedReason) {
      this.patch({ lifecycle: 'error', error: this.blockedReason });
      return;
    }
    this.patch({ lifecycle: 'downloading', progressPercent: 0, error: null });
    try {
      await autoUpdater.downloadUpdate();
    } catch (err) {
      this.patch({
        lifecycle: 'error',
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  /** ⚠️ 立刻退出应用。调用方必须已经跟用户确认过。 */
  install(): void {
    if (this.state.lifecycle !== 'downloaded') return;
    // 第一个参数 isSilent=false：让 NSIS 安装界面显示出来。
    // 静默安装在 Windows 上遇到 UAC 会**卡在一个看不见的弹窗上**，
    // 用户看到的现象是"应用退出了但没装上"。
    autoUpdater.quitAndInstall(false, true);
  }

  // ── 内部 ────────────────────────────────────────────────

  private wire(): void {
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = false;
    // 允许 0.1.x 这类 0 开头的版本正常比较；也允许从预发布版升到正式版
    autoUpdater.allowPrerelease = false;
    autoUpdater.logger = fileLogger;

    // 🔴 必须在任何 checkForUpdates() 之前调用。见 FEED 上面那段说明：
    //    这一行的作用是让 resources/app-update.yml 被改掉也没用。
    autoUpdater.setFeedURL(FEED);

    autoUpdater.on('update-available', (info) => {
      // 白名单：latest.yml 里给的每一个下载地址都要过一遍。
      // 只要有一个出了 GitHub，这一版整个不认 —— 不是"跳过那个文件继续下"
      const files: Array<{ url?: string }> = Array.isArray(info.files) ? info.files : [];
      const blocked =
        files.map((f) => rejectUpdateUrl(String(f.url ?? ''))).find((r) => r !== null) ?? null;
      this.blockedReason = blocked;
      if (blocked) {
        fileLogger.error('拒绝这一版的下载源：', blocked);
        this.patch({
          lifecycle: 'error',
          latestVersion: info.version,
          lastCheckedAt: new Date().toISOString(),
          error: blocked,
        });
        return;
      }
      this.patch({
        lifecycle: 'available',
        latestVersion: info.version,
        releaseNotes: typeof info.releaseNotes === 'string' ? info.releaseNotes : null,
        releaseUrl: releaseUrlFor(info.version),
        lastCheckedAt: new Date().toISOString(),
        error: null,
      });
    });

    autoUpdater.on('update-not-available', (info) => {
      this.patch({
        lifecycle: 'up-to-date',
        latestVersion: info?.version ?? this.state.currentVersion,
        lastCheckedAt: new Date().toISOString(),
        error: null,
      });
    });

    autoUpdater.on('download-progress', (p) => {
      this.patch({
        lifecycle: 'downloading',
        progressPercent: Math.round(p.percent),
        bytesPerSecond: Math.round(p.bytesPerSecond),
        transferredBytes: p.transferred,
        totalBytes: p.total,
      });
    });

    autoUpdater.on('update-downloaded', (info) => {
      this.patch({
        lifecycle: 'downloaded',
        latestVersion: info.version,
        progressPercent: 100,
      });
    });

    autoUpdater.on('error', (err) => {
      this.patch({
        lifecycle: 'error',
        error: explain(err instanceof Error ? err.message : String(err)),
      });
    });
  }

  private patch(p: Partial<UpdateState>): void {
    this.state = { ...this.state, ...p };
    const snapshot = this.getState();
    for (const cb of this.listeners) cb(snapshot);
  }
}

/**
 * 把 electron-updater 那些只有作者看得懂的报错翻译成人话。
 * 不翻译的话，用户看到的是 `HttpError: 404 Not Found` 这种
 * 既不知道哪错了、也不知道该找谁的东西。
 */
function explain(raw: string): string {
  if (/404/.test(raw)) {
    return `查不到更新信息（404）。常见原因：仓库还是私有的、还没发过 Release、` +
      `或者 Release 里漏传了 latest.yml。原文：${raw}`;
  }
  if (/ENOTFOUND|ECONNREFUSED|ETIMEDOUT|network/i.test(raw)) {
    return `连不上 GitHub。检查网络或代理后再试一次。原文：${raw}`;
  }
  if (/signature|code sign/i.test(raw)) {
    return `安装包签名校验没过。这个包可能不是从官方 Release 下来的，先别装。原文：${raw}`;
  }
  return raw;
}

/** Release 页地址。owner/repo 和 electron-builder.yml 里的 publish 必须一致 */
function releaseUrlFor(version: string): string {
  return `https://github.com/Aevorine/Synorive/releases/tag/v${version}`;
}

export const DOWNLOAD_PAGE = 'https://github.com/Aevorine/Synorive/releases/latest';
