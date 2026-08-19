import { useCallback, useEffect, useState } from 'react';
import { CircleHelp, Loader2, Search, Users } from 'lucide-react';
import {
  api,
  type RelationBucket,
  type RelationEntity,
  type RelationTimeline as TL,
} from '../lib/api';

/**
 * 人物关系时间线（提案 35）
 * ============================================================
 * 图谱回答的是"谁和谁有关"，但它没有时间 —— 一张把三年共现全揉在一起的网，
 * 看起来谁都跟谁有关，其实什么也说明不了。
 *
 * 这里换一个问题：**盯住一个人，看他在不同时期分别跟谁一起出现。**
 * "上半年一直跟 A，下半年换成了 B" —— 这种变化才有信息量，
 * 而它在一张无时间的网里完全看不出来。
 *
 * 🔴 **时间是估的就要标出来。** 一次性导入的历史资料没有内容时间，
 *    只能拿入库时间凑，于是所有事都"发生"在导入那天 ——
 *    图很漂亮，结论全是错的。所以每一格都带一个问号图标。
 */
const BUCKETS: { id: RelationBucket; label: string }[] = [
  { id: 'month', label: '按月' },
  { id: 'quarter', label: '按季' },
  { id: 'year', label: '按年' },
  { id: 'week', label: '按周' },
];

export function RelationTimeline() {
  const [q, setQ] = useState('');
  const [cands, setCands] = useState<RelationEntity[]>([]);
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [bucket, setBucket] = useState<RelationBucket>('month');
  const [tl, setTl] = useState<TL | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 打开就先给一批最常出现的，别让用户对着空框想"我该输什么"
  useEffect(() => {
    api.relations
      .entities('', '', 20)
      .then(setCands)
      .catch(() => {
        /* 引擎没起来 */
      });
  }, []);

  const find = useCallback(async () => {
    setErr(null);
    try {
      setCands(await api.relations.entities(q.trim(), '', 20));
    } catch (e) {
      setErr(e instanceof Error ? e.message : '查不了');
    }
  }, [q]);

  useEffect(() => {
    if (!pickedId) return;
    let alive = true;
    setBusy(true);
    setErr(null);
    api.relations
      .timeline(pickedId, bucket)
      .then((r) => alive && setTl(r))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : '出不来'))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [pickedId, bucket]);

  return (
    <div className="panel">
      <h3 className="panel__title">谁在什么时候和谁一起出现</h3>

      <div className="panel__row">
        <input
          className="textinput"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void find()}
          placeholder="输名字找人、机构、地点…"
        />
        <button className="btn btn--sm" onClick={() => void find()} title="按名字查">
          <Search size={13} strokeWidth={1.8} />
        </button>
        {BUCKETS.map((b) => (
          <button
            key={b.id}
            className={`chip${bucket === b.id ? ' chip--on' : ''}`}
            onClick={() => setBucket(b.id)}
            title={`把时间按${b.label.slice(1)}分格`}
          >
            {b.label}
          </button>
        ))}
      </div>

      <div className="rel__cands">
        {cands.map((e) => (
          <button
            key={e.id}
            className={`chip${pickedId === e.id ? ' chip--on' : ''}`}
            onClick={() => setPickedId(e.id)}
            title={`看「${e.name}」的关系变化（共出现 ${e.mentionCount} 次）`}
          >
            {e.name}
          </button>
        ))}
        {cands.length === 0 && <p className="syn-t-caption">库里还没有识别出实体。</p>}
      </div>

      {err && <p className="banner banner--error">{err}</p>}
      {busy && (
        <p className="syn-t-caption">
          <Loader2 size={13} strokeWidth={1.8} className="spin" /> 正在算…
        </p>
      )}

      {tl && !busy && <TimelineBody tl={tl} onPick={setPickedId} />}
    </div>
  );
}

function TimelineBody({ tl, onPick }: { tl: TL; onPick: (id: string) => void }) {
  if (tl.buckets.length === 0) {
    return <p className="syn-t-caption">「{tl.entity.name}」没有可以排上时间的出现记录。</p>;
  }
  const peak = Math.max(...tl.buckets.map((b) => b.count), 1);

  return (
    <>
      {tl.changes.length > 0 && (
        <div className="rel__changes">
          <h4 className="panel__subtitle">变化点</h4>
          <ul>
            {tl.changes.slice(-8).reverse().map((c) => (
              <li key={c.at}>
                <span className="rel__at">{c.at}</span>
                {c.appeared.length > 0 && <span className="rel__in">＋{c.appeared.join('、')}</span>}
                {c.disappeared.length > 0 && (
                  <span className="rel__out">－{c.disappeared.join('、')}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ul className="rel__track">
        {tl.buckets.map((b) => (
          <li key={b.at} className="rel__slot">
            <div className="rel__slothead">
              <span className="rel__at">{b.at}</span>
              <span
                className="rel__bar"
                style={{ width: `${Math.round((b.count / peak) * 100)}%` }}
                title={`这段时间出现在 ${b.count} 份资料里`}
              />
              {b.estimated > 0 && (
                <span
                  className="rel__guess"
                  title={`这一格里有 ${b.estimated} 条没有内容时间，是拿入库时间估的，位置可能不准`}
                >
                  <CircleHelp size={12} strokeWidth={1.8} />
                </span>
              )}
            </div>
            <div className="rel__peers">
              {b.peers.map((p) => (
                <button
                  key={p.id}
                  className="chip"
                  onClick={() => onPick(p.id)}
                  title={`同框 ${p.count} 次 · 点一下换成看「${p.name}」`}
                >
                  <Users size={11} strokeWidth={1.8} />
                  {p.name}
                </button>
              ))}
              {b.peerTotal > b.peers.length && (
                <span className="syn-t-caption">还有 {b.peerTotal - b.peers.length} 个</span>
              )}
              {b.peers.length === 0 && <span className="syn-t-caption">这段时间是单独出现的</span>}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
