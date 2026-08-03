import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  Loader2,
  RefreshCw,
  RotateCw,
} from 'lucide-react';
import type { UpdateState } from '@shared/ipc-contract';

/**
 * U 组 —— 应用更新
 * ============================================================
 * 一屏里要回答四个问题，缺一个用户就会来问："我现在是哪个版本"、
 * "有没有新的"、"下到哪了"、"装了之后会发生什么"。
 *
 * 🔴 **「重启并安装」是这个应用里唯一一个会自己关掉应用的按钮。**
 * 所以它必须：① 文案里就写明会重启 ② 二次确认 ③ 不出现在任何
 * 其他状态下（只有 downloaded 才渲染它）。
 *
 * 🔴 便携版走 `unsupported` 分支而不是报错。那不是故障，
 * 是这个分发形式的固有限制，给一条"去下载页"的路就够了。
 */

const DOWNLOAD_PAGE = 'https://github.com/Aevorine/Synorive/releases/latest';

function humanBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 MB';
  const mb = n / 1024 / 1024;
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

function humanEta(state: UpdateState): string {
  const left = state.totalBytes - state.transferredBytes;
  if (state.bytesPerSecond <= 0 || left <= 0) return '';
  const sec = Math.round(left / state.bytesPerSecond);
  if (sec < 60) return `约还剩 ${sec} 秒`;
  return `约还剩 ${Math.ceil(sec / 60)} 分钟`;
}

export function UpdatePanel() {
  const [state, setState] = useState<UpdateState | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let alive = true;
    void window.synorive.updater.getState().then((s) => {
      if (alive && s) setState(s);
    });
    const off = window.synorive.updater.onStateChanged((s) => setState(s));
    return () => {
      alive = false;
      off();
    };
  }, []);

  if (!state) return <p className="panel__hint">正在读更新状态…</p>;

  const openDownloadPage = () => void window.synorive.sys.openExternal(DOWNLOAD_PAGE);

  return (
    <div className="upd">
      <div className="upd__version">
        <span className="upd__vlabel">当前版本</span>
        <span className="upd__vnum">v{state.currentVersion}</span>
        {state.lastCheckedAt && (
          <span className="upd__checked">
            上次检查 {new Date(state.lastCheckedAt).toLocaleString('zh-CN')}
          </span>
        )}
      </div>

      {/* ── 不支持自更新（便携版 / 开发模式）───────────────── */}
      {state.lifecycle === 'unsupported' && (
        <>
          <p className="upd__note upd__note--info">
            <AlertTriangle size={14} aria-hidden /> {state.unsupportedReason}
          </p>
          <div className="panel__row">
            <button className="btn btn--sm" onClick={openDownloadPage}>
              <ExternalLink size={13} aria-hidden /> 打开下载页
            </button>
          </div>
        </>
      )}

      {/* ── 正常那条路 ──────────────────────────────────── */}
      {state.lifecycle !== 'unsupported' && (
        <>
          {state.lifecycle === 'up-to-date' && (
            <p className="upd__note upd__note--ok">
              <CheckCircle2 size={14} aria-hidden /> 已是最新版本。
            </p>
          )}

          {state.lifecycle === 'error' && (
            <p className="upd__note upd__note--err">
              <AlertTriangle size={14} aria-hidden /> {state.error}
            </p>
          )}

          {(state.lifecycle === 'available' || state.lifecycle === 'downloaded') &&
            state.latestVersion && (
              <div className="upd__new">
                <p className="upd__newline">
                  发现新版本 <strong>v{state.latestVersion}</strong>
                  {state.lifecycle === 'downloaded' && '（已下载完，等你确认安装）'}
                </p>
                {state.releaseNotes && (
                  <details className="upd__notes">
                    <summary>更新说明</summary>
                    <pre className="upd__noteswrap">{state.releaseNotes}</pre>
                  </details>
                )}
              </div>
            )}

          {state.lifecycle === 'downloading' && (
            <div className="upd__progress" role="progressbar" aria-valuenow={state.progressPercent}>
              <div className="upd__bar" style={{ width: `${state.progressPercent}%` }} />
              <span className="upd__pct">
                {state.progressPercent}% · {humanBytes(state.transferredBytes)} /{' '}
                {humanBytes(state.totalBytes)} {humanEta(state)}
              </span>
            </div>
          )}

          <div className="panel__row upd__actions">
            <button
              className="btn btn--sm"
              onClick={() => void window.synorive.updater.check()}
              disabled={state.lifecycle === 'checking' || state.lifecycle === 'downloading'}
            >
              {state.lifecycle === 'checking' ? (
                <>
                  <Loader2 size={13} className="spin" aria-hidden /> 检查中…
                </>
              ) : (
                <>
                  <RefreshCw size={13} aria-hidden /> 检查更新
                </>
              )}
            </button>

            {state.lifecycle === 'available' && (
              <>
                <button
                  className="btn btn--sm btn--primary"
                  onClick={() => void window.synorive.updater.download()}
                >
                  <Download size={13} aria-hidden /> 下载新版本
                </button>
                <button
                  className="btn btn--sm"
                  onClick={() =>
                    state.latestVersion &&
                    void window.synorive.updater.skip(state.latestVersion)
                  }
                  title="只跳过这一个版本，再出更新的还会提示"
                >
                  跳过这个版本
                </button>
              </>
            )}

            {state.lifecycle === 'downloaded' && !confirming && (
              <button className="btn btn--sm btn--primary" onClick={() => setConfirming(true)}>
                <RotateCw size={13} aria-hidden /> 重启并安装
              </button>
            )}

            {/* 🔴 二次确认。这个按钮按下去应用立刻就没了，
                没有确认的话用户手滑一下正在做的事就断了 */}
            {state.lifecycle === 'downloaded' && confirming && (
              <>
                <span className="upd__confirm">
                  这会立刻关闭 Synorive 并运行安装程序，未保存的输入会丢失。确定吗？
                </span>
                <button
                  className="btn btn--sm btn--primary"
                  onClick={() => void window.synorive.updater.install()}
                >
                  确定，现在重启
                </button>
                <button className="btn btn--sm" onClick={() => setConfirming(false)}>
                  再等等
                </button>
              </>
            )}

            <button className="btn btn--sm" onClick={openDownloadPage}>
              <ExternalLink size={13} aria-hidden /> 下载页
            </button>
          </div>

          {state.skippedVersion && (
            <p className="panel__hint">
              你跳过了 v{state.skippedVersion}。它不会再主动提示，但点「检查更新」仍然查得到。
            </p>
          )}
        </>
      )}
    </div>
  );
}
