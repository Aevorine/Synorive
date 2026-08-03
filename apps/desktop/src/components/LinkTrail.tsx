import { ArrowDownRight, ArrowUpRight, Link2 } from 'lucide-react';
import type { ReadUrlResponse } from '../lib/webApi';

/**
 * 链接顺藤摸瓜 —— N4
 * ============================================================
 * `read_url` 原来只把正文抓回来。但一篇文章真正的价值往往不在
 * 它自己说了什么，而在 **它引用了谁** 和 **谁引用了它**：
 *
 *   出链 —— 一篇声称"研究表明 X"却一条站外链接都没有的文章，
 *          这件事本身就是最有力的判据。
 *   反链 —— 谁在讨论它、有没有人反驳它。用的是已有的多引擎搜索，
 *          不需要任何新能力。
 *
 * 🔴 **出链必须按来源等级分组**。一篇文章挂 40 条外链是常态，其中
 * 35 条是导航和分享按钮。平铺出来用户看不出重点；分组之后，
 * 「引用了 3 个官方文档」和「引用了 12 个不认识的站」是一眼可辨的两件事。
 */

const TIER_ORDER = ['官方', '学术', '主流媒体', '社区/个人', '未收录', '低信誉'];

export function LinkTrail({ data, onDeepDive }: { data: ReadUrlResponse; onDeepDive?: () => void }) {
  const t = data.trail;

  return (
    <div className="lt">
      <header className="lt__head">
        <Link2 size={16} aria-hidden />
        <div className="lt__title">
          <a
            href={data.finalUrl}
            onClick={(e) => {
              e.preventDefault();
              void window.synorive.sys.openExternal(data.finalUrl);
            }}
          >
            {data.title || data.finalUrl}
          </a>
          <span className="lt__meta">
            {data.site}
            <span className={`badge webcard__tier webcard__tier--${data.trust.tier}`}>
              {data.trust.tierLabel}
            </span>
            {data.author && `　作者：${data.author}`}
            {data.published ? `　${data.published.slice(0, 10)}` : '　发布时间：抓不到'}
          </span>
        </div>
        {onDeepDive && (
          <button className="btn btn--sm" onClick={onDeepDive}>
            以这篇为起点深挖
          </button>
        )}
      </header>

      {data.warnings.length > 0 && (
        <div className="banner banner--warn">{data.warnings.join('；')}</div>
      )}

      {t && (
        <>
          <p className="lt__note">{t.note}</p>

          <div className="lt__cols">
            <section className="lt__col">
              <h4>
                <ArrowUpRight size={13} aria-hidden /> 它引用了谁（{t.outlinks.length}）
              </h4>
              {Object.keys(t.byTier).length > 0 && (
                <div className="lt__tiers">
                  {TIER_ORDER.filter((k) => t.byTier[k]).map((k) => (
                    <span key={k} className="badge">
                      {k} {t.byTier[k]}
                    </span>
                  ))}
                </div>
              )}
              {!t.outlinks.length && (
                <p className="lt__empty">
                  <strong>一条站外链接都没有</strong> —— 它说的话没有任何可追溯的出处。
                </p>
              )}
              <ul className="lt__list">
                {t.outlinks.slice(0, 25).map((o) => (
                  <li key={o.url}>
                    <span className={`badge webcard__tier webcard__tier--${o.tier}`}>
                      {o.tierLabel}
                    </span>
                    <a
                      href={o.url}
                      onClick={(e) => {
                        e.preventDefault();
                        void window.synorive.sys.openExternal(o.url);
                      }}
                    >
                      {o.text || o.url}
                    </a>
                    <span className="lt__site">{o.site}</span>
                  </li>
                ))}
              </ul>
            </section>

            <section className="lt__col">
              <h4>
                <ArrowDownRight size={13} aria-hidden /> 谁在讨论它（{t.backlinks.length}）
              </h4>
              {!t.backlinks.length && (
                <p className="lt__empty">
                  没搜到别的页面在讨论它。<strong>这不代表没人看过</strong> ——
                  只是搜索引擎没收录到引用它的页面。
                </p>
              )}
              <ul className="lt__list">
                {t.backlinks.map((b) => (
                  <li key={b.url}>
                    <a
                      href={b.url}
                      onClick={(e) => {
                        e.preventDefault();
                        void window.synorive.sys.openExternal(b.url);
                      }}
                    >
                      {b.title || b.url}
                    </a>
                    <span className="lt__site">{b.site}</span>
                    {b.snippet && <p className="lt__snippet">{b.snippet}</p>}
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </>
      )}

      <details className="lt__text">
        <summary>正文（{data.chars} 字{data.truncated ? '，已截断' : ''}）</summary>
        <pre>{data.text}</pre>
      </details>
    </div>
  );
}
