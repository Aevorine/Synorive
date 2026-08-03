import { FolderOpen, Globe, Network, PanelLeft, ScanSearch, Search, Settings, Clock } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
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

export function SideBar() {
  const page = useApp((s) => s.page);
  const setPage = useApp((s) => s.setPage);
  const collapsed = useApp((s) => s.sideBarCollapsed);
  const toggle = useApp((s) => s.toggleSideBar);

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
        title="设置"
      >
        <Settings className="sidebar__icon" size={18} strokeWidth={1.7} />
        <span className="sidebar__label">{PAGE_TITLES.settings}</span>
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
