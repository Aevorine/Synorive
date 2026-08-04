import { useMemo, type ReactNode } from 'react';
import { FileText, Lightbulb, Quote, TriangleAlert } from 'lucide-react';
import type { AskAnswer as AskAnswerData, AskPassage } from '@synorive/shared-types';
import { api } from '../lib/api';
import { highlightSegments } from '../lib/heavy.worker';

/**
 * A3 答案区 —— 一段一段的逐字摘录 + 出处
 * ====================================================================
 * 🔴 **这里显示的每个字都在原文里逐字存在。** 组件不许做任何"顺一下语句"的
 *    处理：不补标点、不去空格、不截断加省略号。做了的话「点回原文」会定位不到，
 *    而定位不到的引用等于没有引用 —— 用户第一次核对失败就再也不信这个功能了。
 *
 * 界面上也刻意不出现「AI 总结」「智能回答」这类字样。它就是**摘录**，
 * 把它包装成一个判断，是在替用户承担一个我们承担不了的责任。
 */

/**
 * 把命中的词包成 <mark>。**只按原文切片拼接，绝不改写字符**。
 *
 * 切片算法本身在 `heavy.worker.ts` 里（那份同时给 Worker 用），
 * 这里只负责把切好的段变成 React 节点。
 * 🔴 **不要在这里再写一份切片逻辑** —— 两份实现迟早会分叉，
 *    而分叉的表现是"同一段文字在两个地方高亮得不一样"，
 *    没人会想到去怀疑是两份代码。
 *
 * 这一段没走 Worker：摘录单段 ≤160 字、一次最多 6 段，
 * 过一趟 postMessage 的往返开销比直接算还大。Worker 留给
 * 真正长的文本（`heavy.ts` 的 `highlight()`）。
 */
function highlight(text: string, matched: string[]): ReactNode {
  const segs = highlightSegments(text, matched);
  if (segs.length <= 1) return text;
  return segs.map((s, i) =>
    s.hit ? (
      <mark key={i} className="syn-hl">
        {s.text}
      </mark>
    ) : (
      s.text
    ),
  );
}

function locationLabel(p: AskPassage): string {
  if (typeof p.page === 'number') return `第 ${p.page} 页`;
  if (typeof p.startSec === 'number') {
    const m = Math.floor(p.startSec / 60);
    const s = Math.floor(p.startSec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  }
  return '';
}

export function AskAnswer({
  data,
  elapsedMs,
  onOpenItem,
}: {
  data: AskAnswerData;
  elapsedMs?: number;
  onOpenItem?: (itemId: string, title: string) => void;
}) {
  const sourceIndex = useMemo(() => {
    const m = new Map<string, number>();
    data.sources.forEach((s, i) => m.set(s.itemId, i + 1));
    return m;
  }, [data.sources]);

  // 依据不足：**不给半截答案然后小字注明**，直接把"为什么"和"怎么办"摆在最前。
  // 半截答案的伤害在于用户会先读它、先相信它，注释是事后补救，来不及
  if (!data.enough && data.passages.length === 0) {
    return (
      <section className="ans ans--none" aria-label="没有找到答案">
        <div className="ans__nonehead">
          <TriangleAlert size={18} strokeWidth={1.7} />
          <span>{data.why || '库里没有能回答这个问题的内容'}</span>
        </div>
        {!!data.suggest?.length && (
          <ul className="ans__tips">
            {data.suggest.map((t, i) => (
              <li key={i}>
                <Lightbulb size={13} strokeWidth={1.7} />
                <span>{t}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }

  return (
    <section className="ans" aria-label="答案">
      <header className="ans__head">
        <Quote size={15} strokeWidth={1.7} className="ans__quote" />
        <span className="ans__label">
          从你的资料里摘出来的{data.passages.length}段
          {/* 说"摘出来的"不说"回答"—— 一字之差，但它决定了用户会不会去核对 */}
        </span>
        <span className="ans__spacer" />
        {typeof elapsedMs === 'number' && (
          <span className="ans__ms">{elapsedMs.toFixed(0)}ms</span>
        )}
      </header>

      {/* 依据不足但仍有段落：先给出警示条，再给段落。
          顺序很重要 —— 警示放下面等于没放 */}
      {!data.enough && (
        <div className="ans__partial">
          <TriangleAlert size={14} strokeWidth={1.7} />
          <span>{data.why || '这几段只答上了一部分'}</span>
        </div>
      )}

      <div className="ans__list">
        {data.passages.map((p, i) => {
          const n = sourceIndex.get(p.itemId) ?? 0;
          const loc = locationLabel(p);
          return (
            <article key={`${p.itemId}-${i}`} className="ans__p">
              <p className="ans__text syn-selectable">{highlight(p.text, p.matched)}</p>
              <button
                className="ans__cite"
                onClick={() => {
                  void api.recordOpen(p.itemId).catch(() => {
                    /* 记录打开失败不该挡住跳转 */
                  });
                  onOpenItem?.(p.itemId, p.title);
                }}
                title={`回到原文：${p.locator}`}
              >
                <span className="ans__citenum">{n || '·'}</span>
                <FileText size={12} strokeWidth={1.7} />
                <span className="ans__citetitle">{p.title || p.locator}</span>
                {loc && <span className="ans__citeloc">{loc}</span>}
              </button>
            </article>
          );
        })}
      </div>

      {data.sources.length > 0 && (
        <footer className="ans__sources">
          <span className="ans__sourceslabel">读了这几份：</span>
          {data.sources.map((s, i) => (
            <button
              key={s.itemId}
              className="ans__source"
              onClick={() => onOpenItem?.(s.itemId, s.title)}
              title={s.locator}
            >
              <span className="ans__citenum">{i + 1}</span>
              {s.title || s.locator}
            </button>
          ))}
        </footer>
      )}

      {!data.enough && !!data.suggest?.length && (
        <ul className="ans__tips">
          {data.suggest.map((t, i) => (
            <li key={i}>
              <Lightbulb size={13} strokeWidth={1.7} />
              <span>{t}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
