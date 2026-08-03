import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Copy, Loader2, RefreshCw, Smartphone } from 'lucide-react';
import { labApi, type SyncStatus } from '../lib/labApi';

/**
 * E17 端到端加密同步 ｜ 6.5 离线队列
 * ============================================================
 * 桌面 ↔ 手机之间同步，载荷用一把**只由配对口令派生**的密钥加密。
 *
 * 🔴 **口令只在配对那一下用一次，之后不再离开这台机器。**
 * 派生出的密钥存在引擎内存里，不落盘、不写日志、不回显。
 * 引擎重启就要重新配对 —— 这是刻意的代价，和云端 Key 同一条约定。
 *
 * 🔴 **指纹是用来肉眼比对的，不是装饰。** 两台设备各自算一遍，
 * 指纹一样才说明钥匙一样。不比对的话，口令打错一个字的表现是
 * "配对成功了，但之后所有数据都解不开" —— 而那时候用户已经不记得
 * 是哪一步出的问题了。
 *
 * 🔴 **没装 cryptography = 同步整个不可用**，不是降级成明文同步。
 * 这里照实说，因为"以为在同步其实一条都没推出去"是最坏的状态。
 */

export function SyncPanel() {
  const [st, setSt] = useState<SyncStatus | null>(null);
  const [pass, setPass] = useState('');
  const [salt, setSalt] = useState('');
  const [fp, setFp] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await labApi.syncStatus());
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pair = async () => {
    if (!pass.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await labApi.syncPair(pass, salt.trim() || undefined);
      setSalt(r.salt);
      setFp(r.fingerprint);
      // 🔴 配对完立刻把输入框里的口令清掉。留在 DOM 里没有任何好处，
      // 而截图、录屏、别人路过看一眼都会把它带走
      setPass('');
      await load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!st) {
    return err ? (
      <p className="sy__err">
        <AlertTriangle size={14} aria-hidden /> 读不到同步状态：{err}
      </p>
    ) : (
      <p className="sy__hint">正在读同步状态…</p>
    );
  }

  return (
    <div className="sy">
      {!st.cryptoAvailable && (
        <p className="sy__warn">
          <AlertTriangle size={13} aria-hidden />
          <span>
            <b>同步整个不可用</b>（不是降级、不是明文同步）—— 引擎缺 <code>cryptography</code> 库。
            装一下：<code>pip install "synorive[sync]"</code>。
            这里不提供任何"先不加密用着"的选项：那等于把你的资料明文发到局域网上。
          </span>
        </p>
      )}

      <p className="sy__meta">
        本机设备号 <code>{st.deviceId}</code> · 逻辑时钟 {st.lamport} · 待推 {st.pending} 条 ·
        已跟踪 {st.entities} 个条目 · 墓碑 {st.tombstones}
      </p>

      <div className="sy__row">
        <input
          className="sy__pass"
          type="password"
          value={pass}
          onChange={(e) => setPass(e.target.value)}
          placeholder="配对口令（两台设备输一样的）"
          autoComplete="off"
          spellCheck={false}
          disabled={!st.cryptoAvailable}
        />
        <input
          className="sy__salt"
          value={salt}
          onChange={(e) => setSalt(e.target.value)}
          placeholder="盐（第二台设备粘这里）"
          spellCheck={false}
          disabled={!st.cryptoAvailable}
        />
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => void pair()}
          disabled={busy || !pass.trim() || !st.cryptoAvailable}
        >
          {busy ? (
            <Loader2 size={15} className="spin" aria-hidden />
          ) : (
            <Smartphone size={15} aria-hidden />
          )}
          配对
        </button>
      </div>

      {fp && (
        <div className="sy__fp">
          <p>
            密钥指纹 <code>{fp}</code>
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => void navigator.clipboard.writeText(`${salt} ${fp}`)}
              title="复制盐和指纹，拿去另一台设备比对"
            >
              <Copy size={12} aria-hidden /> 复制盐+指纹
            </button>
          </p>
          {/* 🔴 这句必须有：不比对指纹的话，口令打错一个字的表现是
              「配对成功了但之后全解不开」，而那时候已经查不出是哪一步了 */}
          <p className="sy__hint">
            到另一台设备上用<b>同一个口令</b>和这个盐再配一次，
            <b>两边指纹必须一模一样</b>。对不上就是口令打错了 ——
            这时候不比对的话，要等到同步之后发现"所有数据都解不开"才知道。
          </p>
        </div>
      )}

      {err && (
        <p className="sy__err">
          <AlertTriangle size={14} aria-hidden /> {err}
        </p>
      )}

      <div className="sy__row">
        <button type="button" className="btn btn--sm" onClick={() => void load()}>
          <RefreshCw size={12} aria-hidden /> 刷新
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() =>
            void labApi
              .syncPurge()
              .then(load)
              .catch((e: Error) => setErr(e.message))
          }
          title="清掉已确认的历史和过期墓碑"
        >
          清理历史
        </button>
        <span className="sy__hint">{st.note}</span>
      </div>
    </div>
  );
}
