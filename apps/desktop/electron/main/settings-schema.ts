/**
 * 设置的运行时校验
 * ============================================================
 * settings.ts 之前是 `JSON.parse(...)` 完直接 `{ ...default, ...raw }` 展开合并，
 * 没有任何运行时校验——磁盘上的 settings.json 只要是合法 JSON，字段类型/范围
 * 全错也会被原样接受（`concurrency: 999`、`dataDir: ""`、`allowNetwork: "abc"`
 * 这类值理论上都能进系统）。这个文件不改合并策略，只加一道"字段级"校验：
 * 逐个字段过 zod，某个字段不合法就丢掉它、退回默认值，其它合法字段照常生效
 * ——不是"整份配置只要有一个字段错就全部作废"那种粗粒度校验。
 */

import { z } from 'zod';
import type { AppSettings, CloudConfig, LibraryEntry } from '@synorive/shared-types';

const RankingWeightsSchema = z.object({
  semantic: z.number(),
  keyword: z.number(),
  recency: z.number(),
  sourceTrust: z.number(),
  popularity: z.number(),
  titleBoost: z.number(),
  diversity: z.number(),
  lengthPenalty: z.number(),
});

const SavedRankingPresetSchema = z.object({
  id: z.string(),
  name: z.string(),
  weights: RankingWeightsSchema,
});

const TrustProfileConfigSchema = z.object({
  tierWeights: z.record(z.string(), z.number()).optional(),
  multiSourceBonus: z.number().optional(),
  loneSourcePenalty: z.number().optional(),
  farmPenalty: z.number().optional(),
  aiPenalty: z.number().optional(),
  staleDays: z.number().optional(),
  stalePenalty: z.number().optional(),
  rankWeight: z.number().optional(),
  overrides: z.record(z.string(), z.string()).optional(),
  blocklist: z.array(z.string()).optional(),
});

const LibraryEntrySchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  dataDir: z.string().min(1),
  createdAt: z.string(),
});

export const CloudConfigSchema = z.object({
  enabled: z.boolean(),
  provider: z.enum(['none', 'openai-compatible', 'anthropic']),
  baseUrl: z.string().optional(),
  credentialKey: z.string().optional(),
  chatModel: z.string().optional(),
  visionModel: z.string().optional(),
  dailyBudget: z.number().nonnegative().nullable().optional(),
});

export const AppSettingsSchema = z.object({
  libraries: z.array(LibraryEntrySchema),
  activeLibraryId: z.string(),
  theme: z.enum(['light', 'dark', 'paper', 'system']),
  fontScheme: z.enum(['a', 'b', 'c']),
  eyeComfort: z.enum(['off', 'low', 'medium', 'high']),
  eyeReminderMinutes: z.number().int().min(0),
  density: z.enum(['compact', 'standard', 'comfortable']),
  startPage: z.enum(['today', 'search']),
  defaultInputMode: z.enum(['ask', 'find']),
  pinnedNav: z.array(z.string()),
  // 🔴 新加的字段一律 .optional()：老 settings.json 里没有它们，
  //    不给 optional 会让整份配置校验失败，表现是"升级后设置全被重置成默认"
  navOrder: z.array(z.string()).optional(),
  uiScale: z.union([z.literal(100), z.literal(125), z.literal(150)]).optional(),
  hotkeys: z
    .object({ focusSearch: z.string().optional(), screenshot: z.string().optional() })
    .optional(),
  savedPresets: z.array(SavedRankingPresetSchema),
  activeProjectId: z.string().nullable(),
  offloadHeavyWork: z.boolean(),
  // M5 注释里写的范围就是 1~16，不是随口一提——引擎按这个数开线程池
  concurrency: z.number().int().min(1).max(16),
  runInTray: z.boolean(),
  launchAtLogin: z.boolean(),
  rerankResults: z.boolean(),
  clipboardSentinel: z.boolean(),
  clipboardAutoArchiveLinks: z.boolean(),
  clipboardPeek: z.boolean(),
  clipboardPeekWeb: z.boolean(),
  watchedFolders: z.array(z.string()),
  dataDir: z.string().min(1),
  modelDir: z.string().min(1),
  cloud: CloudConfigSchema,
  enableFaceClustering: z.boolean(),
  enableAuthenticatedFetch: z.boolean(),
  enableImageDescription: z.boolean(),
  enableGpuAcceleration: z.boolean(),
  sensitiveGuardEnabled: z.boolean(),
  lanPairingEnabled: z.boolean(),
  pairingToken: z.string().min(1),
  allowNetwork: z.boolean(),
  webLineupSize: z.number().int().min(0),
  verifyLevel: z.enum(['annotate', 'counter', 'claim']),
  webEndpoints: z.record(z.string(), z.string()),
  webEngines: z.array(z.string()),
  trustProfile: TrustProfileConfigSchema.optional(),
  autoCheckUpdate: z.boolean(),
  skippedUpdateVersion: z.string().optional(),
});

/**
 * 静态类型对齐检查——不用 `z.ZodType<AppSettings>` 直接标注
 * `AppSettingsSchema` 的类型：那样会把它抹成抽象的 `ZodType`，丢掉
 * `.shape`（下面 `sanitizeSettings` 逐字段遍历要用）。改用两条不占运行时
 * 开销的类型断言：`AppSettings` 加字段/改类型而这里没跟上，
 * `npm run typecheck` 会在这两行报错，而不是"校验默默漏掉新字段"这种
 * 静默 bug。
 */
type _AssertExtends<A, B> = A extends B ? true : never;
const _appSettingsSchemaMatchesType: _AssertExtends<z.infer<typeof AppSettingsSchema>, AppSettings> = true;
const _appSettingsTypeMatchesSchema: _AssertExtends<AppSettings, z.infer<typeof AppSettingsSchema>> = true;
const _cloudConfigSchemaMatchesType: _AssertExtends<z.infer<typeof CloudConfigSchema>, CloudConfig> = true;
const _cloudConfigTypeMatchesSchema: _AssertExtends<CloudConfig, z.infer<typeof CloudConfigSchema>> = true;
void _appSettingsSchemaMatchesType;
void _appSettingsTypeMatchesSchema;
void _cloudConfigSchemaMatchesType;
void _cloudConfigTypeMatchesSchema;

/**
 * 对一个"部分字段可能非法"的原始对象逐字段校验：
 * - 字段不合法 → 丢掉，退回 `base` 里的值，记进 `dropped`
 * - 字段没出现在 `raw` 里 → 保留 `base` 的值（新版本加的字段/被删掉的字段都这样处理）
 * - 字段合法 → 用校验后的值
 *
 * 不用 `schema.safeParse(raw)` 整体校验——那样只要有一个字段错，
 * 整个对象都会被判定失败，等于其它 99 个合法字段陪葬。
 */
function sanitizeObjectFields<T extends Record<string, unknown>>(
  shape: Record<string, z.ZodTypeAny>,
  raw: unknown,
  base: T,
): { value: T; dropped: string[] } {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    return { value: base, dropped: [] };
  }
  const input = raw as Record<string, unknown>;
  const result: Record<string, unknown> = { ...base };
  const dropped: string[] = [];
  for (const key of Object.keys(shape)) {
    if (!(key in input)) continue;
    const fieldSchema = shape[key];
    if (!fieldSchema) continue;
    const check = fieldSchema.safeParse(input[key]);
    if (check.success) {
      result[key] = check.data;
    } else {
      dropped.push(key);
    }
  }
  return { value: result as T, dropped };
}

export function sanitizeSettings(
  raw: unknown,
  base: AppSettings,
): { settings: AppSettings; dropped: string[] } {
  const top = sanitizeObjectFields(
    AppSettingsSchema.shape as unknown as Record<string, z.ZodTypeAny>,
    raw,
    base as unknown as Record<string, unknown>,
  );
  const rawCloud =
    raw !== null && typeof raw === 'object' ? (raw as Record<string, unknown>).cloud : undefined;
  const cloud = sanitizeObjectFields(
    CloudConfigSchema.shape as unknown as Record<string, z.ZodTypeAny>,
    rawCloud,
    base.cloud as unknown as Record<string, unknown>,
  );
  return {
    settings: { ...(top.value as unknown as AppSettings), cloud: cloud.value as unknown as CloudConfig },
    dropped: [...top.dropped, ...cloud.dropped.map((k) => `cloud.${k}`)],
  };
}

/**
 * 多库支持 —— 分流逻辑
 * ============================================================
 * 审查报告要求"每个库独立配置隐私策略和排序权重"。这些字段的值不再
 * 由主 settings.json 一份说了算，而是跟着"当前激活的库"走，存在
 * `<该库 dataDir>/library-settings.json` 里。
 *
 * 这一段全部是纯函数（不碰文件系统、不碰 electron 的 `app` 模块）——
 * 和上面 `sanitizeSettings` 同一个理由：这个仓库的 vitest 配置刻意把
 * `electron/main/**` 的测试收纳范围限定成"不 import electron 模块的纯函数"
 * （见 `vitest.config.ts` 注释），真正的文件读写留在 `settings.ts` 里，
 * 那边会调用这里的纯函数、自己负责 fs 那一半。
 */
export const LIBRARY_SCOPED_KEYS = [
  'watchedFolders',
  'savedPresets',
  'allowNetwork',
  'webLineupSize',
  'verifyLevel',
  'webEndpoints',
  'webEngines',
  'trustProfile',
  'enableFaceClustering',
  'enableAuthenticatedFetch',
  'enableImageDescription',
  'clipboardAutoArchiveLinks',
  'sensitiveGuardEnabled',
  'activeProjectId',
] as const satisfies readonly (keyof AppSettings)[];

export type LibraryScopedKey = (typeof LIBRARY_SCOPED_KEYS)[number];

const LIBRARY_SCOPED_KEY_SET = new Set<string>(LIBRARY_SCOPED_KEYS);

export function isLibraryScopedKey(key: string): key is LibraryScopedKey {
  return LIBRARY_SCOPED_KEY_SET.has(key);
}

/**
 * 把一份 patch 拆成"库级字段"和"全局字段"两份，`settings.ts` 拿这两份
 * 分别去合并、校验、写回各自的文件。**这是唯一的分流入口** —— 全仓
 * 只有这一处判断"这个字段该进哪个文件"，不许散落 if 判断（否则新增一个
 * 库级字段时很容易漏改其中一处）。
 */
export function splitSettingsPatch(
  patch: Partial<AppSettings>,
): { libraryPatch: Partial<AppSettings>; globalPatch: Partial<AppSettings> } {
  const libraryPatch: Record<string, unknown> = {};
  const globalPatch: Record<string, unknown> = {};
  for (const key of Object.keys(patch)) {
    const target = isLibraryScopedKey(key) ? libraryPatch : globalPatch;
    target[key] = (patch as Record<string, unknown>)[key];
  }
  return {
    libraryPatch: libraryPatch as Partial<AppSettings>,
    globalPatch: globalPatch as Partial<AppSettings>,
  };
}

/** 从一份完整 `AppSettings` 里只挑出"库级字段"，用于写进某个库自己的 library-settings.json */
export function pickLibraryScopedFields(settings: AppSettings): Partial<Record<LibraryScopedKey, unknown>> {
  const out: Partial<Record<LibraryScopedKey, unknown>> = {};
  for (const key of LIBRARY_SCOPED_KEYS) {
    out[key] = settings[key];
  }
  return out;
}

/**
 * 用某个库自己的原始配置（磁盘上的 library-settings.json 内容，可能损坏/越界/
 * 是老版本升级上来时随手塞进去的旧值）覆盖 `base` 上的库级字段，其余字段
 * （全局字段、`dataDir`、`libraries`/`activeLibraryId` 本身）原样保留 `base` 的值。
 *
 * 逐字段校验、单个字段损坏各自回退默认值——和 `sanitizeSettings` 同一套哲学，
 * 不因为一个字段坏了就把整个库的配置打回默认值。
 *
 * `defaults` 是字段级校验失败时的回退基准，调用方传 `defaultSettings()` 的结果——
 * 这里不直接调用它，是因为 `defaultSettings()` 要读 `app.getPath()`，
 * 这个文件必须保持零 electron 依赖。
 */
export function overlayLibraryScopedSettings(
  base: AppSettings,
  rawLibraryConfig: unknown,
  defaults: AppSettings,
): { settings: AppSettings; dropped: string[] } {
  const { settings: sanitized, dropped } = sanitizeSettings(rawLibraryConfig, defaults);
  const merged: AppSettings = { ...base };
  for (const key of LIBRARY_SCOPED_KEYS) {
    (merged as unknown as Record<string, unknown>)[key] = sanitized[key];
  }
  return { settings: merged, dropped };
}

/**
 * 首次迁移用：老用户升级上来 `libraries` 是空数组时，拿当前的 `dataDir`
 * 建一条「默认库」。`createdAt` 从外面传进来（而不是内部 `new Date()`）——
 * 保持这个文件里所有导出函数都是给定输入必然给定输出的纯函数，方便测试。
 */
export function buildDefaultLibraryEntry(dataDir: string, createdAt: string): LibraryEntry {
  return { id: 'default', name: '默认库', dataDir, createdAt };
}
