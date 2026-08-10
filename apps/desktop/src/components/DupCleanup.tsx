import { useState } from 'react';
import { AlertTriangle, Copy, Loader2, Trash2 } from 'lucide-react';
import { labApi, type DupSweep } from '../lib/labApi';

/**
 * E9 —— 近重复清理
 * ============================================================
 * 检测这块早就有了（`phash_buckets` + `/items/{id}/duplicates`），
 * 但只能回答"和**这一张**像的还有哪些"—— 而用户真正的问题是
 * **"我库里到底有多少重复，能不能一次清掉"**。
 * 要靠旧那条清库，得先知道该点开哪一张，而那正是他不知道的事。
 *
 * 🔴 **删除会进回收站，但不是瞬间撤销。** 索引照常立刻清干净（这里
 * 是两步：先干跑 `confirm:false`，一条都不动，只告诉你将要删什么，
 * 确认后才真删），恢复要重新投喂一次原路径，跟撤销键的感觉不一样。
 * 一步到位的"清理"按钮在这种代价不对称的操作上依然是危险的。
 *
 * 🔴 **只删库里的记录，不碰硬盘上的原文件。** 界面上必须把这句
 * 明写出来 —— 用户点"删除"时脑子里想的很可能是"把照片删了"，
 * 两者混为一谈的后果不可逆，而且他八成很久以后才发现。
 *
 * 🔴 **每组留哪一张由用户定。** `suggestKeep` 只是按分辨率×体积
 * 排出来的第一名，默认勾上但可以改 —— 他可能要留有 EXIF 的那张。
 */

export function DupCleanup() {
  const [data, setData] = useState<DupSweep | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  /** 勾中 = 要删。默认是每组除建议保留之外的全部 */
  const [marked, setMarked] = useState<Set<string>>(new Set());
  /** 干跑结果。非 null 时显示二次确认条 */
  const [pending, setPending] = useState<{ count: number; titles: string[] } | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const sweep = async () => {
    setBusy(true);
    setErr(null);
    setPending(null);
    setDone(null);
    try {
      const r = await labApi.dupSweep();
      setData(r);
      const next = new Set<string>();
      for (const g of r.groups) for (const m of g.members) if (!m.suggestKeep) next.add(m.id);
      setMarked(next);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) =>
    setMarked((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  /** 第一步：干跑。**不删任何东西**，只拿回"将要删什么" */
  const preview = async () => {
    if (marked.size === 0) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await labApi.deleteItems([...marked], false);
      setPending({ count: r.wouldDelete ?? 0, titles: r.titles ?? [] });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  /** 第二步：真删 */
  const commit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await labApi.deleteItems([...marked], true);
      setDone(r.note + (r.failed?.length ? `　有 ${r.failed.length} 条没删掉：${r.failed[0]?.error}` : ''));
      setPending(null);
      await sweep();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="syn-dup">
      <div className="syn-dup-bar">
        <button type="button" className="btn" onClick={() => void sweep()} disabled={busy}>
          {busy ? <Loader2 size={15} className="spin" aria-hidden /> : <Copy size={15} aria-hidden />}
          扫一遍全库重复图
        </button>
        {data && (
          <span className="syn-dup-sum">
            扫了 {data.scannedImages} 张 · {data.groupCount} 组重复 · 白占 {fmtSize(data.wastedBytes)}
            {data.truncated && '（只显示最占地方的前 200 组）'}
          </span>
        )}
      </div>

      {err && (
        <p className="syn-dup-err">
          <AlertTriangle size={14} aria-hidden /> {err}
        </p>
      )}
      {done && <p className="syn-dup-done">{done}</p>}

      {data && data.groups.length === 0 && <p className="syn-dup-empty">没找到指纹完全相同的图。</p>}

      {data && data.groups.length > 0 && (
        <>
          <ul className="syn-dup-list">
            {data.groups.map((g) => (
              <li className="syn-dup-group" key={g.phash}>
                <p className="syn-dup-ghead">
                  {g.count} 份一样的 · 去掉多余的能省 {fmtSize(g.wastedBytes)}
                </p>
                {g.members.map((m) => (
                  <label className="syn-dup-item" key={m.id} title={m.locator}>
                    <input
                      type="checkbox"
                      checked={marked.has(m.id)}
                      onChange={() => toggle(m.id)}
                    />
                    <span className="syn-dup-name">{m.title || basename(m.locator)}</span>
                    <span className="syn-dup-meta">
                      {m.width}×{m.height} · {fmtSize(m.sizeBytes)} · {m.createdAt.slice(0, 10)}
                    </span>
                    {m.suggestKeep && <span className="syn-dup-keep">建议留这张</span>}
                  </label>
                ))}
              </li>
            ))}
          </ul>

          {/* 🔴 两步删除。第一步只是干跑，屏幕上必须看得出"还没删" */}
          {pending ? (
            <div className="syn-dup-confirm">
              <p>
                <AlertTriangle size={14} aria-hidden /> 将要从库里删掉 <b>{pending.count}</b> 条。
                <b>硬盘上的原文件不动</b>，删的只是检索库里的记录 ——
                会进回收站，30 天内能恢复，但<b>恢复要重新投喂一次，不是瞬间撤销</b>。
              </p>
              <p className="syn-dup-titles">{pending.titles.join('、')}{pending.count > 20 && ' …'}</p>
              <button type="button" className="syn-dup-danger" onClick={() => void commit()} disabled={busy}>
                <Trash2 size={14} aria-hidden /> 确认删除
              </button>
              <button type="button" onClick={() => setPending(null)} disabled={busy}>
                算了
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn"
              onClick={() => void preview()}
              disabled={busy || marked.size === 0}
            >
              先看看要删哪 {marked.size} 条（还不会删）
            </button>
          )}
        </>
      )}

      {data && <p className="syn-dup-note">{data.note}</p>}
    </div>
  );
}

function basename(p: string): string {
  const i = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'));
  return i >= 0 ? p.slice(i + 1) : p;
}

function fmtSize(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)}GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)}MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${n}B`;
}
