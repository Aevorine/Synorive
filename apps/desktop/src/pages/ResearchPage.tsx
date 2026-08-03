import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BookMarked,
  Globe,
  Loader2,
  Search,
  Sparkles,
} from 'lucide-react';
import { PageState } from '../components/PageState';
import { ConsistencyMatrix } from '../components/ConsistencyMatrix';
import { EngineHealthPanel } from '../components/EngineHealthPanel';
import { ProjectBar } from '../components/ProjectBar';
import {
  DEFAULT_RESEARCH_OPTIONS,
  ResearchControls,
  type ResearchOptions,
} from '../components/ResearchControls';
import { LinkTrail } from '../components/LinkTrail';
import { OmniFeed } from '../components/OmniFeed';
import { ResearchProgress } from '../components/ResearchProgress';
import { VerificationPanel } from '../components/VerificationPanel';
import { api } from '../lib/api';
import { PAGE_TITLES, useApp } from '../lib/store';
import {
  webApi,
  type ReadUrlResponse,
  type UnifiedResponse,
  type Briefing,
  type EngineDescriptor,
  type Evidence,
  type GeneratedBriefing,
  type ResearchResponse,
  type ScholarPaper,
  type WebResultItem,
  type WebSearchResponse,
} from '../lib/webApi';

/**
 * 研究工作台 —— 锚点 10 落地页
 * ============================================================
 * 三种模式，共用同一条查询栏：
 *   快搜   直接问 /api/web/search，一份带可信度标注的结果列表（W1~W4）
 *   深挖   问 /api/web/research，出摘录简报（R5/R7/R8 左栏），
 *          右栏是可选的云端生成版（R8 右栏），两栏并排、随时能对照
 *   文献   问 /api/web/scholar，按 DOI 合并的论文清单（L1）
 *
 * 三个模式都不是"输入即搜"——联网请求有真实的时间和（学术源之外）
 * 潜在的费用成本，回车/点按钮才发，这和本地搜索页的实时联想是刻意的区别。
 */

type Mode = 'quick' | 'research' | 'scholar';

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: 'quick', label: '快搜', hint: '多引擎并发搜索，按可信度标注排序' },
  { id: 'research', label: '深挖', hint: '搜索 + 抓正文 + 出带出处的简报，较慢' },
  { id: 'scholar', label: '文献', hint: '五家学术源，按 DOI 合并，带被引数' },
];

const TIME_RANGES: { id: string; label: string }[] = [
  { id: '', label: '不限时间' },
  { id: 'day', label: '一天内' },
  { id: 'week', label: '一周内' },
  { id: 'month', label: '一月内' },
  { id: 'year', label: '一年内' },
];

export function ResearchPage() {
  // 叫 modeState 是因为 runSearch 里有一个同名的局部变量（模式可被显式覆盖）——
  // 同名会让"这里读到的到底是哪个"变成一道阅读理解题
  const [mode, setMode] = useState<Mode>('quick');
  const modeState = mode;
  const setPage = useApp((s) => s.setPage);
  const [unified, setUnified] = useState<UnifiedResponse | null>(null);
  const [readResult, setReadResult] = useState<ReadUrlResponse | null>(null);
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [timeRange, setTimeRange] = useState('');
  const [enginesOpen, setEnginesOpen] = useState(false);
  const [healthOpen, setHealthOpen] = useState(false);
  // S4/S5/S8 + V 档位。快搜只用得上其中的 preset/expand，
  // 但**共用同一份状态**——分成两套的话，用户在深挖里选的"只看官方文档"
  // 切回快搜就悄悄失效了，那种不一致最难排查
  const [opts, setOpts] = useState<ResearchOptions>(DEFAULT_RESEARCH_OPTIONS);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [engineList, setEngineList] = useState<EngineDescriptor[]>([]);
  const [enabledEngines, setEnabledEngines] = useState<Set<string> | null>(null); // null = 用服务端默认
  const [rendererAvailable, setRendererAvailable] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quickResult, setQuickResult] = useState<WebSearchResponse | null>(null);
  const [researchResult, setResearchResult] = useState<ResearchResponse | null>(null);
  const [scholarResult, setScholarResult] = useState<ScholarPaper[] | null>(null);
  const [scholarMeta, setScholarMeta] = useState<{
    total: number;
    merged: number;
    sources: { id: string; outcome: string }[];
  } | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // 引擎清单只在打开面板那一刻拉一次，不用常驻订阅——这块几乎不变
  useEffect(() => {
    if (!enginesOpen || engineList.length) return;
    void webApi.engines().then((r) => {
      setEngineList(r.engines);
      setRendererAvailable(r.health.rendererAvailable);
    });
  }, [enginesOpen, engineList.length]);

  /**
   * `modeOverride` 是给统一投喂条用的：`setMode` 是异步的，
   * 紧接着读 `mode` 拿到的还是上一个值 —— 不显式传的话，
   * 预判说"走深挖"而实际跑的是上一次的模式，且**不会报错**，
   * 只是结果不对。这类竞态是最难查的一类。
   */
  const runSearch = async (q: string, modeOverride?: Mode) => {
    const mode = modeOverride ?? modeState;
    const trimmed = q.trim();
    if (!trimmed) return;
    setQuery(trimmed);
    setLoading(true);
    setError(null);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const engines = enabledEngines ? [...enabledEngines] : undefined;
    try {
      if (mode === 'quick') {
        const r = await webApi.search(
          {
            query: trimmed,
            limit: 30,
            engines,
            timeRange: timeRange || undefined,
            preset: opts.preset,
            // 快搜的 expand 默认跟着深挖走，但**快搜要的就是快** ——
            // 扩写要多花一个维基往返，所以只在用户显式开了跨语言时才带上
            expand: opts.expand,
          },
          controller.signal,
        );
        setQuickResult(r);
      } else if (mode === 'research') {
        const r = await webApi.research(
          {
            query: trimmed,
            fetch: 6,
            limit: 20,
            rounds: opts.rounds,
            expand: opts.expand,
            preset: opts.preset,
            verifyLevel: opts.verifyLevel,
          },
          controller.signal,
        );
        setResearchResult(r);
      } else {
        const r = await webApi.scholar({ query: trimmed, limit: 30 }, controller.signal);
        setScholarResult(r.papers);
        setScholarMeta({
          total: r.totalBeforeMerge, merged: r.mergedCount,
          sources: r.sources.map((s) => ({ id: s.id, outcome: s.outcome })),
        });
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  /**
   * 统一投喂条按下之后往哪走（N1）。
   *
   * **每条路线都真的接到东西上**，没有"点了没反应"的路线 ——
   * 一个给出六个选项、其中三个点了不动的界面，比只给三个选项糟得多。
   */
  const handleOmni = async (route: string, textIn: string, paths: string[]) => {
    setInput(textIn);

    // 文件：走已有的入库流水线（和拖进窗口是同一条），跳到分析中心看进度
    if (paths.length) {
      if (route === 'reverse-image') {
        setLoading(true);
        setError(null);
        try {
          const r = await api.byImage({ path: paths[0], limit: 20 });
          setPage('search');
          void r; // 结果由搜索页接管展示，这里只负责把用户送过去
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setLoading(false);
        }
        return;
      }
      void api.ingest({ targets: paths, source: 'file', recursive: true, priority: 'high' });
      setPage('analyze');
      return;
    }

    if (route === 'unified') {
      setLoading(true);
      setError(null);
      setQuery(textIn);
      try {
        setUnified(await webApi.unified({ query: textIn, limit: 12, preset: opts.preset }));
        setMode('quick');
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
      return;
    }

    setUnified(null);
    setReadResult(null);

    // N4：给一个链接就顺藤摸瓜 —— 抓正文 + 它引用了谁 + 谁在讨论它。
    // 只抓正文没什么意义（点开浏览器就能看），有意义的是那两张链接网
    if (route === 'read-url') {
      setLoading(true);
      setError(null);
      setQuery(textIn);
      try {
        setReadResult(await webApi.read({ url: textIn, trail: true, maxChars: 8000 }));
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
      return;
    }

    const next: Mode =
      route === 'scholar' ? 'scholar' : route === 'quick' ? 'quick' : 'research';
    setMode(next);
    await runSearch(textIn, next);
  };

  const toggleEngine = (id: string) => {
    setEnabledEngines((prev) => {
      const base = prev ?? new Set(engineList.filter((e) => e.defaultOn).map((e) => e.id));
      const next = new Set(base);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const hasResult =
    !!unified ||
    !!readResult ||
    (mode === 'quick' && !!quickResult) ||
    (mode === 'research' && !!researchResult) ||
    (mode === 'scholar' && !!scholarResult);

  const subtitle = useMemo(() => {
    if (!query) return '';
    if (mode === 'quick' && quickResult) {
      return `${quickResult.trustSummary?.note ?? `${quickResult.results.length} 条结果`}　${quickResult.elapsedMs}ms`;
    }
    if (mode === 'research' && researchResult) {
      return `抓到正文 ${researchResult.fetched} 篇　${researchResult.elapsedMs}ms`;
    }
    if (mode === 'scholar' && scholarMeta) {
      return `${scholarMeta.total} 条 → 合并 ${scholarMeta.merged} 篇`;
    }
    return '';
  }, [mode, query, quickResult, researchResult, scholarMeta]);

  return (
    <div className="page">
      <div className="page__header">
        <h1 className="page__title">{PAGE_TITLES.research}</h1>
        <span className="page__subtitle">{subtitle}</span>
      </div>

      {/* N1/N2：一个框吃图片/视频/链接/文件/一句话，打完字那一瞬间就出预判卡。
          下面那排模式按钮保留着 —— 预判判错的时候，改正的成本必须是一次点击 */}
      <OmniFeed busy={loading} initial={input} onRun={handleOmni} />

      <form
        className="researchbar"
        onSubmit={(e) => {
          e.preventDefault();
          void runSearch(input);
        }}
      >
        <div className="researchbar__modes">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              className={`chip${mode === m.id ? ' chip--on' : ''}`}
              title={m.hint}
              onClick={() => setMode(m.id)}
            >
              {m.label}
            </button>
          ))}
        </div>

        <div className="urlbox researchbar__query">
          <Search size={15} className="urlbox__icon" strokeWidth={1.8} />
          <input
            className="urlbox__input"
            placeholder={
              mode === 'quick' ? '搜整个网络…' : mode === 'research' ? '想查清楚什么问题…' : '文献检索式，英文效果更好…'
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
        </div>

        {mode !== 'scholar' && (
          <div className="filterbar__chips">
            {TIME_RANGES.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`chip${timeRange === t.id ? ' chip--on' : ''}`}
                onClick={() => setTimeRange(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}

        <button
          type="button"
          className={`btn btn--sm${enginesOpen ? ' btn--primary' : ''}`}
          onClick={() => setEnginesOpen((v) => !v)}
        >
          <Globe size={13} strokeWidth={1.8} /> 引擎
        </button>

        {/* S1：哪家今天能用不该靠猜。仪表盘给每家一句人话 */}
        <button
          type="button"
          className={`btn btn--sm${healthOpen ? ' btn--primary' : ''}`}
          onClick={() => setHealthOpen((v) => !v)}
          title="看每家引擎最近的成功率、耗时和为什么用不了"
        >
          <Activity size={13} strokeWidth={1.8} /> 健康
        </button>

        <button className="btn btn--primary" type="submit" disabled={loading}>
          {loading ? <Loader2 size={14} className="spin" strokeWidth={2} /> : '搜索'}
        </button>
      </form>

      {/* 四个旋钮。文献模式用不上（学术源没有"定向站点"和"多轮追问"这两回事） */}
      {mode !== 'scholar' && (
        <ResearchControls value={opts} onChange={setOpts} compact={mode === 'quick'} />
      )}

      {healthOpen && <EngineHealthPanel onClose={() => setHealthOpen(false)} />}

      {/* U2：深挖那十几秒不该只有一个转圈图标。
          「正在反向搜辟谣/质疑」比「67%」有用得多 ——
          前者能让你判断这一步值不值得等，后者不能 */}
      {mode === 'research' && <ResearchProgress active={loading} />}

      {/* 深挖出结果之后才显示项目条 —— 没结果时"存入项目"这个按钮没有意义 */}
      {mode === 'research' && researchResult && (
        <ProjectBar
          result={researchResult}
          query={query}
          activeId={projectId}
          onActiveChange={setProjectId}
        />
      )}

      {enginesOpen && (
        <div className="enginepicker">
          {(['web', 'scholar'] as const).map((group) => (
            <div key={group} className="enginepicker__group">
              <span className="filterbar__label">{group === 'web' ? '网页搜索' : '学术文献源'}</span>
              <div className="filterbar__chips">
                {engineList
                  .filter((e) => e.group === group)
                  .map((e) => {
                    const on = enabledEngines ? enabledEngines.has(e.id) : e.defaultOn;
                    const blocked = e.needsBrowser && !rendererAvailable;
                    return (
                      <button
                        key={e.id}
                        type="button"
                        className={`chip${on ? ' chip--on' : ''}`}
                        onClick={() => toggleEngine(e.id)}
                        title={e.note}
                      >
                        {e.label}
                        {e.needsKey && <span className="enginepicker__badge">要 Key</span>}
                        {blocked && <span className="enginepicker__badge enginepicker__badge--warn">未连桌面端</span>}
                      </button>
                    );
                  })}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="page__body page__body--flush">
        <PageState
          loading={loading}
          error={error}
          empty={!hasResult}
          emptyIcon={<Search size={30} strokeWidth={1.2} className="empty__glyph" />}
          emptyTitle="还没有搜索"
          emptyHint="选一个模式、输个问题，回车或点搜索。快搜给一份可信度标注的结果列表；深挖会抓正文出简报，慢一些但更完整；文献查五家学术源。"
        >
          {readResult && (
            <LinkTrail
              data={readResult}
              onDeepDive={() => {
                setReadResult(null);
                setMode('research');
                void runSearch(readResult.title || readResult.finalUrl, 'research');
              }}
            />
          )}
          {unified && <UnifiedView data={unified} onClose={() => setUnified(null)} />}
          {!unified && !readResult && mode === 'quick' && quickResult && (
            <QuickResults data={quickResult} />
          )}
          {!unified && !readResult && mode === 'research' && researchResult && (
            <ResearchSplit query={query} data={researchResult} />
          )}
          {!unified && !readResult && mode === 'scholar' && scholarResult && (
            <ScholarList papers={scholarResult} />
          )}
        </PageState>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════
// P5 本地库 × 网上，并排 + 冲突高亮
//
// 这是锚点 9「三件事一个入口」缺的最后一块：
// 「我自己有的」和「网上说的」之前永远在两个页面里，
// 而真正有价值的问题恰恰是 **两边对不对得上**。
// ════════════════════════════════════════════════════════════
function UnifiedView({ data, onClose }: { data: UnifiedResponse; onClose: () => void }) {
  const local = (data.local.results ?? []) as {
    itemId?: string;
    id?: string;
    title?: string;
    snippet?: string;
  }[];
  const web = data.web.results ?? [];

  return (
    <div className="unified">
      <div className="banner">
        {data.note}
        <button className="btn btn--sm" onClick={onClose}>
          退出并排视图
        </button>
      </div>

      {/* 冲突放最前面 —— 这才是并排视图存在的理由，
          埋在两栏结果下面等于没做 */}
      {data.conflicts.length > 0 && (
        <div className="unified__conflicts">
          <h3 className="panel__subtitle">
            ⚠ 你的资料和网上说法对不上（{data.conflicts.length} 处，没有判断哪边对）
          </h3>
          {data.conflicts.map((c, i) => (
            <div className="disputecard" key={i}>
              <div className="disputecard__pair">
                <p>
                  <span className="badge">你的资料</span>「{c.local.text}」
                  {c.local.title && <em>　—— {c.local.title}</em>}
                </p>
                <p>
                  <span className="badge badge--org">网上</span>「{c.web.text}」
                  {c.web.url && (
                    <a
                      href={c.web.url}
                      onClick={(e) => {
                        e.preventDefault();
                        void window.synorive.sys.openExternal(c.web.url!);
                      }}
                    >
                      　{c.web.site}
                    </a>
                  )}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="briefingsplit">
        <div className="briefingpane">
          <div className="briefingpane__head">
            <span className="briefingpane__title">你自己的资料</span>
            <span className="badge">本地，断网也在</span>
          </div>
          {data.local.error && <p className="panel__hint">本地检索失败：{data.local.error}</p>}
          {!data.local.error && !local.length && (
            <p className="panel__hint">库里没有相关内容。</p>
          )}
          {local.map((h) => (
            <div className="consensuscard" key={h.itemId ?? h.id ?? h.title}>
              <span className="consensuscard__topic">{h.title || '(无标题)'}</span>
              <p>{h.snippet}</p>
            </div>
          ))}
        </div>

        <div className="briefingpane">
          <div className="briefingpane__head">
            <span className="briefingpane__title">网上说的</span>
            <span className="badge badge--org">带可信度标注</span>
          </div>
          {data.web.unavailable && <p className="panel__hint">{data.web.unavailable}</p>}
          {data.web.error && <p className="panel__hint">联网检索失败：{data.web.error}</p>}
          {web.map((r) => (
            <WebResultCard key={r.url} item={r} />
          ))}
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════
// 快搜：单栏结果列表
// ════════════════════════════════════════════════════════════
function QuickResults({ data }: { data: WebSearchResponse }) {
  const [showExcluded, setShowExcluded] = useState(false);
  const failed = data.engines.filter((e) => e.outcome !== 'ok' && e.outcome !== 'empty');

  return (
    <div className="weblist">
      {failed.length > 0 && (
        <div className="banner banner--warn">
          {failed.map((e) => `${e.id}：${e.error ?? e.outcome}`).join('　')}
        </div>
      )}
      {data.results.map((r) => (
        <WebResultCard key={r.url} item={r} />
      ))}
      {data.excluded.length > 0 && (
        <div className="excludedbox">
          <button className="btn btn--sm" onClick={() => setShowExcluded((v) => !v)}>
            {showExcluded ? '收起' : `查看已排除的 ${data.excluded.length} 条`}
          </button>
          {showExcluded && (
            <div className="weblist">
              {data.excluded.map((r) => (
                <WebResultCard key={r.url} item={r} excluded />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WebResultCard({ item, excluded }: { item: WebResultItem; excluded?: boolean }) {
  const t = item.trust;
  return (
    <a
      className={`webcard${excluded ? ' webcard--excluded' : ''}`}
      href={item.url}
      onClick={(e) => {
        e.preventDefault();
        void window.synorive.sys.openExternal(item.url);
      }}
    >
      <div className="webcard__head">
        <span className="webcard__title">{item.title}</span>
        <ArrowUpRight size={13} className="webcard__extlink" strokeWidth={1.8} />
      </div>
      {item.snippet && <p className="webcard__snippet">{item.snippet}</p>}
      <div className="webcard__meta">
        <span className="webcard__site">{item.site}</span>
        {t && <span className={`badge webcard__tier webcard__tier--${t.tier}`}>{t.tierLabel}</span>}
        {t && t.independentSources >= 3 && (
          <span className="badge">{t.independentSources} 个独立来源</span>
        )}
        {t && t.independentSources === 1 && <span className="badge webcard__lone">孤证</span>}
        {t?.aiSuspect && <span className="badge webcard__lone">疑似批量生成</span>}
        {(item.engineCount ?? 1) > 1 && <span className="webcard__engines">{item.engineCount} 家引擎命中</span>}
      </div>
    </a>
  );
}

// ════════════════════════════════════════════════════════════
// 深挖：左摘录 / 右生成 并排
// ════════════════════════════════════════════════════════════
function ResearchSplit({ query, data }: { query: string; data: ResearchResponse }) {
  return (
    <>
      {/* S5：每一轮问了什么、为什么问。
          第二轮的查询词是我替用户决定去搜的，他有权知道为什么 ——
          不说的话，结果里冒出一批他没搜过的东西只会让人困惑 */}
      {data.rounds && data.rounds.length > 1 && (
        <div className="rounds">
          <span className="rounds__title">
            这次挖了 {data.rounds.length} 轮 —— 第二轮开始的问题是读完前一轮之后自己想出来的
          </span>
          {data.rounds.map((r) => (
            <div className="rounds__item" key={r.round}>
              <strong>
                第 {r.round} 轮
                {r.newResults != null && `（新增 ${r.newResults} 条）`}
              </strong>
              {r.skipped && <span className="rounds__why">　{r.skipped}</span>}
              {r.queries.map((q) => (
                <span key={q.text}>
                  <span className="rounds__q">{q.text}</span>
                  <span className="rounds__why">{q.why}</span>
                </span>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* V 组：主动核查。放在简报**上面** ——
          先知道"这批材料有没有人反驳过"，再读结论，顺序反了就白读 */}
      <VerificationPanel v={data.verification} />

      {/* V2：一致性矩阵。分歧是一对一对给的，矩阵才看得出"谁总跟别人不一样" */}
      <ConsistencyMatrix m={data.briefing.matrix} />

      <div className="briefingsplit">
        <div className="briefingpane">
          <div className="briefingpane__head">
            <span className="briefingpane__title">摘录简报</span>
            <span className="badge">原文逐字</span>
          </div>
          <ExtractBriefing briefing={data.briefing} />
        </div>
        <div className="briefingpane">
          <div className="briefingpane__head">
            <span className="briefingpane__title">生成版简报</span>
            <span className="badge badge--org">
              <Sparkles size={11} strokeWidth={2} /> AI 改写
            </span>
          </div>
          <GeneratedPanel query={query} briefing={data.briefing} />
        </div>
      </div>
    </>
  );
}

function ExtractBriefing({ briefing }: { briefing: Briefing }) {
  if (
    !briefing.consensus.length &&
    !briefing.disputes.length &&
    !briefing.timeline.length &&
    !briefing.numbers.length
  ) {
    return <p className="panel__hint">抓到的正文里没有摘出可用的证据——试试换个更具体的问法。</p>;
  }
  return (
    <div className="briefingbody">
      {briefing.disputes.length > 0 && (
        <section>
          <h3 className="panel__subtitle">存在分歧（不替你选一个）</h3>
          {briefing.disputes.map((d) => (
            <div key={d.topic} className="disputecard">
              <span className="disputecard__topic">{d.topic}</span>
              {d.conflicts.map((c, i) => (
                <div key={i} className="disputecard__pair">
                  <EvidenceLine ev={c.a} label="甲" />
                  <EvidenceLine ev={c.b} label="乙" />
                </div>
              ))}
            </div>
          ))}
        </section>
      )}
      {briefing.consensus.length > 0 && (
        <section>
          <h3 className="panel__subtitle">多方共识</h3>
          {briefing.consensus.map((c) => (
            <div key={c.topic} className="consensuscard">
              <span className="consensuscard__topic">
                {c.topic} · {c.independentSites} 个独立站点
              </span>
              {c.evidence.map((ev, i) => (
                <EvidenceLine key={i} ev={ev} />
              ))}
            </div>
          ))}
        </section>
      )}
      {briefing.numbers.length > 0 && (
        <section>
          <h3 className="panel__subtitle">关键数据</h3>
          {briefing.numbers.slice(0, 10).map((n, i) => (
            <EvidenceLine
              key={i}
              ev={{ text: n.sentence, url: n.url ?? '', title: n.title ?? '', site: n.site ?? '', trustScore: 0, tier: '' }}
              prefix={n.value}
            />
          ))}
        </section>
      )}
      {briefing.openQuestions.length > 0 && (
        <section>
          <h3 className="panel__subtitle">还没查清</h3>
          {briefing.openQuestions.map((q, i) => (
            <p key={i} className="panel__hint">
              <AlertTriangle size={12} strokeWidth={2} /> {q}
            </p>
          ))}
        </section>
      )}
    </div>
  );
}

/**
 * P2 逐句可点溯源：给每条摘录一个可被定位的标记。
 *
 * 生成版右栏里的 `[n]` 点下去，要能跳到**左栏这条原文**并高亮 ——
 * 而不是打开浏览器。打开浏览器就把"两栏并排随时能对照"这件事丢掉了，
 * 而那正是当初选并排布局的全部理由。
 *
 * 用 `data-src` 而不是 React ref：引用是跨组件、跨栏的，
 * 用 ref 要把回调一路传下去穿过四层组件；而这里要的只是
 * "按 URL 找到那个元素"，`querySelector` 一行就够。
 */
function evAnchorId(url: string): string {
  // URL 里有 `/` `:` `?` 这些字符，不能直接当 CSS 选择器用，
  // 所以用属性选择器匹配而不是 id。这里只做规范化
  return url.trim();
}

function EvidenceLine({ ev, label, prefix }: { ev: Evidence; label?: string; prefix?: string }) {
  if (!ev.text) return null;
  return (
    <p className="evline" data-src={ev.url ? evAnchorId(ev.url) : undefined}>
      {label && <span className="evline__label">{label}：</span>}
      {prefix && <span className="evline__prefix">{prefix}　</span>}
      「{ev.text}」
      {ev.url && (
        <a
          className="evline__src"
          href={ev.url}
          onClick={(e) => {
            e.preventDefault();
            void window.synorive.sys.openExternal(ev.url);
          }}
        >
          ↳ {ev.site || ev.title}
        </a>
      )}
    </p>
  );
}

function GeneratedPanel({ query, briefing }: { query: string; briefing: Briefing }) {
  const [state, setState] = useState<
    { kind: 'idle' } | { kind: 'loading' } | { kind: 'ok'; data: GeneratedBriefing } | { kind: 'error'; msg: string }
  >({ kind: 'idle' });

  const generate = async () => {
    setState({ kind: 'loading' });
    try {
      const r = await webApi.synthesize({ query, briefing });
      setState({ kind: 'ok', data: r });
    } catch (e) {
      setState({ kind: 'error', msg: (e as Error).message });
    }
  };

  if (state.kind === 'idle') {
    return (
      <div className="genpanel__empty">
        <p className="panel__hint">
          把左边的摘录改写得更通顺，每句仍标注编号并挂着真实出处链接——模型不能编来源。
          没配置云端通道的话点了会直接告诉你去哪里配。
        </p>
        <button className="btn btn--primary btn--sm" onClick={() => void generate()}>
          <Sparkles size={13} strokeWidth={1.8} /> 生成
        </button>
      </div>
    );
  }
  if (state.kind === 'loading') {
    return (
      <div className="loadingstate">
        <Loader2 size={16} className="spin" strokeWidth={2} />
        <span>正在改写…</span>
      </div>
    );
  }
  if (state.kind === 'error') {
    return (
      <div className="genpanel__empty">
        <div className="banner banner--error">{state.msg}</div>
        <button className="btn btn--sm" onClick={() => void generate()}>
          重试
        </button>
      </div>
    );
  }

  const { data } = state;
  if (data.warning) {
    return <p className="panel__hint">{data.warning}</p>;
  }

  return (
    <div className="genpanel">
      <p className="genpanel__text">{renderCitedText(data.text, data.citations)}</p>
      {data.model && <p className="genpanel__model">模型：{data.model}</p>}
      <button className="btn btn--sm" onClick={() => void generate()}>
        重新生成
      </button>
    </div>
  );
}

/** 把模型返回的 `[n](url)` 换成真正的可点击链接；纯本地正则渲染，不引入 markdown 库。 */
function renderCitedText(
  text: string,
  citations: GeneratedBriefing['citations'],
): ReactNode {
  const byN = new Map(citations.map((c) => [c.n, c]));
  const parts: ReactNode[] = [];
  const re = /\[(\d+)\]\((https?:\/\/[^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const n = Number(m[1]);
    // 🔴 必须在循环体内建一个新的 const 绑定——onClick 是异步触发的，
    // 如果直接闭包捕获外层那个会被循环持续重新赋值的 `let m`，
    // 用户点链接时 `m` 早就变成 null 或指向了最后一次匹配，点哪条链接都不对
    // 正则里的两个捕获组都不是可选的（没有 `?`），匹配上就必然都有值 ——
    // 这里的 `!` 是告诉类型检查器这件事，不是绕过刚才修的那个闭包 bug
    const url = m[2]!;
    const c = byN.get(n);
    parts.push(
      <a
        key={key++}
        className="citelink"
        href={url}
        title={
          c?.title
            ? `${c.title}\n点：跳到左栏那句原文并高亮　Ctrl+点：用浏览器打开`
            : '点：跳到左栏原文　Ctrl+点：用浏览器打开'
        }
        onClick={(e) => {
          e.preventDefault();
          // 🔴 P2：默认动作是**跳回左栏的原文**，不是打开浏览器。
          // 打开浏览器就把"两栏并排随时能对照"这件事丢掉了 ——
          // 而那正是当初选并排布局的全部理由。
          // 想去原站的人按 Ctrl 就行，那是次要动作。
          if (e.ctrlKey || e.metaKey || !highlightSource(url)) {
            void window.synorive.sys.openExternal(url);
          }
        }}
      >
        [{n}]
      </a>,
    );
    last = re.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

/**
 * P2：在左栏找到这条出处的原文，滚过去并闪一下。
 *
 * 返回 false 表示**左栏没有这一条** —— 这不是异常：生成版可能引用了
 * 一条没有被摘录到简报里的来源（它读了全文，而左栏只放摘出来的句子）。
 * 那种情况下退回"用浏览器打开"，而不是让用户点了没反应。
 * **点了没反应是最糟的交互**：用户不知道是坏了还是自己点错了。
 */
function highlightSource(url: string): boolean {
  const el = document.querySelector<HTMLElement>(`.evline[data-src="${CSS.escape(url)}"]`);
  if (!el) return false;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  // 用 class 触发一次性动画。先移除再加，保证连续点同一条时每次都会闪 ——
  // 不移除的话第二次点因为 class 已在，动画根本不会重放
  el.classList.remove('evline--flash');
  void el.offsetWidth; // 强制重排，让浏览器认下这次移除
  el.classList.add('evline--flash');
  window.setTimeout(() => el.classList.remove('evline--flash'), 1800);
  return true;
}

// ════════════════════════════════════════════════════════════
// 文献
// ════════════════════════════════════════════════════════════
function ScholarList({ papers }: { papers: ScholarPaper[] }) {
  if (!papers.length) {
    return (
      <div className="empty">
        <BookMarked size={30} strokeWidth={1.2} className="empty__glyph" />
        <div className="empty__title">没查到相关文献</div>
        <p className="empty__hint">试试用英文关键词，学术源对英文检索式的召回明显更好。</p>
      </div>
    );
  }
  return (
    <div className="weblist">
      {papers.map((p) => {
        const m = p.meta ?? {};
        return (
          <a
            key={p.url}
            className="webcard"
            href={m.pdf || p.url}
            onClick={(e) => {
              e.preventDefault();
              void window.synorive.sys.openExternal(m.pdf || p.url);
            }}
          >
            <div className="webcard__head">
              <span className="webcard__title">{p.title}</span>
              <ArrowUpRight size={13} className="webcard__extlink" strokeWidth={1.8} />
            </div>
            {p.snippet && <p className="webcard__snippet">{p.snippet}</p>}
            <div className="webcard__meta">
              {m.year && <span className="webcard__site">{m.year}</span>}
              {m.venue && <span className="webcard__site">{m.venue}</span>}
              {m.citations != null && <span className="badge">被引 {m.citations}</span>}
              {m.openAccess && <span className="badge">开放获取</span>}
              <span className="webcard__engines">收录于 {p.sources.join(' + ')}</span>
              {m.authors?.length ? <span className="webcard__site">{m.authors.slice(0, 3).join('、')}</span> : null}
            </div>
          </a>
        );
      })}
    </div>
  );
}
