import { lazy, Suspense, useEffect, useState, type ComponentType } from 'react';
import { ClipboardPeek } from './components/ClipboardPeek';
import { EngineSetup } from './components/EngineSetup';
import { SideBar } from './components/SideBar';
import { CommandPalette } from './components/CommandPalette';
import { Onboarding } from './components/Onboarding';
import { StatusBar } from './components/StatusBar';
import { TopBar } from './components/TopBar';
import { SearchPage } from './pages/SearchPage';
import { api, setEnginePort } from './lib/api';
import { projectApi } from './lib/webApi';
import { useApp, useResolvedTheme, type PageId } from './lib/store';
import { initPerf, recordEngineState } from './lib/perf';
import { applyAll } from './lib/theme';
import './styles/global.css';
import './styles/shell.css';
import './styles/search.css';
import './styles/pages.css';
import './styles/research.css';
import './styles/lab.css';
// ⚠️ 这两个必须排在最后。layout.css 里的密度规则和 stage.css 里的舞台样式
//    是要**覆盖**前面各页面自己写的间距的，同优先级下靠先后顺序决定谁赢。
//    往上挪一行，密度开关会重新变成"什么都不做"。
import './styles/layout.css';
import './styles/stage.css';
import './styles/compose.css';
import './styles/project.css';

/**
 * N7：随手研究浮窗和主窗口共用同一个渲染包，靠 hash 区分。
 *
 * 为它单开一个 Vite 入口是更"标准"的做法，但代价是多一份构建产物、
 * 多一套要维护的 HTML 和资源路径 —— 而这个浮窗一共就一个组件。
 * hash 判定在**模块顶层**做一次，不放进组件：它一辈子不会变，
 * 放进组件等于每次渲染都重新读一遍 location。
 */
const IS_PEEK = typeof window !== 'undefined' && window.location.hash.startsWith('#peek');

export default function App() {
  if (IS_PEEK) return <ClipboardPeek />;
  return <MainApp />;
}

function MainApp() {
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

  // 首屏画完之后，空闲时把其余七页的分片预取回来
  usePrefetchPages();

  // ── 开机接线：拉设置、拉引擎状态、订阅变化 ────────────────
  useEffect(() => {
    let alive = true;

    // C6：长任务观测要**尽早**装上。装晚了的话，启动期间那几次最严重的
    // 主线程阻塞（正是用户最能感知到的"点开半天没反应"）全都漏掉了
    initPerf();

    void (async () => {
      const [s, sysTheme, eng] = await Promise.all([
        window.synorive.settings.get(),
        window.synorive.theme.getSystem(),
        window.synorive.engine.getState(),
      ]);
      if (!alive) return;
      setSettings(s);
      // A2/A3：首次拿到设置时落地"启动落在哪一页"和"输入框默认什么意图"。
      //
      // 🔴 **只在这里做一次，绝不放进 settings 变化的订阅里。**
      //    放进订阅的话，用户在设置页改一下密度，页面就会被弹回今日页 ——
      //    那是最让人恼火的一类"自作主张"，而且完全看不出是谁干的。
      if (s.startPage && s.startPage !== 'search') setPage(s.startPage);
      if (s.defaultInputMode) useApp.getState().setInputMode(s.defaultInputMode);
      setSystemTheme(sysTheme);
      setEngine(eng);
      recordEngineState(eng?.lifecycle);
      // 引擎端口是启动时动态分配的，不能写死
      setEnginePort(eng?.port ?? null);
      setReady(true);
    })();

    const offSettings = window.synorive.settings.onChanged(setSettings);
    const offTheme = window.synorive.theme.onSystemChanged(setSystemTheme);
    const offEngine = window.synorive.engine.onStateChanged((s) => {
      setEngine(s);
      // E3 冷启动 / E8 掉线次数都从这里出。放在 setEngine 之后是为了
      // 让"界面已经知道它 ready 了"这一刻和记录的时刻对齐
      recordEngineState(s.lifecycle);
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

  // A5：把当前项目的**名字**拉回来。id 存在设置里，但成稿标题、监控标签、
  // 今日页都要显示名字 —— 三处各拉一次是浪费，而且启动那一瞬间会先后闪。
  //
  // 🔴 拉失败时**清成 null 而不是保留上一个名字**：项目可能已经被删了，
  //    继续显示一个不存在的项目名，会让用户以为东西还归在那儿。
  const activeProjectId = settings?.activeProjectId ?? null;
  const setActiveProjectName = useApp((s) => s.setActiveProjectName);
  useEffect(() => {
    if (!activeProjectId || engine?.lifecycle !== 'ready') {
      setActiveProjectName(null);
      return;
    }
    let alive = true;
    void projectApi
      .get(activeProjectId)
      .then((p) => alive && setActiveProjectName(p.title || p.query || null))
      .catch(() => alive && setActiveProjectName(null));
    return () => {
      alive = false;
    };
  }, [activeProjectId, engine?.lifecycle, setActiveProjectName]);

  // F5：库里有多少条内容。**问失败时保持 null 而不是当成 0** ——
  // 当成 0 会给一个已经用了半年的用户弹首次引导，那比不弹糟得多
  const [itemCount, setItemCount] = useState<number | null>(null);
  useEffect(() => {
    if (engine?.lifecycle !== 'ready') return;
    let alive = true;
    api
      .stats()
      .then((s) => alive && setItemCount(Number(s.items ?? 0)))
      .catch(() => {
        /* 问不到就不判断，见上面的注释 */
      });
    return () => {
      alive = false;
    };
  }, [engine?.lifecycle]);

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
      {/* F5 首次引导。**判"首次"用的是库里有没有内容**，不是有没有配置文件 ——
          配置被删了但库里有一万条的用户，不该再看一遍引导。
          itemCount 为 null 时它自己不显示（还没问出来，先别判断） */}
      <Onboarding
        itemCount={itemCount}
        onAddFolder={() => {
          void (async () => {
            const dirs = await window.synorive.sys.pickFolders();
            if (dirs.length) {
              await api.ingest({ targets: dirs, source: 'file', recursive: true });
            }
          })();
        }}
        onGoSearch={() => {
          setPage('search');
          focusSearch();
        }}
        onGoPrivacy={() => setPage('settings')}
      />
    </div>
  );
}

/**
 * 路由。没上路由库 —— 八个页面、无嵌套、无 URL 需求，
 * 引一个库进来只会多一层要维护的东西。
 *
 * ── 为什么只有搜索页是静态导入 ────────────────────────────
 * 其余七页走 `lazy()`，各自切成独立的分片，打开软件时不下载也不解析。
 * 搜索页是默认落地页，把它也切出去等于给最常走的那条路多加一次
 * 网络（这里是磁盘）往返 —— 首屏反而变慢。
 *
 * 🔴 **`lazy()` 必须在模块顶层调用，绝不能放进组件体内。**
 *    放进去的话每次渲染都会造一个新的 lazy 组件，React 认不出它是
 *    同一个，于是每次切页都把整页卸载重挂 —— 表现是页面状态莫名丢失，
 *    而且不报任何错。
 */
const TodayPage = lazy(() => import('./pages/TodayPage').then((m) => ({ default: m.TodayPage })));
const LibraryPage = lazy(() =>
  import('./pages/LibraryPage').then((m) => ({ default: m.LibraryPage })),
);
const AnalyzePage = lazy(() =>
  import('./pages/AnalyzePage').then((m) => ({ default: m.AnalyzePage })),
);
const TimelinePage = lazy(() =>
  import('./pages/TimelinePage').then((m) => ({ default: m.TimelinePage })),
);
const GraphPage = lazy(() => import('./pages/GraphPage').then((m) => ({ default: m.GraphPage })));
const ResearchPage = lazy(() =>
  import('./pages/ResearchPage').then((m) => ({ default: m.ResearchPage })),
);
const SettingsPage = lazy(() =>
  import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })),
);

const PAGES: Record<PageId, ComponentType> = {
  today: TodayPage,
  search: SearchPage,
  library: LibraryPage,
  analyze: AnalyzePage,
  timeline: TimelinePage,
  graph: GraphPage,
  research: ResearchPage,
  settings: SettingsPage,
};

/**
 * 空闲时把其余页面悄悄预取回来。
 *
 * 只做代码切分的话，第一次点某一页会有一段可见的空白 —— 那是"变慢了"的观感，
 * 哪怕启动其实快了。预取放在 `requestIdleCallback` 里：主线程闲下来才做，
 * 抢不到用户正在等的任何一帧；等用户真去点的时候分片已经在内存里，零等待。
 */
function usePrefetchPages(): void {
  useEffect(() => {
    const idle =
      typeof requestIdleCallback === 'function'
        ? requestIdleCallback
        : (cb: () => void) => setTimeout(cb, 1200);
    const handle = idle(() => {
      void import('./pages/TodayPage');
      void import('./pages/LibraryPage');
      void import('./pages/AnalyzePage');
      void import('./pages/TimelinePage');
      void import('./pages/GraphPage');
      void import('./pages/ResearchPage');
      void import('./pages/SettingsPage');
    });
    return () => {
      if (typeof cancelIdleCallback === 'function' && typeof handle === 'number') {
        cancelIdleCallback(handle);
      }
    };
  }, []);
}

function Router({ page }: { page: PageId }) {
  const Comp = PAGES[page] ?? SearchPage;
  return (
    <Suspense fallback={<PageSkeleton />}>
      <Comp />
    </Suspense>
  );
}

/** 分片还在读的那一瞬间垫一下。不是转圈 —— 转圈会让人觉得"卡住了" */
function PageSkeleton() {
  return (
    <div className="page">
      <div className="page__body syn-stack">
        <div className="syn-skel syn-skel--line syn-skel--title" />
        <div className="syn-skel syn-skel--line" />
        <div className="syn-skel syn-skel--line syn-skel--short" />
      </div>
    </div>
  );
}
