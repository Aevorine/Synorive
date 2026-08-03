import { AlertTriangle, Copy, ExternalLink, RotateCw, Wand2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useApp } from '../lib/store';

/**
 * 引擎起不来时的引导页
 *
 * 起因：打包后的应用第一次运行，Python 引擎不在包里（引擎侧依赖 500+ MB，
 * 塞进安装包会让它从 101 MB 涨到 700 MB），于是用户看到的是一个
 * 状态栏写着「引擎启动失败」、其它什么都没有的死界面。
 *
 * 一个装完打不开的应用，比一个装的时候多花两分钟的应用糟糕得多。
 * 所以把"缺什么、怎么补"直接摊在界面正中间，而不是藏在状态栏的五个字里。
 */

const SETUP_STEPS = [
  {
    title: '装 Python 3.11 或更新的版本',
    detail: '官网 python.org 下载，安装时勾上「Add to PATH」。已经装过就跳过。',
    cmd: null as string | null,
  },
  {
    title: '装引擎依赖',
    detail: '约 250 MB，国内会自动走清华镜像。装完就能用文本检索了。',
    cmd: 'pip install -e engine',
  },
  {
    title: '回到这里点「重试」',
    detail: '引擎会自动被拉起来。图片 OCR、语音转写这些可选能力，之后在「分析中心」里一键装。',
    cmd: null,
  },
];

export function EngineSetup() {
  const engine = useApp((s) => s.engine);
  const [copied, setCopied] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<{ step: string; message: string }[]>([]);

  // 自举进度。**装依赖要一两分钟，只给一个转圈的话用户分不清
  // "在装"和"卡死了"** —— 和深挖那个实时进度是同一条理由
  useEffect(
    () => window.synorive.engine.onBootstrapProgress((p) => setLog((l) => [...l, p])),
    [],
  );

  const autoSetup = async () => {
    setBusy(true);
    setLog([]);
    try {
      const r = await window.synorive.engine.bootstrap();
      if (!r.ok && r.error) setLog((l) => [...l, { step: 'error', message: r.error! }]);
    } catch (e) {
      setLog((l) => [...l, { step: 'error', message: (e as Error).message }]);
    } finally {
      setBusy(false);
    }
  };

  const copy = (text: string) => {
    void navigator.clipboard.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(null), 1600);
  };

  const retry = async () => {
    setRetrying(true);
    try {
      await window.synorive.engine.restart();
    } finally {
      // 重启是异步的，状态会从引擎状态推回来，这里只是解掉按钮的禁用
      setTimeout(() => setRetrying(false), 3000);
    }
  };

  return (
    <div className="setup">
      <div className="setup__card">
        <div className="setup__head">
          <AlertTriangle size={22} strokeWidth={1.7} className="setup__icon" />
          <h1 className="setup__title">还差最后一步：装引擎</h1>
        </div>

        <p className="setup__lead">
          界面已经装好了，但分析和检索跑在一个独立的 Python 引擎里 ——
          它的依赖有 250 MB 以上，塞进安装包会让安装包从 100 MB 涨到 700 MB，
          所以放在第一次运行时按需装。
        </p>

        {engine?.lastError && (
          <div className="setup__error">
            引擎报的错：{engine.lastError}
            {engine.restartCount > 0 && `（已自动重试 ${engine.restartCount} 次）`}
          </div>
        )}

        <ol className="setup__steps">
          {SETUP_STEPS.map((s, i) => (
            <li key={s.title} className="setup__step">
              <span className="setup__num">{i + 1}</span>
              <div className="setup__stepbody">
                <div className="setup__steptitle">{s.title}</div>
                <div className="setup__stepdetail">{s.detail}</div>
                {s.cmd && (
                  <div className="setup__cmd">
                    <code>{s.cmd}</code>
                    <button
                      className="setup__copy"
                      onClick={() => copy(s.cmd!)}
                      title="复制命令"
                    >
                      <Copy size={13} strokeWidth={1.8} />
                      {copied === s.cmd ? '已复制' : '复制'}
                    </button>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>

        {/* 🔴 自动配置放在最显眼的位置，手动三步退到它下面 ——
            锚点 2「可以自动配置需要的工具与内容」的落地。
            让用户照着敲两条命令，是把我们该干的活推给了他 */}
        <div className="setup__auto">
          <button
            className="btn btn--primary btn--lg"
            onClick={() => void autoSetup()}
            disabled={busy}
          >
            <Wand2 size={16} strokeWidth={1.8} className={busy ? 'spin' : ''} />
            {busy ? '正在自动配置…' : '让它自己装好（推荐）'}
          </button>
          <p className="setup__autohint">
            自己找一个够格的 Python、建一个专属环境、把引擎装进去。
            全程不动你系统里已有的 Python，装的东西跟着应用走、卸载时一起消失。
            <strong>第一次要一两分钟</strong>，之后开机就直接能用。
          </p>
          {log.length > 0 && (
            <div className="setup__log" role="status" aria-live="polite">
              {log.slice(-6).map((l, i) => (
                <div key={i} className={l.step === 'error' ? 'setup__logerr' : undefined}>
                  {l.message}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="setup__actions">
          <button className="btn" onClick={retry} disabled={retrying || busy}>
            <RotateCw size={15} strokeWidth={1.8} className={retrying ? 'spin' : ''} />
            {retrying ? '正在重试…' : '我自己装好了，重试'}
          </button>
          <button
            className="btn"
            onClick={() => void window.synorive.sys.openExternal('https://www.python.org/downloads/')}
          >
            <ExternalLink size={15} strokeWidth={1.8} /> 打开 Python 下载页
          </button>
        </div>

        <p className="setup__foot">
          不想现在装也没关系 —— 界面可以随便看，只是搜不出东西。
          引擎起来之后这个页面会自动消失。
        </p>
      </div>
    </div>
  );
}
