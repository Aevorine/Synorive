import { useEffect, useRef, useState } from 'react';
import { Minus, Search, Square, Copy, X } from 'lucide-react';
import { useApp } from '../lib/store';
import { useSearch } from '../lib/useSearch';
import { api } from '../lib/api';
import iconUrl from '../../resources/icons/icon-64.png';

/**
 * 顶栏：品牌 + 全局搜索框 + 窗口按钮
 * 搜索框放正中央、永远在、任何界面按 / 或 Ctrl+K 直达 ——
 * 「对于重要的功能显示在界面内重要的位置中」这条要求的直接落地。
 */
export function TopBar() {
  const [isMax, setIsMax] = useState(false);

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
        <SearchBox />
      </div>

      <div className="topbar__actions syn-no-drag">
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

function SearchBox() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [focused, setFocused] = useState(false);
  const [dropping, setDropping] = useState(false);
  const value = useSearch((s) => s.query);
  const setValue = useSearch((s) => s.setQuery);
  const focusNonce = useApp((s) => s.searchFocusNonce);
  const setPage = useApp((s) => s.setPage);

  // 托盘 / 全局快捷键唤起时抢焦点
  useEffect(() => {
    if (focusNonce > 0) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [focusNonce]);

  /**
   * E1 投喂即搜：拖任何东西进窗口都直接开始分析。
   * 把「上传 → 分析 → 再搜索」三步压成一步 —— 不需要先找"上传"按钮。
   *
   * 注意拿路径必须走 preload 里的 webUtils.getPathForFile：
   * Electron 32 起 File.path 被移除了，直接读会拿到 undefined，
   * 而且不报错 —— 拖进来什么都没发生，查半天。
   */
  useEffect(() => {
    const prevent = (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
    };
    const onOver = (e: DragEvent) => {
      prevent(e);
      if (e.dataTransfer?.types.includes('Files')) setDropping(true);
    };
    const onLeave = (e: DragEvent) => {
      prevent(e);
      if (e.relatedTarget === null) setDropping(false);
    };
    const onDrop = (e: DragEvent) => {
      prevent(e);
      setDropping(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) {
        const paths = files
          .map((f) => {
            try {
              return window.synorive.sys.pathForFile(f);
            } catch {
              return '';
            }
          })
          .filter(Boolean);
        if (paths.length) {
          setPage('analyze');
          void api.ingest({ targets: paths, source: 'file', recursive: true, priority: 'high' });
        }
        return;
      }
      // 拖进来的是文字或链接 → 直接当查询词搜
      const text = e.dataTransfer?.getData('text/plain')?.trim();
      if (text) {
        setPage('search');
        setValue(text);
        inputRef.current?.focus();
      }
    };

    window.addEventListener('dragover', onOver);
    window.addEventListener('dragleave', onLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragover', onOver);
      window.removeEventListener('dragleave', onLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [setPage, setValue]);

  // 「/」和 Ctrl+K 任何界面都能直达搜索框
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);

      if (e.key === '/' && !typing) {
        e.preventDefault();
        setPage('search');
        inputRef.current?.focus();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPage('search');
        inputRef.current?.focus();
        inputRef.current?.select();
      }
      if (e.key === 'Escape' && document.activeElement === inputRef.current) {
        inputRef.current?.blur();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setPage]);

  const cls = [
    'searchbox',
    focused ? 'searchbox--focused' : '',
    dropping ? 'searchbox--dropping' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={cls}>
      <Search className="searchbox__icon" size={17} strokeWidth={1.7} />
      <input
        ref={inputRef}
        className="searchbox__input"
        type="text"
        value={value}
        placeholder={
          dropping ? '松手就开始分析' : '搜索一切…　或把文件、图片、链接直接拖进来'
        }
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => setValue(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        aria-label="全局搜索"
      />
      {!focused && !value && (
        <span className="searchbox__kbd" aria-hidden>
          <kbd className="kbd">Ctrl</kbd>
          <kbd className="kbd">K</kbd>
        </span>
      )}
    </div>
  );
}
