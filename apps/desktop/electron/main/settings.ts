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
import {
  buildDefaultLibraryEntry,
  overlayLibraryScopedSettings,
  pickLibraryScopedFields,
  sanitizeSettings,
  splitSettingsPatch,
} from './settings-schema.js';

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
    // 多库支持：空注册表只会在"从没跑过 loadSettings()"这一瞬间存在——
    // loadSettings() 发现空数组就会立刻迁移出一条「默认库」并写回磁盘，
    // 界面永远看不到这个空状态。放在 defaultSettings() 里只是给
    // sanitizeSettings 一个语义正确的"合法但空"的回退值。
    libraries: [],
    activeLibraryId: '',
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
    // 空 = 用内置的按使用频率排的顺序。用户拖过之后才写具体值
    navOrder: [],
    uiScale: 100,
    // 空 = 用内置默认键（Alt+Space / Ctrl+Alt+S）
    hotkeys: { focusSearch: '', screenshot: '' },
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
    // 默认开：投喂目录时自动跳过 .env/私钥/credentials.json 这类文件，
    // 不让它们悄悄进搜索库。关掉这道闸是有意的例外操作，不该是默认状态
    sensitiveGuardEnabled: true,
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

/**
 * 每个库自己的"库级字段"配置文件——不在主 settings.json 里。
 * 放在这个库自己的 dataDir 下：库目录本身就是这个库的完整边界，
 * 拷走整个 dataDir 就带走了这个库的数据和它自己的隐私/排序策略。
 */
function libraryConfigPath(dataDir: string): string {
  return join(dataDir, 'library-settings.json');
}

/** 读一个 JSON 文件；不存在或损坏都当成"空对象"，交给 sanitizeSettings 逐字段回退默认值。 */
function readJsonFile(p: string): unknown {
  if (!existsSync(p)) return {};
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return {};
  }
}

/** 原子写：先 .tmp 再 rename，避免写一半断电留半截 JSON。settings.json 和 library-settings.json 共用。 */
function writeJsonAtomic(p: string, data: unknown): void {
  mkdirSync(dirname(p), { recursive: true });
  const tmp = `${p}.tmp`;
  writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
  renameSync(tmp, p);
}

let cache: AppSettings | null = null;

export function loadSettings(): AppSettings {
  if (cache) return cache;
  const p = settingsPath();
  const base = defaultSettings();
  const rawGlobal = readJsonFile(p);

  // 逐字段校验 + 合并：新版本加的字段老配置里没有，自动补默认值；
  // 字段类型/范围不对（比如 concurrency 被手改成 999、dataDir 被清空）
  // 只丢那一个字段，不因为一处损坏就把整份配置打回默认值
  const { settings: globalSettings, dropped: globalDropped } = sanitizeSettings(rawGlobal, base);
  if (globalDropped.length > 0) {
    console.warn('[settings] 配置文件里这些字段不合法，已各自回退默认值：', globalDropped);
  }

  let libraries = globalSettings.libraries;
  let activeLibraryId = globalSettings.activeLibraryId;
  // 只有两种情况需要把迁移结果写回磁盘：① 老用户升级上来 libraries 是空的，
  // 需要补一条默认库 ② activeLibraryId 指向的库已经不存在了（比如配置文件
  // 被手改，或者上次进程异常退出时库刚好被删）。两种都不该每次启动都重算一遍。
  let needsPersist = false;

  if (libraries.length === 0) {
    // 首次运行 / 老用户升级：拿当前的 dataDir 建一条「默认库」，
    // 把散在主配置里的"库级字段"原样搬进它自己的 library-settings.json——
    // 不能让老用户升级完发现自己的监听目录/隐私策略/排序预设全部消失
    const entry = buildDefaultLibraryEntry(globalSettings.dataDir, new Date().toISOString());
    libraries = [entry];
    activeLibraryId = entry.id;
    writeJsonAtomic(libraryConfigPath(entry.dataDir), pickLibraryScopedFields(globalSettings));
    needsPersist = true;
  }

  let activeEntry = libraries.find((l) => l.id === activeLibraryId);
  if (!activeEntry) {
    // activeLibraryId 指向一个不存在的库——兜底落回注册表第一条，
    // 不能让整个应用因为这一个字段对不上就起不来
    activeEntry = libraries[0]!;
    activeLibraryId = activeEntry.id;
    needsPersist = true;
  }

  const globalWithRegistry: AppSettings = { ...globalSettings, libraries, activeLibraryId, dataDir: activeEntry.dataDir };
  const rawLibrary = readJsonFile(libraryConfigPath(activeEntry.dataDir));
  const { settings: merged, dropped: libraryDropped } = overlayLibraryScopedSettings(
    globalWithRegistry,
    rawLibrary,
    base,
  );
  if (libraryDropped.length > 0) {
    console.warn('[settings] 库配置文件里这些字段不合法，已各自回退默认值：', libraryDropped);
  }

  cache = merged;
  if (needsPersist) {
    writeJsonAtomic(p, merged);
  }
  return cache;
}

export function patchSettings(patch: Partial<AppSettings>): AppSettings {
  // 保证库注册表已经完成过首次迁移（libraries/activeLibraryId 不是空的）——
  // 正常流程里 index.ts 启动时已经调过一次 loadSettings()，这里是防御性的：
  // 万一有代码路径没经过那次启动调用就直接 patch，不能让迁移被跳过。
  loadSettings();

  // 唯一的分流入口：库级字段（监听目录/隐私策略/排序预设……）进库自己的文件，
  // 其余全局字段（主题/窗口/并发度/更新……）还是进主 settings.json
  const { libraryPatch, globalPatch } = splitSettingsPatch(patch);

  const p = settingsPath();
  // 关键：先读盘上的最新版本再合并，不用内存里的旧快照
  const onDisk = readJsonFile(p) as Partial<AppSettings>;

  // patch 是 IPC 传进来的（渲染层调 `settings.patch()`）——同样可能带非法值，
  // IPC 边界就是外部输入边界，不能因为"是自己代码调的"就默认可信。
  // 先按原来的优先级（默认 < 磁盘 < 本次 patch）拼成一份原始对象，
  // 再统一过一遍 sanitizeSettings，语义和以前的三层展开等价，只是加了校验。
  const defaults = defaultSettings();
  const rawGlobalMerged = {
    ...onDisk,
    ...globalPatch,
    cloud: { ...(onDisk.cloud ?? {}), ...(globalPatch.cloud ?? {}) },
  };
  const { settings: mergedGlobal, dropped: globalDropped } = sanitizeSettings(rawGlobalMerged, defaults);
  if (globalDropped.length > 0) {
    console.warn('[settings] 本次改动里这些字段不合法，已各自回退默认值：', globalDropped);
  }

  // 库级字段的目标是"这次 patch 生效后"的 dataDir——`library.switchTo` 会在
  // 同一次 patch 里把 activeLibraryId 和 dataDir 一起改掉，这里读写的必须是
  // 新库的文件，不是旧库的，否则切库时旧库的设置会被错误地写进新库里。
  const targetDataDir = mergedGlobal.dataDir;
  const libraryPath = libraryConfigPath(targetDataDir);
  const onDiskLibrary = readJsonFile(libraryPath) as Partial<AppSettings>;
  const rawLibraryMerged = { ...onDiskLibrary, ...libraryPatch };
  const { settings: merged, dropped: libraryDropped } = overlayLibraryScopedSettings(
    mergedGlobal,
    rawLibraryMerged,
    defaults,
  );
  if (libraryDropped.length > 0) {
    console.warn('[settings] 本次改动里这些库级字段不合法，已各自回退默认值：', libraryDropped);
  }

  writeJsonAtomic(p, mergedGlobal);
  writeJsonAtomic(libraryPath, pickLibraryScopedFields(merged));

  cache = merged;
  return merged;
}

export function ensureDataDirs(s: AppSettings): void {
  for (const d of [s.dataDir, s.modelDir, join(s.dataDir, 'thumbs'), join(s.dataDir, 'archive')]) {
    mkdirSync(d, { recursive: true });
  }
}
