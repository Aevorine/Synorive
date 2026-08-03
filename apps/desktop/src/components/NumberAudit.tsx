import { useState } from 'react';
import { AlertTriangle, CalendarClock, CheckCircle2, HelpCircle, Loader2, Scale } from 'lucide-react';
import {
  labApi,
  type ControversyInfo,
  type NumberCheckResult,
  type TimelineConflictItem,
} from '../lib/labApi';

/**
 * D2 时间线冲突 ｜ D5 数字回原文校对 ｜ D6 争议度
 * ============================================================
 * 三件事放一个面板，因为它们回答的是同一个问题的三个侧面：
 * **这份简报里，哪些地方我不该照抄。**
 *
 *   D5 数字 —— 简报里的数字在原文里找不到
 *   D2 时间 —— 不同来源说这件事发生在不同日期
 *   D6 争议 —— 这个说法本身就有人在反对
 *
 * 🔴 **三者都只标不判**。特别是 D5 的「对不上」：可能是原文换了单位、
 * 换了表述，也可能是引擎给的摘要被截断了。所以界面上写的是
 * 「原文里没找到这个数」而不是「这个数错了」，并且**永远同时给出
 * 「最接近的是哪几个」** —— 那才是用户能直接拿去改的东西。
 *
 * 🔴 **`unverified` 单独一档，不能和 `ok` 混在一起。**
 * 「查了，对上了」和「没能查」是两件事。混起来显示等于把没查的
 * 悄悄算成查过的，那正是这个功能要防的事。
 */

export function NumberAudit({
  briefing,
  results,
  texts,
  verification,
  onFetchTexts,
}: {
  briefing: unknown;
  results: unknown[];
  /** {出处url: 正文全文}。由调用方从 read_url 的结果里攒起来 */
  texts: Record<string, string>;
  verification?: unknown;
  /**
   * 现去抓几篇正文。**不在这个组件里自己发抓取请求** ——
   * 抓正文是要联网的动作，而这个面板看起来像个"分析工具"；
   * 让它偷偷出网会绕过隐私围栏那道闸。由页面来做，页面知道开关状态
   */
  onFetchTexts?: () => Promise<void>;
}) {
  const [nums, setNums] = useState<NumberCheckResult | null>(null);
  const [conflicts, setConflicts] = useState<{
    conflicts: TimelineConflictItem[];
    verdict: string;
    note: string;
  } | null>(null);
  const [contro, setContro] = useState<{
    claims?: { claim: string; controversy?: ControversyInfo }[];
    controversyAvg?: number;
  } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const noTexts = Object.keys(texts).length === 0;

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

  return (
    <section className="syn-audit">
      <h2 className="syn-audit-title">别照抄的地方</h2>

      {/* 拿不到正文时**提前说清楚**，而不是让用户点了之后拿到一堆
          "没法查"再自己推理为什么 */}
      {noTexts && (
        <p className="syn-audit-warn">
          <HelpCircle size={14} aria-hidden />
          还没有抓到任何出处的正文，数字和时间这两项查不了。
          {onFetchTexts && (
            <button
              type="button"
              className="syn-audit-inline"
              disabled={busy != null}
              onClick={() => void run('fetch', onFetchTexts)}
            >
              {busy === 'fetch' ? '抓着呢……' : '现在去抓前几篇正文'}
            </button>
          )}
        </p>
      )}

      <div className="syn-audit-actions">
        <button
          type="button"
          disabled={busy != null || noTexts}
          onClick={() => void run('num', async () => setNums(await labApi.numbers(briefing, texts)))}
        >
          {busy === 'num' ? <Loader2 size={14} className="syn-spin" aria-hidden /> : <Scale size={14} aria-hidden />}
          核对数字
        </button>
        <button
          type="button"
          disabled={busy != null || noTexts}
          onClick={() =>
            void run('time', async () => setConflicts(await labApi.timelineConflicts(results, texts)))
          }
        >
          {busy === 'time' ? (
            <Loader2 size={14} className="syn-spin" aria-hidden />
          ) : (
            <CalendarClock size={14} aria-hidden />
          )}
          查时间冲突
        </button>
        {verification != null && (
          <button
            type="button"
            disabled={busy != null}
            onClick={() =>
              void run('contro', async () => setContro(await labApi.controversy(verification)))
            }
          >
            算争议度
          </button>
        )}
      </div>

      {err && (
        <p className="syn-audit-err">
          <AlertTriangle size={14} aria-hidden /> {err}
        </p>
      )}

      {/* ── D5 数字 ─────────────────────────────────── */}
      {nums && (
        <div className="syn-audit-block">
          <h3>
            数字核对
            <span className="syn-audit-tally">
              <em className="is-ok">对上 {nums.ok}</em>
              <em className="is-bad">对不上 {nums.mismatch}</em>
              <em className="is-unk">没法查 {nums.unverified}</em>
            </span>
          </h3>
          <p className="syn-audit-note">{nums.note}</p>
          {nums.checks
            .filter((c) => c.status !== 'ok')
            .map((c) => (
              <p key={`${c.raw}-${c.sourceUrl}`} className={`syn-audit-num is-${c.status}`}>
                <b>{c.raw}</b>
                <span className="syn-audit-ctx">{c.context}</span>
                <span className="syn-audit-why">
                  {c.note}
                  {c.near.length > 0 && <> —— 你要写的是不是 {c.near.join(' / ')}？</>}
                </span>
              </p>
            ))}
          {nums.mismatch === 0 && nums.unverified === 0 && (
            <p className="syn-audit-ok">
              <CheckCircle2 size={14} aria-hidden /> 全部数字都在原文里找到了。
            </p>
          )}
        </div>
      )}

      {/* ── D2 时间 ─────────────────────────────────── */}
      {conflicts && (
        <div className="syn-audit-block">
          <h3>事件时间</h3>
          <p className="syn-audit-note">
            {conflicts.verdict}。{conflicts.note}
          </p>
          {conflicts.conflicts.map((c) => (
            <div key={`${c.aUrl}-${c.bUrl}`} className="syn-audit-conflict">
              <p className="syn-audit-gap">差 {c.gapDays} 天</p>
              <p>
                <b>{c.aSite}</b> 说 {c.aDate}：{c.aContext}
              </p>
              <p>
                <b>{c.bSite}</b> 说 {c.bDate}：{c.bContext}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* ── D6 争议度 ───────────────────────────────── */}
      {contro?.claims && (
        <div className="syn-audit-block">
          <h3>
            争议度
            {contro.controversyAvg != null && (
              <span className="syn-audit-tally">平均 {contro.controversyAvg}/100</span>
            )}
          </h3>
          <p className="syn-audit-note">
            争议度高**不等于假** —— 学界正在讨论的前沿问题天然就高，那往往正是最值得读的。
            这个数只用来排序和提示。
          </p>
          {contro.claims
            .filter((c) => c.controversy && c.controversy.level !== 'unknown')
            .sort((a, b) => (b.controversy?.score ?? 0) - (a.controversy?.score ?? 0))
            .map((c) => (
              <p key={c.claim} className={`syn-audit-claim is-${c.controversy?.level}`}>
                <span className="syn-audit-score">{c.controversy?.score}</span>
                <span className="syn-audit-claim-text">{c.claim}</span>
                <span className="syn-audit-why">
                  支持 {c.controversy?.support} ／ 反驳 {c.controversy?.refute}
                  {'　'}
                  {c.controversy?.note}
                </span>
              </p>
            ))}
        </div>
      )}
    </section>
  );
}
