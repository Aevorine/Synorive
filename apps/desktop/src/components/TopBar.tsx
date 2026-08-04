import { useEffect, useState } from 'react';
import { Minus, Square, Copy, X } from 'lucide-react';
import { useApp, type PageId } from '../lib/store';
import type { SearchFilters } from '@synorive/shared-types';
import { useSearch, type Preset } from '../lib/useSearch';
import { StageCompact } from './AskStage';
import { Flashback } from './Flashback';
import { ProjectSwitcher } from './ProjectSwitcher';
import iconUrl from '../../resources/icons/icon-64.png';

/**
 * 顶栏：品牌 + 主输入区收窄条 + 窗口按钮
 *
 * ⚠️ **顶栏这一条不再是输入框，是一个按钮。** 真正的输入发生在 B1 主舞台
 *    （`AskStage`）里 —— 一个地方输入、一个地方展示，就不会出现"两个框里
 *    的字不一样"。原来那个高 32px 的单行 input 正是用户反复提的
 *    「重要功能位置不对、输入内容多时界面不够大」的病灶本身，不该在这里复活。
 */
export function TopBar() {
  const [isMax, setIsMax] = useState(false);
  const setPage = useApp((s) => s.setPage);
  const setQuery = useSearch((s) => s.setQuery);
  const setFilters = useSearch((s) => s.setFilters);
  const setPreset = useSearch((s) => s.setPreset);

  useEffect(() => {
    void window.synorive.window.isMaximized().then(setIsMax);
    return window.synorive.window.onStateChanged((s) => setIsMax(s.isMaximized));
  }, []);

  return (
    <header className="topbar syn-drag">
      <div className="topbar__brand">
        <img className="topbar__logo" src={iconUrl} alt="" draggable={false} />
        <span className="topbar__name">Synorive</span>
      </div>

      <div className="topbar__center syn-no-drag">
        <StageCompact />
        <GlobalHotkeys />
      </div>

      <div className="topbar__actions syn-no-drag">
        {/* A5 项目切换器。和闪回一样必须跨页面常驻 ——
            "我现在在哪个项目下"是个持续存在的上下文，
            藏进某一页的话，在别的页面干活时就看不见自己归在哪儿了 */}
        <ProjectSwitcher />
        {/* F3 撤销/闪回。放顶栏是因为它必须**跨页面常驻** ——
            撤销的典型场景恰恰是"我刚跳到别的页面才发现上一步做错了"，
            挂在某个页面里就永远差一步够不着。
            快照由 `useSearch.fire()` 产出（查询词 + 筛选 + 预设），
            这里按同一套字段名还原 —— 两边字段名对不上就会安静地什么都不恢复 */}
        <Flashback
          onRestore={(snap) => {
            setPage(snap.page as PageId);
            // 🔴 **筛选和预设也要一起还原。** 只填回查询词的话，用户回到
            // "十分钟前那个界面"看到的是同一个词配着**现在**的筛选 ——
            // 结果对不上他记忆里的那一屏，而他找不出哪里不一样。
            // 顺序：先设筛选和预设，最后设查询词（setQuery 会触发搜索，
            // 反过来的话第一次搜索用的还是旧筛选）
            const f = snap.state['filters'];
            if (f && typeof f === 'object') setFilters(f as SearchFilters);
            const p = snap.state['preset'];
            if (typeof p === 'string') setPreset(p as Preset);
            const q = snap.state['query'];
            if (typeof q === 'string') setQuery(q);
          }}
        />
        <div className="wincontrols">
          <button
            className="wincontrols__btn"
            onClick={() => void window.synorive.window.minimize()}
            aria-label="最小化"
            title="最小化"
          >
            <Minus size={15} strokeWidth={1.6} />
          </button>
          <button
            className="wincontrols__btn"
            onClick={() => void window.synorive.window.maximizeToggle()}
            aria-label={isMax ? '还原' : '最大化'}
            title={isMax ? '还原' : '最大化'}
          >
            {isMax ? <Copy size={13} strokeWidth={1.6} /> : <Square size={12} strokeWidth={1.6} />}
          </button>
          <button
            className="wincontrols__btn wincontrols__btn--close"
            onClick={() => void window.synorive.window.close()}
            aria-label="关闭"
            title="关闭到托盘"
          >
            <X size={15} strokeWidth={1.6} />
          </button>
        </div>
      </div>
    </header>
  );
}


/**
 * 全局快捷键。
 *
 * 挂在顶栏是因为**顶栏是唯一一个任何页面都挂载着的组件** ——
 * 挂在搜索页里的话，人在图谱页时按 Ctrl+K 不会有任何反应，
 * 而这种"有时候好使有时候不好使"的快捷键比没有快捷键更糟。
 *
 * 它自己不渲染任何东西，只装监听。
 */
function GlobalHotkeys() {
  const openStage = useApp((s) => s.openStage);
  const setCommandPaletteOpen = useApp((s) => s.setCommandPaletteOpen);
  const setInputMode = useApp((s) => s.setInputMode);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing =
        t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);

      // 「/」直达。正在打字时不拦 —— 否则用户在任何输入框里都打不出斜杠
      if (e.key === '/' && !typing) {
        e.preventDefault();
        openStage();
      }

      // Ctrl+K：展开主舞台。它是最常用的一个，所以占最好按的那组键。
      // ⚠️ 在输入框里也要生效 —— 用户在别的输入框里想起要问一句话时，
      //    要求他先点一下别处再按快捷键是没道理的
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        openStage();
      }

      // Ctrl+Shift+K：展开并切到「找东西」。
      // 给一个直达第二模式的键，省掉"展开→点切换→再打字"这三步
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setInputMode('find');
        openStage();
      }

      // E13 命令面板：Ctrl+Shift+P（VS Code 那套）。
      // 不用 Ctrl+K —— 那个给了主输入区，主输入区是一直在用的，
      // 命令面板是偶尔用一次的，抢过来是净亏
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'p') {
        e.preventDefault();
        setCommandPaletteOpen(!useApp.getState().commandPaletteOpen);
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [openStage, setCommandPaletteOpen, setInputMode]);

  return null;
}
