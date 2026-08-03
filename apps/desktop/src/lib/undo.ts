/**
 * F3 —— 撤销与闪回快照
 * ============================================================
 * 两件不同的事，放一个文件因为它们共用一套栈：
 *
 *   **撤销**：刚做的那个操作，退回去（删了一条、改了标签、清了筛选）
 *   **闪回**：回到几分钟前的那个界面状态（搜到一半跑去看别的，想回来）
 *
 * 🔴 **撤销必须自己带"怎么撤"，不能靠重放。** 早期做法是记录动作
 * 然后反着执行一遍，但"反着执行"对删除来说根本不成立 —— 数据已经没了。
 * 所以每条撤销项**在动作发生前就把恢复所需的东西存下来**，
 * 撤销时直接把它塞回去。存不下的动作（比如已经发出去的网络请求）
 * **一开始就不该进这个栈**，进了就是一个点了没反应的撤销按钮。
 *
 * 🔴 **闪回快照只存"看到了什么"，不存"数据本身"。** 存查询词、
 * 筛选条件、滚动位置、选中项 —— 回到那个状态时重新查一遍。
 * 存结果快照的话，几十次闪回就能吃掉几百兆内存，
 * 而且回去看到的是过期数据（库里可能已经变了）。
 */

export interface UndoEntry {
  id: string;
  label: string;
  at: number;
  /** 真正的撤销动作。**必须是幂等的** —— 用户可能连点两下 */
  undo: () => void | Promise<void>;
  /** 撤销之后还能不能重做 */
  redo?: () => void | Promise<void>;
}

export interface Snapshot {
  id: string;
  at: number;
  label: string;
  page: string;
  /** 只存能重建界面的最小状态，**不存结果数据** */
  state: Record<string, unknown>;
}

/** 撤销栈深度。太深没意义 —— 没人会连撤三十步，而每条都占着闭包引用 */
const UNDO_MAX = 20;
/** 闪回快照条数。二十个够覆盖一次工作会话了 */
const SNAP_MAX = 20;
/** 两次快照的最小间隔：连续输入时不该每敲一个字就存一张 */
const SNAP_MIN_GAP_MS = 15_000;

type Listener = () => void;

class History {
  private undoStack: UndoEntry[] = [];
  private redoStack: UndoEntry[] = [];
  private snaps: Snapshot[] = [];
  private listeners = new Set<Listener>();

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(): void {
    for (const fn of this.listeners) fn();
  }

  // ── 撤销 ──────────────────────────────────────────────
  push(entry: Omit<UndoEntry, 'id' | 'at'>): void {
    this.undoStack.push({ ...entry, id: rid(), at: Date.now() });
    if (this.undoStack.length > UNDO_MAX) this.undoStack.shift();
    // 新动作一来就清空重做栈 —— 保留的话会出现"撤销 A、做了 B、
    // 再重做"回到一个从未存在过的混合状态
    this.redoStack = [];
    this.emit();
  }

  canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  peek(): UndoEntry | null {
    return this.undoStack[this.undoStack.length - 1] ?? null;
  }

  async undo(): Promise<string | null> {
    const e = this.undoStack.pop();
    if (!e) return null;
    this.emit();
    try {
      await e.undo();
    } catch (err) {
      // 撤销失败要**说出来**并且把它放回栈里。安静地失败会让用户
      // 以为已经撤销了，然后基于一个错误的认知继续操作
      this.undoStack.push(e);
      this.emit();
      throw err;
    }
    if (e.redo) this.redoStack.push(e);
    this.emit();
    return e.label;
  }

  async redo(): Promise<string | null> {
    const e = this.redoStack.pop();
    if (!e?.redo) return null;
    await e.redo();
    this.undoStack.push(e);
    this.emit();
    return e.label;
  }

  // ── 闪回 ──────────────────────────────────────────────
  snapshot(page: string, label: string, state: Record<string, unknown>): void {
    const last = this.snaps[this.snaps.length - 1];
    if (last && Date.now() - last.at < SNAP_MIN_GAP_MS && last.page === page) {
      // 间隔太短就**替换**而不是追加，否则连续输入会把栈刷满，
      // 真正想回去的那个状态被挤掉
      this.snaps[this.snaps.length - 1] = { ...last, at: Date.now(), label, state };
      this.emit();
      return;
    }
    this.snaps.push({ id: rid(), at: Date.now(), label, page, state });
    if (this.snaps.length > SNAP_MAX) this.snaps.shift();
    this.emit();
  }

  snapshots(): Snapshot[] {
    return [...this.snaps].reverse();
  }

  clear(): void {
    this.undoStack = [];
    this.redoStack = [];
    this.snaps = [];
    this.emit();
  }
}

function rid(): string {
  return Math.random().toString(36).slice(2, 10);
}

export const history = new History();

/**
 * 绑定 Ctrl+Z / Ctrl+Shift+Z。
 *
 * 🔴 **在输入框里不接管**。浏览器自带的文本撤销比我们的好用得多，
 * 抢过来只会让用户在搜索框里按 Ctrl+Z 时莫名其妙地恢复了一条被删的文件。
 */
export function bindUndoKeys(onDone: (label: string, kind: 'undo' | 'redo') => void): () => void {
  const handler = (e: KeyboardEvent): void => {
    const t = e.target as HTMLElement | null;
    const tag = t?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return;
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'z') return;
    e.preventDefault();
    if (e.shiftKey) {
      void history.redo().then((l) => l && onDone(l, 'redo'));
    } else {
      void history.undo().then((l) => l && onDone(l, 'undo'));
    }
  };
  window.addEventListener('keydown', handler);
  return () => window.removeEventListener('keydown', handler);
}
