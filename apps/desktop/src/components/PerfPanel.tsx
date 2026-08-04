import { useCallback, useEffect, useState } from 'react';
import { Activity, Check, Minus, RotateCcw, TriangleAlert } from 'lucide-react';
import { api } from '../lib/api';
import {
  observeClient,
  resetPerf,
  startFrameSampling,
  stopFrameSampling,
  verdict,
  type Observation,
} from '../lib/perf';

/**
 * C6 性能看板 —— 把 E1–E8 从"口号"变成"你这台机器上的数字"
 * ====================================================================
 * 用户口令是「我自己测」。这个面板要做的事，是**把"测"这个动作
 * 从"拿秒表卡八个场景"降到"打开设置页看一眼"** ——
 * 应用自己在你平时用的过程中就把数采好了。
 *
 * ── 三条不许违反的显示纪律 ────────────────────────────────
 * ① **没采到样本显示「还没测」，绝不显示 0。**
 *    「0ms」会被读成"快到没有延迟"，那是最糟的一种谎报，
 *    因为它看起来还特别可信。
 * ② **样本不足时不下达标结论。** n<20 的 P95 基本就是最大值，
 *    拿它宣布"达标"和拿它宣布"不达标"一样没有意义 —— 一律显示「—」。
 * ③ **明写这不是基准测试。** 这里量的是"你刚才那几次操作"，
 *    它比实验室数字更贴近体感，但不能当验收证据。
 *    引擎侧 `metrics.py` 就是这个口径，两边必须一致，不然自己跟自己打架。
 *
 * 🔴 帧率采样只在这个面板挂载期间跑，卸载立刻停 ——
 *    一个永远跑着的 rAF 循环本身就是主线程负担，
 *    "测帧率的东西把帧率拉低了"是最讽刺的一类 bug。
 */

interface EngineBudget {
  id: string;
  label: string;
  target: string;
  how: string;
  hasBench: boolean;
}

interface EngineObserved {
  rssMb?: number | null;
  rssNote?: string;
  engineLatencyMs?: { count: number; median: number | null; max: number | null } | null;
  cache?: unknown;
  allowNetwork?: boolean;
}

export function PerfPanel() {
  const [obs, setObs] = useState<Observation[]>(() => observeClient());
  const [engineBudgets, setEngineBudgets] = useState<EngineBudget[]>([]);
  const [engineObs, setEngineObs] = useState<EngineObserved | null>(null);
  const [stats, setStats] = useState<{ items: number; chunks: number } | null>(null);

  // 采帧 + 定时刷新。1 秒一次：再快没有意义（帧率中位数不会一秒变两次），
  // 再慢会让"我滚了一下，数字动没动"这个反馈断掉
  useEffect(() => {
    startFrameSampling();
    const t = window.setInterval(() => setObs(observeClient()), 1000);
    return () => {
      window.clearInterval(t);
      stopFrameSampling();
    };
  }, []);

  const loadEngine = useCallback(async () => {
    // 每一路各自 catch：引擎没起来时这个面板照样要能显示界面侧那几条，
    // 而不是整块变成一个报错
    const [b, s] = await Promise.all([
      api
        .metricsBudgets()
        .catch(() => null),
      api.stats().catch(() => null),
    ]);
    if (b) {
      setEngineBudgets([...(b.budgets ?? []), ...(b.ingestBudgets ?? [])]);
      setEngineObs((b.observed ?? null) as EngineObserved | null);
    }
    if (s) setStats({ items: Number(s.items ?? 0), chunks: Number(s.chunks ?? 0) });
  }, []);

  useEffect(() => {
    void loadEngine();
  }, [loadEngine]);

  return (
    <div className="perf">
      <div className="perf__note">
        <TriangleAlert size={13} strokeWidth={1.8} />
        <span>
          这些是<strong>你平时用的过程中自然采到的样本</strong>，不是基准测试。
          样本少的时候抖动很大——所以样本不够的行不会给达标结论，只显示「—」。
          想让数字变准，就正常用一会儿（多搜几次、滚一滚长列表）再回来看。
        </span>
      </div>

      <div className="perf__head">
        <Activity size={15} strokeWidth={1.7} />
        <span className="perf__headtitle">界面侧（E1 / E3 / E4 / E6 / E7 / E8）</span>
        <span className="perf__spacer" />
        <button
          className="btn"
          onClick={() => {
            resetPerf();
            setObs(observeClient());
          }}
          title="清掉已有样本重新开始采——想单独量某个操作时用"
        >
          <RotateCcw size={13} strokeWidth={1.7} />
          重新开始测
        </button>
      </div>

      <table className="perf__table">
        <thead>
          <tr>
            <th>指标</th>
            <th>目标</th>
            <th>实测</th>
            <th>样本</th>
            <th>判定</th>
          </tr>
        </thead>
        <tbody>
          {obs.map((o) => {
            const v = verdict(o);
            return (
              <tr key={o.id}>
                <td>
                  <div className="perf__label">{o.label}</div>
                  {o.note && <div className="perf__hint">{o.note}</div>}
                </td>
                <td className="perf__target">{o.target}</td>
                <td className="perf__value">
                  {/* 🔴 null 一定要显示成「还没测」而不是 0 —— 见文件头纪律① */}
                  {o.value === null ? (
                    <span className="perf__unknown">还没测</span>
                  ) : (
                    <>
                      <b>{o.value}</b>
                      <span className="perf__unit">{o.unit}</span>
                    </>
                  )}
                </td>
                <td className="perf__n">{o.n || '—'}</td>
                <td>
                  {v === 'pass' && (
                    <span className="perf__ok" title="达标">
                      <Check size={14} strokeWidth={2.2} />
                    </span>
                  )}
                  {v === 'fail' && (
                    <span className="perf__bad" title="没到目标">
                      <TriangleAlert size={14} strokeWidth={2} />
                    </span>
                  )}
                  {v === null && (
                    <span className="perf__unknown" title="样本不够，不下结论">
                      <Minus size={14} strokeWidth={2} />
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* ── 引擎侧 ────────────────────────────────────── */}
      <div className="perf__head">
        <Activity size={15} strokeWidth={1.7} />
        <span className="perf__headtitle">引擎侧（E2 索引吞吐 / E4 引擎内存）</span>
        <span className="perf__spacer" />
        <button className="btn" onClick={() => void loadEngine()}>
          刷新
        </button>
      </div>

      {engineObs === null ? (
        <p className="perf__hint">引擎还没就绪，读不到它那边的数。</p>
      ) : (
        <div className="perf__nums">
          <span className="perf__num">
            引擎内存{' '}
            {engineObs.rssMb == null ? (
              <span className="perf__unknown">读不到</span>
            ) : (
              <b>{engineObs.rssMb} MB</b>
            )}
          </span>
          {engineObs.rssNote && <span className="perf__hint">{engineObs.rssNote}</span>}
          <span className="perf__num">
            联网引擎耗时中位数{' '}
            {engineObs.engineLatencyMs?.median == null ? (
              <span className="perf__unknown">还没搜过网</span>
            ) : (
              <b>{engineObs.engineLatencyMs.median} ms</b>
            )}
          </span>
          {stats && (
            <span className="perf__num">
              库规模 <b>{stats.items}</b> 条 / <b>{stats.chunks}</b> 片段
            </span>
          )}
        </div>
      )}

      {/* G 组老指标：只列出来并**如实显示有没有基准脚本**。
          hasBench=false 的那些是"还没测过"，不是"测过了但不合格"——
          这两件事在验收表上必须分得开 */}
      {engineBudgets.length > 0 && (
        <details className="perf__more">
          <summary>引擎自己定义的 {engineBudgets.length} 条目标（G 组 / A 组）</summary>
          <table className="perf__table">
            <tbody>
              {engineBudgets.map((b) => (
                <tr key={b.id}>
                  <td>
                    <div className="perf__label">
                      {b.id} {b.label}
                    </div>
                    <div className="perf__hint">{b.how}</div>
                  </td>
                  <td className="perf__target">{b.target}</td>
                  <td>
                    {b.hasBench ? (
                      <span className="perf__hint">有基准脚本</span>
                    ) : (
                      <span className="perf__unknown">还没有基准脚本</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      <p className="perf__hint">
        E5 安装包体积是构建期属性，运行期量不到——看 GitHub Releases 页上那个 exe 的大小。
        E2 索引吞吐在「分析中心」跑一批文件时会直接显示每秒多少条。
      </p>
    </div>
  );
}
