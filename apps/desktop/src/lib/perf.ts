/**
 * C6 运行期性能采样（E1–E8 里界面这一侧能测的部分）
 * ====================================================================
 * 为什么要有它：E1–E8 八条指标，引擎那边（`metrics.py::observe`）只看得见
 * 自己进程里的东西 —— 内存、缓存命中、联网引擎耗时。而**用户实际感受到的
 * 那几条全在渲染进程这边**：搜索到底几毫秒出结果、滚动掉不掉帧、
 * 冷启动到能点要多久、引擎掉过几次线。
 *
 * ── 三条纪律（照抄引擎侧 metrics.py 的口径，两边必须一致）────────
 * ① **没采到样本就报 null，绝不填 0。** 填 0 会在界面上显示成
 *    「0ms，远优于目标」—— 那是彻头彻尾的谎报，而且看起来特别可信。
 * ② **样本少的时候明说样本少。** 跑两次搜索就宣布 "P95 达标" 是自欺欺人，
 *    所以 P95 在 n<20 时直接标注「样本不足，这个数基本等于最大值」。
 * ③ **采样本身不许成为新的性能问题。** 帧率采样只在面板打开时跑，
 *    关掉就停；长任务用 PerformanceObserver（浏览器自己记的，零额外开销）。
 *
 * 🔴 这里的数字是**运行期自然采样，不是基准测试**。它回答的是
 *    "你这台机器上、你这个库、你刚才那几次操作有多快"，
 *    这恰恰比一个实验室数字更有意义 —— 但它不能当成"指标达标"的证明。
 */

/** 每类指标最多留多少个样本。滚动窗口，超了丢最旧的。 */
const WINDOW = 200;

/** P95 至少要这么多样本才有意义。低于它界面上必须标注「样本不足」。 */
export const P95_MIN_SAMPLES = 20;

class Samples {
  private buf: number[] = [];

  push(v: number): void {
    if (!Number.isFinite(v) || v < 0) return;
    this.buf.push(v);
    if (this.buf.length > WINDOW) this.buf.shift();
  }

  get count(): number {
    return this.buf.length;
  }

  /** 百分位。**没样本返回 null，不返回 0。** */
  pct(p: number): number | null {
    if (!this.buf.length) return null;
    const s = [...this.buf].sort((a, b) => a - b);
    // 用 ceil(p·n)-1 而不是 floor(p·n)：n=20、p=0.95 时前者取第 19 个
    // （即"95% 的样本不慢于它"），后者取第 19 个但在 n=10 时会退化成第 9 个，
    // 也就是最大值 —— 那正是"样本少时 P95 等于最大值"的来源
    const i = Math.min(s.length - 1, Math.max(0, Math.ceil(p * s.length) - 1));
    return s[i] ?? null;
  }

  max(): number | null {
    return this.buf.length ? Math.max(...this.buf) : null;
  }

  clear(): void {
    this.buf = [];
  }
}

/** 一条指标的观测结果。**target 是字符串**，因为有些指标的目标是范围或条件。 */
export interface Observation {
  id: string;
  label: string;
  target: string;
  /** 观测值。null = 还没采到样本，界面必须显示"还没采到"而不是 0 */
  value: number | null;
  unit: string;
  /** 样本数。0 = 没测过 */
  n: number;
  /** 样本不足 / 无法运行期测量时的说明。有值时界面必须显示它 */
  note?: string;
  /** 越小越好（延迟）还是越大越好（帧率、吞吐） */
  lowerIsBetter: boolean;
  /** 数值化的目标，用于判定是否达标。null = 这条不做自动判定 */
  targetValue: number | null;
}

// ── 采样池 ────────────────────────────────────────────────

const searchMs = new Samples();
const askMs = new Samples();
const webMs = new Samples();
const frameMs = new Samples();

let engineDropCount = 0;
let engineErrorCount = 0;
/** 从渲染进程开始加载到引擎第一次 ready 的毫秒数。null = 还没 ready 过 */
let coldStartMs: number | null = null;
/** 长任务（>50ms 阻塞主线程）累计次数。E6「主线程长任务 = 0」就看它 */
let longTaskCount = 0;
let longTaskMaxMs = 0;

/** 记一次本地检索耗时。由 useSearch 在每一波结果回来时调用。 */
export function recordSearch(ms: number): void {
  searchMs.push(ms);
}

/** 记一次问答耗时（A3）。 */
export function recordAsk(ms: number): void {
  askMs.push(ms);
}

/** 记一次联网搜索耗时。 */
export function recordWeb(ms: number): void {
  webMs.push(ms);
}

/**
 * 记引擎生命周期变化。
 * 只有 ready→非 ready 才算一次掉线 —— 启动过程中的 starting/ready
 * 是正常流程，把它算成掉线会让 E8 永远难看。
 */
let lastEngineLifecycle: string | null = null;
export function recordEngineState(lifecycle: string | undefined): void {
  const prev = lastEngineLifecycle;
  lastEngineLifecycle = lifecycle ?? null;
  if (lifecycle === 'ready' && coldStartMs === null) {
    // performance.now() 的原点是本渲染进程开始加载的那一刻，
    // 正好是"用户看到窗口"到"能干活"这段
    coldStartMs = Math.round(performance.now());
  }
  if (prev === 'ready' && lifecycle && lifecycle !== 'ready') {
    engineDropCount += 1;
  }
  if (lifecycle === 'failed') engineErrorCount += 1;
}

// ── 长任务观察（零额外开销，浏览器本来就在记）──────────────

let longTaskObserver: PerformanceObserver | null = null;

function ensureLongTaskObserver(): void {
  if (longTaskObserver) return;
  try {
    longTaskObserver = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        longTaskCount += 1;
        longTaskMaxMs = Math.max(longTaskMaxMs, Math.round(e.duration));
      }
    });
    longTaskObserver.observe({ entryTypes: ['longtask'] });
  } catch {
    // 不支持 longtask 的环境：静默跳过。
    // 界面那边会因为 n=0 显示"这个环境测不了"，而不是显示 0 次
    longTaskObserver = null;
  }
}

/** 应用启动时调一次。 */
export function initPerf(): void {
  ensureLongTaskObserver();
}

// ── 帧率采样（只在面板打开时跑）────────────────────────────

let rafId: number | null = null;
let lastFrameAt = 0;

/**
 * 开始采帧。**必须配对调用 stopFrameSampling** ——
 * 忘了停的话，一个 rAF 循环会永远跑下去，而它本身就是一个持续的
 * 主线程负担：**测帧率的东西自己把帧率拉低了**，最讽刺的一种 bug。
 */
export function startFrameSampling(): void {
  if (rafId !== null) return;
  lastFrameAt = performance.now();
  const tick = (t: number) => {
    const dt = t - lastFrameAt;
    lastFrameAt = t;
    // 第一帧和标签页切回来的那一帧间隔极大，会把中位数拖垮。
    // 200ms 以上一律丢弃 —— 那不是掉帧，那是根本没在渲染
    if (dt > 0 && dt < 200) frameMs.push(dt);
    rafId = requestAnimationFrame(tick);
  };
  rafId = requestAnimationFrame(tick);
}

export function stopFrameSampling(): void {
  if (rafId !== null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
}

/** 清掉所有采样。用户点「重新开始测」时用。 */
export function resetPerf(): void {
  searchMs.clear();
  askMs.clear();
  webMs.clear();
  frameMs.clear();
  engineDropCount = 0;
  engineErrorCount = 0;
  longTaskCount = 0;
  longTaskMaxMs = 0;
}

// ── 汇总 ──────────────────────────────────────────────────

function pctNote(n: number): string | undefined {
  if (n === 0) return '还没采到样本 —— 先正常用一会儿再回来看';
  if (n < P95_MIN_SAMPLES)
    return `只有 ${n} 个样本（<${P95_MIN_SAMPLES}），这个 P95 基本等于最大值，别当结论看`;
  return undefined;
}

/** 渲染进程内存。Chromium 才有 performance.memory，拿不到就报 null。 */
function rendererMemMb(): number | null {
  const m = (performance as unknown as { memory?: { usedJSHeapSize?: number } }).memory;
  const bytes = m?.usedJSHeapSize;
  return typeof bytes === 'number' ? Math.round(bytes / 1048576) : null;
}

/**
 * E1–E8 里**界面这一侧**能测的六条。
 * E2（索引吞吐）和 E4 的引擎内存在引擎侧，由 `/metrics/budgets` 给；
 * E5（安装包体积）是构建期属性，运行期测不了 —— 面板里如实说明。
 */
export function observeClient(): Observation[] {
  return [
    {
      id: 'E1',
      label: '本地搜索延迟 P50 / P95',
      target: '≤80ms / ≤300ms',
      value: searchMs.pct(0.95),
      unit: 'ms',
      n: searchMs.count,
      note: pctNote(searchMs.count),
      lowerIsBetter: true,
      targetValue: 300,
    },
    {
      id: 'E1b',
      label: '搜索延迟 P50',
      target: '≤80ms',
      value: searchMs.pct(0.5),
      unit: 'ms',
      n: searchMs.count,
      note: searchMs.count === 0 ? '还没搜过' : undefined,
      lowerIsBetter: true,
      targetValue: 80,
    },
    {
      id: 'E1c',
      label: '问一句话 P95（A3）',
      target: '≤2000ms',
      value: askMs.pct(0.95),
      unit: 'ms',
      n: askMs.count,
      note: pctNote(askMs.count),
      lowerIsBetter: true,
      targetValue: 2000,
    },
    {
      id: 'E3',
      label: '冷启动到可用（引擎 ready）',
      target: '≤3000ms',
      value: coldStartMs,
      unit: 'ms',
      n: coldStartMs === null ? 0 : 1,
      note:
        coldStartMs === null
          ? '引擎这次还没 ready 过'
          : '从这个窗口开始加载算起，只在本次会话第一次 ready 时记一次',
      lowerIsBetter: true,
      targetValue: 3000,
    },
    {
      id: 'E4',
      label: '界面内存占用（渲染进程 JS 堆）',
      target: '≤450MB（含引擎的总量看下面引擎侧）',
      value: rendererMemMb(),
      unit: 'MB',
      n: rendererMemMb() === null ? 0 : 1,
      note:
        rendererMemMb() === null
          ? '这个环境读不到 performance.memory'
          : '只是 JS 堆，不含渲染层的图片和 GPU 显存',
      lowerIsBetter: true,
      targetValue: 450,
    },
    {
      id: 'E6',
      label: '界面帧率（面板打开期间实测）',
      target: '≥55fps',
      // 帧率 = 1000 / 帧间隔中位数。用中位数不用平均：
      // 平均会被几个大间隔拉垮，看起来永远不达标
      value: (() => {
        const med = frameMs.pct(0.5);
        return med && med > 0 ? Math.round(1000 / med) : null;
      })(),
      unit: 'fps',
      n: frameMs.count,
      note:
        frameMs.count === 0
          ? '打开这个面板就会开始采，滚一滚别的页面再回来看更准'
          : '面板关掉就停止采样（采样本身也要占主线程）',
      lowerIsBetter: false,
      targetValue: 55,
    },
    {
      id: 'E6b',
      label: '主线程长任务（>50ms 卡住界面的次数）',
      target: '0 次',
      value: longTaskObserver ? longTaskCount : null,
      unit: '次',
      n: longTaskObserver ? 1 : 0,
      note: longTaskObserver
        ? longTaskCount > 0
          ? `最长一次 ${longTaskMaxMs}ms —— 这就是"卡了一下"的直接来源`
          : '本次会话还没出现过卡住主线程的长任务'
        : '这个环境不支持 longtask 观测',
      lowerIsBetter: true,
      targetValue: 0,
    },
    {
      id: 'E7',
      label: '联网搜首字节 P95',
      target: '≤1200ms',
      value: webMs.pct(0.95),
      unit: 'ms',
      n: webMs.count,
      note: pctNote(webMs.count),
      lowerIsBetter: true,
      targetValue: 1200,
    },
    {
      id: 'E8',
      label: '引擎掉线次数（本次会话）',
      target: '0 次',
      value: engineDropCount,
      unit: '次',
      n: 1,
      note:
        engineErrorCount > 0
          ? `另有 ${engineErrorCount} 次启动失败`
          : '只统计"已经 ready 之后又掉了"，启动过程中的状态变化不算',
      lowerIsBetter: true,
      targetValue: 0,
    },
  ];
}

/** 达标判定。**没有观测值时返回 null（未知），不返回 false。** */
export function verdict(o: Observation): 'pass' | 'fail' | null {
  if (o.value === null || o.targetValue === null) return null;
  // 样本不足时不下结论 —— 那正是"跑两次就宣布达标"的来源
  if (o.id.endsWith('b') === false && o.n > 1 && o.n < P95_MIN_SAMPLES && o.unit === 'ms') {
    return null;
  }
  return o.lowerIsBetter
    ? o.value <= o.targetValue
      ? 'pass'
      : 'fail'
    : o.value >= o.targetValue
      ? 'pass'
      : 'fail';
}
