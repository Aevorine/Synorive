import { useEffect, useState } from 'react';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import { api } from '../lib/api';

/**
 * 自定义同义词
 * ============================================================
 * 内置术语表不可能知道「小李」指的是谁 —— 每个人的黑话、缩写、项目代号
 * 只有他自己知道。加一对之后，搜 a 能命中 b，搜 b 也能命中 a。
 *
 * 🔴 **双向，而且要说出来。** 单向同义在实际使用里几乎总是让人困惑：
 *    "我明明设了同义词，怎么反过来搜就不行"。
 *
 * 🔴 **改完立刻生效。** 引擎侧加完会主动让检索器重读这张表；
 *    如果做成"下次重启才生效"，用户会当成这个功能是坏的。
 */
export function SynonymPanel() {
  const [items, setItems] = useState<{ a: string; b: string }[]>([]);
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.synonyms
      .list()
      .then((r) => alive && setItems(r.items))
      .catch(() => {
        /* 引擎还没起来。等它起来用户再进这一页就有了 */
      });
    return () => {
      alive = false;
    };
  }, []);

  const add = async () => {
    const x = a.trim();
    const y = b.trim();
    if (!x || !y) return;
    if (x === y) {
      setErr('两边写的是同一个词，加了没有意义');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await api.synonyms.add(x, y);
      setItems(r.items);
      setA('');
      setB('');
    } catch (e) {
      setErr(e instanceof Error ? e.message : '加不上，引擎可能没起来');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (x: string, y: string) => {
    setBusy(true);
    try {
      const r = await api.synonyms.remove(x, y);
      setItems(r.items);
    } catch (e) {
      setErr(e instanceof Error ? e.message : '删不掉，引擎可能没起来');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="syn-pairs">
      <div className="panel__row">
        <input
          className="syn-pairs__in"
          value={a}
          onChange={(e) => setA(e.target.value)}
          placeholder="小李"
          aria-label="同义词左边"
          onKeyDown={(e) => e.key === 'Enter' && void add()}
        />
        <span className="syn-pairs__eq" aria-hidden>
          ＝
        </span>
        <input
          className="syn-pairs__in"
          value={b}
          onChange={(e) => setB(e.target.value)}
          placeholder="李明"
          aria-label="同义词右边"
          onKeyDown={(e) => e.key === 'Enter' && void add()}
        />
        <button
          className="btn btn--sm btn--primary"
          onClick={() => void add()}
          disabled={busy || !a.trim() || !b.trim()}
          title="加这一对。两边互相都能搜到"
        >
          {busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} strokeWidth={2} />}
          加上
        </button>
      </div>

      {err && <p className="syn-pairs__err">{err}</p>}

      {items.length === 0 ? (
        <p className="syn-t-caption">还没加过。加了之后搜任意一边都能命中另一边。</p>
      ) : (
        <ul className="syn-pairs__list">
          {items.map((p) => (
            <li key={`${p.a}|${p.b}`} className="syn-pairs__item">
              <span className="syn-pairs__pair">
                {p.a} <span aria-hidden>⇄</span> {p.b}
              </span>
              <button
                className="syn-pairs__x"
                onClick={() => void remove(p.a, p.b)}
                disabled={busy}
                title={`删掉「${p.a} ⇄ ${p.b}」`}
                aria-label={`删掉 ${p.a} 和 ${p.b} 的同义关系`}
              >
                <Trash2 size={12} strokeWidth={1.8} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
