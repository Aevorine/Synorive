import { useCallback, useEffect, useRef, useState } from 'react';
import { CornerDownLeft, FileSearch, Loader2, MessageCircleQuestion, Search, X } from 'lucide-react';
import type { InputMode } from '@synorive/shared-types';
import { useApp } from '../lib/store';
import { useSearch } from '../lib/useSearch';
import { useAsk } from '../lib/useAsk';
import { api } from '../lib/api';

/**
 * B1 主舞台 —— 全应用的主输入区
 * ====================================================================
 * 用户原话（提了三次，这次加了最关键的一句）：
 *   「对于重要的功能显示在界面内重要**核心**的位置中」
 *   「如果是主要使用的功能，而且**输入的内容很多**，则显示的位置的界面要**很大**」
 *
 * 🔴 **在这个组件之前，全应用唯一的输入口是顶栏里一个高 32px 的单行 input。**
 *    它连换行都做不到，更别说"输入的内容很多"。搜索页自己还得写一句
 *    「在上面的搜索框里敲字就能搜」来告诉用户主功能在哪 ——
 *    **一个软件需要用文案指路，就说明那个功能不在它该在的位置上。**
 *
 * ── 两态一体 ────────────────────────────────────────────────
 *   舞台态（AskStage）  没有结果时：居中、大、多行、可拖高、可粘图、可拖文件
 *   收窄态（StageCompact）有结果后：顶栏一条，显示当前问题，点一下重新展开
 *
 * **两态共用同一份 query**，切换、收起、展开、换模式，**输入的字永不丢失**。
 * 这条是硬要求：用户常常先当搜索词打进去，打到一半才意识到自己要的是答案。
 * 那一刻如果切模式要重打一遍，他就再也不会用另一个模式了。
 *
 * ── 为什么两个模式共用一个框，而不是两个框 ────────────────
 * 两个框意味着用户每次要先决定"我这次是搜还是问"。而真实情况是
 * **他打完字才知道自己想要什么**。共用一个框 + 一键切换，代价是多一个
 * 切换控件，收益是不用在打字之前做决定。
 */

interface ModeSpec {
  id: InputMode;
  icon: typeof Search;
  label: string;
  hint: string;
  placeholder: string;
}

// 写成元组而不是数组：`MODES[0]` 在 noUncheckedIndexedAccess 下才不是
// `ModeSpec | undefined`，下面 `?? MODES[0]` 的兜底才真的兜得住
const MODES: readonly [ModeSpec, ModeSpec] = [
  {
    id: 'ask',
    icon: MessageCircleQuestion,
    label: '问一句',
    hint: '回一段带出处的答案，每句都能点回原文',
    placeholder:
      '问一句话，比如「去年那份预算里研发占多少」「相机光圈怎么影响景深」…\n答案从你自己的资料里逐字摘出来，绝不改写。',
  },
  {
    id: 'find',
    icon: FileSearch,
    label: '找东西',
    hint: '回一个结果列表，适合浏览和筛选',
    placeholder:
      '描述你要找的东西，不用记文件名，比如「上个月那张流程图」「讲注意力机制的那篇」…\n也可以把文件、图片、链接直接拖进来。',
  },
];

/** 拖进来的文件直接进分析流水线；拖进来的文字直接填进输入框。 */
function useWindowDrop(onText: (t: string) => void) {
  const setPage = useApp((s) => s.setPage);
  const [dropping, setDropping] = useState(false);

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
      // relatedTarget 为 null 才是真的离开了窗口。
      // 不判这个的话，鼠标从输入框划到按钮上就会闪一下"松手就开始分析"
      if (e.relatedTarget === null) setDropping(false);
    };
    const onDrop = (e: DragEvent) => {
      prevent(e);
      setDropping(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) {
        // 🔴 拿路径必须走 preload 的 webUtils.getPathForFile：
        //    Electron 32 起 File.path 被移除了，直接读拿到 undefined 且**不报错**——
        //    表现是"拖进来什么都没发生"
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
      const text = e.dataTransfer?.getData('text/plain')?.trim();
      if (text) onText(text);
    };

    window.addEventListener('dragover', onOver);
    window.addEventListener('dragleave', onLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragover', onOver);
      window.removeEventListener('dragleave', onLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [onText, setPage]);

  return dropping;
}

/** 两态共用的提交逻辑。 */
function useSubmit() {
  const mode = useApp((s) => s.inputMode);
  const setStageExpanded = useApp((s) => s.setStageExpanded);
  const setPage = useApp((s) => s.setPage);
  const filters = useSearch((s) => s.filters);
  const rerun = useSearch((s) => s.rerun);
  const runAsk = useAsk((s) => s.run);

  return useCallback(
    (q: string) => {
      const text = q.trim();
      if (!text) return;
      setPage('search');
      // 提交即收窄 —— 大输入区的价值在"还没结果的时候"，
      // 有结果之后它挡着的正是用户要看的东西
      setStageExpanded(false);
      if (mode === 'ask') runAsk(text, filters);
      else rerun();
    },
    [mode, filters, rerun, runAsk, setPage, setStageExpanded],
  );
}

/**
 * 舞台态：大输入区。
 * 只在搜索页、且 stageExpanded 时渲染。
 */
export function AskStage() {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const query = useSearch((s) => s.query);
  const setQuery = useSearch((s) => s.setQuery);
  const mode = useApp((s) => s.inputMode);
  const setMode = useApp((s) => s.setInputMode);
  const focusNonce = useApp((s) => s.searchFocusNonce);
  const asking = useAsk((s) => s.loading);
  const submit = useSubmit();

  const dropping = useWindowDrop(
    useCallback(
      (t: string) => {
        setQuery(t);
        taRef.current?.focus();
      },
      [setQuery],
    ),
  );

  // 唤起时抢焦点并把光标放到末尾（不是全选 —— 全选之后随便敲一个字
  // 就把已有内容清空了，而用户按 Ctrl+K 往往是想接着写）
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.focus();
    const n = ta.value.length;
    ta.setSelectionRange(n, n);
  }, [focusNonce]);

  const active = MODES.find((m) => m.id === mode) ?? MODES[0];

  return (
    <div className={`stage${dropping ? ' stage--dropping' : ''}`}>
      <div className="stage__inner">
        <h1 className="stage__lead">
          {mode === 'ask' ? '问一句话，从你自己的资料里找答案' : '描述你要找的东西'}
        </h1>

        {/* 模式切换放在输入框**上面**：用户看到框之前就知道这一框是干嘛的。
            放下面的话他会先打完字才发现模式不对 */}
        <div className="stage__modes" role="tablist" aria-label="输入模式">
          {MODES.map((m) => (
            <button
              key={m.id}
              role="tab"
              aria-selected={mode === m.id}
              className={`stage__mode${mode === m.id ? ' stage__mode--on' : ''}`}
              onClick={() => {
                setMode(m.id);
                taRef.current?.focus();
              }}
              title={m.hint}
            >
              <m.icon size={16} strokeWidth={1.7} />
              <span>{m.label}</span>
            </button>
          ))}
        </div>

        <div className="stage__box">
          <textarea
            ref={taRef}
            className="stage__input syn-selectable"
            value={query}
            rows={6}
            spellCheck={false}
            placeholder={dropping ? '松手就开始分析' : active.placeholder}
            aria-label={active.label}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              // Enter 提交，Shift+Enter 换行。
              // 反过来（Enter 换行、Ctrl+Enter 提交）对"输入内容很多"更友好，
              // 但这里第一位的是**问一句话就走**，长文场景在研究工作台
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit(query);
              }
              // Esc 不清空，只失焦 —— 清空会让用户刚打的一大段没了
              if (e.key === 'Escape') {
                e.preventDefault();
                taRef.current?.blur();
              }
            }}
          />

          <div className="stage__bar">
            <span className="stage__hint">
              <kbd className="kbd">Enter</kbd> {mode === 'ask' ? '提问' : '搜索'}
              <span className="stage__dot">·</span>
              <kbd className="kbd">Shift</kbd>+<kbd className="kbd">Enter</kbd> 换行
              <span className="stage__dot">·</span>
              可以直接把文件、图片、链接拖进来
            </span>
            <span className="stage__spacer" />
            {query.length > 0 && (
              <span className="stage__count" aria-label={`已输入 ${query.length} 字`}>
                {query.length} 字
              </span>
            )}
            {query.length > 0 && (
              <button
                className="stage__clear"
                onClick={() => {
                  setQuery('');
                  taRef.current?.focus();
                }}
                title="清空"
                aria-label="清空输入"
              >
                <X size={14} strokeWidth={1.8} />
              </button>
            )}
            <button
              className="btn btn--primary stage__go"
              disabled={!query.trim() || asking}
              onClick={() => submit(query)}
            >
              {asking ? (
                <Loader2 size={15} strokeWidth={2} className="spin" />
              ) : (
                <CornerDownLeft size={15} strokeWidth={1.8} />
              )}
              {mode === 'ask' ? '提问' : '搜索'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * 收窄态：顶栏那一条。
 *
 * 它是**按钮不是输入框**：真正的输入永远发生在舞台态里，
 * 一个地方输入、一个地方展示，就不会出现"两个框里的字不一样"这种事。
 * （原来那个单行 input 就是问题本身，不该在这里复活一份。）
 */
export function StageCompact() {
  const query = useSearch((s) => s.query);
  const mode = useApp((s) => s.inputMode);
  const openStage = useApp((s) => s.openStage);
  const asking = useAsk((s) => s.loading);
  const searching = useSearch((s) => s.loading);
  const busy = asking || searching;

  const Icon = mode === 'ask' ? MessageCircleQuestion : Search;

  return (
    <button
      className={`stagebar${query ? ' stagebar--filled' : ''}`}
      onClick={openStage}
      title="点开或按 Ctrl+K 展开大输入区"
      aria-label="展开输入区"
    >
      {busy ? (
        <Loader2 className="stagebar__icon spin" size={16} strokeWidth={2} />
      ) : (
        <Icon className="stagebar__icon" size={16} strokeWidth={1.7} />
      )}
      <span className="stagebar__text">
        {query || (mode === 'ask' ? '问一句话…' : '搜索一切…　或把文件、图片、链接拖进来')}
      </span>
      <span className="stagebar__kbd" aria-hidden>
        <kbd className="kbd">Ctrl</kbd>
        <kbd className="kbd">K</kbd>
      </span>
    </button>
  );
}
