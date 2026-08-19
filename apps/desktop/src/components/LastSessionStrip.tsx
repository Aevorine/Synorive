import { useEffect, useState } from 'react';
import { History, RotateCw } from 'lucide-react';
import { api } from '../lib/api';
import { agoText, loadLastSession, shortLocator, type LastSession } from '../lib/lastSession';
import { useSearch } from '../lib/useSearch';
import { useApp } from '../lib/store';

/**
 * 上次那一屏 —— 打开软件立刻有东西看
 * ============================================================
 * 冷启动顺序是：窗口出画面 → 引擎子进程起来 → 模型预热 → 第一次查询才有结果。
 * 中间那一两秒界面是空的，而"空白"看起来和"坏了"一样。
 *
 * ── 为什么是一条附加的窄条，不是替换掉落地页 ────────────────
 * 第一版做的是"启动时直接把上次的结果铺满搜索页"。三个问题：
 *   ① 它要改 `stageExpanded` 和 `inputMode` 才显示得出来，而 `App` 的开机
 *      流程也在异步改这两个值 —— 谁后到谁赢，表现是"有时候显示有时候不显示"。
 *   ② 用户默认落在「今日」时它根本不触发，等于只对一部分人生效。
 *   ③ 最要紧的：用**过期数据**顶替掉用户主动选择的落地界面，是自作主张。
 * 现在改成挂在输入区下面的一条窄条：任何配置下都出现、都不抢位置，
 * 用户点了才用它，**一次真实搜索之后自动消失**。
 *
 * 🔴 **必须明写这是旧的。** 库里的东西可能已经被删了。不标注就是给用户看
 *    一份他以为是实时的过期清单 —— 他点进去发现文件没了，会以为软件把东西弄丢了。
 */
export function LastSessionStrip() {
  const [snap, setSnap] = useState<LastSession | null>(null);
  const searched = useSearch((s) => s.searched);
  const query = useSearch((s) => s.query);
  const setQuery = useSearch((s) => s.setQuery);
  const setInputMode = useApp((s) => s.setInputMode);
  const setStageExpanded = useApp((s) => s.setStageExpanded);

  // 只在挂载时读一次。localStorage 是同步的，读它不值得放进 effect 之外的地方，
  // 但也**不能每次渲染都读** —— 那是每帧一次 JSON.parse
  useEffect(() => {
    setSnap(loadLastSession());
  }, []);

  // 已经搜过一次、或者用户已经开始打字 —— 旧的那一屏就没有存在意义了
  if (!snap || searched || query.trim()) return null;

  const rerun = () => {
    setInputMode('find');
    setStageExpanded(false);
    setQuery(snap.query);
  };

  return (
    <section className="lastsess" aria-label="上次那一屏">
      <header className="lastsess__head">
        <History size={14} strokeWidth={1.7} aria-hidden />
        <span className="lastsess__title">
          上次搜的是「{snap.query}」· {snap.total} 条
        </span>
        <span className="lastsess__age">{agoText(snap.at)}</span>
        <span className="lastsess__spacer" />
        <button className="btn btn--sm" onClick={rerun} title="用现在的库把这个词重新查一遍">
          <RotateCw size={13} strokeWidth={1.8} />
          重新查一遍
        </button>
      </header>

      <ul className="lastsess__list">
        {snap.hits.slice(0, 5).map((h) => (
          <li key={h.item.id}>
            <button
              className="lastsess__row"
              /* 悬停提示也用截短版 —— 完整地址里那串令牌不该被鼠标一停就浮出来 */
              title={`打开 ${shortLocator(h.item.locator)}`}
              onClick={() => {
                // 记一次打开，和正常结果列表一致
                void api.recordOpen(h.item.id);
                if (h.item.source === 'link') {
                  void window.synorive.sys.openExternal(h.item.locator);
                } else {
                  void window.synorive.sys.openPath(h.item.locator);
                }
              }}
            >
              <span className="lastsess__row-title">{h.item.title || shortLocator(h.item.locator)}</span>
              <span className="lastsess__row-loc">{shortLocator(h.item.locator)}</span>
            </button>
          </li>
        ))}
      </ul>

      <p className="lastsess__note">这几条是上次的记录，库里现在的样子以重新查一遍为准</p>
    </section>
  );
}
