/**
 * D9 零结果补救 · 界面
 * ============================================================
 * 搜不到东西时，把引擎算出来的补救方案摆出来，点一下直接重搜。
 *
 * 每条都带确切条数（「去掉时间筛选 → 37 条」），因为引擎那边是**真跑过**
 * 才给出来的。不带数字的建议等于让用户再赌一次，赌输了比不给建议更恼火。
 */

import { CalendarX, Filter, Loader2, Scissors, SpellCheck2, Sparkles } from 'lucide-react';

export interface RecoverySuggestion {
  kind: 'drop-filter' | 'split-term' | 'did-you-mean' | 'indexing' | string;
  label: string;
  count: number;
  payload: { query?: string; drop?: string[] };
}

export interface RecoveryPlan {
  reason: string;
  message: string;
  suggestions: RecoverySuggestion[];
}

const ICON: Record<string, typeof Filter> = {
  'drop-filter': Filter,
  'split-term': Scissors,
  'did-you-mean': SpellCheck2,
  indexing: Loader2,
};

export function Recovery({
  plan,
  onRetry,
  weak = false,
}: {
  plan: RecoveryPlan;
  /** query 为空表示"只改筛选不改词"；drop 是要去掉的筛选键 */
  onRetry: (next: { query?: string; drop?: string[] }) => void;
  /**
   * 有结果但都很勉强。
   * 这时候标题**绝不能**写"没搜到"—— 下面明明列着几条，
   * 说没搜到会让用户直接忽略它们，而那几条常常正是他要的。
   */
  weak?: boolean;
}) {
  const actionable = plan.suggestions.filter((s) => s.kind !== 'indexing');
  const notes = plan.suggestions.filter((s) => s.kind === 'indexing');

  return (
    <div className={`recovery${weak ? ' recovery--weak' : ''}`}>
      <div className="recovery__head">
        <Sparkles size={17} strokeWidth={1.6} className="recovery__glyph" />
        <div>
          <div className="recovery__title">{weak ? '没有很匹配的' : '没搜到东西'}</div>
          <p className="recovery__msg">{plan.message}</p>
        </div>
      </div>

      {actionable.length > 0 && (
        <div className="recovery__list">
          {actionable.map((s, i) => {
            const Icon = ICON[s.kind] ?? Filter;
            return (
              <button
                key={`${s.kind}-${i}`}
                className="recovery__item"
                onClick={() => onRetry(s.payload)}
              >
                <Icon size={15} strokeWidth={1.7} className="recovery__icon" />
                <span className="recovery__label">{s.label}</span>
              </button>
            );
          })}
        </div>
      )}

      {notes.map((s, i) => (
        <p key={`note-${i}`} className="recovery__note">
          <CalendarX size={13} strokeWidth={1.7} />
          {s.label}
        </p>
      ))}
    </div>
  );
}
