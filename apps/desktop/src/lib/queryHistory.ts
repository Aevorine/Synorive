/**
 * 查过什么 —— 补全用
 * ============================================================
 * 现有的「搜索历史」只活在内存里（`lib/undo.ts` 的快照栈），关掉软件就没了，
 * 而且它是一个要点开才看得到的列表。补全要的是另一种东西：
 * **打头几个字就把整句话递到手边**，重复检索少打很多字。
 *
 * 排序规则：前缀命中 > 子串命中；同级里按「用得多」和「用得近」加权。
 * 只按次数排的话，半年前搜过 20 次的老词会永远压着今天搜过 3 次的新词。
 *
 * 🔴 **这份记录能一键清空且能关掉。** 检索词是这个软件里最能反映一个人
 *    在想什么的东西 —— 比文件列表敏感得多。默认开是为了好用，
 *    但"我不想留痕"必须是一次点击就能做到的事，而且清空要**立刻生效**，
 *    不能只是不显示。
 */

const KEY = 'syn.queryHistory.v1';
/** 最多记多少条。超了丢分数最低的 —— 不是丢最旧的，常用词该留下 */
const MAX = 300;
/** 半衰期：30 天前用过一次，权重折半 */
const HALF_LIFE_MS = 30 * 24 * 60 * 60 * 1000;
/** 太短的不记：一两个字的前缀补全没有意义，只会污染列表 */
const MIN_LEN = 2;

export interface QueryRecord {
  q: string;
  /** 用过几次 */
  n: number;
  /** 最后一次用的时间戳 */
  at: number;
}

function read(): QueryRecord[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as QueryRecord[];
    if (!Array.isArray(arr)) return [];
    return arr.filter((r) => r && typeof r.q === 'string' && r.q.length >= MIN_LEN);
  } catch {
    return [];
  }
}

function write(list: QueryRecord[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // 配额满 / 隐私模式。少一个补全而已，绝不能影响这次搜索
  }
}

/** 综合分：次数 × 时间衰减。给排序用，不对外显示 */
export function scoreOf(r: QueryRecord, now: number = Date.now()): number {
  const ageRatio = Math.max(0, now - r.at) / HALF_LIFE_MS;
  return r.n * Math.pow(0.5, ageRatio);
}

export function remember(query: string): void {
  const q = query.trim();
  if (q.length < MIN_LEN) return;
  const list = read();
  const i = list.findIndex((r) => r.q === q);
  if (i >= 0) {
    list[i] = { q, n: list[i]!.n + 1, at: Date.now() };
  } else {
    list.push({ q, n: 1, at: Date.now() });
  }
  if (list.length > MAX) {
    const now = Date.now();
    list.sort((a, b) => scoreOf(b, now) - scoreOf(a, now));
    list.length = MAX;
  }
  write(list);
}

/**
 * 按前缀给建议。
 *
 * 🔴 **前缀命中永远排在子串命中前面，不靠分数混排。**
 *    用户打「注意」时，期待的第一条是「注意力机制怎么算」而不是
 *    「论文里的注意事项」—— 哪怕后者用得更多。混排会让第一条飘忽不定，
 *    而补全列表第一条飘忽不定的代价是：用户不敢闭眼按回车。
 */
export function suggest(prefix: string, limit = 6): QueryRecord[] {
  const p = prefix.trim().toLowerCase();
  const list = read();
  const now = Date.now();
  if (!p) {
    // 空输入时给最常用的几条
    return [...list].sort((a, b) => scoreOf(b, now) - scoreOf(a, now)).slice(0, limit);
  }
  const starts: QueryRecord[] = [];
  const contains: QueryRecord[] = [];
  for (const r of list) {
    const lower = r.q.toLowerCase();
    if (lower === p) continue; // 和已经打完的一模一样，补了等于没补
    if (lower.startsWith(p)) starts.push(r);
    else if (lower.includes(p)) contains.push(r);
  }
  const byScore = (a: QueryRecord, b: QueryRecord) => scoreOf(b, now) - scoreOf(a, now);
  starts.sort(byScore);
  contains.sort(byScore);
  return [...starts, ...contains].slice(0, limit);
}

export function clearQueryHistory(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* 清不掉也没什么可做的 */
  }
}

export function historySize(): number {
  return read().length;
}
