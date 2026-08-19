import { useCallback, useEffect, useState } from 'react';
import {
  BellRing,
  Database,
  FolderPlus,
  Loader2,
  MessageCircleQuestion,
  Play,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import type { SearchHit } from '@synorive/shared-types';
import { api } from '../lib/api';
import { BriefingPanel } from '../components/BriefingPanel';
import { labApi, type WatchItem } from '../lib/labApi';
import { projectApi, type ResearchProject } from '../lib/webApi';
import { useApp } from '../lib/store';
import { useSearch } from '../lib/useSearch';

/**
 * A2 「今日」—— 打开软件就有东西看
 * ====================================================================
 * 用户原话：「目前项目功能没有实际作用，也没有商业价值，一点实用价值也没有」
 *
 * 🔴 **这一页要治的是「没有回访理由」，不是「功能不够」。**
 *    在它之前，打开 Synorive 看到的是一个空搜索框：**软件在等用户想起
 *    要搜什么。** 而人不会每天都恰好想起一个要搜的东西 ——
 *    于是装了、用过两次、再也不打开。功能加得再多都改不了这一点，
 *    因为问题不在功能，在于**这个软件从来没有主动说过一句话**。
 *
 *    这一页把已经存在但用户看不见的东西端出来：到期的订阅、刚进库的内容、
 *    没读完的研究、失败待处理的条目。**全都是本来就有的数据**，
 *    区别只是以前要用户自己去四个页面里翻。
 *
 * ── 三条设计约束 ─────────────────────────────────────────
 * ① **每张卡片都要能一键做点什么。** 只报数字不给动作的卡片是仪表盘不是首页，
 *    用户看两天就不看了。
 * ② **拿不到数据就不显示那张卡，不显示"加载失败"。** 首页天天要看，
 *    一条常驻的红色报错比少一张卡烦人得多。
 * ③ **不自动联网。** 到期的订阅只**列出来**并给一个「跑一次」按钮 ——
 *    打开软件就自动往外发一批搜索请求，是用户没同意过的事。
 */

interface Deck {
  stats: { items: number; ready: number; failed: number; chunks: number } | null;
  watches: WatchItem[];
  due: string[];
  recent: SearchHit[];
  projects: ResearchProject[];
}

const EMPTY: Deck = { stats: null, watches: [], due: [], recent: [], projects: [] };

export function TodayPage() {
  const [deck, setDeck] = useState<Deck>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<string | null>(null);
  const [ranNote, setRanNote] = useState<Record<string, string>>({});

  const setPage = useApp((s) => s.setPage);
  const openStage = useApp((s) => s.openStage);
  const setInputMode = useApp((s) => s.setInputMode);
  const setQuery = useSearch((s) => s.setQuery);
  const engine = useApp((s) => s.engine);
  const ready = engine?.lifecycle === 'ready';

  /**
   * 四路并发拉数据，**每一路各自 catch**。
   *
   * 🔴 用 `Promise.all` 是错的：任何一路失败会让整页空白。
   *    首页最不能接受的就是"一个次要接口挂了导致整页没了" ——
   *    所以每路自己兜底成空值，拿到多少显示多少。
   */
  const load = useCallback(async () => {
    if (!ready) {
      // 🔴 引擎重启期间（比如切库、改联网Key）这一页原来什么都不做，
      // 会把上一个引擎/上一个库留下的旧卡片原样晾在那，用户看不出
      // 这是过期数据还是当前数据。其余五个页面共用的 useEngineData 都
      // 在引擎未就绪时清空数据（见 lib/useEngineData.ts），这里补齐同一条硬化逻辑。
      setDeck(EMPTY);
      setLoading(true);
      return;
    }
    setLoading(true);
    const [stats, watches, recent, projects] = await Promise.all([
      api.stats().catch(() => null),
      labApi.watches().catch(() => ({ watches: [] as WatchItem[], due: [] as string[] })),
      api
        // 空查询 = 引擎走"按时间倒序列内容"那条路（recall_by_filter）
        .search({ query: '', limit: 8, stage: 'keyword' })
        .then((r) => r.hits ?? [])
        .catch(() => [] as SearchHit[]),
      projectApi
        .list('open')
        .then((r) => r.projects ?? [])
        .catch(() => [] as ResearchProject[]),
    ]);
    setDeck({
      stats,
      watches: watches.watches ?? [],
      due: watches.due ?? [],
      recent,
      projects: projects.slice(0, 4),
    });
    setLoading(false);
  }, [ready]);

  useEffect(() => {
    void load();
  }, [load]);

  const dueWatches = deck.watches.filter((w) => deck.due.includes(w.id));

  const runWatch = async (w: WatchItem) => {
    setRunning(w.id);
    try {
      const r = await labApi.runWatch(w.id);
      setRanNote((s) => ({
        ...s,
        [w.id]: r.freshCount > 0 ? `新增 ${r.freshCount} 条` : (r.note || '没有新内容'),
      }));
    } catch (e) {
      setRanNote((s) => ({ ...s, [w.id]: e instanceof Error ? e.message : '跑失败了' }));
    } finally {
      setRunning(null);
      void load();
    }
  };

  return (
    <div className="syn-page">
      <header className="syn-page__head">
        {loading && (
          <span className="syn-page__sub">
            <Loader2 size={12} className="spin" strokeWidth={2} /> 正在看有什么新东西…
          </span>
        )}
        <span className="syn-page__spacer" />
        <button
          className="btn"
          onClick={() => void load()}
          disabled={loading || !ready}
          title="重新看一遍"
        >
          刷新
        </button>
      </header>

      <div className="syn-page__body">
        {/* ── 主行动区：问一句话。放在最上面、最大 ──────────
            「重要功能显示在核心位置」—— 今日页最重要的功能不是看数字，
            是让用户想起"我可以直接问它" */}
        <button className="today__hero" onClick={openStage}>
          <MessageCircleQuestion size={22} strokeWidth={1.6} />
          <span className="today__herotext">
            <strong>问一句话</strong>
            <span>从你自己的资料里找答案，每句都能点回原文</span>
          </span>
          <span className="today__herokbd">
            <kbd className="kbd">Ctrl</kbd>
            <kbd className="kbd">K</kbd>
          </span>
        </button>

        {/* 提案 36：主动把"你可能忘了的东西"端上来。
            下面那些卡片报的是"现在有什么"，这一块报的是"你还没处理什么" */}
        <BriefingPanel
          onOpen={(it) => {
            // 在资源管理器里定位而不是直接打开 —— 简报里端上来的很多是
            // 用户已经忘了的东西，先让他看见它在哪，比直接用默认程序拉起来稳妥
            void api.recordOpen(it.id);
            void window.synorive.sys.reveal(it.locator);
          }}
        />

        <div className="syn-grid syn-grid--wide today__grid">
          {/* ── 到期的订阅 ───────────────────────────────── */}
          {deck.watches.length > 0 && (
            <section className="syn-panel">
              <header className="syn-panel__head">
                <BellRing size={15} strokeWidth={1.7} />
                <span className="syn-panel__title">盯着的题目</span>
                <span className="syn-panel__count">
                  {dueWatches.length > 0 ? `${dueWatches.length} 个该跑了` : '都是最新的'}
                </span>
              </header>
              <div className="syn-panel__body syn-panel__body--flush">
                {(dueWatches.length ? dueWatches : deck.watches.slice(0, 4)).map((w) => (
                  <div key={w.id} className="syn-item">
                    <div className="syn-item__main">
                      <div className="syn-item__title">{w.label || w.query}</div>
                      <div className="syn-item__meta">
                        {ranNote[w.id]
                          ? ranNote[w.id]
                          : w.lastRun
                            ? `上次跑于 ${new Date(w.lastRun * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · 已见过 ${w.seenCount} 条`
                            : '还没跑过'}
                      </div>
                    </div>
                    {/* 🔴 只给按钮，不自动跑 —— 打开软件就往外发一批请求
                        是用户没同意过的事 */}
                    <button
                      className="btn"
                      onClick={() => void runWatch(w)}
                      disabled={running === w.id}
                      title="现在联网跑一次这个订阅"
                    >
                      {running === w.id ? (
                        <Loader2 size={13} className="spin" strokeWidth={2} />
                      ) : (
                        <Play size={13} strokeWidth={1.8} />
                      )}
                      跑一次
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* ── 最近进库 ─────────────────────────────────── */}
          {deck.recent.length > 0 && (
            <section className="syn-panel">
              <header className="syn-panel__head">
                <Sparkles size={15} strokeWidth={1.7} />
                <span className="syn-panel__title">最近进库的</span>
                <span className="syn-panel__count">{deck.recent.length} 条</span>
              </header>
              <div className="syn-panel__body syn-panel__body--flush">
                {deck.recent.slice(0, 6).map((h) => (
                  <button
                    key={h.item.id}
                    className="syn-item"
                    onClick={() => {
                      setInputMode('find');
                      setQuery(h.item.title || '');
                      setPage('search');
                    }}
                    title={h.item.locator}
                  >
                    <div className="syn-item__main">
                      <div className="syn-item__title">{h.item.title || h.item.locator}</div>
                      <div className="syn-item__meta">{h.item.locator}</div>
                    </div>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* ── 没读完的研究 ─────────────────────────────── */}
          {deck.projects.length > 0 && (
            <section className="syn-panel">
              <header className="syn-panel__head">
                <Database size={15} strokeWidth={1.7} />
                <span className="syn-panel__title">没结的研究</span>
                <span className="syn-panel__count">{deck.projects.length} 个</span>
              </header>
              <div className="syn-panel__body syn-panel__body--flush">
                {deck.projects.map((p) => (
                  <button
                    key={p.id}
                    className="syn-item"
                    onClick={() => setPage('research')}
                    title={p.query}
                  >
                    <div className="syn-item__main">
                      <div className="syn-item__title">{p.title || p.query}</div>
                      <div className="syn-item__meta">
                        跑过 {p.runCount} 轮 · 收了 {p.sourceCount} 份来源
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </section>
          )}

          {/* ── 库况。**只在有失败项时才醒目** ─────────────
              没有失败的时候它就是一行安静的数字，不该占用注意力 */}
          {deck.stats && (
            <section className="syn-panel">
              <header className="syn-panel__head">
                <Database size={15} strokeWidth={1.7} />
                <span className="syn-panel__title">库里现在有什么</span>
              </header>
              <div className="syn-panel__body">
                <div className="today__nums">
                  <span className="today__num">
                    <b>{deck.stats.items}</b> 条内容
                  </span>
                  <span className="today__num">
                    <b>{deck.stats.chunks}</b> 个可搜片段
                  </span>
                  {deck.stats.failed > 0 && (
                    <button
                      className="today__num today__num--bad"
                      onClick={() => setPage('analyze')}
                      title="去分析中心看失败清单"
                    >
                      <TriangleAlert size={13} strokeWidth={1.8} />
                      <b>{deck.stats.failed}</b> 条没分析成功
                    </button>
                  )}
                </div>
              </div>
            </section>
          )}
        </div>

        {/* ── 空库：整页只剩一件事可做，就别摆一堆空卡片 ── */}
        {!loading && ready && deck.stats?.items === 0 && (
          <div className="syn-blank">
            <FolderPlus size={30} strokeWidth={1.2} className="syn-blank__glyph" />
            <div className="syn-blank__title">库里还是空的</div>
            <p className="syn-blank__hint">
              选一个文件夹就能开始。关键词层几秒内可用，语义层在后台补齐——
              不用等它全部跑完才能搜。
            </p>
            <button
              className="btn btn--primary"
              onClick={() => {
                void (async () => {
                  const dirs = await window.synorive.sys.pickFolders();
                  if (dirs.length) {
                    await api.ingest({ targets: dirs, source: 'file', recursive: true });
                    setPage('analyze');
                  }
                })();
              }}
            >
              <FolderPlus size={15} strokeWidth={1.7} />
              选一个文件夹
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
