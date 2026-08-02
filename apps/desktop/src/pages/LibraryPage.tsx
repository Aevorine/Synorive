import { useMemo, useState } from 'react';
import { FolderOpen, RotateCcw } from 'lucide-react';
import type { Modality, SearchHit, SourceKind } from '@synorive/shared-types';
import { PageState } from '../components/PageState';
import { SearchResults } from '../components/SearchResults';
import { api } from '../lib/api';
import { useEngineData } from '../lib/useEngineData';
import { PAGE_TITLES } from '../lib/store';

/**
 * 文件管理器 —— 浏览、筛选、管理库里的全部内容
 *
 * 和搜索页的区别：搜索页回答"哪些内容跟这个词有关"，
 * 这里回答"库里都有些什么"。所以主轴是筛选器而不是查询词。
 */

const MODALITIES: { id: Modality; label: string }[] = [
  { id: 'text', label: '文档' },
  { id: 'image', label: '图片' },
  { id: 'video', label: '视频' },
  { id: 'audio', label: '音频' },
  { id: 'link', label: '网页' },
  { id: 'message', label: '消息' },
];

const SOURCES: { id: SourceKind; label: string }[] = [
  { id: 'file', label: '本机文件' },
  { id: 'link', label: '链接' },
  { id: 'clipboard', label: '剪贴板' },
  { id: 'chat-export', label: '聊天导出' },
  { id: 'mail', label: '邮件' },
  { id: 'api', label: '接口投喂' },
];

const TIME_RANGES: { id: string; label: string; days: number | null }[] = [
  { id: 'all', label: '全部', days: null },
  { id: '7d', label: '最近 7 天', days: 7 },
  { id: '30d', label: '最近 30 天', days: 30 },
  { id: '1y', label: '最近一年', days: 365 },
];

export function LibraryPage() {
  const [modalities, setModalities] = useState<Modality[]>([]);
  const [sources, setSources] = useState<SourceKind[]>([]);
  const [range, setRange] = useState('all');

  const filters = useMemo(() => {
    const f: Record<string, unknown> = {};
    if (modalities.length) f.modalities = modalities;
    if (sources.length) f.sources = sources;
    const days = TIME_RANGES.find((r) => r.id === range)?.days;
    if (days) f.timeFrom = new Date(Date.now() - days * 86_400_000).toISOString();
    return f;
  }, [modalities, sources, range]);

  const { data, loading, error, reload } = useEngineData(
    () => api.search({ query: '', filters, limit: 200 } as never),
    [JSON.stringify(filters)],
    { refreshOn: ['ingest.job'] },
  );

  const stats = useEngineData(() => api.stats(), [], { refreshOn: ['ingest.job'] });

  const toggle = <T,>(list: T[], v: T, set: (x: T[]) => void) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const hits = (data?.hits ?? []) as SearchHit[];
  const hasFilter = modalities.length > 0 || sources.length > 0 || range !== 'all';

  const addFolder = async () => {
    const dirs = await window.synorive.sys.pickFolders();
    if (dirs.length) await api.ingest({ targets: dirs, source: 'file', recursive: true });
  };

  return (
    <div className="page">
      <div className="page__header">
        <h1 className="page__title">{PAGE_TITLES.library}</h1>
        <span className="page__subtitle">
          {stats.data
            ? `库里共 ${stats.data.items.toLocaleString('zh-CN')} 条 · ${stats.data.chunks.toLocaleString('zh-CN')} 个文本块`
            : ''}
          {hasFilter && data ? ` · 筛出 ${data.totalEstimate} 条` : ''}
        </span>
      </div>

      <div className="filterbar">
        <FilterGroup label="类型">
          {MODALITIES.map((m) => (
            <button
              key={m.id}
              className={`chip${modalities.includes(m.id) ? ' chip--on' : ''}`}
              onClick={() => toggle(modalities, m.id, setModalities)}
            >
              {m.label}
            </button>
          ))}
        </FilterGroup>

        <FilterGroup label="来源">
          {SOURCES.map((s) => (
            <button
              key={s.id}
              className={`chip${sources.includes(s.id) ? ' chip--on' : ''}`}
              onClick={() => toggle(sources, s.id, setSources)}
            >
              {s.label}
            </button>
          ))}
        </FilterGroup>

        <FilterGroup label="时间">
          {TIME_RANGES.map((r) => (
            <button
              key={r.id}
              className={`chip${range === r.id ? ' chip--on' : ''}`}
              onClick={() => setRange(r.id)}
            >
              {r.label}
            </button>
          ))}
        </FilterGroup>

        {hasFilter && (
          <button
            className="chip"
            onClick={() => {
              setModalities([]);
              setSources([]);
              setRange('all');
            }}
            title="清空全部筛选"
          >
            <RotateCcw size={11} strokeWidth={2} /> 清空
          </button>
        )}
      </div>

      <div className="page__body page__body--flush">
        <PageState
          loading={loading}
          error={error}
          empty={hits.length === 0}
          emptyIcon={<FolderOpen size={30} strokeWidth={1.2} className="empty__glyph" />}
          emptyTitle={hasFilter ? '没有符合筛选条件的内容' : '库里还是空的'}
          emptyHint={
            hasFilter
              ? '换个筛选条件试试，或者点上面的「清空」。'
              : '选一个文件夹开始索引。文档、代码、图片、视频、音频都能收。'
          }
          emptyAction={
            hasFilter ? (
              <button className="btn" onClick={reload}>
                重新加载
              </button>
            ) : (
              <button className="btn btn--primary" onClick={addFolder}>
                <FolderOpen size={15} strokeWidth={1.7} /> 选一个文件夹
              </button>
            )
          }
          onRetry={reload}
        >
          <SearchResults hits={hits} />
        </PageState>
      </div>
    </div>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="filterbar__group">
      <span className="filterbar__label">{label}</span>
      <div className="filterbar__chips">{children}</div>
    </div>
  );
}
