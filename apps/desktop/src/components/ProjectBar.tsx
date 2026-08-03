import { useCallback, useEffect, useState } from 'react';
import { Check, Download, FolderOpen, Loader2, Plus, Save } from 'lucide-react';
import { projectApi, type ResearchProject, type ResearchResponse } from '../lib/webApi';

/**
 * 研究项目条 —— P4 持久化 ＋ P3 导出
 * ============================================================
 * **要治的病**：深挖一次要十几秒、发几十个请求、抓十几篇正文。
 * 关掉窗口就全没了 —— 想接着挖，这个成本要从头再付一遍。
 * 而研究天然是断续的：今天挖一轮，明天想起还有个方向没查。
 *
 * 🔴 **存不存由你按一下，不自动存**：不是每次搜索都值得留档，
 * 自动存会让项目里堆满随手搜的东西，真正要接着挖的那次反而找不到。
 *
 * 导出四种格式。PDF **不在引擎里生成** —— 桌面端本身就是 Chromium，
 * 它的打印排版和中文字体都是现成的，所以这里导 HTML 再交给它打印，
 * 比在 Python 里塞一套排版引擎靠谱得多。
 */

const FORMATS = [
  { id: 'markdown', label: 'Markdown', hint: '纯文本，能直接贴进笔记软件' },
  { id: 'html', label: '网页', hint: '带排版，用浏览器打开可以再打印成 PDF' },
  { id: 'docx', label: 'Word', hint: '要装 python-docx（约 250 KB）' },
  { id: 'json', label: 'JSON', hint: '原始数据，给脚本用' },
  // E6：一个文件、双击就能开、断网也能看，每条出处的原文一起嵌进去。
  // 和上面的「网页」区别在于**出处不再是外链** —— 原站下线了也还看得到
  {
    id: 'single-html',
    label: '离线单文件',
    hint: '证据全内嵌，断网和原站下线之后都还能看（不含图片）',
  },
] as const;

/**
 * E1 简报模板。**四种排法用的是同一批摘录，一个字都不改** ——
 * 换模板改的只是"先看什么后看什么"和"怎么分组"。
 * 如果换个模板结论就变了，那说明其中至少一个在偷偷做提炼。
 */
const TEMPLATES = [
  { id: 'points', label: '要点式', hint: '默认。按主题分组，每组几条摘录' },
  { id: 'timeline', label: '时间线', hint: '所有带日期的摘录按时间排；没日期的单列最后' },
  { id: 'compare', label: '对比表', hint: '一行一个说法，列出谁在说、谁有异议' },
  { id: 'qa', label: '问答式', hint: '每个主题变成一个问句，摘录当答案' },
] as const;

export function ProjectBar({
  result,
  query,
  activeId,
  onActiveChange,
}: {
  /** 当前这一轮的完整结果。没有就只能新建项目、不能存 */
  result: ResearchResponse | null;
  query: string;
  activeId: string | null;
  onActiveChange: (id: string | null) => void;
}) {
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  /** E1 当前选的排法。默认要点式 —— 那是绝大多数场合下最好读的 */
  const [template, setTemplate] = useState<(typeof TEMPLATES)[number]['id']>('points');

  const load = useCallback(async () => {
    try {
      const r = await projectApi.list();
      setProjects(r.projects);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 换了一轮新结果，"已保存"的标记就该消失 ——
  // 不清的话用户会以为新这轮也存过了
  useEffect(() => {
    setSaved(false);
  }, [result]);

  const active = projects.find((p) => p.id === activeId) ?? null;

  const createAndSave = async () => {
    if (!query.trim()) return;
    setBusy('create');
    setErr(null);
    try {
      const p = await projectApi.create({ query, title: query.slice(0, 60) });
      onActiveChange(p.id);
      if (result) {
        await projectApi.saveRun(p.id, { query, mode: 'deep', payload: result });
        setSaved(true);
      }
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const saveToActive = async () => {
    if (!activeId || !result) return;
    setBusy('save');
    setErr(null);
    try {
      await projectApi.saveRun(activeId, { query, mode: 'deep', payload: result });
      setSaved(true);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const doExport = async (fmt: (typeof FORMATS)[number]['id']) => {
    if (!result && !activeId) return;
    setBusy(`export-${fmt}`);
    setErr(null);
    try {
      const r = await projectApi.export({
        payload: result ?? undefined,
        projectId: result ? undefined : (activeId ?? undefined),
        format: fmt,
        title: query || active?.title,
        includeExcluded: true,
        template,
      });
      // base64（docx）和 utf-8（其余）两条路，别混 —— 混了 Word 文件会打不开
      const blob =
        r.encoding === 'base64'
          ? new Blob([Uint8Array.from(atob(r.content), (c) => c.charCodeAt(0))], {
              type: r.mime,
            })
          : new Blob([r.content], { type: r.mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = r.filename;
      a.click();
      // 立刻 revoke 会让某些情况下下载拿不到内容，给浏览器留一帧
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  /**
   * E5 —— 导出**引用可点**的 PDF。
   *
   * 拿的是 `single-html`（证据全内嵌）而不是普通 `html`：普通 html 的出处是外链，
   * 打成 PDF 之后点过去要联网，原站下线就断了；单文件版把原文摘录嵌在同一份文档里，
   * 引用号跳的是**文档内部的锚点**，那才是"离线也点得动"。
   *
   * 🔴 打印交给主进程的 `printToPDF`，不是渲染层的 `window.print()`。
   * 后者走系统打印驱动，到那一层 `<a href>` 已经只剩字形，PDF 里点它没反应 ——
   * 而这正是这个功能唯一要做到的事。
   */
  const doExportPdf = async () => {
    if (!result && !activeId) return;
    setBusy('export-pdf');
    setErr(null);
    try {
      const r = await projectApi.export({
        payload: result ?? undefined,
        projectId: result ? undefined : (activeId ?? undefined),
        format: 'single-html',
        title: query || active?.title,
        includeExcluded: true,
        template,
      });
      const out = await window.synorive.doc.exportPdf(r.content, query || active?.title || '研究简报');
      // ok:false 且没有 error = 用户在保存对话框点了取消。
      // 那是他的选择，不是故障，弹红字只会让人以为出错了
      if (!out.ok && out.error) setErr(`PDF 没导出成：${out.error}`);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="pb">
      <div className="pb__row">
        <button className="pb__pick" onClick={() => setOpen((o) => !o)}>
          <FolderOpen size={14} aria-hidden />
          {active ? active.title : '未归入项目'}
        </button>

        {active ? (
          <button className="pb__btn" onClick={saveToActive} disabled={!result || busy !== null}>
            {busy === 'save' ? (
              <Loader2 size={14} className="spin" />
            ) : saved ? (
              <Check size={14} />
            ) : (
              <Save size={14} />
            )}
            {saved ? '已存入' : '存入这个项目'}
          </button>
        ) : (
          <button className="pb__btn" onClick={createAndSave} disabled={!query || busy !== null}>
            {busy === 'create' ? <Loader2 size={14} className="spin" /> : <Plus size={14} />}
            新建项目并存入
          </button>
        )}

        <span className="pb__spacer" />

        {/* E1：模板选在格式**前面** —— 它决定的是内容怎么排，
            而格式只决定存成什么文件。顺序反了会让人以为
            「Markdown 的时间线」和「Word 的时间线」是两回事 */}
        <span className="pb__label">排法</span>
        <select
          className="pb__tpl"
          value={template}
          onChange={(e) => setTemplate(e.target.value as (typeof TEMPLATES)[number]['id'])}
          title={TEMPLATES.find((t) => t.id === template)?.hint}
          disabled={busy !== null}
        >
          {TEMPLATES.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>

        <span className="pb__label">
          <Download size={14} aria-hidden /> 导出
        </span>
        {FORMATS.map((f) => (
          <button
            key={f.id}
            className="pb__fmt"
            title={f.hint}
            disabled={(!result && !activeId) || busy !== null}
            onClick={() => void doExport(f.id)}
          >
            {busy === `export-${f.id}` ? <Loader2 size={12} className="spin" /> : f.label}
          </button>
        ))}
        {/* E5：PDF 单独一个按钮而不是并进 FORMATS ——
            它走的是完全不同的一条路（引擎出 HTML → 主进程 Chromium 打印），
            混进那个数组会让"格式 = 引擎的一个参数"这个前提悄悄失真 */}
        <button
          className="pb__fmt"
          title="引用号可点：点一下直接跳到这份 PDF 里嵌着的原文摘录，不联网也能跳"
          disabled={(!result && !activeId) || busy !== null}
          onClick={() => void doExportPdf()}
        >
          {busy === 'export-pdf' ? <Loader2 size={12} className="spin" /> : 'PDF'}
        </button>
      </div>

      {active && (
        <p className="pb__meta">
          这个项目累计 {active.runCount} 轮搜索 · {active.sourceCount} 个来源。
          下次打开它可以接着挖，<strong>已经搜过的词不会再搜一遍</strong>。
        </p>
      )}
      {err && <p className="pb__err">{err}</p>}

      {open && (
        <div className="pb__list">
          <button
            className={`pb__item ${!activeId ? 'is-on' : ''}`}
            onClick={() => {
              onActiveChange(null);
              setOpen(false);
            }}
          >
            不归入任何项目
          </button>
          {projects.length === 0 && <p className="pb__empty">还没有研究项目。</p>}
          {projects.map((p) => (
            <button
              key={p.id}
              className={`pb__item ${p.id === activeId ? 'is-on' : ''}`}
              onClick={() => {
                onActiveChange(p.id);
                setOpen(false);
              }}
            >
              <span className="pb__item-title">{p.title}</span>
              <span className="pb__item-meta">
                {p.runCount} 轮 · {p.sourceCount} 来源 · {p.updated_at.slice(0, 10)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
