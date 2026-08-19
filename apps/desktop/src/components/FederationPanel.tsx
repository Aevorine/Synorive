import { useCallback, useEffect, useState } from 'react';
import { Library, Loader2, Plus, Trash2, TriangleAlert } from 'lucide-react';
import { api, type FederatedLib } from '../lib/api';

/**
 * 多库联邦检索（提案 37）
 * ============================================================
 * 工作一个库、私人一个库、外接硬盘上还躺着去年那个 —— 而"我记得看过"
 * 这件事本身不分库，人不会记得那句话当初存在哪儿。登记之后，搜索会一次问遍。
 *
 * 🔴 **必须写明副库只有关键词这一路。** 语义召回要求向量由同一个模型算出来，
 *    拿本库的模型去查别的库存的向量，得到的是"看着正常的胡说"。
 *    不说明的话，用户会以为副库的搜索能力和主库一样，然后困惑于
 *    "同样一句话在主库搜得到、在副库搜不到"。
 *
 * 🔴 **列表要直接显示连不连得上。** 硬盘拔了、库被加密了，
 *    如果只表现为"搜不到东西"，用户根本查不出原因。
 */
export function FederationPanel() {
  const [libs, setLibs] = useState<FederatedLib[]>([]);
  const [path, setPath] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(() => {
    api.federation
      .libs()
      .then(setLibs)
      .catch(() => {
        /* 引擎没起来 */
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

  const pick = async () => {
    // 让用户自己敲一长串路径是这一步最容易出错的地方，能开文件框就开
    const picked = await window.synorive.sys.pickFiles();
    if (picked?.[0]) setPath(picked[0]);
  };

  return (
    <div className="panel">
      <h3 className="panel__title">别的库</h3>
      <p className="panel__hint">
        登记之后，搜索可以一次问遍它们。副库<strong>只走关键词</strong>
        ，没有语义召回也没有重排 —— 语义要求向量由同一个模型算出来，跨库比对出来的分数是错的。
        因此副库有两条搜不到的边界，照实说明：正文按<strong>整词</strong>匹配 ——
        搜「预算」找不到正文里的「预算表」，得搜「预算表」；标题和路径可以搜片段，
        但要三个字以上。主库靠语义那一路把这类都捞回来，副库没有那一路。
        副库一律只读打开，绝不会被改动。
      </p>

      <div className="panel__row">
        <input
          className="textinput"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="库文件路径，例如 D:\\备份\\synorive.db"
        />
        <button className="btn btn--sm" onClick={pick} title="打开文件选择框挑一个库文件">
          浏览…
        </button>
      </div>
      <div className="panel__row">
        <input
          className="textinput"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="给它起个名（可留空，默认用文件名）"
          maxLength={40}
        />
        <button
          className="btn btn--sm btn--primary"
          disabled={busy || !path.trim()}
          onClick={() =>
            wrap(async () => {
              await api.federation.add(path.trim(), label.trim());
              setPath('');
              setLabel('');
            })
          }
          title="登记这个库。登记时会当场验一次能不能打开"
        >
          {busy ? <Loader2 size={13} strokeWidth={1.8} className="spin" /> : <Plus size={13} strokeWidth={1.8} />}
          加进来
        </button>
      </div>

      {err && <p className="banner banner--error">{err}</p>}

      {libs.length === 0 ? (
        <p className="syn-t-caption">还没有登记别的库。</p>
      ) : (
        <ul className="fed__list">
          {libs.map((l) => (
            <li key={l.id} className={l.reachable ? 'fed__row' : 'fed__row fed__row--dead'}>
              <label className="fed__toggle" title={l.enabled ? '暂时不搜这个库' : '把它加回搜索范围'}>
                <input
                  type="checkbox"
                  checked={l.enabled}
                  disabled={busy}
                  onChange={(e) => wrap(() => api.federation.toggle(l.id, e.target.checked))}
                />
                <Library size={13} strokeWidth={1.8} />
                {l.label}
              </label>
              <span className="fed__meta" title={l.dbPath}>
                {l.reachable ? `${l.itemCount} 条` : ''}
              </span>
              {!l.reachable && (
                <span className="fed__problem">
                  <TriangleAlert size={13} strokeWidth={1.8} />
                  {l.problem}
                </span>
              )}
              <button
                className="btn btn--sm"
                disabled={busy}
                onClick={() => wrap(() => api.federation.remove(l.id))}
                title="从列表里移除（不会删掉那个库文件）"
              >
                <Trash2 size={13} strokeWidth={1.8} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
