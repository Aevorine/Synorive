import { useEffect, useState } from 'react';
import { CheckCircle2, CircleAlert, Download, FilePlus, FolderPlus, Link2, Loader2 } from 'lucide-react';
import { BatchCockpit, type BatchState } from '../components/BatchCockpit';
import { CompareView } from '../components/CompareView';
import { ImageLanes } from '../components/ImageLanes';
import { ArchiveShot } from '../components/ArchiveShot';
import { MediaPeek } from '../components/MediaPeek';
import { PageState } from '../components/PageState';
import { api, type DoctorEntry } from '../lib/api';
import { labApi, type IngestJob } from '../lib/labApi';
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
  /** F2：当前正在盯的那个摄取任务。null = 驾驶舱不显示 */
  const [jobId, setJobId] = useState<string | null>(null);
  /**
   * A1 投喂零等待 —— 用户刚点完"选文件"的那一瞬间就有的东西。
   *
   * 🔴 **这是唯一能做到「零等待」的信息来源。** 引擎那边要先展开目录、
   * 建任务、起线程，第一次能回答"总共几个文件"至少是几百毫秒之后；
   * 而这几个路径在对话框关掉的那一刻就已经在手上了。等引擎回话再画，
   * 中间那段空白就是用户唯一会抱怨的那段空白。
   *
   * 🔴 **它不入库、不产生任何记录**，纯粹是界面上的占位。
   * 真往库里插一条半成品记录的话，那条记录在搜索结果里
   * 和真记录长得一模一样，而它其实什么内容都没有。
   */
  const [queued, setQueued] = useState<string[]>([]);
  const [job, setJob] = useState<IngestJob | null>(null);
  const [jobErr, setJobErr] = useState<string | null>(null);

  /**
   * F2 —— 轮询任务进度。
   *
   * 🔴 **用轮询而不是 `ingest.job` 事件**：那个事件引擎只在**任务结束时**
   * 发一次，中途一个字都没有。想要"现在卡在哪个文件"就必须自己问。
   * 1 秒一次：再快没有意义（进度本来就是 0.5 秒才更新一次），
   * 再慢用户会觉得数字是死的。
   *
   * 🔴 **任务跑完就停掉定时器**。跑完了还每秒发一个请求，是把一个
   * 后台任务变成了永久的背景负载 —— 而且这种浪费没有任何症状，
   * 只有翻网络面板才看得见。
   */
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const d = await labApi.ingestJob(jobId);
        if (!alive) return;
        setJob(d);
        setJobErr(null);
        if (d.status === 'running') timer = window.setTimeout(tick, 1000);
      } catch (e) {
        if (!alive) return;
        // 引擎重启过 → 任务表清空 → 404。这是正常情况，
        // 但必须说出来，否则用户看到一个永远停在某个数字的驾驶舱
        setJobErr(`进度查不到了：${(e as Error).message}`);
      }
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  /**
   * 接住命令面板的「比一比两个文件」。
   *
   * 🔴 和 `syn:research-*` 一样，这个事件以前**没有任何监听方** ——
   * 点命令只做了 `setPage('analyze')`，然后就没了。比对面板在这一页
   * 靠下的位置，用户落地后看到的是投喂区，会以为命令点错了。
   */
  useEffect(() => {
    const onOpen = () =>
      document.getElementById('syn-compare')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.addEventListener('syn:open-compare', onOpen);
    return () => window.removeEventListener('syn:open-compare', onOpen);
  }, []);

  const controlJob = async (action: 'pause' | 'resume' | 'cancel') => {
    if (!jobId) return;
    try {
      const r = await labApi.controlIngest(jobId, action);
      // 🔴 不做乐观更新：立刻把引擎回来的真实状态拿一次。
      // 按钮显示"已暂停"而引擎其实没停，是最难查的一类错觉
      const d = await labApi.ingestJob(jobId);
      setJob(d);
      if (!r.ok) setJobErr(r.note);
    } catch (e) {
      setJobErr((e as Error).message);
    }
  };

  const retryPaths = async (paths: string[]) => {
    if (!paths.length) return;
    // 重试 = 拿失败清单重新起一个任务。**不复用原任务** ——
    // 原任务的统计数字已经定稿了，往里塞新结果会让"失败 37 个"
    // 这个数字在用户眼皮底下自己变小，谁也说不清最后到底失败了几个
    const r = await api.ingest({ targets: paths, source: 'file', recursive: false });
    setJobId(r.jobId);
  };

  /**
   * 引擎那份 JSON 翻译成驾驶舱要的形状。
   *
   * A1 的关键在于：**`job` 还是 null 的时候也要有东西可画**。
   * 那一小段（点完对话框 → 引擎回 jobId）是唯一一段用户会觉得
   * "点了没反应"的时间，而它恰好是我们已经知道要处理哪些文件的时间。
   *
   * 🔴 **选的是文件夹时，`queued` 里只有文件夹本身**（展开成几千个文件
   * 是引擎干的活，界面这时候还不知道）。所以占位卡片上写的是
   * "排队中"而不是具体进度 —— 编一个假的总数比不给数字更糟。
   */
  const placeholders = queued.map((p) => ({ path: p, status: 'pending' as const }));
  const batch: BatchState | null = job
    ? {
        jobId: job.jobId,
        total: job.total,
        done: job.done,
        failed: job.failed,
        skipped: job.skipped,
        running: job.status === 'running',
        paused: job.paused,
        current: job.current ?? undefined,
        startedAt: job.startedAt > 0 ? job.startedAt * 1000 : Date.now(),
        // 引擎已经开始报数了就以它为准 —— 这时候占位卡片的使命结束了，
        // 继续留着会和真实清单打架（同一个路径出现两次，一个"排队中"一个"失败"）
        items: job.items.map((i) => ({
          path: i.path,
          status: i.status,
          error: i.error || undefined,
        })),
      }
    : placeholders.length > 0
      ? {
          jobId: '',
          total: placeholders.length,
          done: 0,
          failed: 0,
          skipped: 0,
          running: true,
          paused: false,
          startedAt: Date.now(),
          items: placeholders,
        }
      : null;

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
    if (!dirs.length) return;
    // A1：**先画后发请求**。setState 是同步排进这一帧的，
    // 而 api.ingest 要跨进程往返 —— 顺序反过来，卡片就晚一个往返才出现
    setQueued(dirs);
    setJobId(null);
    const r = await api.ingest({ targets: dirs, source: 'file', recursive: true });
    setJobId(r.jobId);
  };
  const addFiles = async () => {
    const files = await window.synorive.sys.pickFiles();
    if (!files.length) return;
    setQueued(files);
    setJobId(null);
    const r = await api.ingest({ targets: files, source: 'file', recursive: false });
    setJobId(r.jobId);
  };
  const addUrl = async () => {
    const u = url.trim();
    if (!u) return;
    setBusy(true);
    setQueued([u]);
    setJobId(null);
    try {
      const r = await api.ingest({ targets: [u], source: 'link', recursive: false });
      setJobId(r.jobId);
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

        {/* A2 先看后搜：紧贴投喂按钮 —— 它是投喂的**前一步**
            （"这个视频值不值得入库"），放远了就没人会先看再喂 */}
        <section className="panel">
          <h2 className="panel__title">先看一眼</h2>
          <MediaPeek />
        </section>

        {/* C12/C13 整页归档：紧跟在「抓取」那个 URL 框后面 ——
            两者输入一样（一个网址），差别只在存不存版面证据 */}
        <section className="panel">
          <h2 className="panel__title">整页存档</h2>
          <p className="panel__hint">
            正文入库让它<strong>搜得到</strong>，整页截图让它<strong>赖不掉</strong> ——
            页面改了删了，你手上还有当时的版面。
          </p>
          <ArchiveShot />
        </section>

        {/* A3 一图四路：和「先看一眼」是同一类动作（投喂前先弄清这是什么），
            所以挨着放。视频走上面那个，图片走这个 */}
        <section className="panel">
          <h2 className="panel__title">一张图，四路一起查</h2>
          <ImageLanes />
        </section>

        {/* F2 驾驶舱：紧贴投喂区下面 —— 用户刚点完"选文件夹"，
            眼睛就在这一带，进度出现在别处等于没出现 */}
        {batch && (
          <section className="panel">
            {jobErr && <p className="panel__hint">{jobErr}</p>}
            {job?.itemsTruncated && (
              <p className="panel__hint">
                ⚠️ 异常清单封顶 500 条，下面显示的<strong>不是全部</strong>。
                实际失败数以上面那个数字为准。
              </p>
            )}
            <BatchCockpit
              state={batch}
              canControl={jobId != null}
              onPause={() => void controlJob('pause')}
              onResume={() => void controlJob('resume')}
              onCancel={() => void controlJob('cancel')}
              onRetry={(paths) => void retryPaths(paths)}
              onClose={() => {
                setJobId(null);
                setQueued([]);
              }}
            />
          </section>
        )}

        {/* A5：比一比。放在投喂下面、依赖上面 —— 它和投喂是同一类动作
            （"我手上有文件，想让它做点什么"），而依赖那一块是环境配置，
            属于"偶尔来一次"的东西 */}
        <section className="panel" id="syn-compare">
          <CompareView />
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
