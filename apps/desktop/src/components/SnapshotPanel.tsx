import { useCallback, useEffect, useState } from 'react';
import { Camera, GitCompareArrows, Loader2, Trash2 } from 'lucide-react';
import { api, type Snapshot, type SnapshotDiff } from '../lib/api';

/**
 * 时间机器快照（提案 34）
 * ============================================================
 * 记下"某一刻库里有哪些东西"，之后拿两个时刻对比：多了什么、少了什么、
 * 哪些原地改过、哪些是同一份东西重新入了一次库。
 *
 * 🔴 **必须在界面上明说它不是备份。** 名字听起来像能回滚，实际只存清单，
 *    救不回删掉的文件。让用户以为有备份、真出事时发现没有，
 *    比一开始就没这个功能糟得多。
 *
 * 🔴 **"重新入库"要单独一类。** 不然一份换了路径重进的资料会被报成
 *    "删了一条又加了一条"，两行噪音淹掉真正的变化。
 */
export function SnapshotPanel() {
  const [list, setList] = useState<Snapshot[]>([]);
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [diff, setDiff] = useState<SnapshotDiff | null>(null);
  const [against, setAgainst] = useState<string>('');

  const reload = useCallback(() => {
    api.snapshots
      .list()
      .then(setList)
      .catch(() => {
        /* 引擎没起来。等它起来再进这一页就有了 */
      });
  }, []);

  useEffect(reload, [reload]);

  const wrap = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : '操作没成功');
    } finally {
      setBusy(false);
    }
  };

  const compare = (id: string) =>
    wrap(async () => setDiff(await api.snapshots.diff(id, against || undefined)));

  return (
    <div className="panel">
      <h3 className="panel__title">库的快照</h3>
      <p className="panel__hint">
        只记清单（哪些资料在、指纹是什么），几 MB 而已。<strong>它不是备份</strong>
        ，救不回删掉的文件，只回答"这期间变了什么"。
      </p>

      <div className="panel__row">
        <input
          className="textinput"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="给这张快照起个名（可留空）"
          maxLength={60}
        />
        <button
          className="btn btn--sm btn--primary"
          disabled={busy}
          onClick={() => wrap(async () => {
            await api.snapshots.take(label);
            setLabel('');
          })}
          title="记下此刻库里有哪些资料"
        >
          {busy ? <Loader2 size={13} strokeWidth={1.8} className="spin" /> : <Camera size={13} strokeWidth={1.8} />}
          拍一张
        </button>
      </div>

      {err && <p className="banner banner--error">{err}</p>}

      {list.length === 0 ? (
        <p className="syn-t-caption">还没有快照。</p>
      ) : (
        <>
          <div className="panel__row">
            <select
              className="textinput"
              value={against}
              onChange={(e) => setAgainst(e.target.value)}
              title="拿哪张来比。选「现在」就是跟当前的库比"
            >
              <option value="">跟现在比</option>
              {list.map((s) => (
                <option key={s.id} value={s.id}>
                  跟「{s.label || s.takenAt}」比
                </option>
              ))}
            </select>
          </div>

          <ul className="snap__list">
            {list.map((s) => (
              <li key={s.id} className="snap__row">
                <span className="snap__label">{s.label || '（未命名）'}</span>
                <span className="snap__meta">
                  {s.takenAt.replace('T', ' ').slice(0, 16)} · {s.itemCount} 条
                  {s.auto && ' · 自动'}
                </span>
                <button
                  className="btn btn--sm"
                  disabled={busy || s.id === against}
                  onClick={() => compare(s.id)}
                  title={s.id === against ? '不能和自己比' : '看这张和所选目标之间差了什么'}
                >
                  <GitCompareArrows size={13} strokeWidth={1.8} />
                  对比
                </button>
                <button
                  className="btn btn--sm"
                  disabled={busy}
                  onClick={() => wrap(() => api.snapshots.remove(s.id))}
                  title="删掉这张快照"
                >
                  <Trash2 size={13} strokeWidth={1.8} />
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {diff && <DiffView diff={diff} onClose={() => setDiff(null)} />}
    </div>
  );
}

function DiffView({ diff, onClose }: { diff: SnapshotDiff; onClose: () => void }) {
  const groups = [
    { key: 'added', title: '新增', rows: diff.added },
    { key: 'removed', title: '不见了', rows: diff.removed },
    { key: 'changed', title: '原地改过', rows: diff.changed },
    { key: 'reingested', title: '重新入库（同一份内容换了 id）', rows: diff.reingested },
  ].filter((g) => g.rows.length > 0);

  const nothing = groups.length === 0;

  return (
    <div className="snap__diff">
      <div className="panel__row">
        <strong>
          和「{diff.other}」相比：新增 {diff.counts.added} · 不见了 {diff.counts.removed} · 改过{' '}
          {diff.counts.changed} · 重新入库 {diff.counts.reingested}
        </strong>
        <button className="btn btn--sm" onClick={onClose} title="收起对比结果">
          收起
        </button>
      </div>
      {nothing && <p className="syn-t-caption">这两个时刻之间没有变化。</p>}
      {groups.map((g) => (
        <div key={g.key}>
          <h4 className="panel__subtitle">
            {g.title}（{g.rows.length}）
          </h4>
          <ul className="snap__difflist">
            {g.rows.slice(0, 30).map((r) => (
              <li key={r.itemId ?? r.nowId} title={r.locator}>
                {r.title || r.locator || r.itemId}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {diff.truncated && (
        <p className="syn-t-caption">条数太多，上面只列了一部分。</p>
      )}
    </div>
  );
}
