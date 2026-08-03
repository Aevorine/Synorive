import { useEffect, useRef, useState } from 'react';
import { Film, Loader2, Play } from 'lucide-react';
import { api, enginePort, type SceneRow } from '../lib/api';

/**
 * 视频场景缩略条 —— N3
 * ============================================================
 * 拖一个视频进来，**先把它切成一条能点的镜头带**。
 *
 * 这个功能的数据早在阶段 3 就全有了：场景切分 4/4 精确命中、
 * 关键帧 5/5 抽出、每帧都算了 CLIP 向量、快速通道 88.6 倍速。
 * 缺的一直只是两样东西 —— 一条能把关键帧发出来的路由（刚补上），
 * 和这个组件。
 *
 * **为什么值得单独做一条缩略带，而不是让用户去搜**：
 * 搜的前提是你知道要找什么。而拿到一个陌生视频时，第一个问题
 * 是"这里面大概有什么"—— 那是**看**的问题不是**搜**的问题。
 * 一条 20 格的缩略带三秒就能回答它，任何搜索框都做不到。
 *
 * 🔴 **点一格是就地播放，不是调外部播放器。**
 * 外部播放器打开后停在 0 秒，用户还得自己拖到第 3 分 24 秒 ——
 * 那就把"定位到秒"这个唯一有价值的部分丢掉了。
 */

export function SceneStrip({
  itemId,
  locator,
  /** 从搜索结果点进来时，直接高亮命中的那一段 */
  focusSec,
}: {
  itemId: string;
  locator: string;
  focusSec?: number;
}) {
  const [scenes, setScenes] = useState<SceneRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setScenes(null);
    setErr(null);
    api
      .scenes(itemId)
      .then((r) => alive && setScenes(r))
      .catch((e) => alive && setErr((e as Error).message));
    return () => {
      alive = false;
    };
  }, [itemId]);

  // 带上命中的那一段就自动滚过去并选中 —— 从搜索结果点进来时，
  // 用户要的是"那一秒"，让他自己在 20 格里找是本末倒置
  useEffect(() => {
    if (focusSec == null || !scenes?.length) return;
    const i = scenes.findIndex((s) => focusSec >= s.startSec && focusSec < s.endSec);
    if (i >= 0) {
      setActive(i);
      stripRef.current?.children[i]?.scrollIntoView({
        behavior: 'smooth',
        inline: 'center',
        block: 'nearest',
      });
    }
  }, [focusSec, scenes]);

  const play = (i: number, sec: number) => {
    setActive(i);
    const v = videoRef.current;
    if (!v) return;
    // 元数据没加载完时设 currentTime 会被丢掉 —— 这是个很常见的坑，
    // 症状是"第一次点没反应，第二次才跳对"
    const seek = () => {
      v.currentTime = sec;
      void v.play().catch(() => {
        /* 自动播放被拦就算了，用户点一下播放键即可 */
      });
    };
    if (v.readyState >= 1) seek();
    else v.addEventListener('loadedmetadata', seek, { once: true });
  };

  if (err) return <p className="strip__msg">读不出场景：{err}</p>;
  if (!scenes) {
    return (
      <p className="strip__msg">
        <Loader2 size={13} className="spin" /> 正在读场景…
      </p>
    );
  }
  if (!scenes.length) {
    return (
      <p className="strip__msg">
        这个视频还没切出场景 —— 可能还在后台分析，或者它整段没有明显的镜头切换。
      </p>
    );
  }

  const port = enginePort();

  return (
    <div className="strip">
      <header className="strip__head">
        <Film size={14} aria-hidden />
        <span>{scenes.length} 个镜头</span>
        {active != null && (
          <span className="strip__now">
            当前：{fmt(scenes[active]!.startSec)} – {fmt(scenes[active]!.endSec)}
          </span>
        )}
      </header>

      {/* 就地播放。外部播放器打开后停在 0 秒，等于把"定位到秒"丢掉了 */}
      <video
        ref={videoRef}
        className="strip__player"
        controls
        preload="metadata"
        src={`file:///${locator.replace(/\\/g, '/')}`}
      />

      <div className="strip__rail" ref={stripRef}>
        {scenes.map((s, i) => (
          <button
            key={s.index}
            className={`strip__cell ${active === i ? 'is-on' : ''}`}
            onClick={() => play(i, s.startSec)}
            title={s.transcript || `${fmt(s.startSec)} – ${fmt(s.endSec)}`}
          >
            {s.keyframePath && port ? (
              <img
                src={`http://127.0.0.1:${port}/api/media/thumb/${encodeURIComponent(s.keyframePath)}`}
                alt={`${fmt(s.startSec)} 的画面`}
                loading="lazy"
              />
            ) : (
              <span className="strip__nokey">
                <Play size={14} />
              </span>
            )}
            <span className="strip__t">{fmt(s.startSec)}</span>
            {/* 有台词就显示一行 —— 这是"这段讲了什么"最直接的答案，
                比再看一眼画面有用得多 */}
            {s.transcript && <span className="strip__line">{s.transcript.slice(0, 24)}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

function fmt(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
