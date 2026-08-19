import { useState } from 'react';
import { FolderPlus, Loader2 } from 'lucide-react';
import { AskAnswer } from '../components/AskAnswer';
import { AskStage } from '../components/AskStage';
import { ComposeBar } from '../components/ComposeBar';
import { SearchHistory } from '../components/SearchHistory';
import { SideBySide } from '../components/SideBySide';
import { ClipboardTray } from '../components/ClipboardTray';
import { LastSessionStrip } from '../components/LastSessionStrip';
import { QuestionsPanel } from '../components/QuestionsPanel';
import { SceneStrip } from '../components/SceneStrip';
import { ChapterList } from '../components/ChapterList';
import { QueryChips } from '../components/QueryChips';
import { Recovery } from '../components/Recovery';
import { RankingPanel } from '../components/RankingPanel';
import { SearchResults } from '../components/SearchResults';
import { api } from '../lib/api';
import { useAsk } from '../lib/useAsk';
import { useSearch } from '../lib/useSearch';
import { useSelection } from '../lib/useSelection';
import { useApp } from '../lib/store';

const STAGE_LABEL: Record<string, string> = {
  instant: '最近打开',
  keyword: '关键词',
  semantic: '语义',
  reranked: '精排',
};

/**
 * 搜索页 —— B1 之后它是**整个应用的主页面**
 *
 * 三种形态，同一个页面：
 *   ① 舞台态：没提交过东西 → 大输入区占据视觉中心（B1）
 *   ② 问答态：提交了、模式是「问一句」→ 带出处的答案 + 读过的那几条（A3）
 *   ③ 列表态：提交了、模式是「找东西」→ 原来那套三级瀑布结果
 *
 * 排序面板（D1）只在列表态出现 —— 问答态给用户看六个权重滑块没有意义，
 * 他要的是答案，不是调排序。**该出现时出现，是"易于操作"的另一半**：
 * 另一半是"不该出现时别出现"。
 */
export function SearchPage() {
  /** N6：正在看哪一篇的「能回答什么」。null = 抽屉关着 */
  const [asking, setAsking] = useState<{ id: string; title: string } | null>(null);
  /** N3：正在看哪个视频的镜头带。null = 抽屉关着 */
  const [scening, setScening] = useState<
    { id: string; locator: string; title: string; sec?: number } | null
  >(null);
  /** D3：正在并排对比哪几条。null = 没在对比 */
  const [comparing, setComparing] = useState<string[] | null>(null);

  const { query, hits, stage, total, elapsedMs, loading, error, searched, recovery, weakMatch, filters } =
    useSearch();
  const setQuery = useSearch((s) => s.setQuery);
  const setFilters = useSearch((s) => s.setFilters);
  const rerun = useSearch((s) => s.rerun);

  const answer = useAsk((s) => s.answer);
  const askHits = useAsk((s) => s.hits);
  const askMs = useAsk((s) => s.elapsedMs);
  const askLoading = useAsk((s) => s.loading);
  const askError = useAsk((s) => s.error);

  const engine = useApp((s) => s.engine);
  const mode = useApp((s) => s.inputMode);
  const expanded = useApp((s) => s.stageExpanded);
  const ready = engine?.lifecycle === 'ready';

  const addFolder = async () => {
    const dirs = await window.synorive.sys.pickFolders();
    if (!dirs.length) return;
    await api.ingest({ targets: dirs, source: 'file', recursive: true });
  };


  // ── ① 舞台态 ───────────────────────────────────────────
  // 大输入区独占一屏。**不在它下面塞结果列表** —— 那样两边都不完整，
  // 而"输入的内容很多时界面要很大"这条要求首先意味着别的东西要让位
  if (expanded) {
    return (
      <div className="page page--stage">
        <AskStage />
        {/* 剪贴板托盘放在舞台下面：它是"你刚复制的东西"，
            属于顺手可投喂的素材，不抢输入区的位置 */}
        <div className="page--stage__under">
          {/* 打开就有东西看：上次那一屏。一次真实搜索之后它自己消失 */}
          <LastSessionStrip />
          <ClipboardTray />
          {ready && !searched && !answer && (
            <div className="syn-blank">
              <div className="syn-blank__title">库里还没有东西可搜</div>
              <p className="syn-blank__hint">
                选一个文件夹，几秒钟内就能开始搜——关键词层立刻可用，
                语义层在后台补齐，不用等全部跑完。
              </p>
              <button className="btn btn--primary" onClick={addFolder}>
                <FolderPlus size={15} strokeWidth={1.7} />
                选一个文件夹开始索引
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── ② 问答态 ───────────────────────────────────────────
  if (mode === 'ask') {
    return (
      <div className="page">
        <div className="page__meta">
          {askLoading && (
            <span className="page__subtitle page__subtitle--busy">
              <Loader2 size={13} className="spin" strokeWidth={2} />
              正在读你的资料…
            </span>
          )}
          {!askLoading && answer && (
            <span className="page__subtitle">
              引用 {answer.sources.length} 份资料 · {askMs.toFixed(0)}ms
            </span>
          )}
        </div>

        <div className="asklayout">
          {askError && <div className="banner banner--error">出错了：{askError}</div>}

          {askLoading && !answer && (
            <div className="ans" aria-label="正在读取">
              <div className="syn-skel syn-skel--line syn-skel--title" />
              <div className="syn-skel syn-skel--line" />
              <div className="syn-skel syn-skel--line syn-skel--short" />
            </div>
          )}

          {answer && (
            <AskAnswer
              data={answer}
              elapsedMs={askMs}
              onOpenItem={(id, title) => setAsking({ id, title })}
            />
          )}

          {/* 答案下面摊开引擎读过的那几条 —— 用户想自己核对时不用再搜一次。
              这也是"只摘录不生成"这条约束能立住的前提：证据必须够得着 */}
          {askHits.length > 0 && (
            <section className="asklayout__hits">
              <div className="syn-subhead">这几条是我读过的</div>
              <SearchResults
                hits={askHits}
                onAsk={(id, t) => setAsking({ id, title: t })}
                onScenes={(id, loc, t, sec) => setScening({ id, locator: loc, title: t, sec })}
              />
            </section>
          )}
        </div>

        {scening && (
          <SceneDrawer scening={scening} onClose={() => setScening(null)} onJump={setScening} />
        )}
        {asking && (
          <QuestionsPanel itemId={asking.id} title={asking.title} onClose={() => setAsking(null)} />
        )}
      </div>
    );
  }

  // ── ③ 列表态（原有的三级瀑布）────────────────────────
  return (
    <div className="page">
      <div className="page__meta">
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

          {/* D10/L3-plus：把「这句话被理解成了什么」摆在结果**上面**。
              摆下面等于没摆 —— 用户看完一屏少得莫名其妙的结果才发现
              原来有个筛选在起作用，那时候困惑已经产生了 */}
          <QueryChips />

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
              <div className="syn-blank">
                <div className="syn-blank__title">没搜到「{query}」</div>
                <p className="syn-blank__hint">
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
            <SearchResults
              hits={hits}
              // A4/D3：只有**列表态**给勾选框。问答态那个「读了这几条」
              // 是展示用的，出现复选框会让人以为勾了能干什么，而那里没接东西
              selectable
              onAsk={(id, t) => setAsking({ id, title: t })}
              onScenes={(id, loc, t, sec) => setScening({ id, locator: loc, title: t, sec })}
            />
          )}

          {/* A4/D3：选中了才出现。没选中时它一行都不占 */}
          <ComposeBar onCompare={(ids) => setComparing(ids)} />
        </div>

        <div className="searchlayout__side">
          <RankingPanel />
          {/* D4/D5：历史和「盯住它」放排序面板下面 ——
              它们都属于"这次搜完之后还能做什么"，归在同一列里 */}
          <SearchHistory />
        </div>
      </div>

      {scening && (
        <SceneDrawer scening={scening} onClose={() => setScening(null)} onJump={setScening} />
      )}
      {asking && (
        <QuestionsPanel itemId={asking.id} title={asking.title} onClose={() => setAsking(null)} />
      )}
      {comparing && (
        <SideBySide
          // 从**已选**里取而不是从当前结果里取：用户可能是分几次搜索
          // 攒起来的这几条，当前结果列表里根本没有它们
          hits={useSelection.getState().picked.filter((h) => comparing.includes(h.item.id))}
          onClose={() => setComparing(null)}
        />
      )}
    </div>
  );
}

/** 视频镜头抽屉。三种形态共用，抽出来免得写三遍。 */
function SceneDrawer({
  scening,
  onClose,
  onJump,
}: {
  scening: { id: string; locator: string; title: string; sec?: number };
  onClose: () => void;
  onJump: (
    f: (s: { id: string; locator: string; title: string; sec?: number } | null) =>
      | { id: string; locator: string; title: string; sec?: number }
      | null,
  ) => void;
}) {
  return (
    <aside className="qp" role="dialog" aria-label="视频镜头">
      <header className="qp__head">
        <h3>{scening.title}</h3>
        <button className="qp__close" onClick={onClose} aria-label="关闭" title="关闭">
          ×
        </button>
      </header>
      <SceneStrip itemId={scening.id} locator={scening.locator} focusSec={scening.sec} />
      {/* A6：同 LibraryPage —— 点一章把缩略条焦点挪过去 */}
      <ChapterList
        itemId={scening.id}
        onJump={(sec) => onJump((s) => (s ? { ...s, sec } : s))}
      />
    </aside>
  );
}
