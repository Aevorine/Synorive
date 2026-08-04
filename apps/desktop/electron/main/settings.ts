/**
 * 设置持久化
 * ============================================================
 * 写盘策略：合并式写入 + 原子替换。
 *   ① 先读盘上的最新版本再合并本次改动，不拿内存里的旧快照整体覆盖
 *      —— 多实例并存时"后写盘的用旧快照盖掉新的"是踩过的坑。
 *   ② 先写 .tmp 再 rename，避免写一半断电留下半截 JSON。
 *
 * 密钥不存这里（H3）：云端 API key 走系统凭据管理器，
 * 这里只存一个引用键 credentialKey。
 */

import { app } from 'electron';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import type { AppSettings } from '@synorive/shared-types';

const isDev = !app.isPackaged;

/** 仓库根目录（开发时用来把 data/ 放在项目旁边） */
function repoRoot(): string {
  // 开发时 __dirname 指向 out/main，往上四层到 apps/desktop 再往上两层到仓库根
  return resolve(app.getAppPath(), '..', '..');
}

function defaultDataDir(): string {
  const fromEnv = process.env.SYNORIVE_DATA_DIR;
  if (fromEnv) return fromEnv;
  return isDev ? join(repoRoot(), 'data') : join(app.getPath('userData'), 'data');
}

export function defaultSettings(): AppSettings {
  const dataDir = defaultDataDir();
  return {
    theme: 'system',
    // F1-b：正文宋体 + 标题思源宋体
    fontScheme: 'b',
    eyeComfort: 'off',
    eyeReminderMinutes: 0,
    density: 'standard',
    // A2：默认落在「今日」。它的全部价值就是"打开就有东西看"，
    // 不做启动页的话没人会主动点进去，那这一页等于没做
    startPage: 'today',
    // A3：默认「问一句话」。找文件仍然一键可切，且输入内容不丢
    defaultInputMode: 'ask',
    // B7：默认不钉任何东西 —— 钉什么是用户自己的使用习惯，猜不出来
    pinnedNav: [],
    // D1：默认没有自存预设，内置那五个（均衡/求准/求全/看最近/深读）够用了
    savedPresets: [],
    // A5：默认不归属任何项目。**不自动建一个「默认项目」** ——
    // 那会让第一次打开的人对着一个他没建过、也不知道是干嘛的东西
    activeProjectId: null,
    // C3：默认开。关掉它是排查手段，不是常规选项
    offloadHeavyWork: true,
    // M5：默认 CPU 核数 - 1，可 1~16 调
    concurrency: Math.max(1, Math.min(16, (navigatorHardwareConcurrency() || 8) - 1)),
    runInTray: true,
    launchAtLogin: true,
    rerankResults: false,
    clipboardSentinel: true,
    // 默认关。自动落盘这件事必须由你明确打开，不能靠默认值替你决定。
    clipboardAutoArchiveLinks: false,
    // N7 随手研究浮窗：默认关。每次复制都弹窗是敌意，用户得先明确说要
    clipboardPeek: false,
    // 浮窗联网：再单独一道闸。只查本地几十毫秒不出网，联网要几秒还泄露查询词
    clipboardPeekWeb: false,
    watchedFolders: [],
    dataDir,
    modelDir: join(dataDir, 'models'),
    cloud: {
      enabled: false,
      provider: 'none',
      dailyBudget: null,
    },
    // 隐私敏感，默认关，用户要自己在设置里开
    enableFaceClustering: false,
    enableAuthenticatedFetch: false,
    // 会把图片发去云端做描述，默认关且还要 cloud.enabled 一起打开才生效
    enableImageDescription: false,
    // 默认走 CPU；依赖医生里有"启用核显加速"按钮
    enableGpuAcceleration: false,
    // A16 安卓配对：默认关，开了才会让引擎监听局域网
    lanPairingEnabled: false,
    // 令牌一开机就生成好（哪怕配对功能没开），开配对时不用现等一次生成
    pairingToken: randomBytes(16).toString('hex'),

    // ── E12 隐私围栏 / 联网搜索 ────────────────────────────
    // 默认**开**：这是这个软件的主要用途之一，默认关掉等于装完发现半个
    // 功能是灰的。它和 cloud.enabled 分开——那个默认关，因为它发出去的
    // 是你的资料原文，性质完全不同
    allowNetwork: true,
    // 0 = 全部派出。排班要先有历史数据才有意义，冷启动时全派反而收敛更快
    webLineupSize: 0,
    verifyLevel: 'counter',
    webEndpoints: {
      // 自建实例的默认地址。scripts/setup-searxng.mjs 起的就是这个端口
      searxng: 'http://127.0.0.1:8888',
    },
    webEngines: [],

    // ── U 组 应用自更新 ───────────────────────────────────
    // 默认**开**：它只发一个"最新版本号是多少"的请求，不含任何用户内容。
    // 默认关掉的话，绝大多数人永远停在装机那天的版本 —— 修好的 bug
    // 和补上的安全问题都到不了他手上，那比这一个请求的隐私成本大得多。
    // **它不受 allowNetwork 管**：那个开关管的是"把我的查询词发出去"。
    autoCheckUpdate: true,
  };
}

function navigatorHardwareConcurrency(): number {
  try {
    // 主进程里没有 navigator，用 os.cpus()
    return require('node:os').cpus().length as number;
  } catch {
    return 8;
  }
}

function settingsPath(): string {
  return join(app.getPath('userData'), 'settings.json');
}

let cache: AppSettings | null = null;

export function loadSettings(): AppSettings {
  if (cache) return cache;
  const p = settingsPath();
  const base = defaultSettings();

  if (!existsSync(p)) {
    cache = base;
    return cache;
  }

  try {
    const raw = JSON.parse(readFileSync(p, 'utf8')) as Partial<AppSettings>;
    // 逐字段合并：新版本加的字段老配置里没有，要能自动补上默认值
    cache = { ...base, ...raw, cloud: { ...base.cloud, ...(raw.cloud ?? {}) } };
  } catch (err) {
    console.error('[settings] 配置文件损坏，回退默认值：', err);
    cache = base;
  }
  return cache;
}

export function patchSettings(patch: Partial<AppSettings>): AppSettings {
  const p = settingsPath();

  // 关键：先读盘上的最新版本再合并，不用内存里的旧快照
  let onDisk: Partial<AppSettings> = {};
  if (existsSync(p)) {
    try {
      onDisk = JSON.parse(readFileSync(p, 'utf8')) as Partial<AppSettings>;
    } catch {
      /* 损坏就当空的，下面会用默认值补齐 */
    }
  }

  const merged: AppSettings = {
    ...defaultSettings(),
    ...onDisk,
    ...patch,
    cloud: { ...defaultSettings().cloud, ...(onDisk.cloud ?? {}), ...(patch.cloud ?? {}) },
  };

  mkdirSync(dirname(p), { recursive: true });
  // 原子写：先 .tmp 再 rename，避免写一半断电留半截 JSON
  const tmp = `${p}.tmp`;
  writeFileSync(tmp, JSON.stringify(merged, null, 2), 'utf8');
  renameSync(tmp, p);

  cache = merged;
  return merged;
}

export function ensureDataDirs(s: AppSettings): void {
  for (const d of [s.dataDir, s.modelDir, join(s.dataDir, 'thumbs'), join(s.dataDir, 'archive')]) {
    mkdirSync(d, { recursive: true });
  }
}
