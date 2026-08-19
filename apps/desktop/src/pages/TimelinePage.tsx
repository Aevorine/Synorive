import { useMemo, useState } from 'react';
import { Clock } from 'lucide-react';
import type { TimelineBucket } from '@synorive/shared-types';
import { PageState } from '../components/PageState';
import { api } from '../lib/api';
import { useEngineData } from '../lib/useEngineData';
import { useApp } from '../lib/store';
import { useSearch } from '../lib/useSearch';

/**
 * 语义时间轴 E5 —— 所有内容按时间铺开
 *
 * 回答的是「我那段时间在忙什么」这类问题。
 * 点某一段会跳到搜索页并带上那段时间的筛选 ——
 * 光看柱状图没用，得能顺着点进去。
 */

const BUCKETS: { id: TimelineBucket; label: string }[] = [
  { id: 'day', label: '按天' },
  { id: 'week', label: '按周' },
  { id: 'month', label: '按月' },
  { id: 'year', label: '按年' },
];

const MODALITY_LABEL: Record<string, string> = {
  text: '文档',
  image: '图片',
  video: '视频',
  audio: '音频',
  link: '网页',
  message: '消息',
};

/** 各模态用不同颜色堆叠，一眼看出那段时间主要在存什么 */
const MODALITY_VAR: Record<string, string> = {
  text: 'var(--syn-color-primary)',
  image: 'var(--syn-color-success)',
  video: 'var(--syn-color-warning)',
  audio: 'var(--syn-color-info)',
  link: 'var(--syn-color-primary-hover)',
  message: 'var(--syn-color-danger)',
};

export function TimelinePage() {
  const [bucket, setBucket] = useState<TimelineBucket>('month');
  const setPage = useApp((s) => s.setPage);
  const setFilters = useSearch((s) => s.setFilters);
  const setQuery = useSearch((s) => s.setQuery);

  const { data, loading, error } = useEngineData(
    () => api.timeline(bucket, 200),
    [bucket],
    { refreshOn: ['ingest.job'] },
  );

  const rows = data ?? [];
  const max = useMemo(() => Math.max(1, ...rows.map((r) => r.count)), [rows]);
  const total = useMemo(() => rows.reduce((a, r) => a + r.count, 0), [rows]);

  const jump = (at: string) => {
    // 点一段就跳到搜索页并带上这段时间的筛选 ——
    // 只能看不能点的图表是没用的
    const { from, to } = rangeOf(at, bucket);
    setFilters({ timeFrom: from, timeTo: to });
    setQuery('');
    setPage('search');
  };

  return (
    <div className="page">
      <div className="page__meta">
        <span className="page__subtitle">
          {rows.length ? `${rows.length} 个时间段 · 共 ${total.toLocaleString('zh-CN')} 条` : ''}
        </span>
      </div>

      <div className="filterbar">
        <div className="filterbar__group">
          <span className="filterbar__label">粒度</span>
          <div className="filterbar__chips">
            {BUCKETS.map((b) => (
              <button
                key={b.id}
                className={`chip${bucket === b.id ? ' chip--on' : ''}`}
                onClick={() => setBucket(b.id)}
              >
                {b.label}
              </button>
            ))}
          </div>
        </div>
        <div className="filterbar__group">
          <span className="filterbar__label">图例</span>
          <div className="filterbar__chips">
            {Object.entries(MODALITY_LABEL).map(([k, v]) => (
              <span key={k} className="legend">
                <i className="legend__dot" style={{ background: MODALITY_VAR[k] }} />
                {v}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="page__body">
        <PageState
          loading={loading}
          error={error}
          empty={rows.length === 0}
          emptyIcon={<Clock size={30} strokeWidth={1.2} className="empty__glyph" />}
          emptyTitle="还没有带时间的内容"
          emptyHint="索引一些文件之后，它们会按时间铺在这条轴上。照片用拍摄时间，网页用发布时间，文件用修改时间。"
        >
          <div className="timeline">
            {rows.map((r) => (
              <button
                key={r.at}
                className="timeline__row"
                onClick={() => jump(r.at)}
                title={`点进去看这段时间的内容（${r.count} 条）`}
              >
                <span className="timeline__label">{r.at}</span>
                <span className="timeline__bar">
                  {Object.entries(r.byModality).map(([k, v]) => (
                    <i
                      key={k}
                      className="timeline__seg"
                      style={{
                        width: `${((v as number) / max) * 100}%`,
                        background: MODALITY_VAR[k] ?? 'var(--syn-color-border-strong)',
                      }}
                      title={`${MODALITY_LABEL[k] ?? k} ${v}`}
                    />
                  ))}
                </span>
                <span className="timeline__count">{r.count}</span>
              </button>
            ))}
          </div>
        </PageState>
      </div>
    </div>
  );
}

/** 时间桶标签 → 具体的起止时间，用于跳转时带上筛选 */
function rangeOf(at: string, bucket: TimelineBucket): { from: string; to: string } {
  const pad = (n: number) => String(n).padStart(2, '0');
  if (bucket === 'year') {
    return { from: `${at}-01-01T00:00:00Z`, to: `${at}-12-31T23:59:59Z` };
  }
  if (bucket === 'month') {
    const [y, m] = at.split('-').map(Number);
    const last = new Date(Date.UTC(y!, m!, 0)).getUTCDate();
    return { from: `${at}-01T00:00:00Z`, to: `${at}-${pad(last)}T23:59:59Z` };
  }
  if (bucket === 'week') {
    // %Y-W%W 只能定位到周，退化成整年 —— 比给个错的区间强
    const y = at.slice(0, 4);
    return { from: `${y}-01-01T00:00:00Z`, to: `${y}-12-31T23:59:59Z` };
  }
  return { from: `${at}T00:00:00Z`, to: `${at}T23:59:59Z` };
}
