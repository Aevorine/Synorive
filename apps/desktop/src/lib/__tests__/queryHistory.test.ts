/**
 * 查询补全
 * ====================================================================
 * 两条规矩定了这份代码的形状：
 *   ① 前缀命中永远排在子串命中前面，**不和分数混排**。混排会让补全列表
 *      第一条飘忽不定，而那样用户就不敢闭眼按回车 —— 补全的全部价值没了。
 *   ② 只按次数排会让半年前搜过 20 次的老词永远压着今天搜过 3 次的新词。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

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
  seed(v: unknown): void {
    this.m.set('syn.queryHistory.v1', JSON.stringify(v));
  }
}

let store: MemStore;
beforeEach(() => {
  store = new MemStore();
  vi.stubGlobal('localStorage', store);
});

const { remember, suggest, clearQueryHistory, historySize, scoreOf } = await import(
  '../queryHistory'
);

const DAY = 24 * 60 * 60 * 1000;

describe('queryHistory', () => {
  it('记住并按前缀补全', () => {
    remember('注意力机制怎么算');
    remember('注意力机制论文');
    remember('向量检索延迟');
    const got = suggest('注意力').map((r) => r.q);
    expect(got).toContain('注意力机制怎么算');
    expect(got).toContain('注意力机制论文');
    expect(got).not.toContain('向量检索延迟');
  });

  it('🔴 前缀命中排在子串命中之前，哪怕子串那条用得多得多', () => {
    store.seed([
      { q: '论文里的注意事项', n: 50, at: Date.now() },
      { q: '注意力机制', n: 1, at: Date.now() },
    ]);
    expect(suggest('注意')[0]!.q).toBe('注意力机制');
  });

  it('同为前缀命中时，用得多、用得近的排前面', () => {
    const now = Date.now();
    store.seed([
      { q: '向量检索延迟', n: 2, at: now - 60 * DAY },
      { q: '向量检索召回', n: 3, at: now },
    ]);
    expect(suggest('向量')[0]!.q).toBe('向量检索召回');
  });

  it('🔴 时间衰减真的起作用 —— 老词不能永远压着新词', () => {
    const now = Date.now();
    const 老词 = { q: '老词', n: 20, at: now - 120 * DAY };
    const 新词 = { q: '新词', n: 3, at: now };
    // 120 天 = 4 个半衰期，20 * 0.5^4 = 1.25 < 3
    expect(scoreOf(新词, now)).toBeGreaterThan(scoreOf(老词, now));
  });

  it('重复搜同一个词会累加次数，不新增一条', () => {
    remember('同一个词');
    remember('同一个词');
    remember('同一个词');
    expect(historySize()).toBe(1);
    expect(suggest('同一')[0]!.n).toBe(3);
  });

  it('和已经打完的完全一样时不再建议 —— 补了等于没补', () => {
    remember('注意力机制');
    expect(suggest('注意力机制').map((r) => r.q)).not.toContain('注意力机制');
  });

  it('太短的不记 —— 一两个字的补全只会污染列表', () => {
    remember('a');
    remember(' ');
    expect(historySize()).toBe(0);
    remember('ab');
    expect(historySize()).toBe(1);
  });

  it('空输入时给最常用的几条', () => {
    const now = Date.now();
    store.seed([
      { q: '少用的', n: 1, at: now },
      { q: '常用的', n: 9, at: now },
    ]);
    expect(suggest('')[0]!.q).toBe('常用的');
  });

  it('超过上限时丢掉分数最低的，不是丢最旧的 —— 常用词该留下', () => {
    const now = Date.now();
    const seed = Array.from({ length: 300 }, (_, i) => ({ q: `词${i}`, n: 1, at: now }));
    // 一条很老但极常用的
    seed[0] = { q: '老而常用', n: 999, at: now - 10 * DAY };
    store.seed(seed);
    remember('新来的一条');
    const all = JSON.parse(store.getItem('syn.queryHistory.v1')!) as { q: string }[];
    expect(all).toHaveLength(300);
    expect(all.map((r) => r.q)).toContain('老而常用');
  });

  it('🔴 一键清空立刻生效', () => {
    remember('要清掉的');
    expect(historySize()).toBe(1);
    clearQueryHistory();
    expect(historySize()).toBe(0);
    expect(suggest('要清')).toHaveLength(0);
  });

  it('存坏了当作没有，不抛异常', () => {
    store.setItem('syn.queryHistory.v1', '{不是 json');
    expect(() => suggest('x')).not.toThrow();
    expect(suggest('x')).toEqual([]);
  });

  it('🔴 写失败不能往上抛', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
      removeItem: () => {},
    });
    expect(() => remember('随便什么')).not.toThrow();
    expect(() => clearQueryHistory()).not.toThrow();
  });
});
