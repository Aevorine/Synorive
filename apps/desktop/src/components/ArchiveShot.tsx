import { useState } from 'react';
import { AlertTriangle, Camera, KeyRound, Loader2 } from 'lucide-react';
import { labApi, type ArchiveShot as Shot } from '../lib/labApi';
import { enginePort } from '../lib/api';

/**
 * C12 整页截图归档 ｜ C13 登录态抓取
 * ============================================================
 * 存一份「这个页面当时长什么样」。正文入库让它**搜得到**，
 * 整页截图让它**赖不掉** —— 页面改了、删了，你手上还有版面证据。
 *
 * 🔴 **cookie 只在这一次请求里存在。** 引擎不落盘、不写日志、不回显；
 * 桌面端渲染窗口用的是内存分区，每次抓取前先清空、进程退出即消失。
 * **绝不为了"下次不用再传"把它存起来** —— 那是把用户的登录凭证
 * 变成一份躺在磁盘上的长期资产，而他完全不知道。
 *
 * 🔴 **cookie 设失败必须喊出来。** 少了关键的那个 session cookie，
 * 抓回来的是登录页 —— 而截图有了、字节数正常、HTTP 200，
 * 从任何一个数字上都看不出问题。这是这个功能唯一一种「看起来完全成功」的失败。
 */

export function ArchiveShot() {
  const [url, setUrl] = useState('');
  const [cookieText, setCookieText] = useState('');
  const [useLogin, setUseLogin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [out, setOut] = useState<Shot | null>(null);

  const run = async () => {
    const u = url.trim();
    if (!u) return;
    setBusy(true);
    setErr(null);
    setOut(null);
    try {
      const cookies = useLogin ? parseCookies(cookieText) : undefined;
      if (useLogin && (!cookies || cookies.length === 0)) {
        setErr('勾了「带登录态」但没解析出任何 cookie —— 检查一下粘贴的格式');
        return;
      }
      setOut(await labApi.archiveShot({ url: u, cookies, ingest: true }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const port = enginePort();

  return (
    <div className="ash">
      <div className="ash__row">
        <input
          className="ash__url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void run()}
          placeholder="粘一个网址，存一份整页截图 + 正文"
          spellCheck={false}
        />
        <button type="button" className="btn btn--primary" onClick={() => void run()} disabled={busy || !url.trim()}>
          {busy ? <Loader2 size={15} className="spin" aria-hidden /> : <Camera size={15} aria-hidden />}
          整页归档
        </button>
      </div>

      <label className="ash__check">
        <input type="checkbox" checked={useLogin} onChange={(e) => setUseLogin(e.target.checked)} />
        <KeyRound size={13} aria-hidden /> 带登录态抓（C13）
      </label>

      {useLogin && (
        <>
          <textarea
            className="ash__cookies"
            value={cookieText}
            onChange={(e) => setCookieText(e.target.value)}
            rows={3}
            spellCheck={false}
            placeholder={'从浏览器复制的 Cookie 头，例如：\nsessionid=abc123; csrftoken=xyz; uid=42'}
          />
          {/* 🔴 这段话不能折叠、不能缩成 tooltip。用户在这里粘的是
              **能直接冒充他登录**的东西，他有权知道它去了哪、活多久 */}
          <p className="ash__warn">
            <AlertTriangle size={13} aria-hidden />
            这串 cookie <b>能直接冒充你登录那个网站</b>。它只在这一次抓取里用：
            不写磁盘、不进日志、不入库，抓完就随渲染窗口的内存分区一起没了。
            但你自己要清楚 —— <b>别粘网银、邮箱这类账号的 cookie</b>。
          </p>
        </>
      )}

      {err && (
        <p className="ash__err">
          <AlertTriangle size={14} aria-hidden /> {err}
        </p>
      )}

      {out && (
        <div className="ash__out">
          <p className="ash__meta">
            存好了：{out.width}×{out.height} · {fmtSize(out.bytes)}
            {out.usedCookies ? ' · 带登录态' : ''}
            {out.ingest ? ` · 正文：${out.ingest}` : ''}
          </p>

          {/* 🔴 警告用危险色单独一段，不混进上面那行 meta 里 */}
          {out.warning && (
            <p className="ash__warn">
              <AlertTriangle size={13} aria-hidden /> {out.warning}
            </p>
          )}
          {out.cookieFailures?.length ? (
            <p className="ash__warn">
              有 {out.cookieFailures.length} 个 cookie 没设成功：{out.cookieFailures.join('；')}
            </p>
          ) : null}

          {port != null && (
            <img
              className="ash__img"
              src={`http://127.0.0.1:${port}/api/web/archive-shot/${encodeURIComponent(out.shot)}`}
              alt="整页截图"
              loading="lazy"
            />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 解析从浏览器复制的 Cookie 头。
 *
 * 🔴 **按第一个 `=` 切，不是按所有 `=` 切。** cookie 的值里
 * 常常带 base64 padding（`abc==`），用 `split('=')` 会把值截断成 `abc` ——
 * 而截断后的 cookie **服务端只会当成无效会话**，抓回来是登录页。
 * 不报错，只是功能无效。
 */
function parseCookies(
  raw: string,
): { name: string; value: string }[] {
  return raw
    .split(/[;\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((pair) => {
      const i = pair.indexOf('=');
      if (i <= 0) return null;
      return { name: pair.slice(0, i).trim(), value: pair.slice(i + 1).trim() };
    })
    .filter((c): c is { name: string; value: string } => c !== null && c.name.length > 0);
}

function fmtSize(n: number): string {
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)}MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)}KB`;
  return `${n}B`;
}
