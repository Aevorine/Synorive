import { useEffect, useState } from 'react';
import { History, Redo2, Undo2 } from 'lucide-react';
import { history, bindUndoKeys, type Snapshot } from '../lib/undo';

/**
 * F3 —— 撤销与闪回
 * ============================================================
 * 两件不同的事共用一个面板：
 *   **撤销**（Ctrl+Z）：刚做的那个操作退回去
 *   **闪回**：回到几分钟前的那个界面状态（搜到一半跑去看别的，想回来）
 *
 * 🔴 **撤销失败要弹出来说，不能安静地什么都不做。**
 * 安静失败会让用户以为已经撤销了，然后基于一个错误的认知继续操作 ——
 * 那比直接报错糟得多。`history.undo()` 失败时会把条目放回栈里并抛出，
 * 这里接住并显示。
 *
 * 🔴 **闪回快照只存"看到了什么"，不存数据本身。**
 * 存结果快照的话，几十次闪回就能吃掉几百兆内存，而且回去看到的是过期数据
 * （库里可能已经变了）。所以回到那个状态时是**重新查一遍**。
 */

export function Flashback({
  onRestore,
}: {
  /** 用户点了某个快照，由页面负责把界面恢复成那个样子 */
  onRestore: (snap: Snapshot) => void;
}) {
  const [open, setOpen] = useState(false);
  const [, tick] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // 订阅历史栈的变化。**不把栈本身放进 state** ——
  // 它是个跨页面共享的单例，复制进组件状态会让两边不同步
  useEffect(() => history.subscribe(() => tick((n) => n + 1)), []);

  useEffect(
    () =>
      bindUndoKeys((label, kind) => {
        setErr(null);
        setToast(`${kind === 'undo' ? '已撤销' : '已重做'}：${label}`);
        window.setTimeout(() => setToast(null), 2600);
      }),
    [],
  );

  const snaps = history.snapshots();
  const next = history.peek();

  return (
    <div className="syn-fb">
      <button
        type="button"
        className="syn-fb-btn"
        disabled={!history.canUndo()}
        title={next ? `撤销：${next.label}（Ctrl+Z）` : '没有可撤销的操作'}
        onClick={() => {
          setErr(null);
          void history
            .undo()
            .then((l) => l && setToast(`已撤销：${l}`))
            .catch((e: Error) => setErr(`撤销没成功：${e.message}`));
        }}
      >
        <Undo2 size={14} aria-hidden />
        {next ? next.label : '撤销'}
      </button>

      <button
        type="button"
        className="syn-fb-btn"
        disabled={!history.canRedo()}
        title="重做（Ctrl+Shift+Z）"
        onClick={() => {
          void history.redo().then((l) => l && setToast(`已重做：${l}`));
        }}
      >
        <Redo2 size={14} aria-hidden />
      </button>

      <button
        type="button"
        className="syn-fb-btn"
        disabled={snaps.length === 0}
        onClick={() => setOpen((o) => !o)}
        title="回到刚才某个时刻的界面状态"
      >
        <History size={14} aria-hidden /> 闪回
        {snaps.length > 0 && <span className="syn-fb-count">{snaps.length}</span>}
      </button>

      {open && snaps.length > 0 && (
        <div className="syn-fb-pop" role="menu">
          {snaps.map((s) => (
            <button
              key={s.id}
              type="button"
              role="menuitem"
              className="syn-fb-snap"
              title={`回到这一刻：${s.label}`}
              onClick={() => {
                onRestore(s);
                setOpen(false);
              }}
            >
              <span className="syn-fb-time">{fmtAgo(s.at)}</span>
              <span className="syn-fb-label">{s.label}</span>
              <span className="syn-fb-page">{s.page}</span>
            </button>
          ))}
        </div>
      )}

      {toast && <span className="syn-fb-toast">{toast}</span>}
      {err && <span className="syn-fb-toast syn-fb-toast--err">{err}</span>}
    </div>
  );
}

function fmtAgo(ts: number): string {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return `${s} 秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟前`;
  return `${Math.floor(m / 60)} 小时前`;
}
