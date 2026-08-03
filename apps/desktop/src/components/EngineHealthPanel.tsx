import { useEffect, useState } from 'react';
import { Activity, KeyRound, MonitorSmartphone, RefreshCw } from 'lucide-react';
import { webApi, type EngineHealthRow } from '../lib/webApi';

/**
 * 引擎健康仪表盘 —— S1
 * ============================================================
 * **要治的病**：现在派哪几家引擎出去是一份写死的名单，而实测
 * （2026-08-02，23 条通道逐个实调）证明这份名单**每隔一段时间就过期一次** ——
 * Google 强制了 JS、DuckDuckGo 的 html 端点改成 JS 落地页、
 * SearXNG 公共实例集体封代理 IP。
 *
 * 所以引擎那边改成了「记住每家最近的表现，自己决定下轮派谁」。
 * 这个面板是它的可视化：**每家一句人话** ——
 * 用户不该需要理解「0.73」是什么意思才知道今天能不能用它。
 *
 * 🔴 **失败原因必须分类显示**。「要 Key」「要浏览器」「被限流」「解析坏了」
 * 是四种完全不同的处置：填个 Key／开着桌面端／等一会儿／这家废了。
 * 全都显示成一个灰掉的开关，用户只会以为是软件坏了。
 */

function verdictClass(score: number, samples: number): string {
  if (!samples) return 'eh--unknown';
  if (score >= 0.75) return 'eh--good';
  if (score >= 0.45) return 'eh--mid';
  if (score >= 0.08) return 'eh--bad';
  return 'eh--dead';
}

function ms(v?: number | null): string {
  if (v == null) return '—';
  return v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`;
}

export function EngineHealthPanel({ onClose }: { onClose?: () => void }) {
  const [rows, setRows] = useState<EngineHealthRow[] | null>(null);
  const [renderer, setRenderer] = useState(false);
  const [lineup, setLineup] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    try {
      const r = await webApi.engines();
      setRows(r.health.table ?? null);
      setRenderer(r.health.rendererAvailable);
      setLineup(r.health.lineupSize ?? 0);
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const web = (rows ?? []).filter((r) => r.group === 'web');
  const scholar = (rows ?? []).filter((r) => r.group === 'scholar');

  const table = (list: EngineHealthRow[], title: string) => (
    <>
      <h4 className="eh__section">{title}</h4>
      <table className="eh__table">
        <thead>
          <tr>
            <th>引擎</th>
            <th>状态</th>
            <th>成功率</th>
            <th>平均耗时</th>
            <th>样本</th>
          </tr>
        </thead>
        <tbody>
          {list.map((r) => (
            <tr key={r.id} className={verdictClass(r.score, r.samples)}>
              <td>
                <span className="eh__name">{r.label}</span>
                <span className="eh__tags">
                  {r.needsKey && (
                    <span className="eh__tag" title="要在设置里填 API Key 才能用">
                      <KeyRound size={11} /> 要 Key
                    </span>
                  )}
                  {r.needsBrowser && (
                    <span
                      className={`eh__tag ${renderer ? '' : 'eh__tag--off'}`}
                      title={
                        renderer
                          ? '需要浏览器渲染，桌面端已连上'
                          : '需要浏览器渲染，但现在没连上桌面端（命令行/MCP 单独跑引擎时这条走不通）'
                      }
                    >
                      <MonitorSmartphone size={11} /> 要浏览器
                    </span>
                  )}
                  {r.kind === 'api' && <span className="eh__tag">官方接口</span>}
                </span>
              </td>
              <td className="eh__verdict">{r.verdict}</td>
              <td>{r.okRate == null ? '—' : `${Math.round(r.okRate * 100)}%`}</td>
              <td>{ms(r.avgMs)}</td>
              <td>{r.samples || 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );

  return (
    <section className="eh">
      <header className="eh__head">
        <Activity size={16} aria-hidden />
        <h3>引擎健康</h3>
        <button className="eh__refresh" onClick={() => void load()} disabled={busy}>
          <RefreshCw size={14} className={busy ? 'spin' : ''} /> 刷新
        </button>
        {onClose && (
          <button className="eh__close" onClick={onClose}>
            关闭
          </button>
        )}
      </header>

      <p className="eh__note">
        {lineup > 0 ? (
          <>
            自动排班已开：每轮只派表现最好的 <strong>{lineup}</strong> 家，
            外加一个<strong>探索位</strong>去试最久没试过的那家 ——
            没有探索位的话，一家暂时失败的引擎会永远没机会翻身。
          </>
        ) : (
          <>
            当前是<strong>全部派出</strong>。表现分还在记，
            想让它自动挑就去设置里把「每轮派几家」设成 5。
          </>
        )}
      </p>

      {err && <p className="eh__error">拿不到引擎状态：{err}</p>}
      {!rows && !err && <p className="eh__note">正在读取…</p>}
      {rows && (
        <>
          {web.length > 0 && table(web, '网页搜索')}
          {scholar.length > 0 && table(scholar, '学术源')}
          <p className="eh__foot">
            「还没试过」不是坏事 —— 表示这家还没被派出去过，分数是中位默认值。
            一家引擎连续三次<strong>解析失败</strong>才会被熔断；
            被限流不算失败（那是"稍后再来"，不是"这家废了"）。
          </p>
        </>
      )}
    </section>
  );
}
