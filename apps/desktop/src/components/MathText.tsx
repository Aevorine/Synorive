import { useMemo, type ReactNode } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';

/**
 * C4 专业阅读器 —— 公式排版 + 引用悬浮预览
 * ============================================================
 * 一个"什么都能存"的库，存进来的论文、笔记、简报里必然有 `$E=mc^2$` 这种东西。
 * 原样显示美元符号，读起来像乱码；而一旦决定要排版，就必须先想清楚
 * **排版失败的时候长什么样**。
 *
 * 🔴 **公式炸了要原样显示源码，不能崩、不能留空。**
 *    `throwOnError: false` 让 KaTeX 把认不出来的部分标红显示出来而不是抛异常；
 *    外面再包一层 try/catch 兜住 KaTeX 自己都没接住的情况，退回 `<code>` 显示原文。
 *    留空是最糟的一种失败：用户以为原文里本来就没有这段。
 *
 * 🔴 **`dangerouslySetInnerHTML` 在这里是必需的，也是可控的。**
 *    KaTeX 的输出就是一段 HTML 字符串，没有别的接法。安全性靠两条：
 *    ① 只把 `$…$` 之间的内容交给 KaTeX，`$` 之外的正文永远走 React 的文本节点
 *      （也就是永远被转义）；
 *    ② KaTeX 默认 `trust: false`，`\href`、`\url`、`\includegraphics`
 *      这些能产出链接和外部资源的命令一律不执行。
 *    这两条少任何一条，一份从网上抓来的正文就能往界面里塞 HTML。
 *
 * 🔴 **不引 auto-render 那个 contrib。** 它是直接改 DOM 的，和 React 的
 *    渲染周期打架：React 一重渲染就把它排好的公式冲掉，表现是"滚一下公式就没了"。
 *    这里改成把文本切成段，公式段各自 `renderToString`，全程在 React 树里。
 */

// ────────────────────────────────────────────────────────────
// 切分
// ────────────────────────────────────────────────────────────

type Seg =
  | { kind: 'text'; text: string }
  | { kind: 'math'; tex: string; display: boolean; raw: string };

/**
 * 把正文切成「普通文字 / 行内公式 / 独立公式」三种段。
 *
 * 规则刻意保守，宁可少排一个公式，也不要把一句带价格的话
 * （「A 卖 $30，B 卖 $45」）整段吃成公式：
 *   - `$$…$$` 独立成行，中间可以换行
 *   - `$…$` 行内，**中间不许有换行**，且内容不能是空的
 *   - `\$` 是转义的美元符号，不参与配对
 */
export function splitMath(src: string): Seg[] {
  const out: Seg[] = [];
  let buf = '';
  let i = 0;

  const flush = () => {
    if (buf) {
      out.push({ kind: 'text', text: buf });
      buf = '';
    }
  };

  while (i < src.length) {
    const c = src[i]!;

    if (c === '\\' && src[i + 1] === '$') {
      // 转义的美元符号：吃掉反斜杠，留一个字面 $
      buf += '$';
      i += 2;
      continue;
    }

    if (c === '$') {
      const isDisplay = src[i + 1] === '$';
      const open = isDisplay ? 2 : 1;
      const close = src.indexOf(isDisplay ? '$$' : '$', i + open);
      if (close > i + open - 1) {
        const tex = src.slice(i + open, close);
        const okInline = isDisplay || (!tex.includes('\n') && tex.trim().length > 0);
        if (okInline && tex.trim().length > 0) {
          flush();
          out.push({
            kind: 'math',
            tex,
            display: isDisplay,
            raw: src.slice(i, close + open),
          });
          i = close + open;
          continue;
        }
      }
    }

    buf += c;
    i += 1;
  }
  flush();
  return out;
}

function renderTex(tex: string, display: boolean): string | null {
  try {
    return katex.renderToString(tex, {
      displayMode: display,
      // 🔴 认不出来的宏标红显示，而不是抛异常把整段正文炸掉
      throwOnError: false,
      // 🔴 保持默认的 trust: false —— \href / \url / \includegraphics 不执行
      strict: false,
      output: 'html',
    });
  } catch {
    // KaTeX 自己都没接住（极少见，一般是超深嵌套触发了内部限制）
    return null;
  }
}

// ────────────────────────────────────────────────────────────
// 引用悬浮预览
// ────────────────────────────────────────────────────────────

export interface CiteSource {
  /** 正文里 `[n]` 的那个 n */
  n: number;
  title?: string;
  site?: string;
  url?: string;
  /**
   * 悬停时浮出来的**原文片段**。
   * 🔴 这里放的必须是真的从来源里摘出来的字，不是标题的复述 ——
   *    悬浮预览的全部意义就是"不用跳走就能核对一眼"。
   */
  excerpt?: string;
  /**
   * 点一下做什么（比如跳回左栏那句原文并高亮）。不给就只显示不可点。
   * 收 event 是为了让调用方能区分 Ctrl+点（去原站）和普通点（跳左栏）。
   */
  onOpen?: (e: React.MouseEvent) => void;
}

/**
 * 一个可悬停的 `[n]`。
 *
 * 🔴 **用 `<button>` 而不是 `<span>`，浮层用 `:focus-within` 也要能出来。**
 *    只挂 `:hover` 的话，用键盘走到这里的人永远看不到预览 ——
 *    而"全键盘可达"是这一轮的验收项之一。
 */
function CiteChip({ src }: { src: CiteSource }) {
  const head = [src.title, src.site].filter(Boolean).join(' · ');
  return (
    <span className="syn-cite">
      <button
        type="button"
        className="syn-cite__mark"
        onClick={src.onOpen}
        title={head ? `${head}（悬停看原文片段）` : '悬停看原文片段'}
        aria-label={head ? `出处 ${src.n}：${head}` : `出处 ${src.n}`}
      >
        [{src.n}]
      </button>
      <span className="syn-cite__pop" role="tooltip">
        {head && <span className="syn-cite__poptitle">{head}</span>}
        {src.excerpt ? (
          <span className="syn-cite__popbody">「{src.excerpt}」</span>
        ) : (
          // 🔴 没有片段就明说没有，不显示一个空白浮层。
          //    空白浮层会被当成"加载中"，用户会一直等
          <span className="syn-cite__popbody syn-diag">这条出处没带原文片段</span>
        )}
        {src.url && <span className="syn-cite__popurl">{src.url}</span>}
      </span>
    </span>
  );
}

/** 把文字段里的 `[n]` 换成可悬停的引用标记 */
function withCites(text: string, cites: Map<number, CiteSource>, keyBase: string): ReactNode[] {
  if (cites.size === 0) return [text];
  const out: ReactNode[] = [];
  const re = /\[(\d{1,3})\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    const src = cites.get(Number(m[1]));
    if (!src) continue; // 不是我们认识的编号，原样留在文字里
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<CiteChip key={`${keyBase}-c${k++}`} src={src} />);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.length ? out : [text];
}

// ────────────────────────────────────────────────────────────

export interface MathTextProps {
  text: string;
  /** 正文里 `[n]` 对应的出处。不给就不做引用标记 */
  cites?: CiteSource[];
  /** 外层元素的类名 */
  className?: string;
}

/**
 * 正文渲染器：`$…$` 行内公式、`$$…$$` 独立公式、`[n]` 引用悬浮预览。
 *
 * 用在问答答案、简报正文、文献正文这些"要认真读"的地方。
 * 纯列表和标题不用它 —— 那些地方出现公式的概率极低，
 * 而每多一次解析都是白花的。
 */
export function MathText({ text, cites, className }: MathTextProps) {
  const citeMap = useMemo(() => new Map((cites ?? []).map((c) => [c.n, c])), [cites]);

  const nodes = useMemo(() => {
    // 快路：整段既没有 $ 也没有 [n]，直接当纯文本返回，一次正则都不跑
    if (!text.includes('$') && citeMap.size === 0) return [text];

    return splitMath(text).map((seg, i) => {
      if (seg.kind === 'text') {
        return <span key={`t${i}`}>{withCites(seg.text, citeMap, `t${i}`)}</span>;
      }
      const html = renderTex(seg.tex, seg.display);
      if (html === null) {
        // 🔴 排不出来就把源码原样摆出来。留空 = 用户以为原文没这段
        return (
          <code key={`m${i}`} className="syn-math syn-math--raw" title="这段公式没能排版，显示的是原始写法">
            {seg.raw}
          </code>
        );
      }
      return seg.display ? (
        <span
          key={`m${i}`}
          className="syn-math syn-math--block"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <span
          key={`m${i}`}
          className="syn-math"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      );
    });
  }, [text, citeMap]);

  return <span className={className}>{nodes}</span>;
}
