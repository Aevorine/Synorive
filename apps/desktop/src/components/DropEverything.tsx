import { useCallback, useEffect, useRef, useState } from 'react';
import { FolderPlus, Images, Loader2, Search as SearchIcon, Undo2 } from 'lucide-react';
import { api } from '../lib/api';
import { useApp, type PageId } from '../lib/store';
import { useSearch } from '../lib/useSearch';
import { history } from '../lib/undo';

/**
 * C8 拖拽万物
 * ============================================================
 * 把任何东西拖进窗口的任何位置，**松手的位置决定它被怎么处理**：
 *
 *   上 1/3   加进资料库（索引）
 *   中 1/3   拿它去搜（文字 → 本地检索；图片 → 以图搜图）
 *   下 1/3   拿去联网深挖
 *
 * 🔴 **拿本机路径必须走 preload 的 `webUtils.getPathForFile`。**
 *    Electron 32 起 `File.path` 被移除了，直接读拿到的是 `undefined`
 *    **而且不报错** —— 表现成"拖进来什么都没发生"，能查半天。
 *    这条在 `OmniFeed` / `AskStage` / `CompareView` 里都栽过，这里沿用同一个修法。
 *
 * 🔴 **入库是延迟提交的，不是松手就干。**
 *    检索和以图搜图都是纯读操作，撤销就是把上一个查询词填回去，零代价；
 *    但入库会往库里写东西，而"写进去了再删"和"根本没写"对用户不是一回事。
 *    所以入库先进一个 6 秒的待提交状态：倒计时条摆在屏幕正中，
 *    一个大大的「撤销」按钮，Ctrl+Z 也认。**误拖在这 6 秒里是完全无痕的。**
 *
 * 🔴 **过了 6 秒之后的撤销不假装能撤回。** 它会照实说"已经开始入库了"，
 *    并把用户送到文件管理器 —— 那里删掉的东西会进 30 天回收站。
 *    给一个点了什么都不发生的撤销按钮，比没有撤销按钮更糟。
 *
 * 🔴 **`dragleave` 只在 `relatedTarget === null` 时才算真的离开窗口。**
 *    不判这条的话，鼠标从一个元素划到另一个元素就会把高亮层闪掉一次。
 */

/** 入库的反悔窗口。太短来不及点，太长会让人以为卡住了 */
const COMMIT_DELAY_MS = 6000;

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|tiff?|heic|avif)$/i;

type ZoneId = 'ingest' | 'search' | 'research';

const ZONES: { id: ZoneId; title: string; hint: string; icon: typeof FolderPlus }[] = [
  { id: 'ingest', title: '加进资料库', hint: '分析进库，以后能搜到它', icon: FolderPlus },
  { id: 'search', title: '拿它去搜', hint: '文字 → 本地检索；图片 → 以图搜图', icon: SearchIcon },
  { id: 'research', title: '联网深挖', hint: '送到研究工作台，抓正文出简报', icon: Images },
];

/** 拿本机路径。**必须走 preload**，见文件头 */
function pathsOf(files: File[]): string[] {
  return files
    .map((f) => {
      try {
        return window.synorive.sys.pathForFile(f);
      } catch {
        return '';
      }
    })
    .filter(Boolean);
}

function zoneAt(clientY: number): ZoneId {
  const h = window.innerHeight || 1;
  if (clientY < h / 3) return 'ingest';
  if (clientY < (h * 2) / 3) return 'search';
  return 'research';
}

interface Pending {
  paths: string[];
  /** setTimeout 句柄 */
  timer: number;
  /** 提交时刻，用来算倒计时 */
  at: number;
  committed: boolean;
}

export function DropEverything() {
  const [dragging, setDragging] = useState(false);
  const [zone, setZone] = useState<ZoneId>('search');
  /** 拖的是文件还是一段选中的文字 —— 提示语要说的不是同一件事 */
  const [kind, setKind] = useState<'files' | 'text'>('files');
  const [toast, setToast] = useState<string | null>(null);
  const [countdown, setCountdown] = useState(0);

  const pending = useRef<Pending | null>(null);
  /** dragenter/leave 在子元素上会反复触发，用计数器判"真的离开了整个窗口" */
  const depth = useRef(0);
  /**
   * 这次拖动是不是**界面自己的拖动**（侧栏换顺序、卡片排序之类）。
   *
   * 🔴 不区分的话，拖着侧栏图标换个位置会顺手触发一次检索 ——
   *    因为那种拖动的 `text/plain` 里放的是页面 id，在这一层看起来
   *    和"用户拖了一段文字进来"一模一样。
   *    判据是拖动源身上有没有 `draggable="true"`：那是界面自己挂的；
   *    而**选中文字**的拖动源不会有这个属性 —— 那一种恰恰要处理。
   */
  const uiDrag = useRef(false);

  const setPage = useApp((s) => s.setPage);

  const say = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 4200);
  }, []);

  // ── 入库：延迟提交 + 撤销 ────────────────────────────
  const cancelPending = useCallback((): boolean => {
    const p = pending.current;
    if (!p || p.committed) return false;
    window.clearTimeout(p.timer);
    pending.current = null;
    setCountdown(0);
    return true;
  }, []);

  const queueIngest = useCallback(
    (paths: string[]) => {
      // 上一批还在等就先把它提交掉，不然两批会互相顶掉计时器
      const prev = pending.current;
      if (prev && !prev.committed) {
        window.clearTimeout(prev.timer);
        void api.ingest({ targets: prev.paths, source: 'file', recursive: true, priority: 'high' });
        prev.committed = true;
      }

      const timer = window.setTimeout(() => {
        const p = pending.current;
        if (!p) return;
        p.committed = true;
        setCountdown(0);
        void api
          .ingest({ targets: p.paths, source: 'file', recursive: true, priority: 'high' })
          .then(() => say(`已开始分析 ${p.paths.length} 个文件，去「分析中心」看进度`))
          .catch((e: Error) => say(`入库没成功：${e.message}`));
      }, COMMIT_DELAY_MS);

      pending.current = { paths, timer, at: Date.now(), committed: false };
      setCountdown(Math.ceil(COMMIT_DELAY_MS / 1000));

      /**
       * 挂到全局撤销栈上。Ctrl+Z 和顶栏那个撤销按钮走的是同一条路。
       *
       * 🔴 撤销动作**必须幂等**（用户会连点两下），而且**过期之后要说实话**。
       */
      history.push({
        label: `把 ${paths.length} 个文件加进资料库`,
        undo: () => {
          if (cancelPending()) {
            say('已取消，一个文件都没有进库');
            return;
          }
          say('这批文件已经开始入库了，撤不回来。要删的话去「文件管理器」，删掉的会进 30 天回收站。');
          setPage('library');
        },
      });
    },
    [cancelPending, say, setPage],
  );

  // 倒计时只是显示，真正的提交由上面那个 setTimeout 负责 ——
  // 两边各算一次时间的话，显示 0 了但还没提交（或反过来）会很难解释
  useEffect(() => {
    if (countdown <= 0) return;
    const t = window.setInterval(() => {
      const p = pending.current;
      if (!p || p.committed) {
        setCountdown(0);
        return;
      }
      setCountdown(Math.max(0, Math.ceil((COMMIT_DELAY_MS - (Date.now() - p.at)) / 1000)));
    }, 250);
    return () => window.clearInterval(t);
  }, [countdown]);

  // ── 三种落点各自的动作 ──────────────────────────────
  const runSearch = useCallback(
    (text: string) => {
      const before = {
        query: useSearch.getState().query,
        page: useApp.getState().page,
        mode: useApp.getState().inputMode,
      };
      useApp.getState().setInputMode('find');
      setPage('search');
      useSearch.getState().setQuery(text);
      history.push({
        label: `搜「${text.slice(0, 18)}」`,
        undo: () => {
          useSearch.getState().setQuery(before.query);
          useApp.getState().setInputMode(before.mode);
          setPage(before.page as PageId);
        },
        redo: () => {
          setPage('search');
          useSearch.getState().setQuery(text);
        },
      });
      say(`已经拿这段文字去搜了（Ctrl+Z 撤销）`);
    },
    [say, setPage],
  );

  const runImage = useCallback(
    (path: string) => {
      const before = useApp.getState().page;
      setPage('analyze');
      // 交给「一图四路」那块自己去跑 —— 它已经会处理四路各自的失败，
      // 在这里再写一份判失败的逻辑迟早会和那边分叉
      window.dispatchEvent(new CustomEvent('syn:image-lanes', { detail: { path } }));
      history.push({
        label: '以图搜图',
        undo: () => setPage(before as PageId),
      });
      say('正在用这张图搜：像不像库里已有的 / 图里写了什么字 / 网上还有哪里有');
    },
    [say, setPage],
  );

  const runResearch = useCallback(
    (text: string) => {
      const before = useApp.getState().page;
      setPage('research');
      window.dispatchEvent(new CustomEvent('syn:research-prefill', { detail: { text } }));
      history.push({
        label: '送去研究工作台',
        undo: () => setPage(before as PageId),
      });
      say('已经送到研究工作台，按回车开始（不会自动发请求 —— 联网检索有时间成本）');
    },
    [say, setPage],
  );

  // ── 窗口级拖拽监听 ──────────────────────────────────
  useEffect(() => {
    const stop = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
    };

    const onDragStart = (e: DragEvent) => {
      const t = e.target as HTMLElement | null;
      uiDrag.current = !!t?.closest?.('[draggable="true"]');
    };
    const onDragEnd = () => {
      uiDrag.current = false;
      depth.current = 0;
      setDragging(false);
    };

    const onEnter = (e: DragEvent) => {
      if (uiDrag.current) return;
      stop(e);
      depth.current += 1;
      setKind(e.dataTransfer?.types.includes('Files') ? 'files' : 'text');
      setDragging(true);
    };

    const onOver = (e: DragEvent) => {
      if (uiDrag.current) return;
      stop(e);
      setZone(zoneAt(e.clientY));
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    };

    const onLeave = (e: DragEvent) => {
      if (uiDrag.current) return;
      stop(e);
      depth.current -= 1;
      // relatedTarget 为 null 才是真的离开了窗口，见文件头
      if (depth.current <= 0 || e.relatedTarget === null) {
        depth.current = 0;
        setDragging(false);
      }
    };

    const onDrop = (e: DragEvent) => {
      if (uiDrag.current) {
        uiDrag.current = false;
        return;
      }
      stop(e);
      depth.current = 0;
      setDragging(false);
      const dt = e.dataTransfer;
      if (!dt) return;
      const where = zoneAt(e.clientY);

      const files = Array.from(dt.files ?? []);
      if (files.length) {
        const paths = pathsOf(files);
        if (!paths.length) {
          // 🔴 拿不到路径要说出来。静默返回就是那个查了半天的"拖进来什么都没发生"
          say('拿不到这些文件在硬盘上的位置 —— 从压缩包或浏览器里直接拖出来的文件不行，先存到本地再拖。');
          return;
        }
        if (where === 'search') {
          const img = paths.find((p) => IMAGE_EXT.test(p));
          if (img) {
            runImage(img);
            return;
          }
          // 不是图片就没法"拿它去搜"，退回入库并说清楚为什么
          say('这不是图片，没法以图搜图 —— 改成加进资料库了（还能撤销）。');
          queueIngest(paths);
          return;
        }
        if (where === 'research') {
          say('联网深挖要的是一句话或一个网址，不是文件 —— 改成加进资料库了（还能撤销）。');
          queueIngest(paths);
          return;
        }
        queueIngest(paths);
        return;
      }

      const text = (dt.getData('text/uri-list') || dt.getData('text/plain') || '').trim();
      if (!text) return;
      if (where === 'research') runResearch(text);
      else if (where === 'ingest') {
        // 一段文字没法"入库"成文件；当成网址就读它，否则退回搜索
        if (/^https?:\/\/\S+$/i.test(text)) runResearch(text);
        else {
          say('拖进来的是一段文字，没法直接入库 —— 改成搜它了。');
          runSearch(text);
        }
      } else runSearch(text);
    };

    window.addEventListener('dragstart', onDragStart, true);
    window.addEventListener('dragend', onDragEnd, true);
    window.addEventListener('dragenter', onEnter);
    window.addEventListener('dragover', onOver);
    window.addEventListener('dragleave', onLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragstart', onDragStart, true);
      window.removeEventListener('dragend', onDragEnd, true);
      window.removeEventListener('dragenter', onEnter);
      window.removeEventListener('dragover', onOver);
      window.removeEventListener('dragleave', onLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [queueIngest, runImage, runResearch, runSearch, say]);

  return (
    <>
      {dragging && (
        <div className="syn-drop" aria-hidden>
          <div className="syn-drop__what">
            {kind === 'files' ? '松手放下文件' : '松手放下这段文字'}
            <span className="syn-drop__sub">松在哪一格，就按哪一格处理</span>
          </div>
          {ZONES.map((z) => {
            const Icon = z.icon;
            return (
              <div
                key={z.id}
                className={`syn-drop__zone${zone === z.id ? ' syn-drop__zone--on' : ''}`}
              >
                <Icon size={22} strokeWidth={1.6} />
                <span className="syn-drop__title">{z.title}</span>
                <span className="syn-drop__hint">{z.hint}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* 待提交的入库：倒计时 + 一个大按钮。
          🔴 它必须**盖在最上层且不可被忽略** —— 一个藏在角落里的撤销
             等于没有撤销，那正是"误拖造成不可逆后果"的实际发生方式 */}
      {countdown > 0 && (
        <div className="syn-drop__pending" role="status" aria-live="polite">
          <Loader2 size={14} className="spin" aria-hidden />
          <span>
            {countdown} 秒后开始把 {pending.current?.paths.length ?? 0} 个文件加进资料库
          </span>
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => {
              if (cancelPending()) say('已取消，一个文件都没有进库');
            }}
            title="现在取消，一个文件都不会进库"
          >
            <Undo2 size={13} strokeWidth={1.8} aria-hidden /> 撤销
          </button>
        </div>
      )}

      {toast && (
        <div className="syn-drop__toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
    </>
  );
}
