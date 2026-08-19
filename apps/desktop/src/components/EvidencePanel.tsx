import { useState } from 'react';
import { ClipboardCheck, FileWarning, Loader2, ShieldCheck } from 'lucide-react';
import { api, type EvidenceChain, type EvidenceStatus } from '../lib/api';

/**
 * 出稿前的来源核对（提案 33）
 * ============================================================
 * 你摘了几段话准备发出去。这个按钮做的事是：**把每一份来源文件重新读一遍、
 * 重新算一次哈希**，和它当初入库时留下的指纹比对。
 *
 * 🔴 **有价值的输出是"对不上"那几行，不是"全部正常"。**
 *    一份只会显示"一切正常"的清单是没有用的 —— 它不可能出错，
 *    也就证明不了任何事。所以异常项排在最前面并且单独上色。
 *
 * 🔴 **它会真的去读盘，所以慢是正常的。** 按钮上要写清楚这一点，
 *    否则用户会以为卡死了。
 */
const TONE: Record<EvidenceStatus, { label: string; cls: string }> = {
  unchanged: { label: '未改动', cls: 'evi__row--ok' },
  changed: { label: '已改动', cls: 'evi__row--bad' },
  missing: { label: '已丢失', cls: 'evi__row--bad' },
  unverifiable: { label: '无法核对', cls: 'evi__row--warn' },
};

export function EvidencePanel({ itemIds }: { itemIds: string[] }) {
  const [chain, setChain] = useState<EvidenceChain | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const run = async () => {
    setBusy(true);
    setErr(null);
    setCopied(false);
    try {
      setChain(await api.evidenceChain(itemIds, { markdown: true }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : '核对没跑起来');
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!chain?.markdown) return;
    await navigator.clipboard.writeText(chain.markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  if (itemIds.length === 0) return null;

  // 异常的排前面 —— 用户要看的就是这几行
  const rows = chain
    ? [...chain.sources].sort(
        (a, b) => Number(a.status === 'unchanged') - Number(b.status === 'unchanged'),
      )
    : [];
  const bad = chain ? (chain.summary.changed ?? 0) + (chain.summary.missing ?? 0) : 0;

  return (
    <div className="evi">
      <button
        className="btn btn--sm"
        onClick={run}
        disabled={busy}
        title={`重新读取这 ${itemIds.length} 份来源文件并重算哈希，和入库时的指纹比对。文件大时要等几秒`}
      >
        {busy ? (
          <Loader2 size={13} strokeWidth={1.8} className="spin" />
        ) : (
          <ShieldCheck size={13} strokeWidth={1.8} />
        )}
        核对来源（{itemIds.length}）
      </button>

      {err && <p className="banner banner--error">{err}</p>}

      {chain && (
        <div className="evi__out">
          <div className="panel__row">
            <span className={bad > 0 ? 'evi__verdict evi__verdict--bad' : 'evi__verdict'}>
              {bad > 0 ? (
                <>
                  <FileWarning size={13} strokeWidth={1.8} />
                  {chain.summary.total} 份里 {bad} 份需要注意
                </>
              ) : (
                <>
                  <ShieldCheck size={13} strokeWidth={1.8} />
                  {chain.summary.total} 份来源都和入库时一致
                </>
              )}
            </span>
            <button
              className="btn btn--sm"
              onClick={copy}
              title="复制成一段可以贴进报告附录的表格"
            >
              <ClipboardCheck size={13} strokeWidth={1.8} />
              {copied ? '已复制' : '复制清单'}
            </button>
          </div>

          <ul className="evi__list">
            {rows.map((s) => (
              <li key={s.itemId} className={`evi__row ${TONE[s.status].cls}`}>
                <span className="evi__tag">{TONE[s.status].label}</span>
                <span className="evi__title" title={s.locator}>
                  {s.title}
                </span>
                {s.note && <span className="evi__note">{s.note}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
