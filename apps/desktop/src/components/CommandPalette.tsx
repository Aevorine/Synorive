/**
 * E13 命令面板
 * ============================================================
 * Ctrl+Shift+P 打开，敲几个字直接执行。
 *
 * 为什么不用 Ctrl+K：这个应用的 Ctrl+K 和 `/` 已经给了全局搜索框（验收标准 B5）。
 * 抢过来会毁掉一个用得更频繁的快捷键 —— 命令面板是"偶尔用一次"，
 * 搜索框是"一直在用"。
 *
 * 🔑 中文标签必须支持拼音首字母。
 *    「文件管理器」这种标签，用户不可能切到中文输入法再打全名去找一个命令 ——
 *    那比直接用鼠标点还慢，功能就废了。所以每条命令手工标了 py（wjglq）。
 *    手工写二十来条的成本，远低于引一个拼音库。
 *
 * ⚠️ 列表项用 onMouseMove 而不是 onMouseEnter 接管选中：
 *    按快捷键唤起时，光标常常正停在列表将要出现的位置，enter 会立刻触发、
 *    把选中项从第一条抢走 —— 用户根本没动鼠标，却发现选中的不是他以为的那条。
 *    move 只在真的移动了才接管。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Clipboard, Command, FolderPlus, FilePlus2, Gauge, Moon, RefreshCw,
  Search as SearchIcon, Settings as SettingsIcon, Sun, Trash2,
} from 'lucide-react';
import { PAGE_TITLES, useApp, type PageId } from '../lib/store';
import { useSearch } from '../lib/useSearch';
import { api } from '../lib/api';

interface Cmd {
  id: string;
  label: string;
  /** 拼音首字母，中文标签必填 */
  py: string;
  hint?: string;
  group: string;
  icon: typeof Command;
  run: () => void | Promise<void>;
  /** 条件不满足时置灰并说明原因，而不是藏起来 —— 藏起来用户会以为没这功能 */
  disabledReason?: string;
}

/**
 * 模糊匹配：子序列即可（打 wjgl 能命中 wjglq）。
 * 返回 null 表示不匹配，数字越小越靠前。
 */
function score(q: string, cmd: Cmd): number | null {
  const s = q.trim().toLowerCase();
  if (!s) return 0;

  const label = cmd.label.toLowerCase();
  const py = cmd.py.toLowerCase();
  const hint = (cmd.hint ?? '').toLowerCase();

  if (label.startsWith(s)) return 0;
  if (py.startsWith(s)) return 1;
  if (label.includes(s)) return 2;
  if (py.includes(s)) return 3;
  if (hint.includes(s)) return 5;

  // 子序列：把 py 当成一串首字母，允许跳着打
  let i = 0;
  for (const ch of py) {
    if (ch === s[i]) i++;
    if (i === s.length) return 4;
  }
  return null;
}

export function CommandPalette() {
  const open = useApp((s) => s.commandPaletteOpen);
  const setOpen = useApp((s) => s.setCommandPaletteOpen);
  const setPage = useApp((s) => s.setPage);
  const settings = useApp((s) => s.settings);
  const engine = useApp((s) => s.engine);
  const focusSearch = useApp((s) => s.focusSearch);
  const setPreset = useSearch((s) => s.setPreset);
  const toggleExplain = useSearch((s) => s.toggleExplain);

  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const ready = engine?.lifecycle === 'ready';

  const commands = useMemo<Cmd[]>(() => {
    const pages: Array<[PageId, string]> = [
      ['search', 'ss'], ['library', 'wjglq'], ['analyze', 'fxzx'],
      ['timeline', 'sjz'], ['graph', 'zsddtp'], ['research', 'yjgzt'], ['settings', 'sz'],
    ];
    const list: Cmd[] = pages.map(([id, py]) => ({
      id: `go:${id}`,
      label: `转到${PAGE_TITLES[id]}`,
      py: `zd${py}`,
      group: '跳转',
      icon: id === 'settings' ? SettingsIcon : SearchIcon,
      run: () => setPage(id),
    }));

    list.push(
      {
        id: 'ingest:folder',
        label: '索引一个文件夹',
        py: 'sywjj',
        hint: '选个目录，里面的文件全部分析进库',
        group: '投喂',
        icon: FolderPlus,
        disabledReason: ready ? undefined : '引擎还没就绪',
        run: async () => {
          const dirs = await window.synorive.sys.pickFolders();
          if (dirs.length) await api.ingest({ targets: dirs, source: 'file', recursive: true });
        },
      },
      {
        id: 'ingest:files',
        label: '分析几个文件',
        py: 'fxwj',
        group: '投喂',
        icon: FilePlus2,
        disabledReason: ready ? undefined : '引擎还没就绪',
        run: async () => {
          const files = await window.synorive.sys.pickFiles();
          if (files.length) await api.ingest({ targets: files, source: 'file', recursive: false });
        },
      },
      {
        id: 'search:focus',
        label: '跳到搜索框',
        py: 'tdssk',
        hint: '也可以直接按 / 或 Ctrl+K',
        group: '检索',
        icon: SearchIcon,
        run: () => focusSearch(),
      },
      {
        id: 'preset:balanced', label: '排序改为「均衡」', py: 'pxjh',
        group: '检索', icon: Gauge, run: () => setPreset('balanced'),
      },
      {
        id: 'preset:precise', label: '排序改为「求准」', py: 'pxqz',
        hint: '关键词为主，语义只做补充', group: '检索', icon: Gauge,
        run: () => setPreset('precise'),
      },
      {
        id: 'preset:semantic', label: '排序改为「求全」', py: 'pxqq',
        hint: '语义为主，说法不同也能匹配', group: '检索', icon: Gauge,
        run: () => setPreset('semantic'),
      },
      {
        id: 'preset:recent', label: '排序改为「最近优先」', py: 'pxzj',
        group: '检索', icon: Gauge, run: () => setPreset('recent'),
      },
      {
        id: 'search:explain', label: '显示/隐藏排序理由', py: 'xspxly',
        group: '检索', icon: Gauge, run: () => toggleExplain(),
      },
      {
        id: 'theme:toggle',
        label: settings?.theme === 'dark' ? '切到浅色' : '切到深色',
        py: settings?.theme === 'dark' ? 'qdqs' : 'qdss',
        group: '外观',
        icon: settings?.theme === 'dark' ? Sun : Moon,
        run: async () => {
          await window.synorive.settings.patch({
            theme: settings?.theme === 'dark' ? 'light' : 'dark',
          });
        },
      },
      {
        id: 'theme:system', label: '主题跟随系统', py: 'ztgsxt',
        group: '外观', icon: Sun,
        run: async () => { await window.synorive.settings.patch({ theme: 'system' }); },
      },
      {
        id: 'clip:toggle',
        label: settings?.clipboardSentinel ? '关掉剪贴板哨兵' : '打开剪贴板哨兵',
        py: settings?.clipboardSentinel ? 'gdjtbsb' : 'dkjtbsb',
        group: '剪贴板',
        icon: Clipboard,
        run: async () => {
          await window.synorive.settings.patch({
            clipboardSentinel: !settings?.clipboardSentinel,
          });
        },
      },
      {
        id: 'clip:clear', label: '清空剪贴板暂存', py: 'qkjtb',
        group: '剪贴板', icon: Trash2, run: () => window.synorive.clip.clear(),
      },
      {
        id: 'engine:restart', label: '重启引擎', py: 'cqyq',
        hint: '引擎卡住或报错时用', group: '引擎', icon: RefreshCw,
        run: () => window.synorive.engine.restart(),
      },
      {
        id: 'open:data', label: '打开数据目录', py: 'dksjml',
        group: '引擎', icon: FolderPlus,
        disabledReason: settings?.dataDir ? undefined : '还没读到设置',
        run: async () => { if (settings?.dataDir) await window.synorive.sys.openPath(settings.dataDir); },
      },
    );
    return list;
  }, [setPage, focusSearch, setPreset, toggleExplain, settings, ready]);

  const matched = useMemo(() => {
    const withScore = commands
      .map((c) => ({ c, s: score(q, c) }))
      .filter((x): x is { c: Cmd; s: number } => x.s !== null);
    withScore.sort((a, b) => a.s - b.s);
    return withScore.map((x) => x.c);
  }, [commands, q]);

  // 打开时重置。不重置的话上次的搜索词还留着，
  // 用户按下快捷键看到的是一份被过滤过的列表，会以为命令少了。
  useEffect(() => {
    if (open) {
      setQ('');
      setSel(0);
      // 等面板真的挂上去再抢焦点，否则 focus 会打在还没渲染的节点上
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => setSel(0), [q]);

  // 选中项滚进视野。用 block:'nearest' 避免每次都把列表跳到中间
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>('[data-sel="1"]');
    el?.scrollIntoView({ block: 'nearest' });
  }, [sel]);

  if (!open) return null;

  const exec = async (c: Cmd) => {
    if (c.disabledReason) return;
    setOpen(false);
    await c.run();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((i) => (matched.length ? (i + 1) % matched.length : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((i) => (matched.length ? (i - 1 + matched.length) % matched.length : 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const c = matched[sel];
      if (c) void exec(c);
    }
  };

  let lastGroup = '';

  return (
    <div className="palette__backdrop" onMouseDown={() => setOpen(false)}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label="命令面板">
        <div className="palette__inputrow">
          <Command size={16} strokeWidth={1.7} className="palette__glyph" />
          <input
            ref={inputRef}
            className="palette__input"
            value={q}
            placeholder="敲命令名或拼音首字母，比如 wjglq"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            spellCheck={false}
          />
          <kbd className="palette__kbd">Esc</kbd>
        </div>

        <div className="palette__list" ref={listRef}>
          {matched.length === 0 && (
            <div className="palette__empty">没有匹配的命令</div>
          )}
          {matched.map((c, i) => {
            const head = c.group !== lastGroup ? ((lastGroup = c.group), c.group) : null;
            const Icon = c.icon;
            return (
              <div key={c.id}>
                {head && <div className="palette__group">{head}</div>}
                <button
                  className={`palette__item${i === sel ? ' palette__item--sel' : ''}${
                    c.disabledReason ? ' palette__item--off' : ''
                  }`}
                  data-sel={i === sel ? '1' : '0'}
                  onMouseMove={() => setSel(i)}
                  onClick={() => void exec(c)}
                  disabled={!!c.disabledReason}
                >
                  <Icon size={15} strokeWidth={1.7} className="palette__icon" />
                  <span className="palette__label">{c.label}</span>
                  {c.hint && !c.disabledReason && <span className="palette__hint">{c.hint}</span>}
                  {c.disabledReason && <span className="palette__hint">{c.disabledReason}</span>}
                </button>
              </div>
            );
          })}
        </div>

        <div className="palette__foot">
          <kbd className="palette__kbd">↑↓</kbd> 选择
          <kbd className="palette__kbd">Enter</kbd> 执行
          <span className="palette__footnote">{matched.length} / {commands.length} 条命令</span>
        </div>
      </div>
    </div>
  );
}
