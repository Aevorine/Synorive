import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, RotateCcw, Trash2 } from 'lucide-react';
import { labApi, type TrashEntry } from '../lib/labApi';
import { VirtualList } from './VirtualList';

/**
 * 回收站 —— 删除进 30 天缓冲区，不是永久丢失
 * ============================================================
 * 🔴 **恢复不是瞬间撤销。** 删除时向量/关键词索引就已经清干净了（这是
 * repository.py `soft_delete_item` 权衡过的取舍——保留全部索引数据能让
 * 恢复更快，但代价是要么让已删内容继续占着索引表、要么给全库每一条
 * 搜索查询都加一层过滤，后者漏一处就会让"删了"的内容又搜得到）。
 * 恢复实际做的是"把原路径重新投喂一次"，跟第一次投喂差不多耗时，
 * 界面上必须显示进行中状态，不能假装是个瞬时操作。
 *
 * 🔴 **恢复要求原文件还在原来的位置。** 文件被移动/改名/删除的话恢复
 * 会失败，这条回收站记录还留着——用户可以先把文件挪回去再试，
 * 或者干脆点"彻底删除"清掉这条记录。
 */
export function TrashPanel() {
  const [entries, setEntries] = useState<TrashEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<Record<string, string>>({});

  const load = async () => {
    setErr(null);
    try {
      const r = await labApi.trashList();
      setEntries(r.entries);
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const restore = async (id: string) => {
    setBusyId(id);
    setRowError((m) => ({ ...m, [id]: '' }));
    try {
      await labApi.trashRestore(id);
      setEntries((list) => (list ? list.filter((e) => e.id !== id) : list));
    } catch (e) {
      setRowError((m) => ({ ...m, [id]: (e as Error).message }));
    } finally {
      setBusyId(null);
    }
  };

  const purge = async (id: string) => {
    setBusyId(id);
    try {
      await labApi.trashPurge(id);
      setEntries((list) => (list ? list.filter((e) => e.id !== id) : list));
    } catch (e) {
      setRowError((m) => ({ ...m, [id]: (e as Error).message }));
    } finally {
      setBusyId(null);
    }
  };

  if (entries === null) {
    return (
      <div className="trash">
        {err ? (
          <p className="field__hint">读取回收站失败：{err}</p>
        ) : (
          <p className="field__hint">
            <Loader2 size={13} className="spin" strokeWidth={2} /> 读取中…
          </p>
        )}
      </div>
    );
  }

  if (entries.length === 0) {
    return <p className="field__hint">回收站是空的。</p>;
  }

  return (
    // 回收站保留 30 天，重度整理的一天就能删掉上千条。
    // VirtualList 自带阈值：少于 60 条时它整个跳过虚拟化，直接全渲染。
    <VirtualList
      items={entries}
      className="trash"
      estimateHeight={72}
      keyOf={(e) => e.id}
    >
      {(e) => (
        <div className="trash__item" key={e.id}>
          <div className="trash__info">
            <span className="trash__title">{e.title || e.locator}</span>
            <span className="trash__meta">
              {e.locator} · 删除于 {new Date(e.deletedAt).toLocaleString()} ·
              {' '}
              {new Date(e.purgeAt) > new Date()
                ? `将于 ${new Date(e.purgeAt).toLocaleDateString()} 自动清除`
                : '即将自动清除'}
            </span>
            {rowError[e.id] && (
              <span className="trash__error">
                <AlertTriangle size={12} strokeWidth={2} /> {rowError[e.id]}
              </span>
            )}
          </div>
          <div className="trash__actions">
            <button
              className="btn btn--sm"
              disabled={busyId === e.id}
              onClick={() => void restore(e.id)}
              title="重新投喂这个路径，找回来（原文件要还在原位置）"
            >
              {busyId === e.id ? (
                <Loader2 size={13} className="spin" strokeWidth={2} />
              ) : (
                <RotateCcw size={13} strokeWidth={2} />
              )}
              恢复
            </button>
            <button
              className="btn btn--sm"
              disabled={busyId === e.id}
              onClick={() => void purge(e.id)}
              title="不等 30 天了，现在就彻底清掉这条记录（不碰硬盘原文件）"
            >
              <Trash2 size={13} strokeWidth={2} />
              彻底删除
            </button>
          </div>
        </div>
      )}
    </VirtualList>
  );
}
