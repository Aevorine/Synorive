import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, FolderGit2, Loader2, Plus } from 'lucide-react';
import { projectApi, type ResearchProject } from '../lib/webApi';
import { useApp } from '../lib/store';

/**
 * A5 全局项目切换器 —— 把「项目」从研究页抬成全应用的工作上下文
 * ====================================================================
 * 在此之前，`research/projects` 只活在研究工作台里。而项目本来就该是
 * **跨页面**的概念：同一个课题下的搜索、监控、出稿应该归在一起，
 * 而不是全都倒进一个大池子，用得越久越乱。
 *
 * 🔴 **它只改"归属"，不改"可见范围"。**
 *    选了项目 ≠ 搜索只搜这个项目里的东西 —— 那会让用户搜不到明明存在的
 *    文件，而且看不出是谁干的（正是 D2 那条"看不见的筛选"要治的病）。
 *    它影响的是：新建的监控挂在谁名下、成稿默认叫什么、
 *    今日页优先显示哪个项目的未读。**想缩小搜索范围请用筛选，那是显式的。**
 *
 * 🔴 **切项目不清空任何东西。** 选中的结果、搜索历史、当前查询都留着 ——
 *    用户切项目往往正是为了"把刚挑的这几条归到另一个项目下"。
 */

export function ProjectSwitcher() {
  const settings = useApp((s) => s.settings);
  const engine = useApp((s) => s.engine);
  const ready = engine?.lifecycle === 'ready';

  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState('');
  const boxRef = useRef<HTMLDivElement>(null);

  const activeId = settings?.activeProjectId ?? null;
  const active = projects.find((p) => p.id === activeId) ?? null;

  const load = useCallback(async () => {
    if (!ready) return;
    setLoading(true);
    try {
      const r = await projectApi.list('open');
      setProjects(r.projects ?? []);
    } catch {
      // 拉不到就当没有项目。**不显示报错** ——
      // 顶栏上一条常驻的红字，比"暂时没有项目可选"烦人得多
      setProjects([]);
    } finally {
      setLoading(false);
    }
  }, [ready]);

  // 只在展开的那一刻拉。常驻轮询是纯浪费：项目列表一天变不了几次
  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  // 点外面收起。用 mousedown 不用 click —— click 会在选项自己的
  // onClick 之后才冒泡到 document，导致"点选项"被当成"点外面"先收起来，
  // 表现是偶尔点不中
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const pick = (id: string | null) => {
    void window.synorive.settings.patch({ activeProjectId: id });
    setOpen(false);
  };

  const create = async () => {
    const title = draft.trim();
    if (!title) return;
    setCreating(true);
    try {
      // query 用标题占位：引擎那边 query 是必填的，而"项目"这个概念
      // 本身不一定对应一句查询词。留空会被引擎拒掉，而错误信息
      // 会说"query 不能为空"—— 用户完全看不懂那和他建项目有什么关系
      const p = await projectApi.create({ query: title, title });
      setProjects((ps) => [p, ...ps]);
      void window.synorive.settings.patch({ activeProjectId: p.id });
      setDraft('');
      setOpen(false);
    } catch {
      /* 建失败就保持面板开着，用户能看见自己刚打的字还在 */
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="projsw" ref={boxRef}>
      <button
        className={`projsw__btn${active ? ' projsw__btn--on' : ''}`}
        onClick={() => setOpen((v) => !v)}
        disabled={!ready}
        title={
          active
            ? `当前项目：${active.title || active.query}。新建的监控和出稿会归到它名下（不影响搜索范围）`
            : '还没归属任何项目。点一下可以选一个或新建'
        }
        aria-expanded={open}
      >
        <FolderGit2 size={14} strokeWidth={1.7} />
        <span className="projsw__name">{active ? active.title || active.query : '无项目'}</span>
        <ChevronDown size={13} strokeWidth={1.8} />
      </button>

      {open && (
        <div className="projsw__pop" role="menu">
          <div className="projsw__hint">
            项目只决定<strong>新建的监控和出稿归在谁名下</strong>，不会缩小搜索范围。
          </div>

          <button
            className={`projsw__opt${activeId === null ? ' projsw__opt--on' : ''}`}
            onClick={() => pick(null)}
            role="menuitem"
          >
            {activeId === null ? <Check size={13} strokeWidth={2.2} /> : <span className="projsw__dot" />}
            无项目
          </button>

          {loading && (
            <div className="projsw__loading">
              <Loader2 size={13} className="spin" strokeWidth={2} /> 正在读项目…
            </div>
          )}

          {!loading &&
            projects.map((p) => (
              <button
                key={p.id}
                className={`projsw__opt${activeId === p.id ? ' projsw__opt--on' : ''}`}
                onClick={() => pick(p.id)}
                role="menuitem"
                title={p.query}
              >
                {activeId === p.id ? (
                  <Check size={13} strokeWidth={2.2} />
                ) : (
                  <span className="projsw__dot" />
                )}
                <span className="projsw__optname">{p.title || p.query}</span>
                <span className="projsw__optmeta">{p.runCount} 轮</span>
              </button>
            ))}

          {!loading && projects.length === 0 && (
            <div className="projsw__loading">还没有项目——在下面起个名就能建一个。</div>
          )}

          <div className="projsw__new">
            <input
              className="textinput"
              value={draft}
              placeholder="新项目名，比如「毕设文献」"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void create();
                if (e.key === 'Escape') setOpen(false);
              }}
              aria-label="新项目名"
            />
            <button
              className="btn btn--primary"
              onClick={() => void create()}
              disabled={!draft.trim() || creating}
            >
              {creating ? <Loader2 size={13} className="spin" strokeWidth={2} /> : <Plus size={13} strokeWidth={2} />}
              建
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
