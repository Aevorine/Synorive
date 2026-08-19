import { useMemo, useState } from 'react';
import { FolderOpen, RotateCcw } from 'lucide-react';
import type { Modality, SearchHit, SourceKind } from '@synorive/shared-types';
import { PageState } from '../components/PageState';
import { QuestionsPanel } from '../components/QuestionsPanel';
import { SceneStrip } from '../components/SceneStrip';
import { ChapterList } from '../components/ChapterList';
import { DupCleanup } from '../components/DupCleanup';
import { TrashPanel } from '../components/TrashPanel';
import { SearchResults } from '../components/SearchResults';
import { api } from '../lib/api';
import { useEngineData } from '../lib/useEngineData';

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
  /** N6：正在看哪一篇的「能回答什么」。null = 抽屉关着 */
  const [asking, setAsking] = useState<{ id: string; title: string } | null>(null);
  /** N3：正在看哪个视频的镜头带。null = 抽屉关着 */
  const [scening, setScening] = useState<
    { id: string; locator: string; title: string; sec?: number } | null
  >(null);
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
      <div className="page__meta">
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
          <SearchResults hits={hits} onAsk={(id, t) => setAsking({ id, title: t })}
              onScenes={(id, loc, t, sec) => setScening({ id, locator: loc, title: t, sec })} />
        </PageState>

        {/* E9 近重复清理：放在库列表**下面**而不是筛选栏里 ——
            它是"整理这个库"的动作，不是"看这个库的某一部分"。
            混进筛选栏会让人以为点一下就把重复的筛出来了 */}
        <section className="panel">
          <h2 className="panel__title">清理重复图</h2>
          <DupCleanup />
        </section>

        {/* 回收站跟清理重复图放一块——都是"整理这个库"的动作 */}
        <section className="panel">
          <h2 className="panel__title">回收站</h2>
          <TrashPanel />
        </section>
      </div>

      {/* N6：抽屉盖在列表右侧，不替换列表 ——
          用户是在"浏览库"的过程中顺手问一篇，替换掉列表会打断这件事 */}
      {scening && (
        <aside className="qp" role="dialog" aria-label="视频镜头">
          <header className="qp__head">
            <h3>{scening.title}</h3>
            <button className="qp__close" onClick={() => setScening(null)} aria-label="关闭">
              ×
            </button>
          </header>
          <SceneStrip itemId={scening.id} locator={scening.locator} focusSec={scening.sec} />
          {/* A6 章节目录跟在缩略条下面：缩略条给"看起来是什么"，
              目录给"讲了几件事"。点一章就把缩略条的焦点挪过去，
              两块用的是同一个 focusSec，不需要各自维护一份位置 */}
          <ChapterList
            itemId={scening.id}
            onJump={(sec) => setScening((s) => (s ? { ...s, sec } : s))}
          />
        </aside>
      )}

      {asking && (
        <QuestionsPanel
          itemId={asking.id}
          title={asking.title}
          onClose={() => setAsking(null)}
        />
      )}
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
