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
