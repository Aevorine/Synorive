import { useEffect, useState } from 'react';
import QRCode from 'qrcode';
import { QrCode, RefreshCw } from 'lucide-react';

/**
 * 配对二维码
 * ============================================================
 * 原来配对要手抄三串东西：IP、端口、32 位十六进制令牌。
 * 抄错任意一个字符，手机端得到的都是同一句"连不上" —— 而他没法知道
 * 是抄错了、还是网络不通、还是电脑上没开。这是这个功能最大的摩擦点。
 *
 * 🔴 **二维码里带的是完整凭据，等于把钥匙印在屏幕上。**
 *    所以：① 默认折叠，点了才显示 —— 不能让它一直挂在设置页上，
 *    旁边路过一个人、或者屏幕共享/录屏时就出去了；
 *    ② 显示两分钟自动收起。
 *
 * 🔴 **端口每次启动都会变**，所以二维码必须跟着引擎端口实时重画 ——
 *    画一次存起来的话，重启电脑之后那张码扫出来是连不上的旧端口，
 *    而用户完全不知道为什么"扫了没用"。
 */
export function PairingQr({
  addrs,
  port,
  token,
}: {
  addrs: string[];
  /** 引擎端口。null/undefined = 引擎还没就绪 */
  port: number | null | undefined;
  token: string;
}) {
  const [shown, setShown] = useState(false);
  const [pick, setPick] = useState(0);
  const [img, setImg] = useState<string | null>(null);
  const [left, setLeft] = useState(0);

  const host = addrs[pick];
  const payload =
    host && port
      ? `synorive://pair?h=${encodeURIComponent(host)}&p=${port}&t=${encodeURIComponent(token)}`
      : null;

  useEffect(() => {
    if (!shown || !payload) {
      setImg(null);
      return;
    }
    let alive = true;
    void QRCode.toDataURL(payload, { errorCorrectionLevel: 'M', margin: 1, width: 220 })
      .then((d) => alive && setImg(d))
      .catch(() => alive && setImg(null));
    return () => {
      alive = false;
    };
  }, [shown, payload]);

  // 两分钟自动收起。**必须是真的收起**，不是只把图片换掉 ——
  // 留一个"已过期"的空框在那儿，用户会以为功能坏了
  useEffect(() => {
    if (!shown) return;
    setLeft(120);
    const t = window.setInterval(() => {
      setLeft((n) => {
        if (n <= 1) {
          setShown(false);
          return 0;
        }
        return n - 1;
      });
    }, 1000);
    return () => window.clearInterval(t);
  }, [shown]);

  if (!port) {
    return <p className="syn-t-caption">引擎还没就绪，端口未知，暂时出不了二维码。</p>;
  }
  if (addrs.length === 0) {
    return <p className="syn-t-caption">没探测到局域网地址，出不了二维码。</p>;
  }

  if (!shown) {
    return (
      <button
        className="btn btn--sm"
        onClick={() => setShown(true)}
        title="显示配对二维码。它带着完整凭据，看完两分钟自动收起"
      >
        <QrCode size={14} strokeWidth={1.8} />
        显示配对二维码
      </button>
    );
  }

  return (
    <div className="pairqr">
      {addrs.length > 1 && (
        <div className="ranking__presets">
          {addrs.map((a, i) => (
            <button
              key={a}
              className={`chip${i === pick ? ' chip--on' : ''}`}
              onClick={() => setPick(i)}
              title={`用 ${a} 这个网段出码`}
            >
              {a}
            </button>
          ))}
        </div>
      )}

      {img ? (
        <img className="pairqr__img" src={img} alt={`配对二维码：${host}:${port}`} />
      ) : (
        <p className="syn-t-caption">正在生成…</p>
      )}

      <p className="pairqr__note">
        手机上打开 Synorive → 配对 → 「扫二维码」，对着它拍一张。
        <br />
        这张码里有完整的连接凭据，<strong>别截图发给别人</strong>。{left} 秒后自动收起。
      </p>

      <div className="panel__row">
        <button className="btn btn--sm" onClick={() => setShown(false)} title="立刻收起">
          收起
        </button>
        <button
          className="btn btn--sm"
          onClick={() => setLeft(120)}
          title="再给两分钟"
        >
          <RefreshCw size={13} strokeWidth={1.8} />
          续两分钟
        </button>
      </div>
    </div>
  );
}
