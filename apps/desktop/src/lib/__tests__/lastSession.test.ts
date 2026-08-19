/**
 * 「上次那一屏」缓存
 * ====================================================================
 * 这份缓存有三条不能破的规矩，破了都不报错、只是悄悄出问题：
 *   ① 存进去的**不能带正文摘录** —— localStorage 是明文的，
 *      而库文件本身是可以加密的，这份缓存不该成为绕过加密的旁路。
 *   ② 太旧的**不能拿出来用** —— 三天前那一屏和现在的库多半对不上了。
 *   ③ 写失败（配额满、隐私模式）**不能往上抛** —— 抛出去会让整次搜索
 *      显示成失败，为了一个便利功能毁掉主功能。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SearchHit } from '@synorive/shared-types';

// node 环境没有 localStorage，先搭一个最小的
class MemStore {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.get(k) ?? null;
  }
  setItem(k: string, v: string): void {
    this.m.set(k, v);
  }
  removeItem(k: string): void {
    this.m.delete(k);
  }
  raw(k: string): string | null {
    return this.m.get(k) ?? null;
  }
}

let store: MemStore;
beforeEach(() => {
  store = new MemStore();
  vi.stubGlobal('localStorage', store);
});

const { saveLastSession, loadLastSession, clearLastSession, agoText, shortLocator } = await import(
  '../lastSession'
);

function hit(id: string, snippet: string): SearchHit {
  return {
    item: {
      id,
      fingerprint: `fp-${id}`,
      modality: 'text',
      source: 'file',
      status: 'done',
      title: `文档 ${id}`,
      locator: `D:/x/${id}.md`,
      snippet,
      createdAt: '2026-08-01T00:00:00',
      updatedAt: '2026-08-01T00:00:00',
      openCount: 0,
    } as unknown as SearchHit['item'],
    score: 0.9,
    highlight: `命中了<em>${snippet}</em>`,
  };
}

describe('lastSession', () => {
  it('存了能读回来，查询词和条数都对', () => {
    saveLastSession('注意力机制', [hit('a', '正文一'), hit('b', '正文二')], 42);
    const got = loadLastSession();
    expect(got).not.toBeNull();
    expect(got!.query).toBe('注意力机制');
    expect(got!.total).toBe(42);
    expect(got!.hits).toHaveLength(2);
    expect(got!.hits[0]!.item.title).toBe('文档 a');
  });

  it('🔴 正文摘录和高亮片段一个字都不许进 localStorage', () => {
    saveLastSession('查询', [hit('a', '这里有一段很敏感的正文内容')], 1);
    const raw = store.raw('syn.lastSession.v1');
    expect(raw).not.toBeNull();
    expect(raw).not.toContain('这里有一段很敏感的正文内容');
    expect(raw).not.toContain('<em>');
    const got = loadLastSession();
    expect(got!.hits[0]!.item.snippet).toBe('');
    expect(got!.hits[0]!.highlight).toBeUndefined();
  });

  it('空查询、空结果都不存 —— 存了下次启动只会垫出一屏空白', () => {
    saveLastSession('   ', [hit('a', 'x')], 1);
    expect(loadLastSession()).toBeNull();
    saveLastSession('有词', [], 0);
    expect(loadLastSession()).toBeNull();
  });

  it('最多存 20 条 —— 一屏看得见的量就够', () => {
    const many = Array.from({ length: 60 }, (_, i) => hit(`h${i}`, 'x'));
    saveLastSession('多', many, 60);
    expect(loadLastSession()!.hits).toHaveLength(20);
  });

  it('🔴 超过三天的快照当作没有 —— 旧到那个程度已经不能参考了', () => {
    saveLastSession('旧的', [hit('a', 'x')], 1);
    const raw = JSON.parse(store.raw('syn.lastSession.v1')!);
    raw.at = Date.now() - 4 * 24 * 60 * 60 * 1000;
    store.setItem('syn.lastSession.v1', JSON.stringify(raw));
    expect(loadLastSession()).toBeNull();
  });

  it('🔴 写失败不能往上抛 —— 否则一次搜索会因为缓存写不进去而显示成失败', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
      removeItem: () => {},
    });
    expect(() => saveLastSession('查询', [hit('a', 'x')], 1)).not.toThrow();
    expect(() => clearLastSession()).not.toThrow();
  });

  it('存坏了就当没有，不能让软件起不来', () => {
    store.setItem('syn.lastSession.v1', '{ 这不是合法 json');
    expect(loadLastSession()).toBeNull();
  });

  it('🔴 网址不显示问号后面的东西 —— 那里经常是登录令牌', () => {
    expect(shortLocator('https://my.example.com/area.php?token=SECRET-abc123&x=1')).toBe(
      'my.example.com/area.php …',
    );
    expect(shortLocator('https://accounts.google.com/v3/signin/identifier?ifkv=LONGSECRET')).toBe(
      'accounts.google.com/v3/signin/identifier …',
    );
    // 片段标识符同理，OAuth 隐式流的令牌就在 # 后面
    expect(shortLocator('https://x.com/cb#access_token=SECRET')).toBe('x.com/cb …');
  });

  it('没有查询串的网址原样显示，不多加省略号', () => {
    expect(shortLocator('https://example.com/a/b')).toBe('example.com/a/b');
    expect(shortLocator('https://example.com/')).toBe('example.com');
  });

  it('本地文件路径一个字都不动 —— 它没有查询串这回事', () => {
    const p = 'D:\Documents\note.md';
    expect(shortLocator(p)).toBe(p);
    expect(shortLocator('/home/me/a?b.txt')).toBe('/home/me/a?b.txt');
  });

  it('解析不了的网址退回"砍在问号前"，不抛异常', () => {
    expect(shortLocator('https://[bad url?tok=SECRET')).toBe('https://[bad url …');
  });

  it('时间差说人话', () => {
    expect(agoText(Date.now() - 5_000)).toBe('刚刚');
    expect(agoText(Date.now() - 10 * 60_000)).toBe('10 分钟前');
    expect(agoText(Date.now() - 3 * 3600_000)).toBe('3 小时前');
    expect(agoText(Date.now() - 2 * 24 * 3600_000)).toBe('2 天前');
  });
});
