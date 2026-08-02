/**
 * E4 剪贴板哨兵 · 界面
 * ============================================================
 * 显示哨兵攒下来的内容，一条一条决定存不存。
 *
 * 🔒 这里显示的东西**只在主进程内存里**，没落盘。关掉哨兵或退出应用就没了。
 *    看着像"历史记录"，但它刻意不是——剪贴板历史一旦落盘就是个密码库。
 */

import { useEffect, useState } from 'react';
import { Check, Clipboard, Link2, Loader2, Trash2, X } from 'lucide-react';
import type { ClipEntry } from '../../electron/shared/ipc-contract';

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return '刚刚';
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  return `${Math.floor(s / 3600)} 小时前`;
}

export function ClipboardTray() {
  const [entries, setEntries] = useState<ClipEntry[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    void window.synorive.clip.list().then(setEntries);
    // payload 为 null = 哨兵被关掉了，列表要一起清空
    return window.synorive.clip.onCaptured((e) => {
      if (e === null) {
        setEntries([]);
        return;
      }
      setEntries((prev) => [e, ...prev.filter((x) => x.id !== e.id)].slice(0, 20));
    });
  }, []);

  const archive = async (id: string) => {
    setBusy(id);
    const ok = await window.synorive.clip.archive(id);
    setBusy(null);
    setEntries((prev) => prev.map((x) => (x.id === id ? { ...x, archived: ok } : x)));
  };

  const dismiss = async (id: string) => {
    await window.synorive.clip.dismiss(id);
    setEntries((prev) => prev.filter((x) => x.id !== id));
  };

  if (!entries.length) return null;

  return (
    <section className="cliptray">
      <div className="cliptray__head">
        <Clipboard size={14} strokeWidth={1.7} />
        <span className="cliptray__title">剪贴板</span>
        <span className="cliptray__note">只在内存里，存了才进库</span>
        <button
          className="btn btn--sm"
          onClick={() => {
            void window.synorive.clip.clear();
            setEntries([]);
          }}
        >
          全部清掉
        </button>
      </div>

      <div className="cliptray__list">
        {entries.map((e) => (
          <div key={e.id} className={`clip${e.archived ? ' clip--archived' : ''}`}>
            <div className="clip__icon">
              {e.kind === 'link' ? <Link2 size={15} strokeWidth={1.7} /> : <Clipboard size={15} strokeWidth={1.7} />}
            </div>

            {e.kind === 'image' ? (
              <img className="clip__thumb" src={e.content} alt={e.preview} />
            ) : (
              <div className="clip__text" title={e.content}>{e.preview}</div>
            )}

            <span className="clip__time">{timeAgo(e.capturedAt)}</span>

            {e.archived ? (
              <span className="clip__done"><Check size={14} strokeWidth={2} /> 已存</span>
            ) : (
              <button className="btn btn--sm" disabled={busy === e.id} onClick={() => void archive(e.id)}>
                {busy === e.id ? <Loader2 size={13} className="spin" strokeWidth={2} /> : null}
                存进库
              </button>
            )}
            <button className="clip__x" title="不要这条" onClick={() => void dismiss(e.id)}>
              {e.archived ? <Trash2 size={14} strokeWidth={1.7} /> : <X size={14} strokeWidth={1.7} />}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
