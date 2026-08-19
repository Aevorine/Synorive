import { useEffect, useMemo, useState } from 'react';
import {
  Clock,
  FolderOpen,
  Globe,
  Network,
  PanelLeft,
  Pin,
  PinOff,
  RotateCcw,
  ScanSearch,
  Search,
  Settings,
  Sun,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { UpdateState } from '@shared/ipc-contract';
import { PAGE_TITLES, useApp, type PageId } from '../lib/store';

/**
 * 侧栏：一级入口 + 可钉的收藏区
 * ====================================================================
 * 顺序按**使用频率**排，不按功能分类。分类排法看着整齐，但用户每天点的
 * 那两个会被排到中间去 —— 整齐是给设计稿看的，频率是给手看的。
 *
 * ── B7 为什么加"钉住" ────────────────────────────────────
 * 用户原话（对"界面哪里没做对"的回答之一）：**「功能藏得深、步骤多」**。
 * 固定的七个入口解决不了这个问题，因为**每个人常用的不是同一批**：
 * 做研究的人天天开研究工作台，整理素材的人天天开分析中心。
 * 与其我猜一个顺序，不如让他自己把常用的钉到最上面 ——
 * 钉过一次之后，他的高频路径就永远是"一眼看到 + 一次点击"。
 *
 * 钉住状态存在设置里（`pinnedNav`），跟人走不跟窗口走。
 */

const NAV: { id: PageId; icon: LucideIcon; hint: string }[] = [
  // 「今日」排第一：它是启动页，也是唯一一个"不用想就有东西看"的页面
  { id: 'today', icon: Sun, hint: '有什么新东西、有什么没读完' },
  { id: 'search', icon: Search, hint: '问一句话，或搜全部已索引内容' },
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
  const settings = useApp((s) => s.settings);
  const badge = useUpdateBadge();

  const pinned = useMemo(() => settings?.pinnedNav ?? [], [settings?.pinnedNav]);

  /**
   * 用户自己拖出来的顺序。
   *
   * 🔴 **配置里没有的页面接在后面，不是丢掉。** 用户拖过一次之后 navOrder
   *    就固定了那几项；将来加了新页面，如果只按 navOrder 渲染，
   *    新页面会**在导航栏里根本不出现** —— 不报错，用户也不知道有这个功能。
   *    所以永远是"先按存下来的顺序排，剩下的按内置顺序接在后面"。
   *
   * 🔴 配置里有、而代码里已经删掉的页面要过滤掉，否则会渲染一个 undefined。
   */
  const ordered = useMemo(() => {
    const saved = settings?.navOrder ?? [];
    const known = new Map(NAV.map((n) => [n.id, n]));
    const out: typeof NAV = [];
    for (const id of saved) {
      const n = known.get(id as PageId);
      if (n) {
        out.push(n);
        known.delete(id as PageId);
      }
    }
    for (const n of NAV) if (known.has(n.id)) out.push(n);
    return out;
  }, [settings?.navOrder]);

  /** 正在拖的那一项。null = 没在拖 */
  const [dragId, setDragId] = useState<PageId | null>(null);
  /** 松手会落到哪一项之前。用来画那条插入线 */
  const [overId, setOverId] = useState<PageId | null>(null);

  const dropOn = (targetId: PageId) => {
    const from = dragId;
    setDragId(null);
    setOverId(null);
    if (!from || from === targetId) return;
    const ids = ordered.map((n) => n.id).filter((id) => id !== from);
    const at = ids.indexOf(targetId);
    ids.splice(at < 0 ? ids.length : at, 0, from);
    void window.synorive.settings.patch({ navOrder: ids });
  };

  /**
   * 钉 / 取消钉。
   *
   * 直接 patch 设置而不是先改本地 state —— 设置变更会通过 onChanged 广播回来，
   * 自己先改一份的话，广播回来那一下会跟本地那份打架，表现是"点了之后闪一下又跳回去"。
   */
  const togglePin = (id: string) => {
    const next = pinned.includes(id) ? pinned.filter((p) => p !== id) : [...pinned, id];
    void window.synorive.settings.patch({ pinnedNav: next });
  };

  const pinnedItems = ordered.filter((n) => pinned.includes(n.id));
  // 钉住的从下面那组里拿走 —— 同一个入口出现两次会让人以为是两个东西
  const restItems = ordered.filter((n) => !pinned.includes(n.id));

  const renderItem = (n: { id: PageId; icon: LucideIcon; hint: string }, isPinned: boolean) => (
    <div
      key={n.id}
      className={`sidebar__slot${overId === n.id ? ' sidebar__slot--over' : ''}${
        dragId === n.id ? ' sidebar__slot--dragging' : ''
      }`}
      onDragOver={(e) => {
        // 🔴 不 preventDefault 的话浏览器根本不认这是个放置目标，
        //    表现是拖过去光标一直是"禁止"，松手什么都不发生
        if (!dragId) return;
        e.preventDefault();
        setOverId(n.id);
      }}
      onDragLeave={() => setOverId((id) => (id === n.id ? null : id))}
      onDrop={(e) => {
        e.preventDefault();
        dropOn(n.id);
      }}
    >
      <button
        className={`sidebar__item${page === n.id ? ' sidebar__item--active' : ''}`}
        // 拖着换顺序。收起态也能拖 —— 那时候正是最想调顺序的时候
        draggable
        onDragStart={(e) => {
          setDragId(n.id);
          e.dataTransfer.effectAllowed = 'move';
          // Firefox 不设 data 就不触发 drag 事件；值本身用不上
          e.dataTransfer.setData('text/plain', n.id);
        }}
        onDragEnd={() => {
          setDragId(null);
          setOverId(null);
        }}
        onClick={() => setPage(n.id)}
        title={`${PAGE_TITLES[n.id]} —— ${n.hint}（可以拖着换顺序）`}
        aria-label={PAGE_TITLES[n.id]}
        aria-current={page === n.id ? 'page' : undefined}
      >
        <n.icon className="sidebar__icon" size={18} strokeWidth={1.7} />
        <span className="sidebar__label">{PAGE_TITLES[n.id]}</span>
      </button>
      {/* 收起时不显示钉按钮：那时候只剩一列图标，再塞一个按钮会挤成一团。
          要钉先展开侧栏 —— 钉是个低频动作，为它牺牲收起态不划算 */}
      {!collapsed && (
        <button
          className={`sidebar__pin${isPinned ? ' sidebar__pin--on' : ''}`}
          onClick={() => togglePin(n.id)}
          title={isPinned ? '取消钉住' : '钉到最上面'}
          aria-label={isPinned ? `取消钉住${PAGE_TITLES[n.id]}` : `把${PAGE_TITLES[n.id]}钉到最上面`}
        >
          {isPinned ? <PinOff size={12} strokeWidth={1.8} /> : <Pin size={12} strokeWidth={1.8} />}
        </button>
      )}
    </div>
  );

  return (
    <nav className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`} aria-label="主导航">
      {pinnedItems.length > 0 && (
        <>
          {!collapsed && <div className="sidebar__group">常用</div>}
          {pinnedItems.map((n) => renderItem(n, true))}
          <div className="sidebar__rule" />
        </>
      )}

      {restItems.map((n) => renderItem(n, false))}

      <div className="sidebar__spacer" />

      <button
        className={`sidebar__item${page === 'settings' ? ' sidebar__item--active' : ''}`}
        onClick={() => setPage('settings')}
        title={
          badge.show
            ? `设置 —— 有新版本 v${badge.version}${badge.ready ? '（已下载，等你确认安装）' : '可以下载'}`
            : '设置 —— 外观、性能、隐私、更新、同步'
        }
        aria-label="设置"
        aria-current={page === 'settings' ? 'page' : undefined}
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

      {/* 拖乱了要能回去。**只在真的拖过之后才出现** ——
          没拖过的人看到一个"恢复默认顺序"只会疑惑默认是什么、我改过吗 */}
      {(settings?.navOrder?.length ?? 0) > 0 && !collapsed && (
        <button
          className="sidebar__item sidebar__item--minor"
          onClick={() => void window.synorive.settings.patch({ navOrder: [] })}
          title="把导航栏顺序恢复成默认（按使用频率排）"
        >
          <RotateCcw className="sidebar__icon" size={16} strokeWidth={1.7} />
          <span className="sidebar__label">恢复默认顺序</span>
        </button>
      )}

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
