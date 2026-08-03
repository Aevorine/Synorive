import { useState } from 'react';
import { FolderPlus, Loader2, Search as SearchIcon } from 'lucide-react';
import { ClipboardTray } from '../components/ClipboardTray';
import { QuestionsPanel } from '../components/QuestionsPanel';
import { SceneStrip } from '../components/SceneStrip';
import { Recovery } from '../components/Recovery';
import { RankingPanel } from '../components/RankingPanel';
import { SearchResults } from '../components/SearchResults';
import { api } from '../lib/api';
import { useSearch } from '../lib/useSearch';
import { PAGE_TITLES, useApp } from '../lib/store';

const STAGE_LABEL: Record<string, string> = {
  instant: '最近打开',
  keyword: '关键词',
  semantic: '语义',
  reranked: '精排',
};

export function SearchPage() {
  /** N6：正在看哪一篇的「能回答什么」。null = 抽屉关着 */
  const [asking, setAsking] = useState<{ id: string; title: string } | null>(null);
  /** N3：正在看哪个视频的镜头带。null = 抽屉关着 */
  const [scening, setScening] = useState<
    { id: string; locator: string; title: string; sec?: number } | null
  >(null);
  const { query, hits, stage, total, elapsedMs, loading, error, searched, recovery, weakMatch, filters } = useSearch();
  const setQuery = useSearch((s) => s.setQuery);
  const setFilters = useSearch((s) => s.setFilters);
  const rerun = useSearch((s) => s.rerun);
  const engine = useApp((s) => s.engine);
  const ready = engine?.lifecycle === 'ready';

  const addFolder = async () => {
    const dirs = await window.synorive.sys.pickFolders();
    if (!dirs.length) return;
    await api.ingest({ targets: dirs, source: 'file', recursive: true });
  };

  return (
    <div className="page">
      <div className="page__header">
        <h1 className="page__title">{PAGE_TITLES.search}</h1>
        {searched && !loading && (
          <span className="page__subtitle">
            {total} 条结果 · {elapsedMs.toFixed(0)}ms
            {stage && ` · ${STAGE_LABEL[stage] ?? stage}`}
          </span>
        )}
        {loading && (
          <span className="page__subtitle page__subtitle--busy">
            <Loader2 size={13} className="spin" strokeWidth={2} />
            {stage ? `${STAGE_LABEL[stage]}结果已出，语义计算中…` : '搜索中…'}
          </span>
        )}
      </div>

      <div className="searchlayout">
        <div className="searchlayout__main">
          {error && <div className="banner banner--error">搜索出错：{error}</div>}

          {/* 没搜东西的时候才显示剪贴板 —— 搜索中它会抢走结果的位置 */}
          {!searched && <ClipboardTray />}

          {!searched && (
            <div className="empty">
              <SearchIcon size={30} strokeWidth={1.2} className="empty__glyph" />
              <div className="empty__title">在上面的搜索框里敲字就能搜</div>
              <p className="empty__hint">
                支持中文语义搜索——描述内容也能搜到，不用记文件名。
                也可以把文件、图片、链接直接拖进窗口。
              </p>
              {ready && (
                <button className="btn btn--primary" onClick={addFolder}>
                  <FolderPlus size={15} strokeWidth={1.7} />
                  选一个文件夹开始索引
                </button>
              )}
            </div>
          )}

          {/* D9：引擎算出补救方案就用它，每条都带确切条数、点一下直接重搜。
              拿不到方案（老引擎 / 补救本身出错）才退回原来那段泛泛的提示 ——
              退路必须留着，不能因为新功能出问题就让用户对着一片空白。 */}
          {searched && hits.length === 0 && !loading && (
            recovery ? (
              <Recovery
                plan={recovery}
                onRetry={(next) => {
                  if (next.drop?.length) {
                    const f: Record<string, unknown> = { ...filters };
                    for (const k of next.drop) delete f[k];
                    setFilters(f as typeof filters);
                  }
                  if (next.query !== undefined) setQuery(next.query);
                  else rerun();
                }}
              />
            ) : (
              <div className="empty">
                <div className="empty__title">没搜到「{query}」</div>
                <p className="empty__hint">
                  试试换个说法、去掉筛选条件，或者把「语义相关」的滑块往右拉——
                  语义权重高的时候，说法不一样也能匹配上。
                </p>
              </div>
            )
          )}

          {/* 弱匹配：结果照给，但先摆一条说明 + 补救建议。
              把结果删掉是错的 —— 实测正确答案和纯噪声的相似度只差 0.0045，
              删了会连真答案一起删。说清楚比替用户做决定好。 */}
          {hits.length > 0 && weakMatch && recovery && (
            <Recovery
              plan={recovery}
              weak
              onRetry={(next) => {
                if (next.drop?.length) {
                  const f: Record<string, unknown> = { ...filters };
                  for (const k of next.drop) delete f[k];
                  setFilters(f as typeof filters);
                }
                if (next.query !== undefined) setQuery(next.query);
                else rerun();
              }}
            />
          )}

          {hits.length > 0 && (
            <SearchResults hits={hits} onAsk={(id, t) => setAsking({ id, title: t })}
              onScenes={(id, loc, t, sec) => setScening({ id, locator: loc, title: t, sec })} />
          )}
        </div>

        <RankingPanel />
      </div>

      {scening && (
        <aside className="qp" role="dialog" aria-label="视频镜头">
          <header className="qp__head">
            <h3>{scening.title}</h3>
            <button className="qp__close" onClick={() => setScening(null)} aria-label="关闭">
              ×
            </button>
          </header>
          <SceneStrip itemId={scening.id} locator={scening.locator} focusSec={scening.sec} />
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
