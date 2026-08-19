import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, Lock, Unlock } from 'lucide-react';

/**
 * 资料库整库加密
 * ============================================================
 * 「电脑丢了、硬盘被拆走，别人拿到的是一堆乱码」。
 *
 * 🔴 **开启前必须让用户明确确认"口令丢了就永远打不开"。**
 *    这不是免责声明式的客套 —— 它是真的：SQLCipher 没有后门，
 *    我们也没有任何找回手段。一个用户在不理解这一点的情况下开了加密，
 *    等于我们亲手帮他把资料锁死了。所以确认框不是可选的礼貌，
 *    是这个功能能不能交付的前提。
 *
 * 🔴 **状态照实显示。** 这台机器上没有 sqlcipher 时直接说"用不了"，
 *    不给一个点了没反应的开关。
 */
export function DbEncryptPanel() {
  const [st, setSt] = useState<{
    engineReady: boolean;
    cipherAvailable: boolean;
    encrypted: boolean;
    keyStored: boolean;
  } | null>(null);
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = () => void window.synorive.db.encryptStatus().then(setSt);
  useEffect(refresh, []);

  const enable = async () => {
    setErr(null);
    setMsg(null);
    if (pw !== pw2) {
      setErr('两次输入的口令不一样');
      return;
    }
    if (pw.length < 8) {
      setErr('口令至少 8 位');
      return;
    }
    setBusy(true);
    const r = await window.synorive.db.encryptEnable(pw);
    setBusy(false);
    if (r.ok) {
      setMsg('已加密。引擎正在重启，之后这个库离开这台机器就是一堆乱码。');
      setPw('');
      setPw2('');
      setAcknowledged(false);
      setTimeout(refresh, 2500);
    } else {
      setErr(r.error ?? '没能开启');
    }
  };

  const disable = async () => {
    setErr(null);
    setMsg(null);
    setBusy(true);
    const r = await window.synorive.db.encryptDisable(pw);
    setBusy(false);
    if (r.ok) {
      setMsg('已转回明文。');
      setPw('');
      setTimeout(refresh, 2500);
    } else {
      setErr(r.error ?? '没能关闭');
    }
  };

  if (!st) return <p className="syn-t-caption">正在读加密状态…</p>;

  if (!st.engineReady) {
    return <p className="syn-t-caption">引擎还没就绪，读不到加密状态。等它起来这里会自动刷新。</p>;
  }

  if (!st.cipherAvailable) {
    return (
      <p className="dbenc__err">
        <AlertTriangle size={13} aria-hidden />
        这台机器上没有 SQLCipher，整库加密用不了。**不会退回"假加密"** ——
        与其让你以为加密了，不如直接告诉你没有。
      </p>
    );
  }

  if (st.encrypted) {
    return (
      <div className="dbenc">
        <p className="dbenc__on">
          <Lock size={13} aria-hidden /> 已加密。这个库拷到别的机器上、或者硬盘被拆走，
          没有口令都打不开。
          {!st.keyStored && '（这台机器上没存住口令，下次启动要手动输一次）'}
        </p>
        <div className="panel__row">
          <input
            className="syn-pairs__in"
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder="输入当前口令以转回明文"
            aria-label="当前口令"
          />
          <button className="btn btn--sm" onClick={() => void disable()} disabled={busy || !pw}>
            {busy ? <Loader2 size={13} className="spin" /> : <Unlock size={13} strokeWidth={1.8} />}
            转回明文
          </button>
        </div>
        {err && <p className="dbenc__err">{err}</p>}
        {msg && <p className="syn-t-caption">{msg}</p>}
      </div>
    );
  }

  return (
    <div className="dbenc">
      <div className="panel__row">
        <input
          className="syn-pairs__in"
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          placeholder="设一个口令（至少 8 位）"
          aria-label="加密口令"
        />
        <input
          className="syn-pairs__in"
          type="password"
          value={pw2}
          onChange={(e) => setPw2(e.target.value)}
          placeholder="再打一遍"
          aria-label="再次输入口令"
        />
      </div>

      {/* 🔴 这个勾必须由用户亲手点。默认勾上、或者做成一句小字说明，
          都等于没说 —— 而代价是他的全部资料 */}
      <label className="dbenc__ack">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
        />
        <span>
          我知道<strong>口令丢了这个库就永远打不开</strong>，没有后门也没有找回。
          我已经把口令记在别的地方了。
        </span>
      </label>

      <div className="panel__row">
        <button
          className="btn btn--sm btn--primary"
          onClick={() => void enable()}
          disabled={busy || !acknowledged || pw.length < 8 || pw !== pw2}
          title={
            !acknowledged
              ? '先确认上面那条'
              : pw !== pw2
                ? '两次输入的口令不一样'
                : '把整个资料库转成加密的'
          }
        >
          {busy ? <Loader2 size={13} className="spin" /> : <Lock size={13} strokeWidth={1.8} />}
          开启整库加密
        </button>
        <span className="syn-t-caption">转换要重写一遍整个库，库大的话要等一会儿</span>
      </div>

      {err && <p className="dbenc__err">{err}</p>}
      {msg && <p className="syn-t-caption">{msg}</p>}
    </div>
  );
}
