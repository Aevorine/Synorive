import { useEffect, useState } from 'react';
import { ChevronRight, Flame } from 'lucide-react';
import { api, type Briefing } from '../lib/api';
import { useApp } from '../lib/store';

/**
 * 每日简报（提案 36）
 * ============================================================
 * 库越大越有一个毛病：**只有你想得起来的东西才找得到。** 存进去两年没打开过的
 * 资料等于不存在。这一块主动把"你可能忘了的东西"端上来。
 *
 * 🔴 **每一条都要能点。** 只报数字不给动作的卡片是仪表盘不是首页，
 *    用户看两天就不看了。
 *
 * 🔴 **空的块直接不显示。** 一个天天要看的地方，摆着六个"暂无"比少几块烦人得多。
 */
export function BriefingPanel({
  onOpen,
}: {
  // 带上 title：调用方要把它记进「最近打开」，只有 id 和路径的话
  // 命令面板里那一条会显示成一串盘符路径，没人认得出那是什么
  onOpen: (it: { id: string; title?: string; locator: string }) => void;
}) {
  const [brief, setBrief] = useState<Briefing | null>(null);

  /**
   * 🔴 **等引擎 ready 之后再问，引擎 ready 时要重新问一遍。**
   *
   * 原来依赖数组是 `[]`：冷启动时引擎要几秒才起得来，今日页这时候已经挂载了，
   * `api.briefing()` 抛 EngineUnavailable → 被下面那个空 catch 吞掉 →
   * `brief` 永远是 null → `return null` → **整块简报这一整次会话都不出现**，
   * 而且一个字的提示都没有，看起来就像这个功能不存在。
   */
  const engineReady = useApp((s) => s.engine?.lifecycle === 'ready');
  useEffect(() => {
    if (!engineReady) return;
    let alive = true;
    api
      .briefing()
      .then((b) => alive && setBrief(b))
      .catch(() => {
        /* 真的问失败了就整块不显示，不摆一条常驻的红色报错 */
      });
    return () => {
      alive = false;
    };
  }, [engineReady]);

  if (!brief) return null;
  const shown = brief.sections.filter(
    (s) => s.items.length > 0 || (s.entities?.length ?? 0) > 0,
  );
  if (shown.length === 0) return null;

  return (
    <div className="brief">
      {shown.map((s) => (
        <section key={s.key} className="brief__block">
          <header className="brief__head">
            <h3 className="brief__title" title={s.why}>
              {s.title}
            </h3>
            {s.total > s.items.length && <span className="brief__count">共 {s.total}</span>}
          </header>

          {s.key === 'rising' ? (
            <ul className="brief__list">
              {s.entities?.map((e) => (
                <li key={e.id} className="brief__row">
                  <Flame size={13} strokeWidth={1.8} />
                  <span className="brief__name">{e.name}</span>
                  <span
                    className="brief__lift"
                    title={`最近 ${e.recent} 次，上一个同长度的周期 ${e.previous} 次`}
                  >
                    ×{e.lift}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <ul className="brief__list">
              {s.items.map((it) => (
                <li key={it.id}>
                  <button
                    className="brief__row brief__row--btn"
                    onClick={() => onOpen(it)}
                    title={it.locator}
                  >
                    <ChevronRight size={13} strokeWidth={1.8} />
                    <span className="brief__name">{it.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}
