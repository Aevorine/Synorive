/**
 * C2 命令面板 v2
 * ============================================================
 * Ctrl+Shift+P（或 Ctrl+P）打开，敲几个字直接执行。
 *
 * ⚠️ **没有把它挪到 Ctrl+K。** 这个应用的 Ctrl+K 和 `/` 已经给了主输入区
 *    （验收标准 B5，`TopBar.GlobalHotkeys` 里那段注释写了原因）。
 *    命令面板是"偶尔用一次"，主输入区是"一直在用"，抢过来是净亏。
 *    v2 补的是 **Ctrl+P**（VS Code 的快速打开），少按一个键，且不抢谁的。
 *
 * v1 → v2 改了四件事：
 *
 *   ① **排序内核换掉了**。v1 只有 `pinyinMatch.fuzzyScore` 的七八个粗档，
 *      同一档里几十条命令之间没有先后 —— 打两个字之后候选顺序看起来像随机的。
 *      v2 叠了 `lib/fuzzy.ts` 的子序列匹配（连续命中加权、词首加权），
 *      同档之内也排得出来。**中文照样走拼音**，两把尺子换算到同一个量纲再取大。
 *
 *   ② **搜得到的东西多了三类**：全部设置项（`lib/settingsIndex.ts`）、
 *      最近打开的文件、最近搜过的词。原来面板只认"功能入口"，
 *      而用户真正想不起来的恰恰是"上午那个 PDF 叫什么"。
 *
 *   ③ **真的分组了**。v1 的 `lastGroup` 是顺着打分后的列表比对上一条的组名 ——
 *      而排序是按分数的，同一组会被别的组隔开，于是"跳转"这个小标题
 *      在一屏里反复出现三四次。v2 先排序、再按组首次出现的顺序聚拢。
 *
 *   ④ **记住最近用过的**，下次排前面（衰减，不是永久置顶）。
 *
 * ⚠️ 列表项用 onMouseMove 而不是 onMouseEnter 接管选中：
 *    按快捷键唤起时，光标常常正停在列表将要出现的位置，enter 会立刻触发、
 *    把选中项从第一条抢走 —— 用户根本没动鼠标，却发现选中的不是他以为的那条。
 *    move 只在真的移动了才接管。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  BellPlus, Brain, Clipboard, Clock, Command, Crop, FileDiff, FileDown, FilePlus2,
  FlaskConical, FolderPlus, Gauge, Library, Moon, RefreshCw,
  Search as SearchIcon, Settings as SettingsIcon, ShieldQuestion, Sun, Trash2,
} from 'lucide-react';
import { PAGE_TITLES, useApp, type PageId } from '../lib/store';
import { useSearch } from '../lib/useSearch';
import { api } from '../lib/api';
import { fuzzyScore } from '../lib/pinyinMatch';
import { fuzzyMatch, pinyinRankToScore } from '../lib/fuzzy';
import { SETTINGS_INDEX, requestSettingsFocus } from '../lib/settingsIndex';
import { recentFiles, rememberOpen } from '../lib/recentFiles';
import { suggest as suggestQueries } from '../lib/queryHistory';

interface Cmd {
  id: string;
  label: string;
  /**
   * 手写拼音首字母。**可选** —— 默认从标签自动算（见 lib/pinyinMatch.ts）。
   * 只在自动算的结果不好用时才写（多音字、缩写、英文命令想加个中文别名）。
   */
  py?: string;
  hint?: string;
  group: string;
  icon: typeof Command;
  run: () => void | Promise<void>;
  /** 条件不满足时置灰并说明原因，而不是藏起来 —— 藏起来用户会以为没这功能 */
  disabledReason?: string;
  /** 这条命令自己的快捷键，显示在右侧。**只写真的绑了的**，写假的比不写糟 */
  keys?: string;
}

// ────────────────────────────────────────────────────────────
// 「最近用过的排前面」
// ────────────────────────────────────────────────────────────
// 🔴 **是加权不是置顶。** 永久置顶的话，某天误点了一条几乎不用的命令，
//    它会在列表最上面待到天荒地老。这里用和 `queryHistory` 同一套
//    时间衰减：一周不用，权重折半。

const RECENT_KEY = 'syn.paletteRecent.v1';
/** 半衰期：7 天不用，加权折半 */
const RECENT_HALF_LIFE_MS = 7 * 24 * 60 * 60 * 1000;
/** 最近用过最多能加多少分。压不过一次实打实的前缀命中（那是 100+） */
const RECENT_MAX_BOOST = 22;

function readRecent(): Record<string, number> {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return {};
    const o = JSON.parse(raw) as Record<string, number>;
    return o && typeof o === 'object' ? o : {};
  } catch {
    return {};
  }
}

function markUsed(id: string): void {
  try {
    const o = readRecent();
    o[id] = Date.now();
    // 只留最近 40 条，不然这个对象会一直长
    const keep = Object.entries(o)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 40);
    localStorage.setItem(RECENT_KEY, JSON.stringify(Object.fromEntries(keep)));
  } catch {
    // 记不住只是下次排序差一点，绝不能影响这条命令本身
  }
}

function recentBoost(used: Record<string, number>, id: string, now: number): number {
  const at = used[id];
  if (!at) return 0;
  const age = Math.max(0, now - at) / RECENT_HALF_LIFE_MS;
  return RECENT_MAX_BOOST * Math.pow(0.5, age);
}

/**
 * 一条命令对当前输入的匹配分。**越大越好**，null = 不匹配。
 *
 * 两把尺子取大值：
 *   直接匹配（`lib/fuzzy.ts`）—— 英文命令、文件名、搜过的词靠它
 *   拼音匹配（`lib/pinyinMatch.ts`）—— 中文标签靠它
 * hint 和分组名也参与，但**打折**：命中说明文字不该压过命中标题。
 */
function matchScore(q: string, cmd: Cmd): number | null {
  if (!q) return 0;
  let best = -1;

  const direct = fuzzyMatch(q, cmd.label);
  if (direct) best = Math.max(best, direct.score);

  if (cmd.hint) {
    const h = fuzzyMatch(q, cmd.hint);
    if (h) best = Math.max(best, h.score * 0.55);
  }
  const g = fuzzyMatch(q, cmd.group);
  if (g) best = Math.max(best, g.score * 0.45);

  const py = pinyinRankToScore(fuzzyScore(q, cmd.label, cmd.hint));
  if (py !== null) best = Math.max(best, py);

  // 手写 py 覆盖：命中就给一个和"首字母前缀"同档的分
  if (cmd.py) {
    const s = q.trim().toLowerCase();
    const py2 = cmd.py.toLowerCase();
    if (py2.startsWith(s)) best = Math.max(best, 88);
    else if (py2.includes(s)) best = Math.max(best, 66);
  }

  return best >= 0 ? best : null;
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
  const setQuery = useSearch((s) => s.setQuery);

  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  /**
   * 面板每次打开时把「最近文件 / 最近搜索」快照一份。
   *
   * 🔴 **不能在渲染里直接读 localStorage** —— 那会让每敲一个键都去读一次盘，
   *    而且列表会在用户打字的过程中自己变（另一个窗口刚打开了个文件）。
   */
  const [recent, setRecent] = useState<{
    files: ReturnType<typeof recentFiles>;
    queries: ReturnType<typeof suggestQueries>;
    used: Record<string, number>;
  }>({ files: [], queries: [], used: {} });

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const ready = engine?.lifecycle === 'ready';

  const commands = useMemo<Cmd[]>(() => {
    const pages: Array<[PageId, string]> = [
      ['today', 'jr'], ['search', 'ss'], ['library', 'wjglq'], ['analyze', 'fxzx'],
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
      // B1/A3：主输入区的两条直达。放在「检索」组最前面 ——
      // 它们是这个软件最高频的两个动作，排在预设后面等于藏起来
      {
        id: 'stage:ask',
        label: '问一句话',
        py: 'wyjh',
        hint: '展开大输入区，回一段带出处的答案',
        group: '检索',
        icon: SearchIcon,
        keys: 'Ctrl+K',
        run: () => {
          useApp.getState().setInputMode('ask');
          useApp.getState().openStage();
        },
      },
      {
        id: 'stage:find',
        label: '找东西',
        py: 'zdx',
        hint: '展开大输入区，回结果列表',
        group: '检索',
        icon: SearchIcon,
        keys: 'Ctrl+Shift+K',
        run: () => {
          useApp.getState().setInputMode('find');
          useApp.getState().openStage();
        },
      },
      {
        id: 'search:focus',
        label: '跳到搜索框',
        py: 'tdssk',
        group: '检索',
        icon: SearchIcon,
        keys: '/',
        run: () => focusSearch(),
      },
      {
        id: 'help:shortcuts',
        label: '快捷键速查表',
        py: 'kjjscb',
        hint: '显示当前真正生效的按键，含被别的软件抢走后换用的备选键',
        group: '检索',
        icon: Command,
        keys: '?',
        // 🔴 花括号是必需的：`dispatchEvent` 返回 boolean，
        //    写成表达式体的话返回值类型对不上 `run: () => void | Promise<void>`
        run: () => {
          window.dispatchEvent(new CustomEvent('syn:shortcut-sheet'));
        },
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
        id: 'preset:deep', label: '排序改为「深读一处」', py: 'pxsdyc',
        hint: '关掉多样性，把同一个文件夹里所有相关的一次看全',
        group: '检索', icon: Gauge, run: () => setPreset('deep'),
      },
      {
        id: 'search:explain', label: '显示/隐藏排序理由', py: 'xspxly',
        group: '检索', icon: Gauge, run: () => toggleExplain(),
      },

      // ── F1：命令面板扩到研究动作 ─────────────────────
      // 以前这里只有导航和检索两组，而研究工作台上那些动作
      // （深挖、核查、导出、订阅）恰恰是**步骤多、藏得深**的那一批 ——
      // 它们比"转到设置"更需要一个键盘入口
      {
        id: 'research:deep',
        label: '把当前查询拿去深挖',
        py: 'sw',
        hint: '多轮递进搜索 + 反向核查，十几秒',
        group: '研究',
        icon: FlaskConical,
        disabledReason: ready ? undefined : '引擎还没就绪',
        run: () => {
          setPage('research');
          // 用事件而不是直接调 API：研究页自己持有"用什么档位、
          // 开不开扩写"这些状态，从外面绕过它去发请求会跑出一个
          // 和界面上显示的设置不一致的结果
          window.dispatchEvent(new CustomEvent('syn:research-run', { detail: { mode: 'deep' } }));
        },
      },
      {
        id: 'research:verify',
        label: '核查一个说法',
        py: 'hcysf',
        hint: '主动去找反驳材料，两三秒',
        group: '研究',
        icon: ShieldQuestion,
        disabledReason: ready ? undefined : '引擎还没就绪',
        run: () => {
          setPage('research');
          window.dispatchEvent(new CustomEvent('syn:research-run', { detail: { mode: 'verify' } }));
        },
      },
      {
        id: 'research:export',
        label: '导出这份简报',
        py: 'dcjb',
        hint: 'Markdown / Word / PDF / 离线单文件',
        group: '研究',
        icon: FileDown,
        run: () => {
          setPage('research');
          window.dispatchEvent(new CustomEvent('syn:research-export'));
        },
      },
      {
        id: 'research:save-library',
        label: '把这份简报存进本地库',
        py: 'bcjbcjbdk',
        hint: '存完以后本地搜索也能搜到它',
        group: '研究',
        icon: Library,
        run: () => {
          setPage('research');
          window.dispatchEvent(new CustomEvent('syn:research-save-library'));
        },
      },
      {
        id: 'research:memory',
        label: '这个话题我以前查过什么',
        py: 'zghtwyqcgsm',
        group: '研究',
        icon: Brain,
        run: () => {
          setPage('research');
          window.dispatchEvent(new CustomEvent('syn:research-recall'));
        },
      },
      {
        id: 'research:watch',
        label: '订阅这个主题',
        py: 'dyzgzt',
        hint: '定时重跑，只提醒新出现的',
        group: '研究',
        icon: BellPlus,
        run: () => {
          setPage('research');
          window.dispatchEvent(new CustomEvent('syn:research-watch'));
        },
      },
      {
        id: 'tools:screenshot',
        label: '截图直搜',
        py: 'jtzs',
        hint: '拉起系统截图，框选完自动进投喂条',
        group: '工具',
        icon: Crop,
        run: async () => {
          const r = await window.synorive.hotkeys.screenshot();
          // 拉不起来时**要说出来**。静默失败在这里特别难查：
          // 用户按了以后什么都没发生，会以为是自己操作错了
          if (!r.ok) window.alert(r.note);
        },
      },
      {
        id: 'tools:compare',
        label: '比一比两个文件',
        py: 'bybllgwj',
        hint: '文本 diff / 图片相似度 / 视频重复片段',
        group: '工具',
        icon: FileDiff,
        run: () => {
          setPage('analyze');
          window.dispatchEvent(new CustomEvent('syn:open-compare'));
        },
      },
      // 🔴 三主题之后**不能再写二元三目**（`theme === 'dark' ? A : B`）——
      //    paper 会掉进 else，于是"切换主题"在纸感下永远只跳到深色，
      //    而且不报错。这里改成显式轮转，加主题时只需在数组里加一项。
      {
        id: 'theme:cycle',
        label: '主题换一档',
        py: 'zthyd',
        hint: '浅色 → 深色 → 纸感，循环。纸感 = 纸黄底棕墨字，长时间读最省眼',
        group: '外观',
        icon: settings?.theme === 'dark' ? Sun : Moon,
        run: async () => {
          const order = ['light', 'dark', 'paper'] as const;
          const cur = settings?.theme;
          const i = order.indexOf(cur as (typeof order)[number]);
          // 当前是 system（indexOf = -1）时从浅色开始，不要跳到最后一档
          const next = order[(i + 1) % order.length]!;
          await window.synorive.settings.patch({ theme: next });
        },
      },
      {
        id: 'theme:paper',
        label: '切到纸感主题',
        py: 'qdzgzt',
        hint: '纸黄底 + 棕墨字，长时间读文字最省眼；它是独立配色不是滤镜，不影响流畅度',
        group: '外观',
        icon: Sun,
        run: async () => { await window.synorive.settings.patch({ theme: 'paper' }); },
      },
      {
        id: 'theme:system', label: '主题跟随系统', py: 'ztgsxt',
        group: '外观', icon: Sun,
        run: async () => { await window.synorive.settings.patch({ theme: 'system' }); },
      },
      // F4：护眼和密度在设置页里有，但那是"改一次就不动"的位置。
      // 而这两个恰恰是**会随环境反复切**的（白天/夜里、大屏/小屏），
      // 每次都要翻进设置页四层太重
      {
        id: 'eye:cycle',
        label: '护眼强度换一档',
        py: 'hyqdhyd',
        hint: '关 → 低 → 中 → 高，循环',
        group: '外观',
        icon: Sun,
        run: async () => {
          const order = ['off', 'low', 'medium', 'high'] as const;
          const cur = settings?.eyeComfort ?? 'off';
          const next = order[(order.indexOf(cur as (typeof order)[number]) + 1) % order.length]!;
          await window.synorive.settings.patch({ eyeComfort: next });
        },
      },
      {
        id: 'density:cycle',
        label: '信息密度换一档',
        py: 'xxmdhyd',
        hint: '宽松 → 标准 → 紧凑，循环',
        group: '外观',
        icon: Gauge,
        run: async () => {
          const order = ['comfortable', 'standard', 'compact'] as const;
          const cur = settings?.density ?? 'standard';
          const next = order[(order.indexOf(cur as (typeof order)[number]) + 1) % order.length]!;
          await window.synorive.settings.patch({ density: next });
        },
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

    // ── 全部设置项 ────────────────────────────────────
    // 跳过去 + 滚到那一条 + 闪一下。只跳到设置页顶部是不够的 ——
    // 那页有一千三百行，用户还得自己找
    for (const e of SETTINGS_INDEX) {
      list.push({
        id: `setting:${e.section}:${e.label}`,
        label: e.isSection ? `设置：${e.label}` : e.label,
        hint: e.isSection ? '设置里的一整块' : `设置 › ${e.section}${e.hint ? ` · ${e.hint}` : ''}`,
        group: '设置',
        icon: SettingsIcon,
        run: () => {
          setPage('settings');
          requestSettingsFocus(e.label);
        },
      });
    }

    // ── 最近打开的文件 ────────────────────────────────
    for (const f of recent.files) {
      list.push({
        id: `recent-file:${f.id}`,
        label: f.title,
        hint: f.locator,
        group: '最近打开',
        icon: Clock,
        run: () => {
          rememberOpen(f); // 再打开一次，时间刷到最新
          void api.recordOpen(f.id).catch(() => {
            /* 记录热度失败不该挡住打开 */
          });
          if (f.source === 'link') void window.synorive.sys.openExternal(f.locator);
          else void window.synorive.sys.openPath(f.locator);
        },
      });
    }

    // ── 最近搜过的词 ──────────────────────────────────
    for (const r of recent.queries) {
      list.push({
        id: `recent-query:${r.q}`,
        label: r.q,
        hint: `再搜一次（用过 ${r.n} 次）`,
        group: '最近搜索',
        icon: SearchIcon,
        run: () => {
          useApp.getState().setInputMode('find');
          setPage('search');
          setQuery(r.q);
        },
      });
    }

    return list;
  }, [setPage, focusSearch, setPreset, toggleExplain, setQuery, settings, ready, recent]);

  /**
   * 排序 + 真正的分组。
   *
   * 🔴 先按分数排全表，**再**按"组第一次出现的顺序"把同组的聚到一起。
   *    v1 是边渲染边比上一条的组名，而列表是按分数排的 ——
   *    同一个小标题会在一屏里出现三四次。
   */
  const groups = useMemo(() => {
    const now = Date.now();
    const scored: { c: Cmd; s: number }[] = [];
    for (const c of commands) {
      const base = matchScore(q, c);
      if (base === null) continue;
      scored.push({ c, s: base + recentBoost(recent.used, c.id, now) });
    }
    scored.sort((a, b) => b.s - a.s);

    const order: string[] = [];
    const bucket = new Map<string, Cmd[]>();
    for (const { c } of scored) {
      let arr = bucket.get(c.group);
      if (!arr) {
        arr = [];
        bucket.set(c.group, arr);
        order.push(c.group);
      }
      arr.push(c);
    }
    return order.map((g) => ({ group: g, items: bucket.get(g)! }));
  }, [commands, q, recent.used]);

  /** 键盘导航用的扁平序 —— 必须和渲染顺序**一模一样**，否则回车执行的不是高亮那条 */
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  // 打开时重置。不重置的话上次的搜索词还留着，
  // 用户按下快捷键看到的是一份被过滤过的列表，会以为命令少了。
  useEffect(() => {
    if (!open) return;
    setQ('');
    setSel(0);
    setRecent({ files: recentFiles(12), queries: suggestQueries('', 8), used: readRecent() });
    // 等面板真的挂上去再抢焦点，否则 focus 会打在还没渲染的节点上
    requestAnimationFrame(() => inputRef.current?.focus());
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
    markUsed(c.id);
    setOpen(false);
    await c.run();
  };

  const onKey = (e: React.KeyboardEvent) => {
    const n = flat.length;
    if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((i) => (n ? (i + 1) % n : 0));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((i) => (n ? (i - 1 + n) % n : 0));
    } else if (e.key === 'Home') {
      e.preventDefault();
      setSel(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      setSel(Math.max(0, n - 1));
    } else if (e.key === 'PageDown') {
      e.preventDefault();
      setSel((i) => Math.min(n - 1, i + 8));
    } else if (e.key === 'PageUp') {
      e.preventDefault();
      setSel((i) => Math.max(0, i - 8));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const c = flat[sel];
      if (c) void exec(c);
    }
  };

  let running = -1;

  return (
    <div className="palette__backdrop" onMouseDown={() => setOpen(false)}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label="命令面板">
        <div className="palette__inputrow">
          <Command size={16} strokeWidth={1.7} className="palette__glyph" />
          <input
            ref={inputRef}
            className="palette__input"
            value={q}
            placeholder="命令名 / 拼音首字母 / 设置项 / 最近打开的文件，比如 wjglq"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            spellCheck={false}
            aria-label="命令面板搜索框"
          />
          <kbd className="palette__kbd">Esc</kbd>
        </div>

        <div className="palette__list" ref={listRef} role="listbox" aria-label="命令列表">
          {flat.length === 0 && <div className="palette__empty">没有匹配的命令</div>}
          {groups.map((g) => (
            <div key={g.group} className="palette__section">
              <div className="palette__group">{g.group}</div>
              {g.items.map((c) => {
                running += 1;
                const i = running;
                const Icon = c.icon;
                return (
                  <button
                    key={c.id}
                    className={`palette__item${i === sel ? ' palette__item--sel' : ''}${
                      c.disabledReason ? ' palette__item--off' : ''
                    }`}
                    data-sel={i === sel ? '1' : '0'}
                    role="option"
                    aria-selected={i === sel}
                    onMouseMove={() => setSel(i)}
                    onClick={() => void exec(c)}
                    disabled={!!c.disabledReason}
                  >
                    <Icon size={15} strokeWidth={1.7} className="palette__icon" />
                    <span className="palette__label">{c.label}</span>
                    {c.hint && !c.disabledReason && <span className="palette__hint">{c.hint}</span>}
                    {c.disabledReason && <span className="palette__hint">{c.disabledReason}</span>}
                    {c.keys && <kbd className="palette__kbd palette__itemkbd">{c.keys}</kbd>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>

        <div className="palette__foot">
          <kbd className="palette__kbd">↑↓</kbd> 选择
          <kbd className="palette__kbd">Enter</kbd> 执行
          <kbd className="palette__kbd">Home/End</kbd> 首尾
          <span className="palette__footnote">
            {flat.length} / {commands.length} 条
          </span>
        </div>
      </div>
    </div>
  );
}
