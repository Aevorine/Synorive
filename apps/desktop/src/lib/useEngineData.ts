/**
 * 引擎数据的通用取数 hook
 * ============================================================
 * 五个页面都要"打开时拉一次、引擎推事件时刷新"。
 * 各自写一遍会写出五份略有差异的加载/错误/竞态处理，
 * 收敛成一个 hook 之后行为一致，也少了四份可能出 bug 的地方。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useApp } from './store';

interface State<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useEngineData<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  opts: { refreshOn?: string[]; refreshMs?: number } = {},
): State<T> & { reload: () => void } {
  const [state, setState] = useState<State<T>>({ data: null, loading: true, error: null });
  const engineReady = useApp((s) => s.engine?.lifecycle === 'ready');
  const seq = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(() => {
    if (!engineReady) {
      setState({ data: null, loading: false, error: '引擎还没就绪' });
      return;
    }
    const my = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcherRef
      .current()
      .then((data) => {
        // 慢请求后到会把新数据盖掉 —— 只认最后一次发起的
        if (my === seq.current) setState({ data, loading: false, error: null });
      })
      .catch((e: Error) => {
        if (my === seq.current) setState({ data: null, loading: false, error: e.message });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engineReady, ...deps]);

  useEffect(() => {
    load();
  }, [load]);

  // 引擎推来相关事件时自动刷新
  useEffect(() => {
    const kinds = opts.refreshOn;
    if (!kinds?.length) return;
    return window.synorive.engine.onEvent((raw) => {
      const ev = raw as { type?: string };
      if (ev?.type && kinds.includes(ev.type)) load();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, opts.refreshOn?.join(',')]);

  // 定时刷新（分析进度这类需要）
  useEffect(() => {
    if (!opts.refreshMs) return;
    const t = setInterval(load, opts.refreshMs);
    return () => clearInterval(t);
  }, [load, opts.refreshMs]);

  return { ...state, reload: load };
}
