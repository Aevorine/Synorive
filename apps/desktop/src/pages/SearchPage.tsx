import { FolderPlus, Loader2, Search as SearchIcon } from 'lucide-react';
import { ClipboardTray } from '../components/ClipboardTray';
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
  const { query, hits, stage, total, elapsedMs, loading, error, searched } = useSearch();
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

          {searched && hits.length === 0 && !loading && (
            <div className="empty">
              <div className="empty__title">没搜到「{query}」</div>
              <p className="empty__hint">
                试试换个说法、去掉筛选条件，或者把「语义相关」的滑块往右拉——
                语义权重高的时候，说法不一样也能匹配上。
              </p>
            </div>
          )}

          {hits.length > 0 && <SearchResults hits={hits} />}
        </div>

        <RankingPanel />
      </div>
    </div>
  );
}
