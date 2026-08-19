/**
 * 拼音模糊匹配
 * ============================================================
 * 打 `qingli`（全拼）或 `qlcft`（首字母）都要能命中「清理重复图」。
 *
 * 🔴 **拼音串从标签算出来，不是手写的。**
 *    原来每条命令手写一个 `py: 'qlczt'`。问题不在于麻烦，在于**它会被忘**：
 *    新加一条命令忘了写 py，那条就永远搜不到 —— 不报错、不告警，
 *    只是用户打拼音时它不出现。这是"运行正常、功能无效"的标准形态。
 *
 * 🔴 **多音字全试，不能只取第一个读音。**
 *    `重` 单字读 zhong，「重复」里读 chong。只取第一个读音的话，
 *    用户按直觉打 `qlcft` 一条都中不了，而他不会想到是多音字，
 *    只会觉得这个拼音搜索是坏的。所以按位置存多个读音，匹配时逐个试。
 *
 * 🔴 **精确匹配永远排在拼音之前。** 混排会让候选第一条随输入飘忽，
 *    而那样用户就不敢闭眼按回车 —— 面板的全部价值没了。
 */

import { readingsOf } from './pinyinMap.generated';

/** 每个位置上的候选读音。非汉字位置只有一个候选（字符本身，小写） */
type Slots = string[][];

const slotCache = new Map<string, Slots>();

function slotsOf(text: string): Slots {
  const hit = slotCache.get(text);
  if (hit) return hit;
  const slots: Slots = [];
  for (const ch of text) {
    const reads = readingsOf(ch);
    if (reads && reads.length) {
      slots.push(reads);
    } else if (/[a-z0-9]/i.test(ch)) {
      slots.push([ch.toLowerCase()]);
    }
    // 标点、空格、符号一律丢掉 —— 用户打拼音时不会去打顿号
  }
  slotCache.set(text, slots);
  return slots;
}

/**
 * 给调试和测试用：取每个位置的第一个读音拼出来的全拼与首字母。
 * **匹配本身不用它**（那样会丢掉多音字），只用于看一眼算出来是什么。
 */
export function pinyinKeys(text: string): { full: string; initials: string } {
  const slots = slotsOf(text);
  return {
    full: slots.map((s) => s[0]!).join(''),
    initials: slots.map((s) => s[0]![0]!).join(''),
  };
}

/**
 * 查询能不能作为「从某个位置开始、逐位置拼接读音」的前缀。
 *
 * 多音字在这里体现为分支：一个位置有几个读音就试几条路。
 * 词长有限（命令标签最多十几个字），分支不会爆。
 */
function matchesFrom(q: string, slots: Slots, start: number, useInitial: boolean): boolean {
  if (!q) return true;
  if (start >= slots.length) return false;
  for (const read of slots[start]!) {
    const piece = useInitial ? read[0]! : read;
    if (q.startsWith(piece)) {
      if (matchesFrom(q.slice(piece.length), slots, start + 1, useInitial)) return true;
    } else if (piece.startsWith(q)) {
      // 查询在这个音节中间结束（`qing` 之于 `qingli`），算命中
      return true;
    }
  }
  return false;
}

/** 从任意位置开始匹配（"包含"语义） */
function matchesAnywhere(q: string, slots: Slots, useInitial: boolean): boolean {
  for (let i = 0; i < slots.length; i++) {
    if (matchesFrom(q, slots, i, useInitial)) return true;
  }
  return false;
}

/** 跳着打首字母：`qct` 命中 `qlcft` */
function subsequenceInitials(q: string, slots: Slots): boolean {
  let i = 0;
  for (const reads of slots) {
    if (reads.some((r) => r[0] === q[i])) i++;
    if (i === q.length) return true;
  }
  return q.length === 0;
}

/**
 * 打分。返回 null = 不匹配；数字越小越靠前。
 */
export function fuzzyScore(query: string, label: string, hint?: string): number | null {
  const q = query.trim().toLowerCase();
  if (!q) return 0;

  const l = label.toLowerCase();
  if (l.startsWith(q)) return 0;
  if (l.includes(q)) return 1;

  const slots = slotsOf(label);
  if (matchesFrom(q, slots, 0, true)) return 2; // 首字母前缀
  if (matchesFrom(q, slots, 0, false)) return 3; // 全拼前缀
  if (matchesAnywhere(q, slots, true)) return 4;
  if (matchesAnywhere(q, slots, false)) return 5;
  if (subsequenceInitials(q, slots)) return 6;

  const h = (hint ?? '').toLowerCase();
  if (h) {
    if (h.includes(q)) return 7;
    if (matchesAnywhere(q, slotsOf(h), false)) return 8;
  }
  return null;
}
