import type { ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

/**
 * 加载 / 出错 / 空 三态的统一处理
 *
 * 起因是一个真实的 bug：页面写成
 *   {!loading && rows.length === 0 && <空状态/>}
 *   {rows.length > 0 && <内容/>}
 * 加载中这两个条件都不成立 —— **页面一片空白**，用户以为坏了。
 * 三个状态必须都有对应的画面，收敛到一处才不会在第六个页面上再犯一次。
 */
export function PageState({
  loading,
  error,
  empty,
  emptyIcon,
  emptyTitle,
  emptyHint,
  emptyAction,
  onRetry,
  children,
}: {
  loading: boolean;
  error?: string | null;
  empty: boolean;
  emptyIcon?: ReactNode;
  emptyTitle: string;
  emptyHint: string;
  emptyAction?: ReactNode;
  onRetry?: () => void;
  children: ReactNode;
}) {
  if (error) {
    return (
      <div className="empty">
        <div className="empty__title">出错了</div>
        <p className="empty__hint">{error}</p>
        {onRetry && (
          <button className="btn" onClick={onRetry}>
            重试
          </button>
        )}
      </div>
    );
  }

  // 加载中且还没有任何数据 → 骨架，不是空白也不是转圈占满整屏
  if (loading && empty) {
    return (
      <div className="loadingstate">
        <Loader2 size={18} className="spin" strokeWidth={2} />
        <span>加载中…</span>
      </div>
    );
  }

  if (empty) {
    return (
      <div className="empty">
        {emptyIcon}
        <div className="empty__title">{emptyTitle}</div>
        <p className="empty__hint">{emptyHint}</p>
        {emptyAction}
      </div>
    );
  }

  return <>{children}</>;
}
