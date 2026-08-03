import { useEffect, useState } from 'react';
import { FolderOpen, Globe, Network, PanelLeft, ScanSearch, Search, Settings, Clock } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { UpdateState } from '@shared/ipc-contract';
import { PAGE_TITLES, useApp, type PageId } from '../lib/store';

/**
 * 侧栏：七个一级入口，多了就是没想清楚。
 * 顺序按使用频率，不按功能分类。
 */
const NAV: { id: PageId; icon: LucideIcon; hint: string }[] = [
  { id: 'search', icon: Search, hint: '搜索全部已索引内容' },
  { id: 'library', icon: FolderOpen, hint: '浏览、筛选、管理库里的内容' },
  { id: 'analyze', icon: ScanSearch, hint: '投喂新内容并查看分析进度' },
  { id: 'timeline', icon: Clock, hint: '按时间铺开所有内容' },
  { id: 'graph', icon: Network, hint: '人物、地点、组织之间的关系' },
  { id: 'research', icon: Globe, hint: '联网搜索、深挖简报、文献检索' },
];

/**
 * 「设置」上要不要挂更新角标。
 *
 * 🔴 **没有这个角标，启动时的自动检查就是白做的** —— 查到了新版本，
 *    但除非用户碰巧打开设置页，否则他永远不知道。自动检查的全部价值
 *    就在于"不用我去找它，它会告诉我"。
 *
 * 只在 available / downloaded 两态亮，而且**跳过的版本不亮** ——
 * 用户明确说了不想要这一版，还继续挂个红点就是不听话。
 */
function useUpdateBadge(): { show: boolean; version: string | null; ready: boolean } {
  const [state, setState] = useState<UpdateState | null>(null);

  useEffect(() => {
    let alive = true;
    void window.synorive.updater.getState().then((s) => {
      if (alive && s) setState(s);
    });
    const off = window.synorive.updater.onStateChanged(setState);
    return () => {
      alive = false;
      off();
    };
  }, []);

  if (!state) return { show: false, version: null, ready: false };
  const interesting = state.lifecycle === 'available' || state.lifecycle === 'downloaded';
  const skipped = !!state.latestVersion && state.latestVersion === state.skippedVersion;
  return {
    show: interesting && !skipped,
    version: state.latestVersion,
    ready: state.lifecycle === 'downloaded',
  };
}

export function SideBar() {
  const page = useApp((s) => s.page);
  const setPage = useApp((s) => s.setPage);
  const collapsed = useApp((s) => s.sideBarCollapsed);
  const toggle = useApp((s) => s.toggleSideBar);
  const badge = useUpdateBadge();

  return (
    <nav className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`} aria-label="主导航">
      {NAV.map(({ id, icon: Icon, hint }) => (
        <button
          key={id}
          className={`sidebar__item${page === id ? ' sidebar__item--active' : ''}`}
          onClick={() => setPage(id)}
          title={collapsed ? `${PAGE_TITLES[id]} —— ${hint}` : hint}
          aria-current={page === id ? 'page' : undefined}
        >
          <Icon className="sidebar__icon" size={18} strokeWidth={1.7} />
          <span className="sidebar__label">{PAGE_TITLES[id]}</span>
        </button>
      ))}

      <div className="sidebar__spacer" />

      <button
        className={`sidebar__item${page === 'settings' ? ' sidebar__item--active' : ''}`}
        onClick={() => setPage('settings')}
        title={
          badge.show
            ? `设置 —— 有新版本 v${badge.version}${badge.ready ? '（已下载，等你确认安装）' : '可以下载'}`
            : '设置'
        }
      >
        <Settings className="sidebar__icon" size={18} strokeWidth={1.7} />
        <span className="sidebar__label">{PAGE_TITLES.settings}</span>
        {/* 侧栏收起时只剩图标，角标要跟着图标走，所以放在按钮里用绝对定位 */}
        {badge.show && (
          <span
            className={`sidebar__badge${badge.ready ? ' sidebar__badge--ready' : ''}`}
            aria-label={`有新版本 v${badge.version}`}
          />
        )}
      </button>

      <button
        className="sidebar__item"
        onClick={toggle}
        title={collapsed ? '展开侧栏' : '收起侧栏'}
        aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
      >
        <PanelLeft className="sidebar__icon" size={18} strokeWidth={1.7} />
        <span className="sidebar__label">收起</span>
      </button>
    </nav>
  );
}
