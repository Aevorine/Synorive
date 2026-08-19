import { useEffect, useRef, useState } from 'react';
import { FileText, Globe, Loader2, X } from 'lucide-react';
import type { SearchHit } from '@synorive/shared-types';
import { api, setEnginePort } from '../lib/api';
import { webApi, type WebResultItem } from '../lib/webApi';

/**
 * 随手研究浮窗（渲染层）—— N7
 * ============================================================
 * 屏幕右下角浮出来的那个小窗。复制一段话 → 三条最相关的。
 *
 * 剪贴板哨兵早就跑通了，但它一直只是"攒着"—— 攒下来的东西除非你
 * 主动去托盘翻，否则一辈子不会被看到。这个组件是把它用起来。
 *
 * 🔴 **窗口本身是 `focusable: false` 的**（见 electron/main/peek.ts），
 * 所以这里**不能有任何需要键盘输入的东西** —— 输入框、可聚焦的
 * 文本区都不行，用户敲不进去。只能是"看一眼 + 点一下"。
 * 这不是限制，是这个功能能存在的前提：你复制东西是为了粘贴，
 * 抢焦点等于毁掉你正在做的事。
 *
 * 只显示 3 条。浮窗的全部价值是"扫一眼"，列十条就变成了另一个搜索页 ——
 * 那还不如直接打开主窗口。
 */

const MAX = 3;

export function ClipboardPeek() {
  const [query, setQuery] = useState('');
  const [local, setLocal] = useState<SearchHit[]>([]);
  const [web, setWeb] = useState<WebResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  /**
   * 🔴 浮窗是**独立的渲染进程**，不共享主窗口的模块状态 ——
   * 引擎端口必须自己拿一遍。少了这一步的症状很隐蔽：
   * 浮窗正常弹出、正常显示"查不到"，而真相是它**一个请求都没发出去**
   * （`api.call` 在 `basePort == null` 时直接抛 EngineUnavailable）。
   * 这类"看起来在工作、其实什么都没做"的失败最难查。
   */
  useEffect(() => {
    let alive = true;
    void window.synorive.engine.getState().then((s) => {
      if (alive) setEnginePort(s?.port ?? null);
    });
    const off = window.synorive.engine.onStateChanged((s) => {
      setEnginePort(s.lifecycle === 'ready' || s.lifecycle === 'degraded' ? s.port : null);
    });
    return () => {
      alive = false;
      off();
    };
  }, []);

  useEffect(() => {
    return window.synorive.peek.onQuery(async (p) => {
      setQuery(p.query);
      setLocal([]);
      setWeb([]);
      setErr(null);
      setLoading(true);
      abortRef.current?.abort();
      const ctl = new AbortController();
      abortRef.current = ctl;

      try {
        // 本地那一路**永远先跑**：几十毫秒就回来，先把画面填上。
        // 等联网一起回来再显示，等于让本地检索白白背上几秒延迟
        const r = await api.search(
          { query: p.query, limit: MAX, stage: 'semantic' } as never,
          ctl.signal,
        );
        if (!ctl.signal.aborted) setLocal(((r as { hits?: SearchHit[] }).hits ?? []).slice(0, MAX));
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setErr((e as Error).message);
      } finally {
        if (!ctl.signal.aborted) setLoading(false);
      }

      if (!p.web || ctl.signal.aborted) return;
      try {
        const w = await webApi.search({ query: p.query, limit: MAX }, ctl.signal);
        if (!ctl.signal.aborted) setWeb(w.results.slice(0, MAX));
      } catch {
        /* 联网这一路失败不报错 —— 本地结果已经在屏幕上了，
           为一个可选的补充弹一条错误提示是帮倒忙 */
      }
    });
  }, []);

  const close = () => void window.synorive.peek.close();

  const openItem = (h: SearchHit) => {
    void api.recordOpen(h.item.id);
    if (h.item.source === 'link') void window.synorive.sys.openExternal(h.item.locator);
    else void window.synorive.sys.openPath(h.item.locator);
    close();
  };

  const nothing = !loading && !local.length && !web.length && !err;

  return (
    <div className="peek">
      <header className="peek__head">
        <span className="peek__q" title={query}>
          {query.length > 42 ? `${query.slice(0, 41)}…` : query}
        </span>
        {loading && <Loader2 size={12} className="spin" aria-hidden />}
        <button className="peek__close" onClick={close} aria-label="关闭" title="关闭">
          <X size={13} />
        </button>
      </header>

      {err && <p className="peek__msg">查不了：{err}</p>}

      {nothing && (
        <p className="peek__msg">
          你的库里没有相关内容。
          <br />
          <span className="peek__dim">（这只查了本地。要连网上一起查，去设置里打开）</span>
        </p>
      )}

      {local.length > 0 && (
        <ul className="peek__list">
          {local.map((h) => (
            <li key={h.item.id}>
              <button onClick={() => openItem(h)}>
                <FileText size={12} aria-hidden />
                <span className="peek__title">{h.item.title}</span>
                {h.location?.page != null && <span className="peek__loc">第 {h.location.page} 页</span>}
                {h.location?.startSec != null && (
                  <span className="peek__loc">
                    {Math.floor(h.location.startSec / 60)}:
                    {String(Math.floor(h.location.startSec % 60)).padStart(2, '0')}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      {web.length > 0 && (
        <>
          <div className="peek__sep">网上</div>
          <ul className="peek__list">
            {web.map((r) => (
              <li key={r.url}>
                <button
                  onClick={() => {
                    void window.synorive.sys.openExternal(r.url);
                    close();
                  }}
                >
                  <Globe size={12} aria-hidden />
                  <span className="peek__title">{r.title}</span>
                  {r.trust && <span className="peek__loc">{r.trust.tierLabel}</span>}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
