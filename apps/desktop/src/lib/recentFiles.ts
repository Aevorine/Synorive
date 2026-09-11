/**
 * 最近打开过什么 —— C2 命令面板的「最近文件」那一组
 * ============================================================
 * 引擎那边的 `recordOpen` 记的是**排序用的热度**，回不来一份"我最近开过什么"
 * 的清单；`lastSession` 只存最后一次搜索的那一屏。两个都答不上
 * 「上午那个 PDF 叫什么来着」这个问题，而那是命令面板最值钱的用途之一。
 *
 * 🔴 **和 `queryHistory` 用同一套隐私约定**：存在本机 localStorage、
 *    只存列表上本来就显示的字段（标题 + 路径）、能一键清空。
 *    绝不存正文摘录 —— 库可以整库加密，这份缓存不该成为绕过它的旁路。
 *
 * 🔴 **写入永远不能让打开文件这件事失败。** localStorage 满了会抛
 *    QuotaExceededError，在打开回调里抛出去会变成"点了没反应"。全部包在 try 里。
 */

const KEY = 'syn.recentFiles.v1';
/** 存多少条。命令面板一次最多显示 8 条，多存是为了打字过滤时还有东西可挑 */
const MAX = 60;

export interface RecentFile {
  id: string;
  title: string;
  /** 本机路径，或者 link 类型的 URL */
  locator: string;
  /** 'link' 走 openExternal，其余走 openPath */
  source: string;
  at: number;
}

function read(): RecentFile[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as RecentFile[];
    if (!Array.isArray(arr)) return [];
    return arr.filter((r) => r && typeof r.id === 'string' && typeof r.locator === 'string');
  } catch {
    return [];
  }
}

function write(list: RecentFile[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // 少一条最近记录而已，绝不能影响这次打开
  }
}

/**
 * 记一次打开。**同一份资料只留一条**（时间刷新到最新），
 * 否则连着打开同一个文件三次，「最近」那一组就全是它。
 */
export function rememberOpen(f: { id: string; title?: string; locator: string; source?: string }): void {
  if (!f.id || !f.locator) return;
  const list = read().filter((r) => r.id !== f.id);
  list.unshift({
    id: f.id,
    title: (f.title || '').trim() || f.locator,
    locator: f.locator,
    source: f.source ?? 'file',
    at: Date.now(),
  });
  write(list.slice(0, MAX));
}

export function recentFiles(limit = MAX): RecentFile[] {
  return read()
    .sort((a, b) => b.at - a.at)
    .slice(0, limit);
}

export function clearRecentFiles(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* 清不掉也没有别的办法，至少不要炸掉调用方 */
  }
}
