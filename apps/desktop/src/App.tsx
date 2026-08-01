import { useEffect } from 'react';
import { SideBar } from './components/SideBar';
import { StatusBar } from './components/StatusBar';
import { TopBar } from './components/TopBar';
import { SearchPage } from './pages/SearchPage';
import { setEnginePort } from './lib/api';
import { PAGE_TITLES, useApp, useResolvedTheme } from './lib/store';
import { applyAll } from './lib/theme';
import './styles/global.css';
import './styles/shell.css';
import './styles/search.css';

export default function App() {
  const settings = useApp((s) => s.settings);
  const setSettings = useApp((s) => s.setSettings);
  const setEngine = useApp((s) => s.setEngine);
  const setSystemTheme = useApp((s) => s.setSystemTheme);
  const setReady = useApp((s) => s.setReady);
  const focusSearch = useApp((s) => s.focusSearch);
  const setPage = useApp((s) => s.setPage);
  const page = useApp((s) => s.page);
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
        {page === 'search' ? <SearchPage /> : <Page title={PAGE_TITLES[page]} />}
      </main>
      <StatusBar />
    </div>
  );
}

/**
 * 一期只出骨架：每个页面先立好标题与空状态，
 * 二期起往里填真实功能。空状态一定要告诉人下一步做什么，
 * 而不是一片空白。
 */
function Page({ title }: { title: string }) {
  const page = useApp((s) => s.page);
  const engine = useApp((s) => s.engine);

  const HINTS: Record<string, string> = {
    search: '索引建好之后，在上面的搜索框里敲字就能搜。支持中文语义搜索——描述内容也能搜到，不用记文件名。',
    library: '这里会列出全部已索引的内容，可以按类型、时间、来源、标签筛选。',
    analyze: '把文件、图片、视频或链接拖进窗口，就会开始分析。分析全程在独立进程里跑，界面不会卡。',
    timeline: '所有内容会按时间铺在一条可缩放的轴上，搜索结果会高亮投影上去。',
    graph: '自动抽取的人物、地点、组织会连成一张网，点任意节点就能顺藤摸瓜。',
    settings: '主题、字体、护眼、并发度、索引目录、云端接入、隐私围栏都在这里。',
  };

  return (
    <div className="page">
      <div className="page__header">
        {/* 界面主标题：小二 24px 思源宋体 —— 用户点名的那个 */}
        <h1 className="page__title">{title}</h1>
        <span className="page__subtitle">
          {engine?.lifecycle === 'ready' ? '引擎就绪' : '等待引擎启动…'}
        </span>
      </div>
      <div className="page__body">
        <div className="empty">
          <div className="empty__title">一期骨架 · 功能施工中</div>
          <p className="empty__hint">{HINTS[page] ?? ''}</p>
        </div>
      </div>
    </div>
  );
}
