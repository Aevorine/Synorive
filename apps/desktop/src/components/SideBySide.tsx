import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import type { SearchHit } from '@synorive/shared-types';
import { api } from '../lib/api';
import { diff, type DiffLine } from '../lib/heavy';
import { stripHighlight } from '../lib/compose';

/**
 * D3 并排对比 —— 勾 2~4 条，逐段看差异
 * ====================================================================
 * 和「比一比」（`CompareView`）的分工：
 *   CompareView  比**两个磁盘上的文件**，要拖进来，走引擎的 `/compare/files`
 *   SideBySide   比**已经搜到的几条结果**，一次点，纯前端
 *
 * 后者才是搜索场景里真正需要的那个：用户手上已经有了四条候选，
 * 问题是"哪一条才是我要的"—— 让他为此再去文件管理器里把四个文件
 * 拖进另一个页面，等于告诉他这功能不值得用。
 *
 * 🔴 **正好两条且都是文本时才做逐行 diff。**
 *    三条以上没有"逐行差异"这个概念（三方 diff 是另一回事，而且很难读懂）；
 *    图片和视频之间比文本行更是毫无意义 —— 硬做只会得到一屏
 *    看起来很专业但完全没用的红绿条。
 */

export function SideBySide({ hits, onClose }: { hits: SearchHit[]; onClose: () => void }) {
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [lines, setLines] = useState<DiffLine[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // 拉全文。**每条各自 catch** —— 四条里有一条读不到（文件被移走了、
  // 权限没了）时，另外三条照样要能对比，而不是整个面板变成一句报错
  useEffect(() => {
    let alive = true;
    setLoading(true);
    void Promise.all(
      hits.map((h) =>
        api
          .content(h.item.id, 8000)
          .then((r) => [h.item.id, r.text] as const)
          .catch(() => [h.item.id, ''] as const),
      ),
    ).then((pairs) => {
      if (!alive) return;
      setTexts(Object.fromEntries(pairs));
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [hits]);

  // 正好两条文本 → 逐行 diff。走 Worker，长文比对不卡界面
  useEffect(() => {
    if (loading) return;
    const [a, b] = hits;
    if (hits.length !== 2 || !a || !b) {
      setLines(null);
      return;
    }
    if (a.item.modality !== 'text' || b.item.modality !== 'text') {
      setLines(null);
      return;
    }
    const ta = texts[a.item.id] ?? '';
    const tb = texts[b.item.id] ?? '';
    if (!ta && !tb) {
      setLines(null);
      setErr('这两条都读不到正文，没法做逐行比对');
      return;
    }
    let alive = true;
    void diff(ta, tb).then((r) => {
      if (alive) setLines(r);
    });
    return () => {
      alive = false;
    };
  }, [loading, hits, texts]);

  const same = lines?.filter((l) => l.kind === 'same').length ?? 0;
  const changed = (lines?.length ?? 0) - same;

  return (
    <aside className="sbs" role="dialog" aria-label="并排对比">
      <header className="sbs__head">
        <h3 className="sbs__title">并排对比 · {hits.length} 条</h3>
        {lines && (
          <span className="sbs__stat">
            相同 {same} 行 · 有差异 {changed} 行
          </span>
        )}
        <span className="sbs__spacer" />
        <button className="qp__close" onClick={onClose} aria-label="关闭" title="关闭">
          ×
        </button>
      </header>

      {err && <div className="banner banner--error">{err}</div>}

      {loading ? (
        <div className="sbs__loading">
          <Loader2 size={16} className="spin" strokeWidth={2} />
          正在读这几条的正文…
        </div>
      ) : (
        <>
          {/* 并排列：宽度均分。**不给横向滚动** —— 对比的价值在于
              一眼同时看到，要横滚才能看到第三列就已经失去意义了。
              所以上限卡在 4 条（ComposeBar 那边控制） */}
          <div className="sbs__cols" style={{ gridTemplateColumns: `repeat(${hits.length}, 1fr)` }}>
            {hits.map((h, i) => (
              <section key={h.item.id} className="sbs__col">
                <header className="sbs__colhead">
                  <span className="sbs__coln">{i + 1}</span>
                  <span className="sbs__coltitle" title={h.item.locator}>
                    {h.item.title || h.item.locator}
                  </span>
                </header>
                <dl className="sbs__facts">
                  <div>
                    <dt>类型</dt>
                    <dd>{h.item.modality}</dd>
                  </div>
                  <div>
                    <dt>相关度</dt>
                    <dd>{h.score.toFixed(4)}</dd>
                  </div>
                  <div>
                    <dt>时间</dt>
                    <dd>{h.item.contentTime ? new Date(h.item.contentTime).toLocaleDateString('zh-CN') : '—'}</dd>
                  </div>
                  <div>
                    <dt>大小</dt>
                    <dd>{h.item.sizeBytes != null ? `${(h.item.sizeBytes / 1024).toFixed(0)} KB` : '—'}</dd>
                  </div>
                  <div>
                    <dt>正文</dt>
                    <dd>{(texts[h.item.id] ?? '').length || '读不到'} 字</dd>
                  </div>
                </dl>
                {/* 命中片段：这是"为什么它被搜出来"，比全文开头有用得多 */}
                <p className="sbs__snip syn-selectable">
                  {stripHighlight(h.highlight) || h.item.snippet || '（没有可用的摘录片段）'}
                </p>
              </section>
            ))}
          </div>

          {lines && (
            <div className="sbs__diff">
              <div className="syn-subhead">逐行差异（左 = 第 1 条，右 = 第 2 条）</div>
              <div className="sbs__difflist">
                {/* 只渲染有差异的行 + 它们周围的相同行。
                    全渲染的话一份 3000 行的文档会铺出一屏找不到重点的东西，
                    而"哪里不一样"正是用户唯一想看的 */}
                {condense(lines).map((l, i) =>
                  l === null ? (
                    <div key={`gap${i}`} className="sbs__gap">
                      ⋯ 中间若干行相同 ⋯
                    </div>
                  ) : (
                    <div key={i} className={`sbs__line sbs__line--${l.kind}`}>
                      <span className="sbs__sign">
                        {l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : ' '}
                      </span>
                      <span className="sbs__linetext">{l.text || ' '}</span>
                    </div>
                  ),
                )}
              </div>
            </div>
          )}

          {!lines && hits.length === 2 && (
            <p className="sbs__note">
              这两条里有非文本内容，逐行比对没有意义——上面的并排信息还是可以用的。
            </p>
          )}
          {!lines && hits.length > 2 && (
            <p className="sbs__note">
              三条以上不做逐行比对（三方差异很难读懂）。想逐行比就只勾两条。
            </p>
          )}
        </>
      )}
    </aside>
  );
}

/**
 * 压缩 diff：有差异的行前后各留 2 行上下文，中间大段相同的折叠成一条。
 * `null` 表示"这里折叠了一段"。
 *
 * 上下文留 2 行是折中：留 0 行会让改动行失去参照（"这句改了，但改在哪一章？"），
 * 留太多又回到全渲染。
 */
function condense(lines: DiffLine[], ctx = 2): (DiffLine | null)[] {
  const keep = new Set<number>();
  lines.forEach((l, i) => {
    if (l.kind === 'same') return;
    for (let j = Math.max(0, i - ctx); j <= Math.min(lines.length - 1, i + ctx); j++) keep.add(j);
  });
  if (keep.size === 0) return [];
  const out: (DiffLine | null)[] = [];
  let gap = false;
  lines.forEach((l, i) => {
    if (keep.has(i)) {
      out.push(l);
      gap = false;
    } else if (!gap) {
      out.push(null);
      gap = true;
    }
  });
  return out;
}
