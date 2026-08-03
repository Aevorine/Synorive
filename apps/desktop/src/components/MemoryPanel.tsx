import { useEffect, useState } from 'react';
import { Brain, GitCompare, Loader2, Trash2 } from 'lucide-react';
import { labApi, type MemoryRecall, type RunDiff } from '../lib/labApi';

/**
 * E2 长期记忆 ｜ E4 差异复读
 * ============================================================
 * 挖一个话题之前先问一句「**我以前查过什么**」。
 *
 * 🔴 **记忆库里只存逐字摘录，不存任何总结。** 存总结等于把一次
 * 可能出错的提炼固化成"记忆"，之后每次都在这个可能错的基础上继续 ——
 * 错误会累积，而且再也追不回源头。存原文摘录则永远能回去核对。
 *
 * 🔴 **必须有"忘掉"按钮。** 一个只进不出的记忆库，用户迟早会不敢用它：
 * 他会开始担心"我随手搜的东西是不是也被记住了"。给一个明确的清理入口，
 * 这个顾虑就不存在了。
 *
 * **E4 差异复读**比的是**事实级**不是文本级：两次简报的字面几乎不会
 * 重样（引擎排序天天变），按文本 diff 会显示"全变了"，那毫无用处。
 * 所以比的是新出现的来源、新出现的数字、争议度变化。
 */

export function MemoryPanel({
  topic,
  currentRun,
  previousRun,
  briefing,
  clusters,
}: {
  topic: string;
  /** 这次的完整响应，用于 E4 对比和"记住" */
  currentRun?: unknown;
  /** 上一次的（从项目历史里取），有才显示对比按钮 */
  previousRun?: unknown;
  briefing?: unknown;
  clusters?: unknown[];
}) {
  const [recall, setRecall] = useState<MemoryRecall | null>(null);
  const [diff, setDiff] = useState<RunDiff | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // 话题一变就自动回忆一次。**这一步不该要用户主动点** ——
  // "我以前查过什么"要在他开始查之前就出现，事后再问就晚了
  useEffect(() => {
    if (!topic.trim()) return;
    let alive = true;
    setRecall(null);
    setDiff(null);
    labApi
      .recall(topic)
      .then((r) => alive && setRecall(r))
      .catch(() => {
        /* 回忆失败不该打断主流程：这是个锦上添花的信息 */
      });
    return () => {
      alive = false;
    };
  }, [topic]);

  async function run(key: string, fn: () => Promise<void>): Promise<void> {
    setBusy(key);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (!topic.trim()) return null;

  return (
    <section className="syn-mem">
      <h3 className="syn-mem-title">
        <Brain size={15} aria-hidden /> 关于「{topic}」我记得的
      </h3>

      {recall && !recall.known && <p className="syn-mem-empty">这个话题以前没查过，这是第一次。</p>}

      {recall?.known && (
        <>
          <p className="syn-mem-meta">
            以前查过 {recall.runCount} 次，最近一次 {recall.lastSeen.slice(0, 10)}
            {recall.controversy != null && <>　上次的争议度 {recall.controversy}/100</>}
          </p>
          <ul className="syn-mem-facts">
            {recall.facts.slice(0, 12).map((f) => (
              <li key={f.url + f.text.slice(0, 16)}>
                <span className="syn-mem-text">{f.text}</span>
                <a href={f.url} target="_blank" rel="noreferrer" className="syn-mem-src">
                  {f.site || '出处'}
                </a>
                <span className="syn-mem-seen">见过 {f.seen_count} 次</span>
              </li>
            ))}
          </ul>
          <p className="syn-mem-note">
            按「被反复见到的次数」排序，不是按时间 —— 反复出现的更可能是这个话题的骨干信息，
            而最新看到的往往只是这次搜索的排序噪声。
          </p>
        </>
      )}

      <div className="syn-mem-actions">
        {briefing != null && (
          <button
            type="button"
            disabled={busy != null}
            onClick={() =>
              void run('save', async () => {
                const r = await labApi.remember(topic, briefing, clusters ?? [], undefined);
                setSaved(r.note);
                setRecall(await labApi.recall(topic));
              })
            }
          >
            {busy === 'save' ? <Loader2 size={14} className="syn-spin" aria-hidden /> : null}
            记住这一轮
          </button>
        )}

        {previousRun != null && currentRun != null && (
          <button
            type="button"
            disabled={busy != null}
            onClick={() =>
              void run('diff', async () => setDiff(await labApi.diffRuns(previousRun, currentRun)))
            }
          >
            <GitCompare size={14} aria-hidden /> 和上次比，只看新增的
          </button>
        )}

        {recall?.known && (
          <button
            type="button"
            className="syn-mem-danger"
            disabled={busy != null}
            onClick={() =>
              void run('forget', async () => {
                await labApi.forget(topic);
                setRecall(await labApi.recall(topic));
              })
            }
          >
            <Trash2 size={14} aria-hidden /> 忘掉这个话题
          </button>
        )}
      </div>

      {saved && <p className="syn-mem-saved">{saved}</p>}
      {err && <p className="syn-mem-err">{err}</p>}

      {diff && (
        <div className="syn-mem-diff">
          <h4>{diff.summary}</h4>
          {diff.added.length > 0 && (
            <>
              <p className="syn-mem-diff-h">新出现的来源</p>
              {diff.added.map((x) => (
                <p key={x.url} className="syn-mem-diff-row">
                  <a href={x.url} target="_blank" rel="noreferrer">
                    {x.title || x.url}
                  </a>
                  <span>{x.site}</span>
                </p>
              ))}
            </>
          )}
          {diff.newNumbers.length > 0 && (
            <>
              <p className="syn-mem-diff-h">新出现的数字</p>
              <p className="syn-mem-diff-nums">{diff.newNumbers.join('　')}</p>
            </>
          )}
          {diff.controversyDelta != null && (
            <p className="syn-mem-diff-h">
              争议度 {diff.controversyBefore} → {diff.controversyAfter}
            </p>
          )}
          {diff.gone.length > 0 && (
            <details className="syn-mem-gone">
              <summary>这次没再出现的 {diff.gone.length} 条</summary>
              {diff.gone.map((x) => (
                <p key={x.url}>{x.title || x.url}</p>
              ))}
              <p className="syn-mem-note">{diff.note}</p>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
