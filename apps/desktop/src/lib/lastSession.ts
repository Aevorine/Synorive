/**
 * 上次那一屏 —— 打开软件立刻有东西看
 * ============================================================
 * 冷启动的顺序是：窗口出画面 → 引擎子进程起来 → 模型预热 → 第一次查询才有结果。
 * 中间那一两秒里界面是空的，而"空白"看起来和"坏了"是一样的。
 *
 * 这里把上一次搜索的那一屏存下来，下次打开先把它画出来，引擎就绪后
 * 用真实数据无缝换掉。
 *
 * 🔴 **必须明写这是旧的。** 库里的东西可能已经被删了、改了。
 *    不标注就是在给用户看一份他以为是实时的、实际过期的清单 ——
 *    他点进去发现文件没了，会以为是软件把东西弄丢了。
 *    所以 `SearchPage` 在快照态下会挂一条"上次的结果"横幅，
 *    而且**任何一次真实查询回来都立刻替换**，不做合并。
 *
 * 🔴 **只存摘要，不存正文。** 存的是标题、路径、时间这些列表上本来就显示的字段。
 *    正文摘录可能包含敏感内容，而 localStorage 是明文的 ——
 *    库文件本身在同一台机器上，但库是可以加密的，这份缓存不该成为绕过它的旁路。
 *
 * 🔴 **写入必须能整体失败而不影响搜索。** localStorage 满了会抛 QuotaExceededError，
 *    在搜索回调里抛出去会让整次搜索显示成失败。全部包在 try 里。
 */

import type { SearchHit } from '@synorive/shared-types';

const KEY = 'syn.lastSession.v1';
/** 存多少条。一屏看得见的量就够，存 60 条只是让 localStorage 变大 */
const MAX_HITS = 20;
/** 超过这个时长就不再拿出来用了 —— 三天前那一屏对现在没有参考价值 */
const MAX_AGE_MS = 3 * 24 * 60 * 60 * 1000;

export interface LastSession {
  query: string;
  total: number;
  at: number;
  hits: SearchHit[];
}

/** 只留列表上会显示的字段，正文摘录一律丢掉 */
function slim(hit: SearchHit): SearchHit {
  return {
    item: { ...hit.item, snippet: '' },
    score: hit.score,
    // highlight（带高亮的命中片段）和 explain（打分明细）都不存 —— 见文件头第三条
  };
}

export function saveLastSession(query: string, hits: SearchHit[], total: number): void {
  if (!query.trim() || hits.length === 0) return;
  try {
    const payload: LastSession = {
      query,
      total,
      at: Date.now(),
      hits: hits.slice(0, MAX_HITS).map(slim),
    };
    localStorage.setItem(KEY, JSON.stringify(payload));
  } catch {
    // 配额满了 / 隐私模式 —— 少一个便利功能而已，绝不能影响这次搜索
  }
}

export function loadLastSession(): LastSession | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as LastSession;
    if (!p || !Array.isArray(p.hits) || p.hits.length === 0) return null;
    if (typeof p.at !== 'number' || Date.now() - p.at > MAX_AGE_MS) return null;
    return p;
  } catch {
    // 存坏了就当没有。绝不因为一份缓存读不动就让软件起不来
    return null;
  }
}

export function clearLastSession(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* 清不掉也没什么可做的 */
  }
}

/** 人话时间差：给横幅上那句"X 之前的结果"用 */
export function agoText(at: number): string {
  const s = Math.max(0, Math.round((Date.now() - at) / 1000));
  if (s < 60) return '刚刚';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.round(h / 24)} 天前`;
}

/**
 * 只显示到路径，不显示问号后面的东西。
 *
 * 🔴 **网址的查询串里经常带登录令牌。** 收藏过的「登录 xxx」这类链接，
 *    问号后面那一长串就是会话凭据。它在结果列表里一闪而过还好，
 *    而这条窄条是**每次打开软件都摆在那儿**的 —— 等于把令牌长期显示在屏幕上，
 *    旁边有人、或者录屏/截图时就跟着出去了。
 *    点击打开时用的仍然是完整地址，只是不显示。
 */
export function shortLocator(loc: string): string {
  if (!/^https?:\/\//i.test(loc)) return loc;
  try {
    const u = new URL(loc);
    const path = u.pathname === '/' ? '' : u.pathname;
    return `${u.host}${path}${u.search || u.hash ? ' …' : ''}`;
  } catch {
    // 解析不了就砍在问号前，砍不到就原样返回
    const q = loc.indexOf('?');
    return q > 0 ? `${loc.slice(0, q)} …` : loc;
  }
}
