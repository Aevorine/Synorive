import { useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, GitBranch, ShieldAlert, ShieldCheck } from 'lucide-react';
import type { ClaimVerdict, Verification } from '../lib/webApi';

/**
 * 核查面板 —— V1 / V4 / V6 / V7 的展示层
 * ============================================================
 * 🔴 **贯穿整块的一条约束**：这里**不下"这是假的"这种结论**。
 * 找到反驳源不等于原说法就错了（也可能是反驳的人错了）。
 * 所以每一处显示的都是「支持 N ／ 反驳 M ／ 无证据 K」+ 两边的原文出处，
 * 判断留给用户。
 *
 * 界面上的四块，对应四种"可疑"，**刻意不合成一个总分**：
 *   反面材料（V6）—— 有人公开质疑过这件事
 *   溯源（V4）—— 这话最早从哪冒出来的、是不是复制链
 *   撤稿（V7）—— 引用的论文已经被撤了
 *   断言核查（V1）—— 逐句去查，最贵的一档
 * 压成一个分数的话，"这个站专发假消息"和"没查到发布时间"会变成同一个数字。
 */

const VERDICT_STYLE: Record<ClaimVerdict['verdict'], { label: string; cls: string }> = {
  supported: { label: '有多方印证', cls: 'vd--ok' },
  disputed: { label: '有人反驳', cls: 'vd--bad' },
  weak: { label: '孤证', cls: 'vd--warn' },
  unverified: { label: '没查到', cls: 'vd--muted' },
};

const ORIGIN_STYLE: Record<string, { icon: typeof GitBranch; cls: string }> = {
  burst: { icon: AlertTriangle, cls: 'origin--bad' },
  'weak-origin': { icon: AlertTriangle, cls: 'origin--warn' },
  unknown: { icon: GitBranch, cls: 'origin--muted' },
  ok: { icon: GitBranch, cls: 'origin--ok' },
};

export function VerificationPanel({ v }: { v?: Verification | null }) {
  const [openClaims, setOpenClaims] = useState<Set<number>>(new Set());
  const [chainOpen, setChainOpen] = useState(false);

  if (!v) return null;

  const counter = v.counterEvidence ?? [];
  const retracted = Object.entries(v.retracted ?? {});
  const origin = v.origin;
  const claims = v.claims ?? [];

  // annotate 档什么都没查，如实说明为什么这里是空的 ——
  // 空着不解释，用户只会以为核查坏了
  if (v.level === 'annotate') {
    return (
      <section className="verify">
        <header className="verify__head">
          <ShieldCheck size={16} aria-hidden />
          <h3>核查</h3>
          <span className="verify__level">只标注档</span>
        </header>
        <p className="verify__note">
          当前是「只标注」档：只做来源分级、内容农场特征、时效判断，
          <strong>没有额外出网核查</strong>。想让它主动去找反驳材料，
          在设置里把核查档位调到「反向检索」。
        </p>
      </section>
    );
  }

  const toggle = (i: number) =>
    setOpenClaims((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  return (
    <section className="verify">
      <header className="verify__head">
        {counter.length || retracted.length ? (
          <ShieldAlert size={16} className="verify__icon--alert" aria-hidden />
        ) : (
          <ShieldCheck size={16} aria-hidden />
        )}
        <h3>核查</h3>
        <span className="verify__level">
          {v.level === 'claim' ? '断言级逐句核查' : '反向检索档'}
        </span>
      </header>

      {v.note && <p className="verify__note">{v.note}</p>}

      {/* V7 撤稿 —— 放最前面，这是唯一一类"确定性事实" */}
      {retracted.length > 0 && (
        <div className="verify__block verify__block--danger">
          <h4>已撤稿的文献（{retracted.length} 篇）</h4>
          <ul>
            {retracted.map(([doi, info]) => (
              <li key={doi}>
                <strong>{info.title ?? doi}</strong>
                <span className="verify__meta">
                  DOI {doi}
                  {info.citedBy != null && ` · 撤稿后仍被引 ${info.citedBy} 次`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* V6 反面材料 */}
      <div className="verify__block">
        <h4>反面材料（{counter.length}）</h4>
        {counter.length === 0 ? (
          <p className="verify__empty">
            没搜到公开的质疑或辟谣。<strong>这不等于它是真的</strong> ——
            只说明没人公开反驳过。
          </p>
        ) : (
          <ul className="verify__list">
            {counter.map((c) => (
              <li key={c.url}>
                <a href={c.url} target="_blank" rel="noopener noreferrer">
                  {c.title}
                </a>
                <span className="verify__meta">{c.site}</span>
                <p className="verify__snippet">{c.snippet}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* V4 溯源 */}
      {origin && (
        <div className={`verify__block origin ${ORIGIN_STYLE[origin.verdict]?.cls ?? ''}`}>
          <h4>溯源</h4>
          <p className="verify__note">{origin.note}</p>
          {origin.earliest && (
            <p className="origin__earliest">
              最早可查：
              <a href={origin.earliest.url} target="_blank" rel="noopener noreferrer">
                {origin.earliest.title}
              </a>
              <span className="verify__meta">
                {origin.earliest.site} · {origin.earliest.published} · {origin.earliest.tier}
              </span>
            </p>
          )}
          {origin.chain.length > 1 && (
            <>
              <button className="verify__toggle" onClick={() => setChainOpen((o) => !o)}>
                {chainOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                传播链（{origin.chain.length} 条
                {origin.undated ? `，另有 ${origin.undated} 条没日期排不进来` : ''}）
              </button>
              {chainOpen && (
                <ol className="origin__chain">
                  {origin.chain.map((c) => (
                    <li key={c.url}>
                      <span className="origin__date">{c.published}</span>
                      <a href={c.url} target="_blank" rel="noopener noreferrer">
                        {c.title}
                      </a>
                      <span className="verify__meta">{c.site}</span>
                    </li>
                  ))}
                </ol>
              )}
            </>
          )}
        </div>
      )}

      {/* V1 断言核查 */}
      {claims.length > 0 && (
        <div className="verify__block">
          <h4>
            断言核查（{claims.length} 条）
            {v.claimSummary && (
              <span className="verify__meta">
                　印证 {v.claimSummary.supported} · 有争议 {v.claimSummary.disputed} ·
                孤证 {v.claimSummary.weak} · 没查到 {v.claimSummary.unverified}
              </span>
            )}
          </h4>
          <ul className="claims">
            {claims.map((c, i) => {
              const st = VERDICT_STYLE[c.verdict];
              const open = openClaims.has(i);
              return (
                <li key={`${c.claim}-${i}`} className="claim">
                  <button className="claim__head" onClick={() => toggle(i)}>
                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <span className={`vd ${st.cls}`}>{st.label}</span>
                    <span className="claim__text">{c.claim}</span>
                    <span className="claim__counts">
                      支持 {c.supportCount} ／ 反驳 {c.refuteCount}
                    </span>
                  </button>
                  {open && (
                    <div className="claim__body">
                      <p className="verify__note">{c.note}</p>
                      {c.refute.length > 0 && (
                        <>
                          <h5>反驳</h5>
                          <ul className="verify__list">
                            {c.refute.map((s) => (
                              <li key={s.url}>
                                <a href={s.url} target="_blank" rel="noopener noreferrer">
                                  {s.title}
                                </a>
                                <span className="verify__meta">{s.site}</span>
                                <p className="verify__snippet">{s.snippet}</p>
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                      {c.support.length > 0 && (
                        <>
                          <h5>支持</h5>
                          <ul className="verify__list">
                            {c.support.map((s) => (
                              <li key={s.url}>
                                <a href={s.url} target="_blank" rel="noopener noreferrer">
                                  {s.title}
                                </a>
                                <span className="verify__meta">{s.site}</span>
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}
