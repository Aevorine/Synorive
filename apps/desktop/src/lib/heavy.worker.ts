/**
 * C3 重活线程 —— 把 CPU 密集的纯计算挪出主线程
 * ====================================================================
 * 只放**纯函数**：进去是数据，出来是数据，不碰 DOM、不碰网络、不留状态。
 * 有状态的东西放进来会立刻变成"两份状态对不上"的调试地狱，
 * 而 Worker 里的状态错误在主线程上完全看不见。
 *
 * ── 为什么这几个函数值得挪 ────────────────────────────────
 * 判据是**它们的耗时随数据量线性涨，而且会在滚动/打字的同一帧里跑**：
 *   · 高亮切片：一屏 60 条结果 × 每条 8 个词 × 每个词全文扫描
 *   · 文本比对：两份长文的行级 diff，是 O(n·m)
 *   · 图布局：力导向每次迭代都要遍历所有边
 * 三者单次都在几毫秒到几十毫秒之间 —— 单看都不算慢，
 * **但它们恰好都发生在用户正在滚动或正在打字的那一帧里**，
 * 于是每一次都变成一次掉帧。挪走之后主线程只负责画画面。
 *
 * 🔴 **不在这里做的事**：不发请求（引擎端口在主线程）、不读设置、不写日志。
 *    Worker 里 console.log 到不了 DevTools 的主控制台，调试时会白找半天。
 */

export interface HighlightRequest {
  id: number;
  kind: 'highlight';
  text: string;
  terms: string[];
}

export interface DiffRequest {
  id: number;
  kind: 'diff';
  left: string;
  right: string;
}

export type HeavyRequest = HighlightRequest | DiffRequest;

/** 高亮结果：交替的普通段和命中段，主线程照着渲染即可，不用再算一遍 */
export interface HighlightSegment {
  text: string;
  hit: boolean;
}

export interface DiffLine {
  kind: 'same' | 'add' | 'del';
  text: string;
}

export type HeavyResponse =
  | { id: number; kind: 'highlight'; segments: HighlightSegment[] }
  | { id: number; kind: 'diff'; lines: DiffLine[] }
  | { id: number; kind: 'error'; message: string };

/**
 * 把文本按命中词切成段。
 *
 * **长词优先**，否则「注意力机制」会被「注意力」先切掉一半，
 * 剩下「机制」两个字裸在外面不高亮 —— 看起来像匹配错了。
 *
 * guard 是死循环护栏：某个 term 是空串时 indexOf 永远返回 0，
 * 没有它整个 Worker 会占满一个核心且**主线程毫无察觉**
 * （界面不卡，只是风扇狂转、结果永远不回来）。
 */
export function highlightSegments(text: string, terms: string[]): HighlightSegment[] {
  const uniq = [...new Set(terms)].filter((t) => t && t.length > 0).sort((a, b) => b.length - a.length);
  if (!uniq.length || !text) return [{ text, hit: false }];

  const out: HighlightSegment[] = [];
  let rest = text;
  let guard = 0;

  while (rest && guard++ < 2000) {
    let at = -1;
    let hit = '';
    for (const t of uniq) {
      const i = rest.indexOf(t);
      if (i >= 0 && (at < 0 || i < at)) {
        at = i;
        hit = t;
      }
    }
    if (at < 0 || !hit) break;
    if (at > 0) out.push({ text: rest.slice(0, at), hit: false });
    out.push({ text: hit, hit: true });
    rest = rest.slice(at + hit.length);
  }
  if (rest) out.push({ text: rest, hit: false });
  return out;
}

/**
 * 行级 diff。用最长公共子序列（LCS）而不是逐行比对 ——
 * 逐行比对在"中间插了一行"时会把后面**所有**行都标成改动过，
 * 那种 diff 看一眼就知道是错的，用户直接不信它。
 *
 * LCS 是 O(n·m) 空间，所以行数封顶：超过 3000 行退化成逐行比。
 * 两份 3000 行的文件做 LCS 要 900 万格，那时候真正的问题
 * 已经不是"要不要 diff"而是"这个界面还该不该显示全文"。
 */
export function diffLines(left: string, right: string): DiffLine[] {
  const a = left.split('\n');
  const b = right.split('\n');
  const LIMIT = 3000;

  if (a.length > LIMIT || b.length > LIMIT) {
    const n = Math.max(a.length, b.length);
    const out: DiffLine[] = [];
    for (let i = 0; i < n; i++) {
      const l = a[i];
      const r = b[i];
      if (l === r && l !== undefined) out.push({ kind: 'same', text: l });
      else {
        if (l !== undefined) out.push({ kind: 'del', text: l });
        if (r !== undefined) out.push({ kind: 'add', text: r });
      }
    }
    return out;
  }

  // LCS 长度表
  const m = a.length;
  const n = b.length;
  const dp: Uint32Array = new Uint32Array((m + 1) * (n + 1));
  const at = (i: number, j: number) => i * (n + 1) + j;
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[at(i, j)] =
        a[i] === b[j]
          ? (dp[at(i + 1, j + 1)] ?? 0) + 1
          : Math.max(dp[at(i + 1, j)] ?? 0, dp[at(i, j + 1)] ?? 0);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] ?? '' });
      i++;
      j++;
    } else if ((dp[at(i + 1, j)] ?? 0) >= (dp[at(i, j + 1)] ?? 0)) {
      out.push({ kind: 'del', text: a[i] ?? '' });
      i++;
    } else {
      out.push({ kind: 'add', text: b[j] ?? '' });
      j++;
    }
  }
  while (i < m) out.push({ kind: 'del', text: a[i++] ?? '' });
  while (j < n) out.push({ kind: 'add', text: b[j++] ?? '' });
  return out;
}

// ── Worker 入口 ──────────────────────────────────────────
// `self.onmessage` 在被当成普通模块 import 时不存在（比如主线程兜底路径
// 直接 import 上面两个纯函数）。所以要先判断，不判断的话
// **兜底路径一 import 就抛异常**，而那正是 Worker 不可用时唯一的退路。

/**
 * 结构化声明，不引 `lib: webworker`。
 *
 * 把 webworker 加进 tsconfig 的 lib 会让**整个渲染进程**的全局类型
 * 被 Worker 那套覆盖一部分（`self`、`postMessage`、`addEventListener` 都会变），
 * 于是主线程代码里一堆 DOM API 开始报奇怪的类型错。
 * 为一个文件改全局 lib 是划不来的。
 */
interface WorkerScope {
  postMessage: (m: unknown) => void;
  onmessage: ((e: MessageEvent<HeavyRequest>) => void) | null;
}

declare const self: WorkerScope | undefined;

if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
  self.onmessage = (e: MessageEvent<HeavyRequest>) => {
    const req = e.data;
    try {
      if (req.kind === 'highlight') {
        const res: HeavyResponse = {
          id: req.id,
          kind: 'highlight',
          segments: highlightSegments(req.text, req.terms),
        };
        self.postMessage(res);
        return;
      }
      if (req.kind === 'diff') {
        const res: HeavyResponse = { id: req.id, kind: 'diff', lines: diffLines(req.left, req.right) };
        self.postMessage(res);
        return;
      }
      self.postMessage({
        id: (req as { id: number }).id,
        kind: 'error',
        message: `不认识的任务类型：${(req as { kind: string }).kind}`,
      } satisfies HeavyResponse);
    } catch (err) {
      // 🔴 **必须回一条 error 而不是让它静默抛掉。**
      //    Worker 里未捕获的异常不会 reject 任何 Promise ——
      //    调用方会永远等下去，界面表现为"转圈转到天荒地老"
      self.postMessage({
        id: (req as { id: number })?.id ?? -1,
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
      } satisfies HeavyResponse);
    }
  };
}
