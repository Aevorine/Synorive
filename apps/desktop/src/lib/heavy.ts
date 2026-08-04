/**
 * C3 重活调度器 —— 主线程这一侧
 * ====================================================================
 * 对外只有两个 async 函数，调用方不需要知道它跑在哪个线程。
 *
 * ── 三条设计约束 ──────────────────────────────────────────
 * ① **永远有主线程兜底。** Worker 创建失败（打包路径不对、CSP 拦了、
 *    极老的 Electron）时直接在主线程跑同一份纯函数。
 *    🔴 没有兜底的话，故障表现是"搜索结果全都不高亮了"——
 *       不报错、不崩溃，只是功能安静地消失，最难查的那一类。
 * ② **单例 + 懒创建。** 每次调用新建一个 Worker 的话，
 *    创建开销（几毫秒到几十毫秒）比它省下来的计算还多。
 * ③ **超时必须有。** Worker 里死循环时不会有任何回信，
 *    没有超时的话调用方的 Promise 永远挂着，界面永远转圈。
 */

// 🔴 这个 import 必须是**静态**的。Vite 靠 `?worker` 后缀在构建时把它
//    单独打成一个 chunk 并注入正确 URL；写成 `require()` 或动态拼路径，
//    开发时照样能跑，**打包后才 404** —— 而那时候症状是"高亮功能没了"。
import HeavyWorker from './heavy.worker?worker';
import type { DiffLine, HeavyResponse, HighlightSegment } from './heavy.worker';
import { diffLines, highlightSegments } from './heavy.worker';
import { useApp } from './store';

/** 超时。1.5 秒还没算完的任务，与其等它不如在主线程重算一遍 */
const TIMEOUT_MS = 1500;

let worker: Worker | null = null;
/** 创建失败过一次就不再试 —— 反复创建失败的 Worker 比不用 Worker 还慢 */
let workerDead = false;
let seq = 0;

const pending = new Map<
  number,
  { resolve: (r: HeavyResponse) => void; reject: (e: Error) => void; timer: number }
>();

function ensureWorker(): Worker | null {
  if (workerDead) return null;
  if (worker) return worker;
  // 用户显式关掉了就不创建（设置里那个开关是排查手段）
  if (useApp.getState().settings?.offloadHeavyWork === false) return null;

  try {
    worker = new HeavyWorker();
  } catch {
    workerDead = true;
    return null;
  }

  worker.onmessage = (e: MessageEvent<HeavyResponse>) => {
    const res = e.data;
    const p = pending.get(res.id);
    if (!p) return;
    pending.delete(res.id);
    clearTimeout(p.timer);
    p.resolve(res);
  };

  // Worker 整体崩掉：把所有在等的 Promise 全部 reject，
  // 调用方走各自的兜底。**不能让它们静静挂着**
  worker.onerror = () => {
    workerDead = true;
    for (const [, p] of pending) {
      clearTimeout(p.timer);
      p.reject(new Error('worker 挂了'));
    }
    pending.clear();
    worker?.terminate();
    worker = null;
  };

  return worker;
}

function ask(payload: Record<string, unknown> & { kind: string }): Promise<HeavyResponse> {
  const w = ensureWorker();
  if (!w) return Promise.reject(new Error('worker 不可用'));

  const id = ++seq;
  return new Promise<HeavyResponse>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      pending.delete(id);
      reject(new Error('worker 超时'));
    }, TIMEOUT_MS);
    pending.set(id, { resolve, reject, timer });
    w.postMessage({ ...payload, id });
  });
}

/**
 * 把文本按命中词切段。**任何失败都退回主线程算**，绝不返回空。
 *
 * 🔴 返回空数组是最坏的失败方式：调用方渲染出一段空白，
 *    看起来像"这条结果没有内容"，而它其实只是高亮失败了。
 */
export async function highlight(text: string, terms: string[]): Promise<HighlightSegment[]> {
  try {
    const r = await ask({ kind: 'highlight', text, terms });
    if (r.kind === 'highlight') return r.segments;
    throw new Error(r.kind === 'error' ? r.message : '返回类型不对');
  } catch {
    return highlightSegments(text, terms);
  }
}

export async function diff(left: string, right: string): Promise<DiffLine[]> {
  try {
    const r = await ask({ kind: 'diff', left, right });
    if (r.kind === 'diff') return r.lines;
    throw new Error(r.kind === 'error' ? r.message : '返回类型不对');
  } catch {
    return diffLines(left, right);
  }
}

/** 设置里关掉开关时调用，把已经建好的那个也停掉，省一个常驻线程 */
export function shutdownHeavy(): void {
  worker?.terminate();
  worker = null;
  pending.clear();
}

export type { DiffLine, HighlightSegment };
