import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * F8 —— 键盘全流程可达
 * ============================================================
 * ↑↓ 选、Enter 进、Space 预览、Esc 退出，全程不碰鼠标。
 *
 * **为什么这值得单独做**：搜索是个高频动作，而高频动作里
 * 手离开键盘去够鼠标是最大的一笔时间开销。真正好用的检索工具
 * （Everything、Alfred、fzf）无一例外都是键盘优先的。
 *
 * 🔴 **必须处理"选中项在虚拟滚动之外"的情况。** 用 ↓ 一路按下去，
 * 选中项会走出可视区，而虚拟列表里它根本不在 DOM 里 ——
 * 这时候 `scrollIntoView` 是个空操作，症状是按了没反应。
 * 所以这里回调出去让列表自己滚，而不是自作主张去操作 DOM。
 *
 * 🔴 **Space 预览而不是 Enter 预览。** Enter 是"进去"，Space 是"瞄一眼"。
 * 混成一个键的话，用户浏览一列结果时每看一条都要退出来一次。
 * 这是从 macOS Finder 的 Quick Look 借来的，用过的人不用学。
 */

export interface KeyNavOptions {
  /** 一共几项 */
  count: number;
  /** Enter：打开这一项 */
  onOpen?: (index: number) => void;
  /** Space：预览这一项（再按一次关掉） */
  onPreview?: (index: number) => void;
  /** Esc：退出选择态 */
  onEscape?: () => void;
  /** 选中项变了，通知列表把它滚进可视区 */
  onScrollTo?: (index: number) => void;
  /** 关掉键盘导航（比如弹窗打开时） */
  disabled?: boolean;
}

export function useKeyNav({
  count,
  onOpen,
  onPreview,
  onEscape,
  onScrollTo,
  disabled,
}: KeyNavOptions) {
  const [index, setIndex] = useState(-1);
  const idxRef = useRef(index);
  idxRef.current = index;

  // 列表长度变了（重新搜了一次）就复位。**不复位的话**选中项会停在
  // 一个下标上，而那个下标现在指向的是另一条完全不同的结果 ——
  // 用户按 Enter 打开的会是他没看过的东西
  useEffect(() => {
    setIndex(-1);
  }, [count]);

  const move = useCallback(
    (delta: number) => {
      if (count <= 0) return;
      const cur = idxRef.current;
      // 从"没选中"开始按 ↑ 应该跳到最后一条，而不是留在 -1
      const next = cur < 0 ? (delta > 0 ? 0 : count - 1) : clamp(cur + delta, 0, count - 1);
      setIndex(next);
      onScrollTo?.(next);
    },
    [count, onScrollTo],
  );

  useEffect(() => {
    if (disabled) return;
    const handler = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable;

      // 在搜索框里也要能用 ↑↓ 选结果 —— 那是这套交互的关键：
      // 敲完关键词不用把手从输入框移开就能选。
      // 但 Space 和 Enter 在输入框里必须留给输入本身
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        move(1);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        move(-1);
        return;
      }
      if (inInput) return;

      if (e.key === 'Enter' && idxRef.current >= 0) {
        e.preventDefault();
        onOpen?.(idxRef.current);
        return;
      }
      if (e.key === ' ' && idxRef.current >= 0) {
        e.preventDefault();
        onPreview?.(idxRef.current);
        return;
      }
      if (e.key === 'Escape') {
        if (idxRef.current >= 0) {
          setIndex(-1);
        }
        onEscape?.();
      }
      if (e.key === 'Home') {
        e.preventDefault();
        setIndex(0);
        onScrollTo?.(0);
      }
      if (e.key === 'End' && count > 0) {
        e.preventDefault();
        setIndex(count - 1);
        onScrollTo?.(count - 1);
      }
      if (e.key === 'PageDown') {
        e.preventDefault();
        move(10);
      }
      if (e.key === 'PageUp') {
        e.preventDefault();
        move(-10);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [count, disabled, move, onEscape, onOpen, onPreview, onScrollTo]);

  return { index, setIndex };
}

function clamp(n: number, lo: number, hi: number): number {
  return n < lo ? lo : n > hi ? hi : n;
}

/**
 * 给列表项用的 a11y 属性。**`aria-selected` 不能只靠 CSS 高亮** ——
 * 读屏软件看不到 CSS，只看得到这个属性。
 */
export function navItemProps(index: number, active: number) {
  return {
    role: 'option' as const,
    'aria-selected': index === active,
    tabIndex: index === active ? 0 : -1,
    'data-nav-index': index,
  };
}
