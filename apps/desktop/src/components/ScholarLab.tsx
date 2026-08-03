import { useState } from 'react';
import {
  BookOpen,
  Download,
  GitBranch,
  Layers,
  Loader2,
  Table2,
  Merge,
  AlertTriangle,
  Quote,
} from 'lucide-react';
import {
  labApi,
  type AlignTable,
  type HarvestPlan,
  type PaperNode,
  type ReviewSection,
  type ScholarCluster,
} from '../lib/labApi';

/**
 * C 组 —— 文献工作台
 * ============================================================
 * 一批文献搜回来之后，五件最费手工的事全在这一个面板里：
 *
 *   聚类（C8）→ 综述（C4）→ 抽表（C5）→ 引用图谱（C3）→ 批量下载（C2）
 *
 * **为什么是一个面板而不是五个入口**：这五件事用的是**同一批文献**，
 * 而且顺序上是递进的 —— 先分堆知道有几个流派，再看每堆讲什么，
 * 再把指标拉平了比，最后决定下哪几篇。拆成五个页面的话，
 * 用户每次都要重新把"哪批文献"这件事说一遍。
 *
 * 🔴 **每一块都把引擎返回的 `note` 原样显示出来**。那句话里写着
 * 这个功能的边界（"这是按词面相似度分的，不是语义"、"只抽了摘要里
 * 写了的"、"预印本常常查不到"）。吞掉它，用户就会拿一个有明确
 * 适用范围的结果当成通用结论用。
 */

type Tab = 'cluster' | 'review' | 'table' | 'graph' | 'harvest';

const TABS: { id: Tab; label: string; icon: typeof Layers; hint: string }[] = [
  { id: 'cluster', label: '分堆', icon: Layers, hint: '这批文献分成几个流派' },
  { id: 'review', label: '综述', icon: BookOpen, hint: '每堆一段，每句都带出处' },
  { id: 'table', label: '抽表', icon: Table2, hint: '同一指标在各篇里各是多少' },
  { id: 'graph', label: '引用', icon: GitBranch, hint: '这个领域绕不过去的几篇' },
  { id: 'harvest', label: '下载', icon: Download, hint: '把开放全文批量存进库' },
];

export function ScholarLab({
  entries,
  topic,
}: {
  entries: Record<string, unknown>[];
  topic: string;
}) {
  const [tab, setTab] = useState<Tab>('cluster');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [clusters, setClusters] = useState<{ clusters: ScholarCluster[]; note: string } | null>(null);
  const [review, setReview] = useState<{ sections: ReviewSection[]; note: string } | null>(null);
  const [table, setTable] = useState<AlignTable | null>(null);
  const [graph, setGraph] = useState<{
    foundations: PaperNode[];
    followups: PaperNode[];
    resolved: number;
    requested: number;
    note: string;
  } | null>(null);
  const [plan, setPlan] = useState<HarvestPlan | null>(null);
  const [harvested, setHarvested] = useState<string | null>(null);
  const [mergedNote, setMergedNote] = useState<string | null>(null);
  const [working, setWorking] = useState(entries);

  async function run<T>(fn: () => Promise<T>, set: (v: T) => void): Promise<void> {
    setBusy(true);
    setErr(null);
    try {
      set(await fn());
    } catch (e) {
      // 失败必须显示出来。以前这类面板出错就是一片空白，
      // 用户分不清是"没有结果"还是"坏了"——那是最糟的两种状态
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /**
   * C9 —— 下载参考文献。
   *
   * 🔴 **走 fetch + Blob 而不是 `<a download href="接口地址">`**：
   * 引擎跑在 `127.0.0.1:动态端口` 上，直接给 `<a>` 一个跨端口地址，
   * 浏览器会当成导航而不是下载，症状是**弹出一个新窗口显示一堆 .bib 文本**，
   * 而不是存成文件。而且那样也拿不到接口设的 `Content-Disposition` 文件名。
   */
  async function downloadCitations(format: 'bibtex' | 'gbt7714'): Promise<void> {
    setBusy(true);
    setErr(null);
    try {
      const { enginePort } = await import('../lib/api');
      const port = enginePort();
      if (port == null) throw new Error('引擎还没就绪');
      const resp = await fetch(`http://127.0.0.1:${port}/api/scholar/citations/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entries: working, format }),
      });
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = format === 'bibtex' ? 'references.bib' : 'references.txt';
      a.click();
      // 立刻 revoke 会让某些情况下下载拿不到内容，给浏览器留一帧
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function go(next: Tab): void {
    setTab(next);
    if (next === 'cluster' && !clusters) void run(() => labApi.cluster(working), setClusters);
    if (next === 'review' && !review) void run(() => labApi.review(working, topic), setReview);
    if (next === 'table' && !table) void run(() => labApi.table(working), setTable);
    if (next === 'graph' && !graph) void run(() => labApi.citations(working), setGraph);
    if (next === 'harvest' && !plan) void run(() => labApi.harvestPlan(working, 30), setPlan);
  }

  return (
    <section className="syn-lab">
      <header className="syn-lab-head">
        <h2 className="syn-lab-title">文献工作台</h2>
        <span className="syn-lab-count">{working.length} 篇</span>
        {/* C9 引用格式导出。放在标题栏而不是某个 tab 里 ——
            它对**当前这批文献**永远可用，不依赖你先看过哪一块。
            塞进某个 tab 会让人以为"得先分堆才能导出参考文献" */}
        <button
          type="button"
          className="syn-lab-cite"
          disabled={busy || working.length === 0}
          title="下载 .bib，Zotero / EndNote / LaTeX 都认"
          onClick={() => void downloadCitations('bibtex')}
        >
          <Quote size={13} aria-hidden /> BibTeX
        </button>
        <button
          type="button"
          className="syn-lab-cite"
          disabled={busy || working.length === 0}
          title="GB/T 7714-2015，中文期刊和学位论文要的那种格式"
          onClick={() => void downloadCitations('gbt7714')}
        >
          <Quote size={13} aria-hidden /> 国标
        </button>

        <button
          type="button"
          className="syn-lab-merge"
          disabled={busy}
          onClick={() =>
            void run(
              () => labApi.mergePreprints(working),
              (r) => {
                setWorking(r.entries);
                setMergedNote(r.note);
                // 合并之后前面算的全过期了，清掉重算 ——
                // 留着旧结果比没有结果更糟：条数对不上，用户会以为哪里丢了
                setClusters(null);
                setReview(null);
                setTable(null);
                setGraph(null);
                setPlan(null);
              },
            )
          }
        >
          <Merge size={14} aria-hidden /> 合并预印本
        </button>
      </header>

      {mergedNote && <p className="syn-lab-note">{mergedNote}</p>}

      <nav className="syn-lab-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`syn-lab-tab ${tab === t.id ? 'is-on' : ''}`}
            onClick={() => go(t.id)}
            title={t.hint}
          >
            <t.icon size={15} aria-hidden />
            {t.label}
          </button>
        ))}
      </nav>

      {busy && (
        <p className="syn-lab-busy">
          <Loader2 size={15} className="syn-spin" aria-hidden /> 算着呢……
        </p>
      )}
      {err && (
        <p className="syn-lab-err">
          <AlertTriangle size={15} aria-hidden /> {err}
        </p>
      )}

      {tab === 'cluster' && clusters && (
        <div className="syn-lab-body">
          <p className="syn-lab-note">{clusters.note}</p>
          {clusters.clusters.map((c) => (
            <article key={c.id} className="syn-lab-cluster">
              <h3>
                {c.label}
                <span className="syn-lab-meta">
                  {c.size} 篇{c.yearSpan ? ` · ${c.yearSpan}` : ''}
                </span>
              </h3>
              <p className="syn-lab-kw">{c.keywords.join(' · ')}</p>
              {c.representative != null && (
                <p className="syn-lab-rep">
                  代表作：{String((c.representative as { title?: string }).title ?? '')}
                </p>
              )}
            </article>
          ))}
        </div>
      )}

      {tab === 'review' && review && (
        <div className="syn-lab-body">
          <p className="syn-lab-note">{review.note}</p>
          {review.sections.map((s) => (
            <article key={s.heading} className="syn-lab-section">
              <h3>
                {s.heading}
                <span className="syn-lab-meta">
                  {s.paperCount} 篇{s.yearSpan ? ` · ${s.yearSpan}` : ''}
                </span>
              </h3>
              {s.quotes.map((q) => (
                <p key={`${q.ref}-${q.text.slice(0, 12)}`} className="syn-lab-quote">
                  {q.text}
                  <a href={q.url} target="_blank" rel="noreferrer" className="syn-lab-ref">
                    [{q.ref}]
                  </a>
                </p>
              ))}
              {s.disputes.map((d) => (
                <div key={`${d.a.ref}-${d.b.ref}`} className="syn-lab-dispute">
                  <span className="syn-lab-dispute-tag">这里有分歧，两边都摆出来</span>
                  <p>A [{d.a.ref}] {d.a.text}</p>
                  <p>B [{d.b.ref}] {d.b.text}</p>
                </div>
              ))}
            </article>
          ))}
        </div>
      )}

      {tab === 'table' && table && (
        <div className="syn-lab-body">
          <p className="syn-lab-note">{table.note}</p>
          <div className="syn-lab-tablewrap">
            <table className="syn-lab-table">
              <thead>
                <tr>
                  {table.columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((r) => (
                  <tr key={r.url || r.title}>
                    <td className="syn-lab-td-title">
                      <a href={r.url} target="_blank" rel="noreferrer">
                        {r.title}
                      </a>
                    </td>
                    <td>{r.year || '—'}</td>
                    {table.columns
                      .filter((c) => c !== '文献' && c !== '年份')
                      .map((c) => {
                        const cell = r.cells[c];
                        return (
                          <td key={c} title={cell?.raw ?? '摘要里没写这个指标'}>
                            {cell ? `${cell.value}${cell.unit}` : '—'}
                          </td>
                        );
                      })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'graph' && graph && (
        <div className="syn-lab-body">
          <p className="syn-lab-note">{graph.note}</p>
          <h3 className="syn-lab-h3">奠基论文 —— 被这批文献共同引用最多的</h3>
          {graph.foundations.map((n) => (
            <p key={n.id} className="syn-lab-node">
              <a href={n.url} target="_blank" rel="noreferrer">
                {n.title}
              </a>
              <span className="syn-lab-meta">
                {n.year} · 这批里 {n.coCited} 篇引用 · 总被引 {n.citations}
              </span>
            </p>
          ))}
          {graph.followups.length > 0 && (
            <>
              <h3 className="syn-lab-h3">后续工作 —— 引用了这批文献的</h3>
              {graph.followups.map((n) => (
                <p key={n.id} className="syn-lab-node">
                  <a href={n.url} target="_blank" rel="noreferrer">
                    {n.title}
                  </a>
                  <span className="syn-lab-meta">
                    {n.year} · 引了这批里 {n.citesSeeds} 篇
                  </span>
                </p>
              ))}
            </>
          )}
        </div>
      )}

      {tab === 'harvest' && plan && (
        <div className="syn-lab-body">
          <p className="syn-lab-note">{plan.note}</p>
          {!harvested && (
            <button
              type="button"
              className="syn-lab-primary"
              disabled={busy || plan.count === 0}
              onClick={() =>
                void run(
                  () => labApi.harvest(working, 30, ['文献', topic].filter(Boolean)),
                  (r) => setHarvested(r.note),
                )
              }
            >
              <Download size={15} aria-hidden /> 真的下这 {plan.count} 篇
            </button>
          )}
          {harvested && <p className="syn-lab-done">{harvested}</p>}
          {plan.skipped.length > 0 && (
            <details className="syn-lab-skipped">
              <summary>跳过的 {plan.skipped.length} 篇（附原因）</summary>
              {plan.skipped.map((s) => (
                <p key={s.title}>
                  {s.title} —— {s.reason}
                </p>
              ))}
            </details>
          )}
        </div>
      )}
    </section>
  );
}
