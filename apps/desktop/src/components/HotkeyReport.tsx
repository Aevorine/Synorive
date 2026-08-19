import { useEffect, useState } from 'react';
import { AlertTriangle, Keyboard, RotateCcw } from 'lucide-react';

/**
 * F7 —— 全局快捷键的**真实**注册结果
 * ============================================================
 * 🔴 **这块界面以前根本不存在，而没有它 F7 就是半个功能。**
 * 主进程一直在算 `hotkeyReport`、IPC 通道也接好了，
 * 但**渲染层从来没有调用过 `hotkeys.report()`** ——
 * 于是「Alt+空格 被别的软件占了，现在用的是 Ctrl+Alt+空格」
 * 这句话谁也看不到。用户按 Alt+空格没反应，日志干干净净，
 * 他唯一能得出的结论是「这功能坏了」。
 *
 * 🔴 `globalShortcut.register()` 抢不到时**返回 false 而不抛异常** ——
 * 这是 F7 那一整条代码路径的根因，也是它必须把结果显示出来的理由：
 * 失败是静默的，那就只能靠界面把它喊出来。
 */

interface Row {
  id: string;
  label: string;
  /** 实际生效的键。**可能是 null** —— 一个都没抢到 */
  active: string | null;
  usedFallback: boolean;
  tried: string[];
}

export function HotkeyReport() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  /** 正在给哪一项抓新键。null = 没在抓 */
  const [capturing, setCapturing] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const save = async (id: string, accelerator: string) => {
    setCapturing(null);
    const r = await window.synorive.hotkeys.set(id, accelerator);
    setSaveErr(r.ok ? null : (r.error ?? '没能改成功'));
    const fresh = await window.synorive.hotkeys.report();
    setRows(fresh);
  };

  /**
   * 抓键。
   *
   * 🔴 **只有修饰键的组合一律不收。** 单按一个 Ctrl 会先触发一次 keydown，
   *    直接收下的话用户刚点"改键"、手还没离开 Ctrl，就被设成了一个
   *    注册不了的"Control"。必须等到有真正的主键才算数。
   *
   * 🔴 **不带修饰键的单键也不收。** 全局快捷键把一个裸的 `S` 抢走，
   *    意味着他在任何软件里打字都会触发这个应用 —— 那是灾难。
   */
  useEffect(() => {
    if (!capturing) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Escape') {
        setCapturing(null);
        return;
      }
      const MOD = new Set(['Control', 'Shift', 'Alt', 'Meta']);
      if (MOD.has(e.key)) return;

      const parts: string[] = [];
      if (e.ctrlKey || e.metaKey) parts.push('CommandOrControl');
      if (e.altKey) parts.push('Alt');
      if (e.shiftKey) parts.push('Shift');
      if (parts.length === 0) {
        setSaveErr('至少要带一个 Ctrl / Alt / Shift —— 裸键会让你在任何软件里打字都触发这个应用。');
        return;
      }
      const key =
        e.key === ' '
          ? 'Space'
          : e.key.length === 1
            ? e.key.toUpperCase()
            : e.key.replace(/^Arrow/, '');
      void save(capturing, [...parts, key].join('+'));
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [capturing]);

  useEffect(() => {
    let alive = true;
    window.synorive.hotkeys
      .report()
      .then((r) => alive && setRows(r))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, []);

  if (err) {
    return (
      <p className="hk__err">
        <AlertTriangle size={14} aria-hidden /> 读不到快捷键状态：{err}
      </p>
    );
  }
  if (!rows) return <p className="hk__hint">正在读快捷键状态…</p>;
  if (rows.length === 0) return <p className="hk__hint">没有注册全局快捷键。</p>;

  const trouble = rows.filter((r) => r.active === null || r.usedFallback);

  return (
    <div className="hk">
      <ul className="hk__list">
        {rows.map((r) => (
          <li key={r.id} className={`hk__row${r.active === null ? ' hk__row--dead' : ''}`}>
            <span className="hk__label">
              <Keyboard size={13} aria-hidden /> {r.label}
            </span>
            {capturing === r.id ? (
              <kbd className="kbd hk__capturing">按下你想要的组合…（Esc 取消）</kbd>
            ) : r.active ? (
              <kbd className="kbd">{r.active}</kbd>
            ) : (
              <span className="hk__dead">一个都没抢到</span>
            )}
            {r.usedFallback && r.active && capturing !== r.id && (
              <span className="hk__fallback">首选被占，用的是备选</span>
            )}
            <span className="hk__spacer" />
            <button
              className="btn btn--sm"
              onClick={() => {
                setSaveErr(null);
                setCapturing(capturing === r.id ? null : r.id);
              }}
              title={capturing === r.id ? '取消改键' : `改「${r.label}」这个键`}
            >
              {capturing === r.id ? '取消' : '改键'}
            </button>
            <button
              className="hk__reset"
              onClick={() => void save(r.id, '')}
              title="恢复成内置默认键"
              aria-label={`把「${r.label}」恢复成默认键`}
            >
              <RotateCcw size={12} strokeWidth={1.8} />
            </button>
          </li>
        ))}
      </ul>

      {saveErr && (
        <p className="hk__saveerr">
          <AlertTriangle size={13} aria-hidden /> {saveErr}
        </p>
      )}

      {/* 🔴 出问题时必须把「试过哪些键」列出来 ——
          只说"失败了"用户没法自己排查是哪个软件占了 */}
      {trouble.length > 0 && (
        <p className="hk__note">
          <AlertTriangle size={13} aria-hidden />
          有 {trouble.length} 项没拿到首选键。系统里同一个组合只能被一个程序占住，
          常见的占用方是输入法、截图工具、录屏软件。
          试过的组合：{trouble.map((t) => t.tried.join(' → ')).join('；')}。
          要用回首选键，先在那个程序里把它解绑，再重启本应用。
        </p>
      )}
    </div>
  );
}
