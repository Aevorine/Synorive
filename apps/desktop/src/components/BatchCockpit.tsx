import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Pause, Play, RotateCcw, X } from 'lucide-react';
import { VirtualList } from './VirtualList';

/**
 * F2 —— 批量驾驶舱
 * ============================================================
 * 往库里灌几万个文件时，用户唯一想知道的三件事：
 * **还要多久 / 现在卡在哪个文件 / 哪些失败了**。
 *
 * 现在的进度条只回答了第一件的一部分。这个面板补齐另外两件。
 *
 * 🔴 **失败清单是这个组件存在的主要理由，不是附属功能。**
 * 一万个文件里失败 37 个，不列出来的话用户永远不会知道 ——
 * 进度条走到 100% 看起来就像全成功了。那是最典型的静默失败：
 * 不报错、不崩溃、只是少了 37 个文件，而且再也没人发现。
 *
 * 🔴 **暂停要真的能暂停。** 一个点了没反应的暂停按钮比没有暂停按钮糟：
 * 用户会以为自己点错了，反复点，然后放弃。所以按钮状态严格跟随
 * 主进程回来的真实状态，不做乐观更新。
 */

export interface BatchItem {
  path: string;
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped';
  error?: string;
  elapsedMs?: number;
}

export interface BatchState {
  jobId: string;
  total: number;
  done: number;
  failed: number;
  skipped: number;
  running: boolean;
  paused: boolean;
  current?: string;
  startedAt: number;
  items: BatchItem[];
}

export function BatchCockpit({
  state,
  onPause,
  onResume,
  onRetry,
  onCancel,
  onClose,
  canControl = true,
}: {
  state: BatchState;
  onPause: () => void;
  onResume: () => void;
  onRetry: (paths: string[]) => void;
  onCancel: () => void;
  onClose: () => void;
  /**
   * A1：任务还没在引擎那边建起来的那一小段（占位卡片阶段）传 false。
   *
   * 🔴 **这时候必须把按钮真的禁掉，不能只让 onPause 空转。**
   * 一个点了没反应的暂停按钮正是这个组件开头警告的那件事 ——
   * 用户会以为自己点错了，反复点，然后连带不信任别的按钮。
   */
  canControl?: boolean;
}) {
  const [tab, setTab] = useState<'all' | 'failed'>('all');
  const [now, setNow] = useState(Date.now());

  // 每秒刷一次只为了让"已用时/剩余"这两个数字在走。
  // **只在真的还在跑的时候开这个定时器** —— 跑完了还每秒重渲染一次
  // 整个列表，是纯粹的白烧电
  useEffect(() => {
    if (!state.running) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [state.running]);

  const failed = useMemo(() => state.items.filter((i) => i.status === 'failed'), [state.items]);
  const shown = tab === 'failed' ? failed : state.items;

  const elapsed = Math.max(0, now - state.startedAt);
  const rate = state.done > 0 ? elapsed / state.done : 0;
  const remain = rate > 0 ? Math.round((rate * (state.total - state.done)) / 1000) : null;
  const pct = state.total > 0 ? Math.round((state.done / state.total) * 100) : 0;

  return (
    <section className="syn-cockpit" aria-label="批量投喂进度">
      <header className="syn-cockpit-head">
        <h2>批量投喂</h2>
        <span className="syn-cockpit-count">
          {state.done} / {state.total}
        </span>
        <button type="button" className="syn-cockpit-x" onClick={onClose} aria-label="收起">
          <X size={16} aria-hidden />
        </button>
      </header>

      <div
        className="syn-cockpit-bar"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="syn-cockpit-bar-fill" style={{ width: `${pct}%` }} />
      </div>

      <p className="syn-cockpit-line">
        {state.paused ? '已暂停' : state.running ? '进行中' : '已结束'}
        {'　'}已用 {fmtDur(elapsed / 1000)}
        {remain != null && state.running && !state.paused ? `　约剩 ${fmtDur(remain)}` : ''}
        {state.failed > 0 && (
          <span className="syn-cockpit-failed">
            {'　'}
            <AlertTriangle size={13} aria-hidden /> 失败 {state.failed}
          </span>
        )}
      </p>

      {/* 当前文件单独一行且**允许很长** —— 卡住的时候，
          "卡在哪个文件"是用户唯一能拿去做判断的信息 */}
      {state.current && state.running && (
        <p className="syn-cockpit-current" title={state.current}>
          正在处理：{state.current}
        </p>
      )}

      <div className="syn-cockpit-actions">
        {state.running &&
          (state.paused ? (
            <button type="button" onClick={onResume} disabled={!canControl}>
              <Play size={14} aria-hidden /> 继续
            </button>
          ) : (
            <button
              type="button"
              onClick={onPause}
              disabled={!canControl}
              title={canControl ? '暂停（正在处理的那个文件会做完）' : '任务还在建，稍等一下就能暂停'}
            >
              <Pause size={14} aria-hidden /> 暂停
            </button>
          ))}
        {failed.length > 0 && (
          <button type="button" onClick={() => onRetry(failed.map((f) => f.path))}>
            <RotateCcw size={14} aria-hidden /> 重试失败的 {failed.length} 个
          </button>
        )}
        {state.running && (
          <button
            type="button"
            className="syn-cockpit-danger"
            onClick={onCancel}
            disabled={!canControl}
          >
            取消剩下的
          </button>
        )}
      </div>

      <nav className="syn-cockpit-tabs">
        <button
          type="button"
          className={tab === 'all' ? 'is-on' : ''}
          onClick={() => setTab('all')}
        >
          全部 {state.items.length}
        </button>
        <button
          type="button"
          className={tab === 'failed' ? 'is-on' : ''}
          onClick={() => setTab('failed')}
          disabled={failed.length === 0}
        >
          失败 {failed.length}
        </button>
      </nav>

      {/* 几万条必须虚拟滚动，否则打开这个面板本身就会卡住几秒 */}
      <VirtualList
        items={shown}
        className="syn-cockpit-list"
        estimateHeight={40}
        keyOf={(i) => i.path}
      >
        {(it) => (
          <p className={`syn-cockpit-item is-${it.status}`} title={it.path}>
            <span className="syn-cockpit-item-name">{basename(it.path)}</span>
            {it.status === 'failed' && (
              <span className="syn-cockpit-item-err">{it.error || '没给出原因'}</span>
            )}
          </p>
        )}
      </VirtualList>

      {tab === 'failed' && failed.length === 0 && (
        <p className="syn-cockpit-empty">没有失败的文件。</p>
      )}
    </section>
  );
}

function basename(p: string): string {
  const i = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'));
  return i >= 0 ? p.slice(i + 1) : p;
}

function fmtDur(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分 ${s % 60} 秒`;
  return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
}
