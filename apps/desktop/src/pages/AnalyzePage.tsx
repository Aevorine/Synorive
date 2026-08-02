import { useEffect, useState } from 'react';
import { CheckCircle2, CircleAlert, Download, FilePlus, FolderPlus, Link2, Loader2 } from 'lucide-react';
import { PageState } from '../components/PageState';
import { api, type DoctorEntry } from '../lib/api';
import { useEngineData } from '../lib/useEngineData';
import { PAGE_TITLES, useApp } from '../lib/store';

/**
 * 分析中心 —— 投喂入口 + 依赖医生 + 进度看板（E3 / E14）
 *
 * 「自动配置需要的工具与内容」这条要求在这里变成用户看得见的东西：
 * 缺什么一目了然，缺了会失去什么写清楚，装它就是点一下。
 */

interface DepProgress {
  id: string;
  state: string;
  progress?: number;
  speed?: number;
  detail?: string;
  error?: string;
  downloaded?: number;
  totalBytes?: number;
}

const STATE_TEXT: Record<string, string> = {
  ok: '已就绪',
  missing: '未安装',
  failed: '有问题',
  installing: '安装中',
  downloading: '下载中',
};

export function AnalyzePage() {
  const engine = useApp((s) => s.engine);
  const detail = (engine?.detail ?? {}) as { indexedItems?: number; queueDepth?: number; activeJobs?: number };
  const [progress, setProgress] = useState<Record<string, DepProgress>>({});
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);

  const deps = useEngineData(() => api.doctor(), [], { refreshMs: 4000 });
  const stats = useEngineData(() => api.stats(), [], { refreshOn: ['ingest.job'], refreshMs: 3000 });

  // 依赖安装进度是引擎推过来的，不能靠轮询 —— 下载进度每秒变好几次
  useEffect(
    () =>
      window.synorive.engine.onEvent((raw) => {
        const ev = raw as { type?: string; payload?: DepProgress };
        if (ev?.type === 'dependency.status' && ev.payload?.id) {
          setProgress((p) => ({ ...p, [ev.payload!.id]: ev.payload! }));
        }
      }),
    [],
  );

  const addFolder = async () => {
    const dirs = await window.synorive.sys.pickFolders();
    if (dirs.length) await api.ingest({ targets: dirs, source: 'file', recursive: true });
  };
  const addFiles = async () => {
    const files = await window.synorive.sys.pickFiles();
    if (files.length) await api.ingest({ targets: files, source: 'file', recursive: false });
  };
  const addUrl = async () => {
    const u = url.trim();
    if (!u) return;
    setBusy(true);
    try {
      await api.ingest({ targets: [u], source: 'link', recursive: false });
      setUrl('');
    } finally {
      setBusy(false);
    }
  };

  const required = (deps.data ?? []).filter((d) => !d.optional);
  const optional = (deps.data ?? []).filter((d) => d.optional);

  return (
    <div className="page">
      <div className="page__header">
        <h1 className="page__title">{PAGE_TITLES.analyze}</h1>
        <span className="page__subtitle">
          {stats.data ? `已索引 ${stats.data.items.toLocaleString('zh-CN')} 条` : ''}
          {detail.queueDepth ? ` · 队列 ${detail.queueDepth}` : ''}
          {detail.activeJobs ? ` · ${detail.activeJobs} 个任务进行中` : ''}
        </span>
      </div>

      <div className="page__body">
        <section className="panel">
          <h2 className="panel__title">投喂内容</h2>
          <p className="panel__hint">
            也可以直接把文件、图片、链接拖到窗口任意位置 —— 松手就开始分析。
          </p>
          <div className="panel__row">
            <button className="btn btn--primary" onClick={addFolder}>
              <FolderPlus size={15} strokeWidth={1.7} /> 选文件夹
            </button>
            <button className="btn" onClick={addFiles}>
              <FilePlus size={15} strokeWidth={1.7} /> 选文件
            </button>
          </div>
          <div className="panel__row">
            <div className="urlbox">
              <Link2 size={15} strokeWidth={1.7} className="urlbox__icon" />
              <input
                className="urlbox__input"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && addUrl()}
                placeholder="粘一个网址，抓正文并存档一份快照（原网页删了你这儿还在）"
                spellCheck={false}
              />
            </div>
            <button className="btn" onClick={addUrl} disabled={busy || !url.trim()}>
              {busy ? <Loader2 size={15} className="spin" /> : null} 抓取
            </button>
          </div>
        </section>

        <section className="panel">
          <h2 className="panel__title">能力与依赖</h2>
          <p className="panel__hint">
            缺什么、缺了会失去什么，都写在下面。装它就是点一下——下载支持断点续传，
            国内会自动切镜像。
          </p>

          <PageState
            loading={deps.loading}
            error={deps.error}
            empty={(deps.data ?? []).length === 0}
            emptyTitle="拿不到依赖清单"
            emptyHint="引擎可能还没就绪。稍等一下，或者点重试。"
            onRetry={deps.reload}
          >
            <>
              {required.length > 0 && <DepList title="必需" items={required} progress={progress} />}
              {optional.length > 0 && <DepList title="可选" items={optional} progress={progress} />}
            </>
          </PageState>
        </section>
      </div>
    </div>
  );
}

function DepList({
  title,
  items,
  progress,
}: {
  title: string;
  items: DoctorEntry[];
  progress: Record<string, DepProgress>;
}) {
  return (
    <>
      <h3 className="panel__subtitle">{title}</h3>
      <div className="deplist">
        {items.map((d) => {
          const p = progress[d.id];
          const state = p?.state ?? d.state;
          const busy = state === 'installing' || state === 'downloading';
          return (
            <div key={d.id} className="dep">
              <div className="dep__icon">
                {state === 'ok' ? (
                  <CheckCircle2 size={17} strokeWidth={1.8} className="dep__ok" />
                ) : busy ? (
                  <Loader2 size={17} strokeWidth={1.8} className="spin" />
                ) : (
                  <CircleAlert size={17} strokeWidth={1.8} className="dep__miss" />
                )}
              </div>

              <div className="dep__main">
                <div className="dep__name">{d.name}</div>
                <div className="dep__purpose">{d.purpose}</div>
                {state !== 'ok' && d.degradesTo && (
                  <div className="dep__degrade">缺了会：{d.degradesTo}</div>
                )}
                {(p?.error || d.error) && (
                  <div className="dep__error">{p?.error || d.error}</div>
                )}
                {d.note && <div className="dep__degrade">{d.note}</div>}

                {busy && (
                  <div className="dep__progress">
                    <div
                      className="dep__bar"
                      style={{ width: `${Math.round((p?.progress ?? 0) * 100)}%` }}
                    />
                    <span className="dep__pct">
                      {Math.round((p?.progress ?? 0) * 100)}%
                      {p?.speed ? `　${(p.speed / 1e6).toFixed(1)} MB/s` : ''}
                      {p?.detail ? `　${p.detail}` : ''}
                    </span>
                  </div>
                )}
              </div>

              <div className="dep__action">
                {state === 'ok' ? (
                  <span className="dep__state">{STATE_TEXT[state]}</span>
                ) : busy ? (
                  <span className="dep__state">{STATE_TEXT[state]}</span>
                ) : d.kind === 'binary' ? (
                  <span className="dep__state" title="外部程序需要你自己装">
                    手动安装
                  </span>
                ) : (
                  <button className="btn btn--sm" onClick={() => void api.installDep(d.id)}>
                    <Download size={13} strokeWidth={1.8} /> 安装
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
