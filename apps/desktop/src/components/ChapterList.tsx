import { useEffect, useState } from 'react';
import { AlertTriangle, ListTree, Loader2 } from 'lucide-react';
import { labApi, type ChaptersResult } from '../lib/labApi';

/**
 * A6 —— 长视频/音频章节目录
 * ============================================================
 * 场景缩略条（N3）回答的是"这里面大概有什么画面"；
 * 章节目录回答的是"**这里面讲了几件事，各在第几分钟**"。
 * 两个粒度差一个数量级：缩略条 200 多格，目录十几条。
 *
 * 🔴 **`method === 'equal'` 必须显眼地说出来。** 那表示没有转写、
 * 也没有足够的场景数据，章节边界纯粹是按时长等分的 ——
 * 不说的话用户会点进"第 5 章"发现讲的是别的东西，
 * 然后再也不用这个功能。说清楚了它反而还有用（起码能跳）。
 */

export function ChapterList({
  itemId,
  onJump,
}: {
  itemId: string;
  /** 点一章跳到那一秒。不给就只显示不跳 —— 有些场景（MCP 预览）没有播放器 */
  onJump?: (sec: number) => void;
}) {
  const [data, setData] = useState<ChaptersResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr(null);
    labApi
      .chapters(itemId)
      .then((r) => alive && setData(r))
      .catch((e) => alive && setErr((e as Error).message));
    return () => {
      alive = false;
    };
  }, [itemId]);

  if (err) {
    return (
      <p className="syn-chap-err">
        <AlertTriangle size={14} aria-hidden /> 章节读不出来：{err}
      </p>
    );
  }
  if (!data) {
    return (
      <p className="syn-chap-busy">
        <Loader2 size={14} className="syn-spin" aria-hidden /> 正在分章……
      </p>
    );
  }
  if (!data.chapters.length) {
    return <p className="syn-chap-empty">{data.note}</p>;
  }

  return (
    <nav className="syn-chap" aria-label="章节目录">
      <h3 className="syn-chap-title">
        <ListTree size={15} aria-hidden /> 章节　<span>{data.count} 章</span>
      </h3>

      {data.method === 'equal' && (
        <p className="syn-chap-warn">
          <AlertTriangle size={14} aria-hidden />
          按时长等分，不是按内容切分。时间码可跳转，标题不代表该段内容。
        </p>
      )}

      <ol className="syn-chap-list">
        {data.chapters.map((c) => (
          <li key={c.index}>
            <button
              type="button"
              className="syn-chap-item"
              onClick={() => onJump?.(c.startSec)}
              disabled={!onJump}
            >
              <span className="syn-chap-tc">{c.timecode}</span>
              <span className="syn-chap-name">
                {c.title}
                {c.titleSource === 'timecode' && (
                  <em className="syn-chap-noname">（这一段没挑出标题）</em>
                )}
              </span>
              {c.summary && <span className="syn-chap-sum">{c.summary}</span>}
            </button>
          </li>
        ))}
      </ol>

      <p className="syn-chap-note">{data.note}</p>
    </nav>
  );
}
