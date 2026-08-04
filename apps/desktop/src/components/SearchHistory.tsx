import { useEffect, useState } from 'react';
import { BellPlus, Clock, Loader2, RotateCcw } from 'lucide-react';
import type { SearchFilters } from '@synorive/shared-types';
import { history, type Snapshot } from '../lib/undo';
import { labApi } from '../lib/labApi';
import { useSearch, type Preset } from '../lib/useSearch';
import { useApp } from '../lib/store';

/**
 * D4 搜索历史 / D5 存成监控
 * ====================================================================
 * ── D4：回到那次搜索，**连筛选和权重一起回去** ────────────
 * 只填回查询词是不够的：用户回到"十分钟前那一屏"，看到的是同一个词
 * 配着**现在**的筛选，结果对不上他记忆里的样子，而他找不出哪里不一样。
 * 这个坑闪回（Flashback）踩过一次，这里按同一套字段名还原。
 *
 * ── D5：这次搜索存成监控 ─────────────────────────────────
 * 「搜过一次、以后有新的告诉我」是这个软件最像"有用"的一件事，
 * 但在此之前它藏在研究工作台的订阅面板里 —— 用户搜完东西，
 * 不会想到要切到另一个页面去把刚才那句话再打一遍。
 * **在搜完的那一刻给按钮**，才是它该出现的位置。
 *
 * 🔴 存监控**不会立刻联网跑**。存下来之后要不要现在跑一次由用户点，
 *    在搜索页顺手点一个按钮就往外发一批请求，是他没同意过的事。
 */

function ago(t: number): string {
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 60) return '刚刚';
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return new Date(t).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

export function SearchHistory() {
  const [snaps, setSnaps] = useState<Snapshot[]>(() => history.snapshots());
  const [watching, setWatching] = useState<string | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});

  const query = useSearch((s) => s.query);
  const setQuery = useSearch((s) => s.setQuery);
  const setFilters = useSearch((s) => s.setFilters);
  const setPreset = useSearch((s) => s.setPreset);
  const setPage = useApp((s) => s.setPage);
  const setInputMode = useApp((s) => s.setInputMode);
  const project = useApp((s) => s.activeProjectName);

  // history 是个手写的可订阅对象，不是 store —— 订阅它而不是轮询，
  // 否则历史面板挂着的时候会一直定时重渲染
  useEffect(() => history.subscribe(() => setSnaps(history.snapshots())), []);

  const restore = (s: Snapshot) => {
    // 🔴 顺序：先筛选和预设，**最后**查询词。
    //    setQuery 会触发搜索，反过来的话第一次搜索用的还是旧筛选，
    //    结果闪一下才变成对的 —— 而那一闪会让用户以为点错了
    const f = s.state['filters'];
    if (f && typeof f === 'object') setFilters(f as SearchFilters);
    const p = s.state['preset'];
    if (typeof p === 'string') setPreset(p as Preset);
    setInputMode('find');
    setPage('search');
    const q = s.state['query'];
    if (typeof q === 'string') setQuery(q);
  };

  const saveAsWatch = async (label: string, q: string) => {
    if (!q.trim()) return;
    setWatching(q);
    try {
      // A5：归在项目下时给监控加个前缀，「今日」页上一眼看出它属于哪个课题。
      // **只加前缀不改 query** —— 改了查询词就不是用户存的那次搜索了
      const withProj = project ? `[${project}] ${label || q}` : label || q;
      await labApi.addWatch({ query: q, label: withProj, intervalHours: 24 });
      setNote((n) => ({ ...n, [q]: '已存成监控，有新内容会出现在「今日」' }));
    } catch (e) {
      setNote((n) => ({ ...n, [q]: e instanceof Error ? e.message : '存失败了' }));
    } finally {
      setWatching(null);
    }
  };

  return (
    <section className="hist">
      <div className="syn-subhead">
        <Clock size={13} strokeWidth={1.8} />
        搜索历史
      </div>

      {/* D5：当前这次搜索直接存成监控。放在历史列表**上面** ——
          用户刚搜完，想盯住的是"刚才这个"，不是列表里某条旧的 */}
      {query.trim() && (
        <div className="hist__now">
          <span className="hist__nowq" title={query}>
            {query}
          </span>
          <button
            className="btn"
            onClick={() => void saveAsWatch(query, query)}
            disabled={watching === query}
            title="以后每天自动重搜一次，只把新出现的告诉你（存下来不会立刻联网）"
          >
            {watching === query ? (
              <Loader2 size={13} className="spin" strokeWidth={2} />
            ) : (
              <BellPlus size={13} strokeWidth={1.8} />
            )}
            盯住它
          </button>
        </div>
      )}
      {note[query] && <div className="hist__note">{note[query]}</div>}

      {snaps.length === 0 ? (
        <p className="hist__empty">还没有历史——搜过几次之后这里会记下来。</p>
      ) : (
        <ul className="hist__list">
          {snaps.map((s) => {
            const q = typeof s.state['query'] === 'string' ? (s.state['query'] as string) : '';
            return (
              <li key={s.id} className="hist__item">
                <button className="hist__go" onClick={() => restore(s)} title="回到那次搜索（连筛选和排序一起）">
                  <RotateCcw size={12} strokeWidth={1.8} />
                  <span className="hist__label">{s.label || q || '（空）'}</span>
                  <span className="hist__at">{ago(s.at)}</span>
                </button>
                {q && (
                  <button
                    className="hist__watch"
                    onClick={() => void saveAsWatch(s.label, q)}
                    disabled={watching === q}
                    title="把这次搜索存成监控"
                    aria-label={`把「${s.label}」存成监控`}
                  >
                    {watching === q ? (
                      <Loader2 size={12} className="spin" strokeWidth={2} />
                    ) : (
                      <BellPlus size={12} strokeWidth={1.8} />
                    )}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
