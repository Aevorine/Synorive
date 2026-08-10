/**
 * 设置 schema 校验 —— 损坏/越界字段被单独拦下，不连累其它合法字段
 * ====================================================================
 * 复现审查报告里点名的那个场景：
 *   { concurrency: 999, dataDir: "", allowNetwork: "abc", webEndpoints: ... }
 * 这类值以前会被 `{ ...default, ...raw }` 原样接受。
 *
 * 只测 sanitizeSettings 这个纯函数，不碰 electron/文件系统。
 *
 * 跑：npm test --workspace=@synorive/desktop
 */

import { describe, expect, it } from 'vitest';
import type { AppSettings } from '@synorive/shared-types';
import { sanitizeSettings } from '../settings-schema.js';

function makeBase(): AppSettings {
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
  };
}

describe('sanitizeSettings', () => {
  it('审查报告里的损坏配置——越界/空值/错类型字段各自回退默认值，其它字段不受影响', () => {
    const base = makeBase();
    const { settings, dropped } = sanitizeSettings(
      {
        concurrency: 999, // 超出 1~16
        dataDir: '', // 空字符串不是合法路径
        allowNetwork: 'abc', // 类型错了，该是 boolean
        theme: 'dark', // 这个是合法的，应该生效
      },
      base,
    );

    expect(settings.concurrency).toBe(base.concurrency); // 回退默认值 7
    expect(settings.dataDir).toBe(base.dataDir);
    expect(settings.allowNetwork).toBe(base.allowNetwork);
    expect(settings.theme).toBe('dark'); // 合法字段正常生效，没被连累

    expect(dropped).toContain('concurrency');
    expect(dropped).toContain('dataDir');
    expect(dropped).toContain('allowNetwork');
    expect(dropped).not.toContain('theme');
  });

  it('字段没出现在 raw 里——保留 base 的值（覆盖"新版本加字段"场景）', () => {
    const base = makeBase();
    const { settings, dropped } = sanitizeSettings({ theme: 'paper' }, base);
    expect(settings.theme).toBe('paper');
    expect(settings.concurrency).toBe(base.concurrency);
    expect(dropped).toHaveLength(0);
  });

  it('cloud 子对象也是逐字段校验，不是整个 cloud 对象一起作废', () => {
    const base = makeBase();
    const { settings, dropped } = sanitizeSettings(
      {
        cloud: {
          enabled: true, // 合法
          dailyBudget: -5, // 非法：不能是负数
        },
      },
      base,
    );
    expect(settings.cloud.enabled).toBe(true);
    expect(settings.cloud.dailyBudget).toBe(base.cloud.dailyBudget);
    expect(dropped).toContain('cloud.dailyBudget');
    expect(dropped).not.toContain('cloud.enabled');
  });

  it('raw 整个不是对象（比如磁盘上存了一个数组或字符串）——原样回退到 base，不报错', () => {
    const base = makeBase();
    const r1 = sanitizeSettings('不是对象', base);
    expect(r1.settings).toEqual(base);
    expect(r1.dropped).toHaveLength(0);

    const r2 = sanitizeSettings(null, base);
    expect(r2.settings).toEqual(base);

    const r3 = sanitizeSettings([1, 2, 3], base);
    expect(r3.settings).toEqual(base);
  });

  it('枚举字段给了一个不存在的取值——回退默认值', () => {
    const base = makeBase();
    const { settings, dropped } = sanitizeSettings({ verifyLevel: 'yolo' }, base);
    expect(settings.verifyLevel).toBe(base.verifyLevel);
    expect(dropped).toContain('verifyLevel');
  });

  it('savedPresets 数组元素结构不对（少了 weights 里的字段）——整个数组字段回退，不是部分元素回退', () => {
    const base = makeBase();
    const { settings, dropped } = sanitizeSettings(
      { savedPresets: [{ id: 'x', name: 'x', weights: { semantic: 1 } }] },
      base,
    );
    expect(settings.savedPresets).toEqual(base.savedPresets);
    expect(dropped).toContain('savedPresets');
  });

  it('合法的完整 patch——全部生效，没有任何字段被丢', () => {
    const base = makeBase();
    const { settings, dropped } = sanitizeSettings(
      { concurrency: 4, allowNetwork: false, webEngines: ['bing', 'brave'] },
      base,
    );
    expect(settings.concurrency).toBe(4);
    expect(settings.allowNetwork).toBe(false);
    expect(settings.webEngines).toEqual(['bing', 'brave']);
    expect(dropped).toHaveLength(0);
  });
});
