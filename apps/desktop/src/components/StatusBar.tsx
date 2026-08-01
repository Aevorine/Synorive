import { useApp } from '../lib/store';
import type { EngineProcessState } from '../../electron/shared/ipc-contract';

/**
 * 状态栏：后台在忙什么永远可见，但不打扰。
 * 这条要求来自「使用时不卡顿」的另一面 —— 用户需要知道
 * "它正在后台干活" 而不是 "它是不是死了"。
 */

const LIFECYCLE_TEXT: Record<EngineProcessState['lifecycle'], string> = {
  stopped: '引擎已停止',
  starting: '引擎启动中',
  ready: '引擎就绪',
  degraded: '引擎降级运行',
  restarting: '引擎重启中',
  failed: '引擎启动失败',
};

function dotClass(lc: EngineProcessState['lifecycle'] | undefined): string {
  switch (lc) {
    case 'ready':
      return 'statusbar__dot--ready';
    case 'starting':
    case 'restarting':
    case 'degraded':
      return 'statusbar__dot--busy';
    case 'failed':
      return 'statusbar__dot--failed';
    default:
      return 'statusbar__dot--idle';
  }
}

interface EngineDetail {
  indexedItems?: number;
  queueDepth?: number;
  cpuPercent?: number;
  memoryMb?: number;
  concurrency?: number;
  executionProvider?: string;
  dbSizeMb?: number;
}

export function StatusBar() {
  const engine = useApp((s) => s.engine);
  const settings = useApp((s) => s.settings);
  const detail = (engine?.detail ?? null) as EngineDetail | null;

  const items = detail?.indexedItems;
  const queue = detail?.queueDepth ?? 0;

  return (
    <footer className="statusbar" aria-label="状态">
      <span className="statusbar__group" title={engine?.lastError ?? undefined}>
        <span className={`statusbar__dot ${dotClass(engine?.lifecycle)}`} aria-hidden />
        {engine ? LIFECYCLE_TEXT[engine.lifecycle] : '引擎连接中'}
        {engine?.lifecycle === 'ready' && engine.bootMs
          ? `　${(engine.bootMs / 1000).toFixed(1)}s`
          : ''}
      </span>

      {typeof items === 'number' && (
        <span className="statusbar__group">已索引 {items.toLocaleString('zh-CN')} 条</span>
      )}

      {queue > 0 && <span className="statusbar__group">队列 {queue.toLocaleString('zh-CN')}</span>}

      <span className="statusbar__spacer" />

      {typeof detail?.cpuPercent === 'number' && (
        <span className="statusbar__group">CPU {detail.cpuPercent.toFixed(0)}%</span>
      )}
      {typeof detail?.memoryMb === 'number' && (
        <span className="statusbar__group">内存 {detail.memoryMb.toFixed(0)} MB</span>
      )}
      <span className="statusbar__group" title="分析并发度，设置里可调 1~16">
        并发 {detail?.concurrency ?? settings?.concurrency ?? '—'}
      </span>
      {/* 加「推理」两个字：不然和左边的 CPU 占用率并排显示成两个 CPU，没人看得懂 */}
      <span className="statusbar__group" title="推理执行器：CPU 或核显 DirectML">
        推理 {detail?.executionProvider ?? 'CPU'}
      </span>
    </footer>
  );
}
