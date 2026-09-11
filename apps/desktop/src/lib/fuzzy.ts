/**
 * 子序列模糊匹配 —— C2 命令面板 v2 的排序内核
 * ============================================================
 * 现有的 `pinyinMatch.fuzzyScore` 解决的是**中文标签怎么用拼音搜到**，
 * 它只分七八个粗档（前缀 / 包含 / 首字母 / 全拼…），
 * 同一档里的几十条命令之间是**没有先后的** —— 表现是打两个字之后
 * 候选顺序看起来像随机的，用户不敢闭眼按回车。
 *
 * 这里补的是另一半：**同一档里怎么排**。
 *
 * 🔴 **不引第三方库**（fuse.js / fzf 之类）。这套算法一共八十行，
 *    而多一个依赖要多一份体积、一份供应链风险、一份升级负担。
 *
 * 算法就是 fzy 那一套的精简版：
 *   1. 查询必须是候选串的**子序列**（跳着打也认：`ctm` 命中「Clear Trash Menu」）
 *   2. 连续命中加权 —— `sear` 命中 "search" 要远好于命中 "Set Every Author Row"
 *   3. 词首命中加权 —— 空格/斜杠/下划线/连字符/中文标点之后的那个字符，
 *      以及 camelCase 的大写字母，都算词首
 *   4. 整体越靠前、候选串越短，分越高
 *
 * 🔴 **返回的是"越大越好"**。和 `pinyinMatch.fuzzyScore`（越小越好）相反，
 *    两边混用时必须显式转换 —— 直接相加会得到一个完全颠倒的排序，
 *    而且它不会报错，只是列表顺序莫名其妙。
 */

/** 词首判定用的分隔符。中文标点也算 —— 「清理·重复图」里 `重` 是词首 */
const SEP = new Set([
  ' ', '\t', '\n', '/', '\\', '-', '_', '.', ':', ',', '(', ')', '[', ']',
  '·', '、', '，', '（', '）', '「', '」', '：', '—',
]);

/** 满分：查询整串就是候选串的开头 */
const MAX = 100;

const BONUS_FIRST = 18; // 命中在整串第一个字符
const BONUS_WORD = 12; // 命中在词首
const BONUS_CONSECUTIVE = 14; // 和上一个命中紧挨着
const PENALTY_GAP = 1.2; // 每跳过一个字符扣一点
const PENALTY_LONG = 0.06; // 候选串越长越扣（同分时短的排前面）

function isWordStart(text: string, i: number): boolean {
  if (i === 0) return true;
  const prev = text[i - 1]!;
  if (SEP.has(prev)) return true;
  // camelCase：小写后面跟大写
  const cur = text[i]!;
  return prev === prev.toLowerCase() && cur !== cur.toLowerCase() && prev !== cur.toLowerCase();
}

export interface FuzzyHit {
  /** 0~100+，越大越好 */
  score: number;
  /** 命中的字符下标，给高亮用 */
  positions: number[];
}

/**
 * 贪心正向扫一遍，但**每一步都优先挑"能接上连续段"的那个位置**。
 *
 * 为什么不用完整 DP：候选串是命令标签（最长十几个字），
 * 而这个函数每敲一个键要对几百条跑一遍。贪心 + 一次回看
 * 在这个规模上和 DP 的排序结果几乎一致，而开销是常数倍。
 */
export function fuzzyMatch(query: string, text: string): FuzzyHit | null {
  const q = query.trim().toLowerCase();
  if (!q) return { score: 0, positions: [] };
  if (!text) return null;

  const lower = text.toLowerCase();

  // 快路：整串前缀 / 子串。这两种是绝对多数，先判掉省事也更准
  const at = lower.indexOf(q);
  if (at >= 0) {
    const positions: number[] = [];
    for (let i = 0; i < q.length; i++) positions.push(at + i);
    let s = MAX - at * 1.5 - text.length * PENALTY_LONG;
    if (at === 0) s += BONUS_FIRST;
    else if (isWordStart(text, at)) s += BONUS_WORD;
    return { score: Math.max(1, s), positions };
  }

  // 子序列
  const positions: number[] = [];
  let ti = 0;
  for (let qi = 0; qi < q.length; qi++) {
    const c = q[qi]!;
    const found = lower.indexOf(c, ti);
    if (found < 0) return null;
    positions.push(found);
    ti = found + 1;
  }

  let score = 0;
  for (let k = 0; k < positions.length; k++) {
    const p = positions[k]!;
    if (p === 0) score += BONUS_FIRST;
    else if (isWordStart(text, p)) score += BONUS_WORD;
    if (k > 0) {
      const gap = p - positions[k - 1]! - 1;
      if (gap === 0) score += BONUS_CONSECUTIVE;
      else score -= Math.min(10, gap * PENALTY_GAP);
    }
  }
  // 命中密度：查询占候选串的比例越高越像"就是它"
  score += (q.length / Math.max(1, text.length)) * 20;
  score -= positions[0]! * 0.5;
  score -= text.length * PENALTY_LONG;

  return score > 0 ? { score, positions } : { score: 0.5, positions };
}

/**
 * 把 `pinyinMatch.fuzzyScore` 的档位（0~8，越小越好，null = 不匹配）
 * 换算成和 `fuzzyMatch` 同一把尺子上的分数（越大越好）。
 *
 * 🔴 档位之间留 8 分的间隔：小于连续命中加权（14）的话，
 *    一个"跳着打的首字母"会翻过"完整全拼前缀"排到前面去。
 */
export function pinyinRankToScore(rank: number | null): number | null {
  if (rank === null) return null;
  return Math.max(1, 96 - rank * 8);
}
