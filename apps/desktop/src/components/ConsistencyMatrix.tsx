import { useState } from 'react';
import type { ConsistencyMatrix as MatrixData } from '../lib/webApi';

/**
 * 一致性矩阵 —— V2
 * ============================================================
 * **为什么值得单独做一个矩阵，而不是让用户读那几段分歧文字**：
 * 分歧是一对一对给的，三个话题各有分歧就是六段文字，读完还是不知道
 * "到底哪个站老是跟别人不一样"。
 *
 * 矩阵能显示出**模式**：某一列整列都是叉，那这个站本身就可疑 ——
 * 这个信息在逐对呈现的文字里根本看不出来。
 *
 * 🔴 **空白格 = 没提，不是中立。** 这两件事完全不同，
 * 合成一个符号会让"三个站都没提"看起来像"三个站都保持中立"。
 * 所以空白格真的留空（不画符号），并在图例里写明。
 */

const CELL: Record<string, { sym: string; cls: string; title: string }> = {
  positive: { sym: '✔', cls: 'mx--pos', title: '这个来源在肯定这个说法' },
  negative: { sym: '✘', cls: 'mx--neg', title: '这个来源在否定这个说法' },
  mixed: { sym: '～', cls: 'mx--mix', title: '提到了但态度不明确' },
  silent: { sym: '', cls: 'mx--silent', title: '这个来源没提这个话题（不是中立）' },
};

export function ConsistencyMatrix({ m }: { m?: MatrixData | null }) {
  const [focus, setFocus] = useState<{ t: number; s: number } | null>(null);

  if (!m || !m.sites.length || !m.topics.length) return null;

  const cell = focus ? m.cells[focus.t]?.[focus.s] : null;

  return (
    <section className="matrix">
      <header className="matrix__head">
        <h3>一致性矩阵</h3>
        <span className="matrix__note">{m.note}</span>
      </header>

      {/* 站点多的时候会超出宽度，让它自己横向滚，别把整页撑宽 */}
      <div className="matrix__scroll">
        <table className="matrix__table">
          <thead>
            <tr>
              <th className="matrix__corner">话题 ＼ 来源</th>
              {m.sites.map((s) => (
                <th key={s} title={s}>
                  {/* 域名太长会把表头撑爆，截断但 title 里给全名 */}
                  {s.length > 14 ? `${s.slice(0, 13)}…` : s}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.topics.map((topic, ti) => (
              <tr key={topic}>
                <th scope="row">{topic}</th>
                {m.sites.map((site, si) => {
                  const c = m.cells[ti]?.[si];
                  const st = CELL[c?.stance ?? 'silent']!;
                  const active = focus?.t === ti && focus?.s === si;
                  return (
                    <td
                      key={site}
                      className={`${st.cls} ${active ? 'is-active' : ''}`}
                      title={st.title}
                      onClick={() => setFocus(active ? null : { t: ti, s: si })}
                    >
                      {st.sym}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="matrix__legend">
        <span className="mx--pos">✔ 肯定</span>
        <span className="mx--neg">✘ 否定</span>
        <span className="mx--mix">～ 态度不明</span>
        <span className="mx--silent">空白 = 这个来源<strong>没提</strong>，不是中立</span>
      </div>

      {/* 点一个格子看它背后的原文。矩阵只给形状，判断还是要回到原文 */}
      {cell && cell.text && (
        <blockquote className="matrix__quote">
          「{cell.text}」
          {cell.url && (
            <a href={cell.url} target="_blank" rel="noopener noreferrer">
              看原文
            </a>
          )}
        </blockquote>
      )}
      {cell && !cell.text && (
        <p className="matrix__quote matrix__quote--empty">
          这个来源没有提到这个话题 —— 没有原文可看。
        </p>
      )}
    </section>
  );
}
