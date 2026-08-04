/**
 * 结果多选 —— A4 一键成稿 / D3 并排对比 共用的底座
 * ====================================================================
 * 单独一个 store 而不是塞进 useSearch：**选中状态要跨搜索存活**。
 * 用户的真实动作是"搜一次挑两条 → 换个词再搜 → 再挑两条 → 一起出稿"，
 * 挂在 useSearch 里的话每次重搜都会被清空，那这个功能基本没法用。
 *
 * 存的是**整条 hit 而不只是 id**：
 * 🔴 只存 id 的话，出稿时要为每一条再发一次 `/items/{id}` 把标题和摘录拉回来 ——
 *    而那时候用户已经点了「出稿」，多等几秒不说，**其中任何一条拉失败
 *    都会让整份稿子缺一段**，还看不出缺了什么。选中那一刻数据就在手上，
 *    没有理由扔掉再去要一遍。
 */

import { create } from 'zustand';
import type { SearchHit } from '@synorive/shared-types';

/** 上限。超过这个数就不叫"挑几条出稿"了，那是导出整个库，走别的路。 */
export const MAX_SELECTION = 40;

interface SelectionState {
  /** 按选中顺序排列 —— 出稿时的段落顺序就是用户挑的顺序，不重排 */
  picked: SearchHit[];
  /** 快速判定用。和 picked 一起维护，两边不同步会让复选框显示错 */
  ids: Set<string>;
  /** 挑满了之后再点会给一次提示，只提示一次别烦人 */
  warnedFull: boolean;

  toggle: (hit: SearchHit) => void;
  remove: (id: string) => void;
  clear: () => void;
  has: (id: string) => boolean;
  /** 把当前一屏全选/全不选 */
  toggleAll: (hits: SearchHit[]) => void;
}

export const useSelection = create<SelectionState>((set, get) => ({
  picked: [],
  ids: new Set(),
  warnedFull: false,

  has: (id) => get().ids.has(id),

  toggle: (hit) => {
    const { picked, ids } = get();
    const id = hit.item.id;
    if (ids.has(id)) {
      const nextIds = new Set(ids);
      nextIds.delete(id);
      set({ picked: picked.filter((h) => h.item.id !== id), ids: nextIds, warnedFull: false });
      return;
    }
    if (picked.length >= MAX_SELECTION) {
      set({ warnedFull: true });
      return;
    }
    const nextIds = new Set(ids);
    nextIds.add(id);
    set({ picked: [...picked, hit], ids: nextIds });
  },

  remove: (id) => {
    const { picked, ids } = get();
    const nextIds = new Set(ids);
    nextIds.delete(id);
    set({ picked: picked.filter((h) => h.item.id !== id), ids: nextIds, warnedFull: false });
  },

  clear: () => set({ picked: [], ids: new Set(), warnedFull: false }),

  toggleAll: (hits) => {
    const { ids } = get();
    // 当前这批**全都已选**才算"全选状态"，否则一律按"补齐"处理。
    // 反过来（选了一条就当已全选、再点变清空）会让用户点一下丢掉刚挑的东西
    const allPicked = hits.length > 0 && hits.every((h) => ids.has(h.item.id));
    if (allPicked) {
      const nextIds = new Set(ids);
      for (const h of hits) nextIds.delete(h.item.id);
      set((s) => ({
        picked: s.picked.filter((p) => nextIds.has(p.item.id)),
        ids: nextIds,
        warnedFull: false,
      }));
      return;
    }
    const nextIds = new Set(ids);
    const add: SearchHit[] = [];
    for (const h of hits) {
      if (nextIds.has(h.item.id)) continue;
      if (nextIds.size >= MAX_SELECTION) break;
      nextIds.add(h.item.id);
      add.push(h);
    }
    set((s) => ({
      picked: [...s.picked, ...add],
      ids: nextIds,
      warnedFull: nextIds.size >= MAX_SELECTION,
    }));
  },
}));
