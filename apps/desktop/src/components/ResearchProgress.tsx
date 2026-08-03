import { useEffect, useRef, useState } from 'react';
import { Check, Loader2 } from 'lucide-react';

/**
 * 深挖实时进度 —— U2
 * ============================================================
 * **要治的病**：深挖含两轮加核查要十几到三十秒，而这段时间界面上
 * 只有一个转圈图标。用户分不清「它在干活」和「它卡死了」——
 * 而这两种情况下他该做的事**完全相反**（继续等 vs 重来）。
 * 一个转圈图标同时代表这两种状态，等于什么都没说。
 *
 * 所以这里显示的不是百分比，是**它此刻在做什么**：
 *   「正在反向搜辟谣/质疑」比「67%」有用得多 ——
 *   前者能让用户判断"这一步值不值得等"，后者不能。
 *
 * 🔴 **不做假进度条。** 每一步的真实耗时差好几倍（抓正文可能 2 秒也可能
 * 20 秒），按步数匀速走的进度条会在最后一步卡住不动，那比没有进度条
 * 更让人焦虑 —— 用户会以为它是死了。所以只画"走到第几步"，不骗人。
 */

export interface ProgressEvent {
  stage: string;
  detail: string;
  step: number;
  totalStages: number;
  elapsedMs: number;
  query?: string;
  round?: number;
  queries?: { text: string; why: string }[];
}

/** 和引擎侧 `deepdive.STAGES` 一一对应，顺序要稳定 */
const STAGE_LABELS: Record<string, string> = {
  expand: '想清楚该搜什么',
  search: '多引擎并发搜索',
  rank: '判可信度、排序',
  fetch: '抓正文',
  brief: '出摘录简报',
  followup: '读完之后追问一轮',
  verify: '主动核查',
  done: '完成',
};

const ORDER = ['expand', 'search', 'rank', 'fetch', 'brief', 'followup', 'verify', 'done'];

export function ResearchProgress({ active }: { active: boolean }) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const startedAt = useRef<number>(0);

  // 订阅引擎事件。**不在这里过滤掉重复 stage** ——
  // 同一个 stage 会推好几条（比如 fetch 阶段每抓完一篇），
  // 那些细节正是"它还活着"的证据
  useEffect(() => {
    return window.synorive.engine.onEvent((raw) => {
      const ev = raw as { type?: string; payload?: ProgressEvent };
      if (ev?.type !== 'research.progress' || !ev.payload) return;
      setEvents((prev) => [...prev.slice(-40), ev.payload!]);
    });
  }, []);

  // 新一轮开始时清空。不清的话上一次的「完成」会挂在那儿，
  // 让人以为这次也已经跑完了
  useEffect(() => {
    if (active) {
      setEvents([]);
      startedAt.current = Date.now();
      setElapsed(0);
    }
  }, [active]);

  // 秒表：**必须有**。没有它的话，一次 25 秒的深挖和一次卡死
  // 在界面上长得一模一样
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setElapsed(Date.now() - startedAt.current), 200);
    return () => clearInterval(t);
  }, [active]);

  if (!active && !events.length) return null;

  const last = events[events.length - 1];
  const reached = new Set(events.map((e) => e.stage));
  const currentIdx = last ? ORDER.indexOf(last.stage) : -1;

  return (
    <div className="rp">
      <div className="rp__head">
        {active && last?.stage !== 'done' ? (
          <Loader2 size={14} className="spin" aria-hidden />
        ) : (
          <Check size={14} aria-hidden />
        )}
        <span className="rp__now">{last?.detail ?? '正在开始…'}</span>
        <span className="rp__clock">{(elapsed / 1000).toFixed(1)}s</span>
      </div>

      <ol className="rp__steps">
        {ORDER.map((s, i) => {
          const state =
            i < currentIdx || last?.stage === 'done'
              ? 'done'
              : i === currentIdx
                ? 'now'
                : reached.has(s)
                  ? 'done'
                  : 'todo';
          return (
            <li key={s} className={`rp__step rp__step--${state}`}>
              {STAGE_LABELS[s] ?? s}
            </li>
          );
        })}
      </ol>

      {/* 追问那一步单独展开：这是唯一一处「我替你决定去搜了别的东西」，
          必须让你在等待期间就看到，而不是等结果出来才补一句 */}
      {events
        .filter((e) => e.stage === 'followup' && e.queries?.length)
        .slice(-1)
        .map((e) => (
          <div className="rp__followup" key={e.step}>
            <span className="rp__followup-title">第 {e.round} 轮追问的是：</span>
            {e.queries!.map((q) => (
              <span key={q.text} className="rp__followup-q">
                <code>{q.text}</code> — {q.why}
              </span>
            ))}
          </div>
        ))}

      {/* 明细逐条留着。深挖慢的时候，用户最想知道的是"慢在哪一步"，
          而不是"总共花了多久" */}
      {events.length > 1 && (
        <details className="rp__log">
          <summary>看每一步花了多久</summary>
          <ul>
            {events.map((e, i) => (
              <li key={`${e.step}-${i}`}>
                <span className="rp__log-t">{(e.elapsedMs / 1000).toFixed(1)}s</span>
                <span className="rp__log-s">{STAGE_LABELS[e.stage] ?? e.stage}</span>
                <span className="rp__log-d">{e.detail}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
