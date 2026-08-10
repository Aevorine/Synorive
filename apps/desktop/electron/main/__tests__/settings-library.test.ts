/**
 * 多库支持 —— 分流逻辑测试
 * ====================================================================
 * 覆盖 settings-schema.ts 里新增的那批纯函数：判定一个字段是不是"库级字段"、
 * 把 patch 拆成库级/全局两份、从完整 AppSettings 里挑出库级字段、
 * 用某个库自己的配置覆盖库级字段。
 *
 * settings.ts 里真正读写 `<dataDir>/library-settings.json` 的那层因为
 * import 了 `electron` 的 `app` 模块，不在这个仓库 vitest 的收纳范围内
 * （见 vitest.config.ts 顶部注释）——所以这里只测它调用的这几个纯函数，
 * 且最后一组测试用真实文件系统（临时目录）走一遍"两个库各自的
 * library-settings.json 互不影响"，而不是拿内存对象模拟。
 * "新建库→切换→引擎重启"这条完整链路走真实构建 + 启动验证（见验收报告）。
 *
 * 跑：npm test --workspace=@synorive/desktop
 */

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import type { AppSettings } from '@synorive/shared-types';
import {
  LIBRARY_SCOPED_KEYS,
  buildDefaultLibraryEntry,
  isLibraryScopedKey,
  overlayLibraryScopedSettings,
  pickLibraryScopedFields,
  splitSettingsPatch,
} from '../settings-schema.js';

function makeBase(overrides: Partial<AppSettings> = {}): AppSettings {
  return {
    libraries: [{ id: 'default', name: '默认库', dataDir: 'D:/data', createdAt: '2026-01-01T00:00:00.000Z' }],
    activeLibraryId: 'default',
    theme: 'system',
    fontScheme: 'b',
    eyeComfort: 'off',
    eyeReminderMinutes: 0,
    density: 'standard',
    startPage: 'today',
    defaultInputMode: 'ask',
    pinnedNav: [],
    savedPresets: [],
    activeProjectId: null,
    offloadHeavyWork: true,
    concurrency: 7,
    runInTray: true,
    launchAtLogin: true,
    rerankResults: false,
    clipboardSentinel: true,
    clipboardAutoArchiveLinks: false,
    clipboardPeek: false,
    clipboardPeekWeb: false,
    watchedFolders: [],
    dataDir: 'D:/data',
    modelDir: 'D:/data/models',
    cloud: { enabled: false, provider: 'none', dailyBudget: null },
    enableFaceClustering: false,
    enableAuthenticatedFetch: false,
    enableImageDescription: false,
    enableGpuAcceleration: false,
    sensitiveGuardEnabled: true,
    lanPairingEnabled: false,
    pairingToken: 'abc123',
    allowNetwork: true,
    webLineupSize: 0,
    verifyLevel: 'counter',
    webEndpoints: { searxng: 'http://127.0.0.1:8888' },
    webEngines: [],
    autoCheckUpdate: true,
    ...overrides,
  };
}

describe('isLibraryScopedKey', () => {
  it('审查报告点名的 14 个字段全部判定为库级', () => {
    for (const key of LIBRARY_SCOPED_KEYS) {
      expect(isLibraryScopedKey(key)).toBe(true);
    }
  });

  it('全局字段（主题/窗口/并发度/更新……）不算库级', () => {
    for (const key of ['theme', 'concurrency', 'dataDir', 'modelDir', 'cloud', 'autoCheckUpdate', 'libraries', 'activeLibraryId']) {
      expect(isLibraryScopedKey(key)).toBe(false);
    }
  });
});

describe('splitSettingsPatch', () => {
  it('混合 patch 按字段各归各类，不遗漏不错分', () => {
    const { libraryPatch, globalPatch } = splitSettingsPatch({
      theme: 'dark',
      allowNetwork: false,
      watchedFolders: ['C:/docs'],
      concurrency: 4,
    });
    expect(libraryPatch).toEqual({ allowNetwork: false, watchedFolders: ['C:/docs'] });
    expect(globalPatch).toEqual({ theme: 'dark', concurrency: 4 });
  });

  it('空 patch 拆完两份都是空对象', () => {
    const { libraryPatch, globalPatch } = splitSettingsPatch({});
    expect(libraryPatch).toEqual({});
    expect(globalPatch).toEqual({});
  });

  it('activeLibraryId / dataDir（library.switchTo 会一起改的两个字段）都归全局', () => {
    const { libraryPatch, globalPatch } = splitSettingsPatch({
      activeLibraryId: 'lib-2',
      dataDir: 'D:/libs/lib-2',
    });
    expect(libraryPatch).toEqual({});
    expect(globalPatch).toEqual({ activeLibraryId: 'lib-2', dataDir: 'D:/libs/lib-2' });
  });
});

describe('pickLibraryScopedFields', () => {
  it('只挑出那 14 个字段，不带别的', () => {
    const base = makeBase({ watchedFolders: ['C:/a'], allowNetwork: false });
    const picked = pickLibraryScopedFields(base);
    expect(Object.keys(picked).sort()).toEqual([...LIBRARY_SCOPED_KEYS].sort());
    expect(picked.watchedFolders).toEqual(['C:/a']);
    expect(picked.allowNetwork).toBe(false);
    expect('theme' in picked).toBe(false);
    expect('dataDir' in picked).toBe(false);
  });
});

describe('overlayLibraryScopedSettings', () => {
  it('库自己配置里的库级字段覆盖 base，其余全局字段原样保留 base 的值', () => {
    const base = makeBase({ theme: 'dark', watchedFolders: ['C:/old'], allowNetwork: true });
    const defaults = makeBase();
    const { settings, dropped } = overlayLibraryScopedSettings(
      base,
      { watchedFolders: ['D:/new'], allowNetwork: false },
      defaults,
    );
    expect(settings.watchedFolders).toEqual(['D:/new']);
    expect(settings.allowNetwork).toBe(false);
    // 全局字段（theme）不受库配置影响，原样是 base 的值
    expect(settings.theme).toBe('dark');
    expect(dropped).toHaveLength(0);
  });

  it('库配置里某个库级字段损坏——只回退那一个字段到 defaults，不连累其它字段', () => {
    const base = makeBase({ webLineupSize: 3 });
    const defaults = makeBase({ webLineupSize: 0 });
    const { settings, dropped } = overlayLibraryScopedSettings(
      base,
      { webLineupSize: -1, allowNetwork: false }, // webLineupSize 越界（min 0），allowNetwork 合法
      defaults,
    );
    expect(settings.webLineupSize).toBe(0); // 回退到 defaults 的值
    expect(settings.allowNetwork).toBe(false); // 合法字段照常生效
    expect(dropped).toContain('webLineupSize');
  });

  it('库配置文件是空对象（新建的库还没写过任何库级设置）——库级字段全部落回 defaults', () => {
    const base = makeBase({ theme: 'paper' });
    const defaults = makeBase({ watchedFolders: [], allowNetwork: true, sensitiveGuardEnabled: true });
    const { settings, dropped } = overlayLibraryScopedSettings(base, {}, defaults);
    expect(settings.watchedFolders).toEqual([]);
    expect(settings.allowNetwork).toBe(true);
    expect(settings.sensitiveGuardEnabled).toBe(true);
    expect(settings.theme).toBe('paper'); // 全局字段还是来自 base
    expect(dropped).toHaveLength(0);
  });

  it('不覆盖 libraries / activeLibraryId 本身——那两个字段永远来自 base（全局注册表）', () => {
    const base = makeBase({
      libraries: [{ id: 'a', name: 'A', dataDir: 'D:/a', createdAt: '2026-01-01T00:00:00.000Z' }],
      activeLibraryId: 'a',
    });
    const defaults = makeBase();
    const { settings } = overlayLibraryScopedSettings(base, { allowNetwork: false }, defaults);
    expect(settings.activeLibraryId).toBe('a');
    expect(settings.libraries).toEqual(base.libraries);
  });
});

describe('buildDefaultLibraryEntry', () => {
  it('给定 dataDir 和 createdAt，构造出固定形状的「默认库」条目——纯函数，同输入同输出', () => {
    const entry = buildDefaultLibraryEntry('D:/data', '2026-08-10T00:00:00.000Z');
    expect(entry).toEqual({
      id: 'default',
      name: '默认库',
      dataDir: 'D:/data',
      createdAt: '2026-08-10T00:00:00.000Z',
    });
  });
});

describe('多库真实文件系统场景：两个库的 library-settings.json 互不影响', () => {
  const tmpDirs: string[] = [];

  afterEach(() => {
    for (const d of tmpDirs.splice(0)) {
      rmSync(d, { recursive: true, force: true });
    }
  });

  it('库 A 写自己的 watchedFolders/allowNetwork，库 B 保持默认——读回来互不干扰', () => {
    const dirA = mkdtempSync(join(tmpdir(), 'synorive-lib-a-'));
    const dirB = mkdtempSync(join(tmpdir(), 'synorive-lib-b-'));
    tmpDirs.push(dirA, dirB);

    const defaults = makeBase();
    const base = makeBase({
      libraries: [
        { id: 'a', name: '库 A', dataDir: dirA, createdAt: '2026-01-01T00:00:00.000Z' },
        { id: 'b', name: '库 B', dataDir: dirB, createdAt: '2026-01-02T00:00:00.000Z' },
      ],
      activeLibraryId: 'a',
    });

    // 库 A 落过盘：监听了一个目录，且关掉了联网搜索
    const libAPayload = pickLibraryScopedFields(
      makeBase({ watchedFolders: ['C:/work'], allowNetwork: false }),
    );
    writeFileSync(join(dirA, 'library-settings.json'), JSON.stringify(libAPayload, null, 2), 'utf8');
    // 库 B 从没写过——目录里根本没有这个文件，模拟"新建库还没配过"

    // 真实文件系统读回来，走 overlayLibraryScopedSettings
    const rawA = JSON.parse(readFileSync(join(dirA, 'library-settings.json'), 'utf8'));
    const { settings: settingsA } = overlayLibraryScopedSettings(
      { ...base, dataDir: dirA },
      rawA,
      defaults,
    );
    expect(settingsA.watchedFolders).toEqual(['C:/work']);
    expect(settingsA.allowNetwork).toBe(false);

    expect(existsSync(join(dirB, 'library-settings.json'))).toBe(false);
    const rawB = existsSync(join(dirB, 'library-settings.json'))
      ? JSON.parse(readFileSync(join(dirB, 'library-settings.json'), 'utf8'))
      : {};
    const { settings: settingsB } = overlayLibraryScopedSettings(
      { ...base, dataDir: dirB, activeLibraryId: 'b' },
      rawB,
      defaults,
    );
    // 库 B 完全没受库 A 的设置影响——保持 defaults 的值
    expect(settingsB.watchedFolders).toEqual([]);
    expect(settingsB.allowNetwork).toBe(true);
  });
});
