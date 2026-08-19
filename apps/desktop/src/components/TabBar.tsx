import { X } from 'lucide-react';
import { PAGE_TITLES, useApp, type PageId } from '../lib/store';

/**
 * 标签页条
 * ============================================================
 * 一边挂着研究工作台跑检索，一边开搜索 —— 切回来进度还在。
 *
 * 🔴 **每个界面最多一个标签。** 允许同一个界面开两个的话，同一个组件会被
 *    挂载两次，而 `useSearch` / `useAsk` 这些 store 是**全局单例**：
 *    两份实例读写同一份状态，症状是"在 A 标签改了筛选，B 标签跟着变"。
 *    那种串味极难排查，因为两边看起来都是"自己的"界面。
 *    一页一个之后这条路整个不存在，代价只是"不能开两个搜索页" ——
 *    而顶栏本来就只有一个输入框，开两个搜索页本来也没有意义。
 *
 * 🔴 **只剩一个标签时不给关。** 关掉最后一个的话主区会空掉，
 *    而空白看起来和坏了一样。
 *
 * 🔴 只有一个标签时整条不显示 —— 一个标签的"标签栏"是纯粹的噪声，
 *    白占一行高度还让人以为多了个概念要理解。
 */
export function TabBar() {
  const tabs = useApp((s) => s.tabs);
  const page = useApp((s) => s.page);
  const setPage = useApp((s) => s.setPage);
  const closeTab = useApp((s) => s.closeTab);

  if (tabs.length <= 1) return null;

  return (
    <div className="tabbar" role="tablist" aria-label="打开的界面">
      {tabs.map((t) => (
        <div key={t} className={`tabbar__tab${t === page ? ' tabbar__tab--on' : ''}`}>
          <button
            role="tab"
            aria-selected={t === page}
            className="tabbar__label"
            onClick={() => setPage(t)}
            title={`切到${PAGE_TITLES[t]}`}
          >
            {PAGE_TITLES[t]}
          </button>
          <button
            className="tabbar__x"
            onClick={(e) => {
              e.stopPropagation();
              closeTab(t as PageId);
            }}
            title={`关掉${PAGE_TITLES[t]}（这一页正在跑的东西会停）`}
            aria-label={`关掉${PAGE_TITLES[t]}`}
          >
            <X size={11} strokeWidth={2} />
          </button>
        </div>
      ))}
    </div>
  );
}
