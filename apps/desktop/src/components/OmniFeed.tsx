import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, FileUp, Loader2, Sparkles, X } from 'lucide-react';
import { routeLabel, triage, triageDrop, type Triage } from '../lib/triage';

/**
 * 统一投喂条 + 秒级预判卡 —— N1 / N2 / U1
 * ============================================================
 * **一个框吃所有东西**：图片、视频、链接、文件、一句话、一整段文章。
 * 原来投喂和搜索是两套入口，用户得先想"这该往哪放" —— 那个问题
 * 本来就不该由用户来答。
 *
 * 打完字（或者松开鼠标）的**那一瞬间**就出预判卡：
 * 这是什么 ／ 我打算怎么办 ／ 大概几秒 ／ 换一条路。
 * 判定是纯本地正则，不发请求，所以它真的是"秒级"——
 * 一旦这里要等一个网络往返，"预判"自己就变成了要等的东西。
 *
 * 🔴 **预判不等于替你决定**：默认路线只是高亮，回车才执行；
 * 备选路线一直摆在旁边，一键就能改。我判错的时候，改正的成本
 * 必须是一次点击，不能是"先撤销再重来"。
 */

export interface OmniFeedProps {
  /** 用户确认执行。paths 非空说明是拖进来的本机文件（已解析成真实路径） */
  onRun: (route: Triage['route'], text: string, paths: string[]) => void;
  busy?: boolean;
  /** 外部想预填（命令面板/剪贴板哨兵会用） */
  initial?: string;
  placeholder?: string;
}

/**
 * 拿本机路径必须走 preload 的 `webUtils.getPathForFile`。
 *
 * 🔴 Electron 32 起 `File.path` 被移除了，直接读拿到的是 undefined，
 * **而且不报错** —— 表现成"拖进来什么都没发生"，能查半天。
 * TopBar 的全局投喂也是栽在这一条上，这里沿用同一个修法。
 */
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

export function OmniFeed({ onRun, busy, initial, placeholder }: OmniFeedProps) {
  const [text, setText] = useState(initial ?? '');
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [override, setOverride] = useState<Triage['route'] | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // 拖拽事件在子元素上会反复 enter/leave，用计数器判"真的离开了整个区域"，
  // 否则鼠标从边框划过一次就会把高亮闪掉一下
  const dragDepth = useRef(0);

  useEffect(() => {
    if (initial !== undefined) setText(initial);
  }, [initial]);

  /** 有文件时按第一个文件的名字判定 —— 用户拖一批图片进来，
   *  预判说的是"这是一批图片"，而不是文本框里那句无关的话 */
  const subject = files.length ? files[0]!.name : text;
  const t = useMemo(() => triage(subject), [subject]);
  const route = override ?? t.route;

  const run = useCallback(() => {
    if (busy) return;
    if (!text.trim() && !files.length) return;
    onRun(route, text.trim(), pathsOf(files));
  }, [busy, files, onRun, route, text]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    // 🔴 必须挡住冒泡：TopBar 在 window 上挂了全局投喂监听（E1），
    // 不挡的话拖一个文件进这个框会**同时**触发全局的"直接入库"，
    // 用户看到的是"我还没确认它就自己开始了" —— 那正好废掉预判卡的意义
    e.stopPropagation();
    dragDepth.current = 0;
    setDragging(false);
    const { text: dropped, files: dropFiles } = triageDrop(e.dataTransfer);
    if (dropFiles.length) setFiles(dropFiles);
    // 拖进来的是文字/网址就直接当查询词。E1「投喂即搜」的老约定，
    // 这里沿用同一条 —— 两处行为不一致比两处都笨更让人困惑
    if (dropped && !dropFiles.length) setText(dropped);
    setOverride(null);
    inputRef.current?.focus();
  };

  const clear = () => {
    setText('');
    setFiles([]);
    setOverride(null);
    inputRef.current?.focus();
  };

  const hasInput = !!text.trim() || files.length > 0;

  return (
    <div
      className={`omni ${dragging ? 'omni--drag' : ''}`}
      onDragEnter={(e) => {
        e.preventDefault();
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => {
        e.preventDefault();
        dragDepth.current -= 1;
        if (dragDepth.current <= 0) setDragging(false);
      }}
      onDrop={onDrop}
    >
      <div className="omni__box">
        <Sparkles size={18} className="omni__icon" aria-hidden />
        {/* B2：从 2 行改成 8 行、可手动拉高到半屏。
            用户原话「输入的内容很多则显示的位置的界面要很大」——
            研究工作台恰恰是最容易粘进一大段材料的地方，2 行意味着
            粘进来之后只看得见开头两行，改错字都得靠滚。
            拖了文件时收成 3 行：那时候主体是文件列表，输入框只是补一句说明 */}
        <textarea
          ref={inputRef}
          className="omni__input"
          rows={files.length ? 3 : 8}
          value={text}
          placeholder={placeholder ?? '把图片 / 视频 / 链接 / 文件拖进来，或者直接打字问一句'}
          onChange={(e) => {
            setText(e.target.value);
            setOverride(null);
          }}
          onKeyDown={(e) => {
            // 回车执行，Shift+回车换行 —— 长文粘进来时还要能排版
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              run();
            }
          }}
        />
        {/* 字数：粘一大段进来时，"它到底收了多少"必须看得见。
            600 字以上才显示 —— 平时挂个「3 字」是纯噪声 */}
        {text.length > 600 && (
          <span className="omni__count" aria-label={`已输入 ${text.length} 字`}>
            {text.length} 字
          </span>
        )}
        {hasInput && (
          <button className="omni__clear" onClick={clear} title="清空" aria-label="清空">
            <X size={16} />
          </button>
        )}
        <button className="omni__go" onClick={run} disabled={busy || !hasInput} title={routeLabel(route)}>
          {busy ? <Loader2 size={16} className="spin" /> : <ArrowRight size={16} />}
          <span>{routeLabel(route)}</span>
        </button>
      </div>

      {files.length > 0 && (
        <div className="omni__files">
          <FileUp size={14} aria-hidden />
          <span>
            {files.length === 1
              ? files[0]!.name
              : `${files[0]!.name} 等 ${files.length} 个文件`}
          </span>
          <button onClick={() => setFiles([])}>移除</button>
        </div>
      )}

      {hasInput && (
        <div className="triage" role="status" aria-live="polite">
          <div className="triage__row">
            <span className="triage__label">这是</span>
            <span className="triage__what">{t.what}</span>
            <span className="triage__eta">
              约 {t.etaS[0]}–{t.etaS[1]} 秒
            </span>
          </div>
          <div className="triage__row">
            <span className="triage__label">我打算</span>
            <span className="triage__plan">{t.plan}</span>
          </div>
          {t.uncertain && <div className="triage__uncertain">⚠ {t.uncertain}</div>}
          {t.alternatives.length > 0 && (
            <div className="triage__alts">
              <span className="triage__label">或者</span>
              {t.alternatives.map((a) => (
                <button
                  key={a.route}
                  className={`triage__alt ${route === a.route ? 'is-active' : ''}`}
                  onClick={() => setOverride(a.route)}
                >
                  {a.label}
                </button>
              ))}
              {override && (
                <button className="triage__alt triage__alt--reset" onClick={() => setOverride(null)}>
                  用回默认（{routeLabel(t.route)}）
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {dragging && (
        <div className="omni__dropmask">
          松手就开始 —— 我会先告诉你打算怎么处理，再动手
        </div>
      )}
    </div>
  );
}
