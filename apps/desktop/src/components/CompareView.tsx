import { useState } from 'react';
import { AlertTriangle, FileDiff, Loader2 } from 'lucide-react';
import { labApi, type CompareResult } from '../lib/labApi';

/**
 * A5 —— 拖两个文件进来，直接告诉我哪里不一样
 * ============================================================
 * 三件事各自都有专门的工具（Beyond Compare / 看图软件 / 视频编辑器），
 * 但都要先找到文件、打开软件、配一遍。而"手上正好有两个文件、
 * 正好想知道差别"的那一刻，往往就不值得为它开一个新软件 —— 于是就不比了。
 *
 * 🔴 **只报差异，不判断哪个版本更好。** 哪个对是用户的事。
 *
 * 🔴 **两边类型不同时直接说"没法比"**，不硬凑一个相似度数字出来。
 * 一张图和一个 txt 做行级 diff 会得到 0%，那个数字看起来像结论，
 * 实际毫无意义，而且会误导。
 */

export function CompareView() {
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [res, setRes] = useState<CompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function onDrop(e: React.DragEvent, slot: 'a' | 'b'): void {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    // 🔴 **必须走 preload 的 `webUtils.getPathForFile`。**
    //    这里原来读的是 `f.path` —— 那个属性 **Electron 32 起已经被移除**，
    //    读出来永远是 undefined 且不报错。也就是说这个组件的拖拽
    //    从升级到 Electron 41 那天起就一直走的是下面那条"拿不到路径"分支，
    //    而提示语写的是"试试从文件管理器里拖"，把一个必然失败
    //    引导成了用户的操作问题。（同一个坑 TopBar 的拖拽早就修过了。）
    let p = '';
    try {
      if (f) p = window.synorive.sys.pathForFile(f);
    } catch {
      p = '';
    }
    if (!p) {
      setErr('拿不到这个文件的路径，试试从文件管理器里拖，或者直接粘贴完整路径');
      return;
    }
    setErr(null);
    (slot === 'a' ? setA : setB)(p);
  }

  async function run(): Promise<void> {
    if (!a || !b) return;
    setBusy(true);
    setErr(null);
    try {
      setRes(await labApi.compareFiles(a, b));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="syn-cmp">
      <h2 className="syn-cmp-title">
        <FileDiff size={16} aria-hidden /> 比一比
      </h2>

      <div className="syn-cmp-slots">
        {(['a', 'b'] as const).map((slot) => {
          const val = slot === 'a' ? a : b;
          const set = slot === 'a' ? setA : setB;
          return (
            <div
              key={slot}
              className={`syn-cmp-slot ${val ? 'is-filled' : ''}`}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => onDrop(e, slot)}
            >
              <label htmlFor={`cmp-${slot}`}>{slot === 'a' ? '文件一' : '文件二'}</label>
              <input
                id={`cmp-${slot}`}
                value={val}
                onChange={(e) => set(e.target.value)}
                placeholder="拖一个文件进来，或粘贴完整路径"
                spellCheck={false}
              />
            </div>
          );
        })}
      </div>

      <button type="button" className="syn-cmp-go" disabled={!a || !b || busy} onClick={() => void run()}>
        {busy ? <Loader2 size={14} className="syn-spin" aria-hidden /> : null}
        比一下
      </button>

      {err && (
        <p className="syn-cmp-err">
          <AlertTriangle size={14} aria-hidden /> {err}
        </p>
      )}

      {res?.error && <p className="syn-cmp-err">{res.error}</p>}

      {res && !res.error && (
        <div className="syn-cmp-out">
          <p className="syn-cmp-verdict">{res.verdict}</p>
          {res.note && <p className="syn-cmp-note">{res.note}</p>}

          {res.kind === 'text' && (
            <>
              <p className="syn-cmp-stat">
                相似度 {Math.round((res.similarity ?? 0) * 100)}%　新增 {res.added} 行　删除{' '}
                {res.removed} 行
              </p>
              <div className="syn-cmp-hunks">
                {(res.hunks ?? []).map((h) => (
                  <div key={`${h.tag}-${h.aStart}`} className="syn-cmp-hunk">
                    <p className="syn-cmp-hunk-head">
                      第 {h.aStart} 行　{tagLabel(h.tag)}
                    </p>
                    {h.aLines.map((l, i) => (
                      <p key={`a${i}`} className="syn-cmp-del">
                        − {l}
                      </p>
                    ))}
                    {h.bLines.map((l, i) => (
                      <p key={`b${i}`} className="syn-cmp-add">
                        + {l}
                      </p>
                    ))}
                  </div>
                ))}
              </div>
            </>
          )}

          {res.kind === 'image' && (
            <p className="syn-cmp-stat">
              感知哈希距离 {res.distance}
              {res.aSize && res.bSize && (
                <>
                  　尺寸 {res.aSize.join('×')} vs {res.bSize.join('×')}
                </>
              )}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function tagLabel(tag: string): string {
  return tag === 'insert' ? '只在文件二里有' : tag === 'delete' ? '只在文件一里有' : '两边都有但不一样';
}
