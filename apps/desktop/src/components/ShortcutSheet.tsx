import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Keyboard, X } from 'lucide-react';

/**
 * C6 快捷键速查表 —— 按 `?` 唤出
 * ============================================================
 * 🔴 **全局快捷键那一半必须显示"真正抢到的键"，不许写死。**
 *    `globalShortcut.register()` 抢不到时**返回 false 而不抛异常**
 *    （`HotkeyReport.tsx` 文件头写了这条根因）。写死一张"Alt+空格"的表，
 *    在输入法/截图工具占了那个键的机器上就是一张**骗人的表** ——
 *    用户按了没反应，还以为是自己记错了。所以这一半每次打开都现问主进程。
 *
 * 🔴 **应用内的那一半反过来：必须写在这里，而且要和真实绑定一一对应。**
 *    它们是渲染层自己 `addEventListener` 装的，主进程根本不知道。
 *    这份表下面每一条都标了它是在哪个文件里绑的，改键的人一眼能看到要同步改哪。
 *
 * 🔴 **在输入框里按 `?` 不接管。** 中文标点、疑问句都要打问号；
 *    抢过来的话用户在搜索框里就永远打不出「？」了。
 */

interface HotkeyRow {
  id: string;
  label: string;
  /** 实际生效的键。**可能是 null** —— 一个都没抢到 */
  active: string | null;
  usedFallback: boolean;
  tried: string[];
}

/**
 * 应用内按键。**这张表里的每一条都在代码里真的绑了**，
 * 括号里是绑定它的地方，改键时两边一起改。
 */
const IN_APP: { group: string; rows: { keys: string[]; what: string; where: string }[] }[] = [
  {
    group: '随时随地',
    rows: [
      { keys: ['Ctrl', 'K'], what: '展开主输入区，问一句话', where: 'TopBar.GlobalHotkeys' },
      { keys: ['Ctrl', 'Shift', 'K'], what: '展开主输入区，找东西', where: 'TopBar.GlobalHotkeys' },
      { keys: ['/'], what: '展开主输入区（输入框里不接管）', where: 'TopBar.GlobalHotkeys' },
      { keys: ['Ctrl', 'Shift', 'P'], what: '命令面板', where: 'TopBar.GlobalHotkeys' },
      { keys: ['Ctrl', 'P'], what: '命令面板（同上，少按一个键）', where: 'TopBar.GlobalHotkeys' },
      { keys: ['?'], what: '就是这张表', where: 'ShortcutSheet' },
    ],
  },
  {
    group: '撤销',
    rows: [
      { keys: ['Ctrl', 'Z'], what: '撤销上一步（输入框里让给浏览器自带的文本撤销）', where: 'lib/undo.bindUndoKeys' },
      { keys: ['Ctrl', 'Shift', 'Z'], what: '重做', where: 'lib/undo.bindUndoKeys' },
    ],
  },
  {
    group: '结果列表里',
    rows: [
      { keys: ['↑', '↓'], what: '上下选（在搜索框里也能用，手不用离开输入框）', where: 'lib/keynav' },
      { keys: ['Home'], what: '跳到第一条', where: 'lib/keynav' },
      { keys: ['End'], what: '跳到最后一条', where: 'lib/keynav' },
      { keys: ['PageUp', 'PageDown'], what: '一次翻十条', where: 'lib/keynav' },
      { keys: ['Enter'], what: '打开选中的这一条', where: 'lib/keynav' },
      { keys: ['Space'], what: '预览一眼（不离开列表）', where: 'lib/keynav' },
      { keys: ['Esc'], what: '退出当前这一层：先取消选中，再关抽屉/弹层', where: 'lib/keynav' },
    ],
  },
  {
    group: '命令面板里',
    rows: [
      { keys: ['↑', '↓'], what: '选', where: 'CommandPalette' },
      { keys: ['Home', 'End'], what: '首 / 尾', where: 'CommandPalette' },
      { keys: ['PageUp', 'PageDown'], what: '一次翻八条', where: 'CommandPalette' },
      { keys: ['Enter'], what: '执行', where: 'CommandPalette' },
      { keys: ['Esc'], what: '关掉', where: 'CommandPalette' },
    ],
  },
];

function Keys({ keys }: { keys: string[] }) {
  return (
    <span className="syn-ks__keys">
      {keys.map((k, i) => (
        <span key={k}>
          {i > 0 && <span className="syn-ks__plus">+</span>}
          <kbd className="kbd">{k}</kbd>
        </span>
      ))}
    </span>
  );
}

export function ShortcutSheet() {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<HotkeyRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // 唤起：`?` 键，或命令面板派的事件
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        // 🔴 不 preventDefault：Esc 是分层的，这一层关掉之后
        //    别的层（抽屉、选中态）还要能继续收到它
        setOpen(false);
        return;
      }
      if (e.key !== '?') return;
      const t = e.target as HTMLElement | null;
      // 输入框里不接管 —— 否则永远打不出问号
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      e.preventDefault();
      setOpen((v) => !v);
    };
    const onEvt = () => setOpen(true);
    window.addEventListener('keydown', onKey);
    window.addEventListener('syn:shortcut-sheet', onEvt);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('syn:shortcut-sheet', onEvt);
    };
  }, []);

  /**
   * 🔴 **每次打开都重新问一遍**，不缓存。
   *    快捷键是可以在设置页当场改的，也可能被后启动的别的软件抢走；
   *    缓存一份的话，这张表会理直气壮地显示一个已经失效的键。
   */
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setErr(null);
    window.synorive.hotkeys
      .report()
      .then((r) => alive && setRows(r))
      .catch((e: Error) => alive && setErr(e.message));
    // 打开就把焦点收进弹层，Tab 不会跑到后面那一屏上去
    requestAnimationFrame(() => closeRef.current?.focus());
    return () => {
      alive = false;
    };
  }, [open]);

  if (!open) return null;

  const trouble = (rows ?? []).filter((r) => r.active === null || r.usedFallback);

  return (
    <div className="syn-ks__backdrop" onMouseDown={() => setOpen(false)}>
      <div
        className="syn-ks"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="快捷键速查表"
      >
        <header className="syn-ks__head">
          <Keyboard size={16} strokeWidth={1.7} aria-hidden />
          <h2 className="syn-ks__title">快捷键</h2>
          <span className="syn-ks__spacer" />
          <button
            ref={closeRef}
            type="button"
            className="syn-ks__close"
            onClick={() => setOpen(false)}
            title="关闭（Esc）"
            aria-label="关闭快捷键速查表"
          >
            <X size={15} strokeWidth={1.8} />
          </button>
        </header>

        <div className="syn-ks__body">
          {/* ── 全局键：显示真实注册结果 ────────────────── */}
          <section className="syn-ks__group">
            <h3 className="syn-ks__grouptitle">
              系统全局（应用没在前台也生效）
              <span className="syn-ks__live">实时读取，不是写死的</span>
            </h3>
            {err && (
              <p className="syn-diag">
                <AlertTriangle size={13} aria-hidden /> 读不到全局快捷键的真实状态：{err}
              </p>
            )}
            {!err && !rows && <p className="syn-diag">正在问主进程…</p>}
            {rows?.length === 0 && <p className="syn-diag">没有注册任何全局快捷键。</p>}
            {rows?.map((r) => (
              <div className="syn-ks__row" key={r.id}>
                <span className="syn-ks__what">{r.label}</span>
                {r.active ? (
                  <Keys keys={r.active.split('+')} />
                ) : (
                  <span className="syn-ks__dead">一个都没抢到</span>
                )}
                {r.usedFallback && r.active && (
                  <span className="syn-ks__note">首选被占，这是备选</span>
                )}
              </div>
            ))}
            {trouble.length > 0 && (
              <p className="syn-ks__trouble">
                <AlertTriangle size={13} aria-hidden />
                有 {trouble.length} 项没拿到首选键。同一个组合在系统里只能被一个程序占住，
                常见占用方是输入法、截图工具、录屏软件。试过：
                {trouble.map((t) => t.tried.join(' → ')).join('；')}。
                在设置 › 全局快捷键里可以改成别的。
              </p>
            )}
          </section>

          {/* ── 应用内键：写在代码里的那一批 ────────────── */}
          {IN_APP.map((g) => (
            <section className="syn-ks__group" key={g.group}>
              <h3 className="syn-ks__grouptitle">{g.group}</h3>
              {g.rows.map((r) => (
                <div className="syn-ks__row" key={`${g.group}-${r.keys.join('+')}-${r.what}`}>
                  <span className="syn-ks__what">{r.what}</span>
                  <Keys keys={r.keys} />
                </div>
              ))}
            </section>
          ))}
        </div>

        <footer className="syn-ks__foot">
          再按一次 <kbd className="kbd">?</kbd> 或 <kbd className="kbd">Esc</kbd> 关掉。
          在输入框里打字时 <kbd className="kbd">?</kbd> 不会被抢走。
        </footer>
      </div>
    </div>
  );
}
