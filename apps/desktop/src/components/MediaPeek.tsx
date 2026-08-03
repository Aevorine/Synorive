import { useState } from 'react';
import { AlertTriangle, Eye, Loader2 } from 'lucide-react';
import { labApi, type MediaPreview } from '../lib/labApi';

/**
 * A2 —— 视频「先看后搜」
 * ============================================================
 * 拿到一个陌生视频，第一个问题不是"搜什么"，是"**这里面是什么**"。
 * 完整分析要几十秒到几分钟；这条快速通道目标 1 秒内给两样东西：
 * 一条等距缩略带 + 一条语音波形。
 *
 * 🔴 **不入库。** 这是"看一眼"不是"分析"。看完觉得有用再按投喂 ——
 * 反过来（先入库再让用户决定要不要）会让库里堆满随手看过的东西。
 *
 * 🔴 **必须把 `note` 原样显示出来。** 里面写着两条能力边界：
 * ① 缩略带是等距抽的，不是镜头分割 —— 不说的话用户会拿这 12 格
 *    去对正式分析出来的镜头数，然后以为哪里出错了；
 * ② 波形只反映音量，分不出人声和背景音乐。
 * 把它折叠或省略，等于把这个功能唯一的免责说明删掉。
 */

export function MediaPeek() {
  const [data, setData] = useState<MediaPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pick = async () => {
    const files = await window.synorive.sys.pickFiles();
    const first = files[0];
    if (!first) return;
    setBusy(true);
    setErr(null);
    setData(null);
    try {
      setData(await labApi.previewMedia(first));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="syn-peek">
      <div className="syn-peek-bar">
        <button type="button" className="btn" onClick={() => void pick()} disabled={busy}>
          {busy ? <Loader2 size={15} className="spin" aria-hidden /> : <Eye size={15} aria-hidden />}
          先看一眼视频
        </button>
        <span className="syn-peek-hint">
          不入库、不分析，只抽一条缩略带和音量波形 —— 看完觉得有用再投喂
        </span>
        {data?.ok && <span className="syn-peek-ms">{data.elapsedMs} ms</span>}
      </div>

      {err && (
        <p className="syn-peek-err">
          <AlertTriangle size={14} aria-hidden /> {err}
        </p>
      )}

      {data && !data.ok && (
        <p className="syn-peek-err">
          <AlertTriangle size={14} aria-hidden /> {data.note}
        </p>
      )}

      {data?.ok && (
        <>
          <p className="syn-peek-meta">
            {fmtDur(data.durationSec)}
            {data.width ? ` · ${data.width}×${data.height}` : ''}
            {data.hasAudio ? ' · 有音轨' : ' · 无音轨'}
          </p>

          {data.thumbs.length > 0 && (
            <div className="syn-peek-strip">
              {/* key 用 sec+下标：引擎已经去过重，但 key 撞了是**静默**漏渲染，
                  多一个下标兜底的成本是零 */}
              {data.thumbs.map((t, i) => (
                <figure key={`${t.sec}-${i}`} className="syn-peek-cell">
                  <img src={t.dataUrl} alt={`第 ${fmtDur(t.sec)} 秒`} loading="lazy" />
                  <figcaption>{fmtDur(t.sec)}</figcaption>
                </figure>
              ))}
            </div>
          )}

          {data.waveform.length > 0 && (
            <div className="syn-peek-wave" aria-label="音量包络">
              {data.waveform.map((v, i) => (
                <span
                  // 波形条没有天然主键，位置就是它的身份 —— 这里用下标是对的，
                  // 数组不会被插入或重排，只会整条换掉
                  key={i}
                  className="syn-peek-bar-i"
                  // 高度是**数据**不是样式：每根条子的高就是那一段的音量。
                  // 这种东西没法放进设计令牌，只能内联
                  style={{ height: `${Math.max(2, Math.round(v * 100))}%` }}
                />
              ))}
            </div>
          )}

          {/* 🔴 能力边界原样显示，不折叠 */}
          <p className="syn-peek-note">{data.note}</p>
        </>
      )}
    </div>
  );
}

function fmtDur(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const mm = String(m % 60).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}
