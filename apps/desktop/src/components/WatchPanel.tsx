import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, BellPlus, BellRing, Loader2, Play, Trash2 } from 'lucide-react';
import { labApi, type WatchItem } from '../lib/labApi';
import { history } from '../lib/undo';

/**
 * C7 —— 主题订阅管理
 * ============================================================
 * 把一次检索存成订阅，定时重跑，**只把新出现的**告诉你。
 *
 * 🔴 **「新出现的」是靠 URL 指纹判的，不是靠时间戳。**
 * 用发布时间判会漏掉两类：没有发布时间的页面（很常见），
 * 和被重新编辑过而时间戳变新的旧内容。指纹只回答一个问题 ——
 * 「这条我上次看过没有」，那正是订阅唯一要回答的问题。
 *
 * 🔴 **自动入库默认关。** 一觉醒来库里多出几百个网页，
 * 会把语义检索的信噪比整个拉低，而用户根本不知道是谁干的。
 * 开了也有上限：单次最多入库 10 条。
 *
 * 🔴 **「跑一次」是显式动作，不做后台静默轮询的 UI。**
 * 引擎侧有 `/watches/run-due` 可以被定时触发，但**界面上必须能看到
 * 上次什么时候跑的、跑出了什么** —— 一个你看不见它在做什么的
 * 后台任务，等于一个你没法信任的后台任务。
 */

export function WatchPanel({
  /** 当前查询词，用来一键把它存成订阅 */
  currentQuery,
  engines,
}: {
  currentQuery?: string;
  engines?: string[];
}) {
  const [items, setItems] = useState<WatchItem[]>([]);
  const [due, setDue] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, string>>({});
  const [interval, setIntervalHours] = useState(24);
  const [autoIngest, setAutoIngest] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await labApi.watches();
      setItems(r.watches);
      setDue(r.due);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(key: string, fn: () => Promise<void>): Promise<void> {
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

  return (
    <section className="syn-watch">
      <header className="syn-watch-head">
        <h3 className="syn-watch-title">
          <BellRing size={15} aria-hidden /> 订阅
          {due.length > 0 && <span className="syn-watch-due">{due.length} 条该跑了</span>}
        </h3>
      </header>

      {currentQuery?.trim() && (
        <div className="syn-watch-add">
          <span className="syn-watch-q">「{currentQuery}」</span>
          <label className="syn-watch-field">
            每
            <input
              type="number"
              min={1}
              max={720}
              value={interval}
              onChange={(e) => setIntervalHours(Math.max(1, Number(e.target.value) || 24))}
            />
            小时跑一次
          </label>
          <label className="syn-watch-check">
            <input
              type="checkbox"
              checked={autoIngest}
              onChange={(e) => setAutoIngest(e.target.checked)}
            />
            新的自动入库
            <span className="syn-watch-hint">
              （默认关。开了单次最多入 10 条 —— 一觉醒来库里多出几百个网页会把检索信噪比拉低）
            </span>
          </label>
          <button
            type="button"
            className="syn-watch-primary"
            disabled={busy != null}
            onClick={() =>
              void act('add', async () => {
                await labApi.addWatch({
                  query: currentQuery,
                  label: currentQuery.slice(0, 40),
                  engines,
                  intervalHours: interval,
                  autoIngest,
                });
                await load();
              })
            }
          >
            {busy === 'add' ? (
              <Loader2 size={14} className="syn-spin" aria-hidden />
            ) : (
              <BellPlus size={14} aria-hidden />
            )}
            订阅这个主题
          </button>
        </div>
      )}

      {err && (
        <p className="syn-watch-err">
          <AlertTriangle size={14} aria-hidden /> {err}
        </p>
      )}

      {items.length === 0 ? (
        <p className="syn-watch-empty">还没有订阅。搜到一个想持续跟的主题，在上面点一下就行。</p>
      ) : (
        <ul className="syn-watch-list">
          {items.map((w) => (
            <li key={w.id} className={`syn-watch-item ${due.includes(w.id) ? 'is-due' : ''}`}>
              <div className="syn-watch-main">
                <span className="syn-watch-label">{w.label || w.query}</span>
                <span className="syn-watch-meta">
                  每 {w.intervalHours} 小时 · 已记住 {w.seenCount} 条
                  {w.lastRun > 0
                    ? ` · 上次 ${new Date(w.lastRun * 1000).toLocaleString('zh-CN')}`
                    : ' · 还没跑过'}
                  {w.autoIngest ? ' · 自动入库' : ''}
                </span>
                {result[w.id] && <span className="syn-watch-result">{result[w.id]}</span>}
              </div>
              <button
                type="button"
                title="现在跑一次，只报新出现的"
                disabled={busy != null}
                onClick={() =>
                  void act(`run-${w.id}`, async () => {
                    const r = await labApi.runWatch(w.id);
                    setResult((prev) => ({ ...prev, [w.id]: r.note }));
                    await load();
                  })
                }
              >
                {busy === `run-${w.id}` ? (
                  <Loader2 size={14} className="syn-spin" aria-hidden />
                ) : (
                  <Play size={14} aria-hidden />
                )}
              </button>
              <button
                type="button"
                className="syn-watch-danger"
                title="删掉这条订阅"
                disabled={busy != null}
                onClick={() =>
                  void act(`del-${w.id}`, async () => {
                    await labApi.removeWatch(w.id);
                    // F3：删订阅是**真的可以撤销**的 —— 重新建一条一样的就行，
                    // 而且是瞬间完成、不会失败。
                    // 🔴 只给这种真能可靠还原的动作压撤销栈。删库条目虽然现在
                    // 会进回收站，但"恢复"是重新投喂原路径——不是瞬间的，
                    // 原文件被挪走/删了还会直接失败。压进这个撤销栈的话，
                    // 点了会以为立刻恢复了，其实要等、还可能失败 ——
                    // 那种"看起来撤销了、其实没有"的落差比没有撤销更糟。
                    // ⚠️ 还原出来的是**新 id**，`seen` 指纹表回不来，
                    // 所以撤销之后第一次跑会把已经看过的当成新的报一遍。
                    // 这个代价写在标签里，用户看得见
                    history.push({
                      label: `删除订阅「${w.label || w.query}」（撤销后会重新报一遍旧内容）`,
                      undo: async () => {
                        await labApi.addWatch({
                          query: w.query,
                          label: w.label,
                          engines: w.engines,
                          intervalHours: w.intervalHours,
                          autoIngest: w.autoIngest,
                        });
                        await load();
                      },
                    });
                    await load();
                  })
                }
              >
                <Trash2 size={14} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}

    </section>
  );
}
