import { useEffect, type ComponentType } from 'react';
import { EngineSetup } from './components/EngineSetup';
import { SideBar } from './components/SideBar';
import { CommandPalette } from './components/CommandPalette';
import { StatusBar } from './components/StatusBar';
import { TopBar } from './components/TopBar';
import { AnalyzePage } from './pages/AnalyzePage';
import { GraphPage } from './pages/GraphPage';
import { LibraryPage } from './pages/LibraryPage';
import { ResearchPage } from './pages/ResearchPage';
import { SearchPage } from './pages/SearchPage';
import { SettingsPage } from './pages/SettingsPage';
import { TimelinePage } from './pages/TimelinePage';
import { setEnginePort } from './lib/api';
import { useApp, useResolvedTheme, type PageId } from './lib/store';
import { applyAll } from './lib/theme';
import './styles/global.css';
import './styles/shell.css';
import './styles/search.css';
import './styles/pages.css';
import './styles/research.css';

export default function App() {
  const settings = useApp((s) => s.settings);
  const setSettings = useApp((s) => s.setSettings);
  const setEngine = useApp((s) => s.setEngine);
  const setSystemTheme = useApp((s) => s.setSystemTheme);
  const setReady = useApp((s) => s.setReady);
  const focusSearch = useApp((s) => s.focusSearch);
  const setPage = useApp((s) => s.setPage);
  const page = useApp((s) => s.page);
  const engine = useApp((s) => s.engine);
  const theme = useResolvedTheme();

  // ── 开机接线：拉设置、拉引擎状态、订阅变化 ────────────────
  useEffect(() => {
    let alive = true;

    void (async () => {
      const [s, sysTheme, eng] = await Promise.all([
        window.synorive.settings.get(),
        window.synorive.theme.getSystem(),
        window.synorive.engine.getState(),
      ]);
      if (!alive) return;
      setSettings(s);
      setSystemTheme(sysTheme);
      setEngine(eng);
      // 引擎端口是启动时动态分配的，不能写死
      setEnginePort(eng?.port ?? null);
      setReady(true);
    })();

    const offSettings = window.synorive.settings.onChanged(setSettings);
    const offTheme = window.synorive.theme.onSystemChanged(setSystemTheme);
    const offEngine = window.synorive.engine.onStateChanged((s) => {
      setEngine(s);
      setEnginePort(s.lifecycle === 'ready' || s.lifecycle === 'degraded' ? s.port : null);
    });

    const offEvent = window.synorive.engine.onEvent((raw) => {
      const ev = raw as { type?: string };
      if (ev?.type === 'ui.focus-search') focusSearch();
      if (ev?.type === 'ui.open-settings') setPage('settings');
    });

    return () => {
      alive = false;
      offSettings();
      offTheme();
      offEngine();
      offEvent();
    };
  }, [setSettings, setSystemTheme, setEngine, setReady, focusSearch, setPage]);

  // 主题 / 字体方案 / 护眼 / 密度 一律写到 <html> 的 data-*，
  // 不在组件里改样式 —— 切换是一次属性写入，零重渲染，零掉帧。
  useEffect(() => {
    if (settings) applyAll(settings, theme);
  }, [settings, theme]);

  return (
    <div className="shell">
      <TopBar />
      <SideBar />
      <main className="main">
        {/* 引擎起不来时，把"缺什么怎么补"摊在正中间，
            而不是让用户对着一个状态栏里的「引擎启动失败」发呆。
            设置页不拦 —— 那页不依赖引擎，而且用户可能想先改数据目录。 */}
        {engine?.lifecycle === 'failed' && page !== 'settings' ? (
          <EngineSetup />
        ) : (
          <Router page={page} />
        )}
      </main>
      <StatusBar />
      {/* E13：放在最外层，任何页面都能唤起；它自己判断 open 决定渲不渲染 */}
      <CommandPalette />
    </div>
  );
}

/**
 * 路由。没上路由库 —— 五个页面、无嵌套、无 URL 需求，
 * 引一个库进来只会多一层要维护的东西。
 */
const PAGES: Record<PageId, ComponentType> = {
  search: SearchPage,
  library: LibraryPage,
  analyze: AnalyzePage,
  timeline: TimelinePage,
  graph: GraphPage,
  research: ResearchPage,
  settings: SettingsPage,
};

function Router({ page }: { page: PageId }) {
  const Comp = PAGES[page] ?? SearchPage;
  return <Comp />;
}
