import { useState } from 'react';
import { AlertTriangle, Globe, Images, Loader2 } from 'lucide-react';
import { labApi, type ImageLanes as Lanes, type ReverseMulti } from '../lib/labApi';

/**
 * A3 —— 一张图，四路同时跑，一屏出完
 * ============================================================
 * 四路各回答一个不同的问题：
 *   ① **像不像我已经有的** —— 以图搜图，连视频里的镜头一起搜
 *   ② **图里写了什么字** —— OCR，再拿这些字回库里搜一遍
 *   ③ **网上还有哪里有** —— 反查出处 / 更高清版 / 搬运源
 *   ④ **像不像被改过** —— 四条判据初筛
 *
 * 🔴 **每一路的错误各显示各的。** 反查那一路最容易挂（要联网、
 * 会被限流、可能被隐私开关关掉），但它挂掉时另外三路完全有效。
 * 整块显示"分析失败"会把三份好结果一起丢掉。
 *
 * 🔴 **篡改初筛的分数永远不写成"是/不是"。** 那四条判据在设计上
 * 就不足以支撑"确定被改过"这种结论，只能说"可疑度"。
 * 把它显示成一个红色的"已篡改"标签，是在拿一个初筛冒充鉴定。
 */

export function ImageLanes() {
  const [data, setData] = useState<Lanes | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  /** B2：刚才选的那张图，三家反查要复用它，不让用户再选一遍 */
  const [picked, setPicked] = useState<string | null>(null);
  const [multi, setMulti] = useState<ReverseMulti | null>(null);
  const [multiBusy, setMultiBusy] = useState(false);

  const pick = async () => {
    const files = await window.synorive.sys.pickFiles();
    const first = files[0];
    if (!first) return;
    setBusy(true);
    setErr(null);
    setData(null);
    setMulti(null);
    setPicked(first);
    try {
      setData(await labApi.imageLanes(first));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const runMulti = async () => {
    if (!picked) return;
    setMultiBusy(true);
    try {
      setMulti(await labApi.reverseMulti(picked));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setMultiBusy(false);
    }
  };

  const L = data?.lanes;
  const similarCount = Array.isArray(L?.similar?.hits) ? L.similar.hits.length : 0;
  const ocrHits = Array.isArray(L?.ocr?.hits) ? L.ocr.hits.length : 0;
  // 🔴 `pagesIncluding` + `visualSimilar` 两段都算 —— 引擎从来没有 `results` 这个字段。
  // 读错 key 的后果是这一路永远显示 0，而且不报错、不崩溃、数据其实好好地回来了
  const reversePages = L?.reverse?.pagesIncluding ?? [];
  const reverseSimilar = L?.reverse?.visualSimilar ?? [];
  const reverseCount = reversePages.length + reverseSimilar.length;
  const susp = typeof L?.tamper?.suspicion === 'number' ? L.tamper.suspicion : null;

  return (
    <div className="syn-lanes">
      <div className="syn-lanes-bar">
        <button type="button" className="btn" onClick={() => void pick()} disabled={busy}>
          {busy ? (
            <Loader2 size={15} className="spin" aria-hidden />
          ) : (
            <Images size={15} aria-hidden />
          )}
          选一张图，四路一起查
        </button>
        <span className="syn-lanes-hint">
          相似图 · 图里的字 · 网上出处 · 是否被改过 —— 四路并发，总耗时等于最慢那一路
        </span>
      </div>

      {err && (
        <p className="syn-lanes-err">
          <AlertTriangle size={14} aria-hidden /> {err}
        </p>
      )}

      {L && (
        <div className="syn-lanes-grid">
          <Lane
            title="① 库里像它的"
            error={L.similar?.error}
            empty={similarCount === 0 ? '库里没有像它的东西' : null}
          >
            <p className="syn-lane-big">{similarCount}</p>
            <p className="syn-lane-sub">条相似内容（含视频里的镜头）</p>
          </Lane>

          <Lane
            title="② 图里的字"
            error={L.ocr?.error}
            empty={!L.ocr?.text ? (L.ocr?.note ?? '没认出文字') : null}
          >
            <p className="syn-lane-text">{L.ocr?.text?.slice(0, 400)}</p>
            <p className="syn-lane-sub">
              {L.ocr?.charCount} 个字
              {ocrHits > 0 ? ` · 拿这些字回库里搜到 ${ocrHits} 条` : ''}
            </p>
          </Lane>

          <Lane
            title="③ 网上还有哪里有"
            error={L.reverse?.error ?? undefined}
            empty={reverseCount === 0 ? (L.reverse?.note ?? '没找到别处出现过') : null}
          >
            <p className="syn-lane-big">{reverseCount}</p>
            <p className="syn-lane-sub">
              处出现（{reversePages.length} 处引用了这张图 / {reverseSimilar.length} 张视觉相似）
            </p>
            <ul className="syn-multi-list">
              {[...reversePages, ...reverseSimilar].slice(0, 5).map((h) => (
                <li key={h.pageUrl}>
                  <a
                    href={h.pageUrl}
                    onClick={(e) => {
                      e.preventDefault();
                      void window.synorive.sys.openExternal(h.pageUrl);
                    }}
                  >
                    {h.title || h.pageUrl}
                  </a>
                </li>
              ))}
            </ul>
          </Lane>

          <Lane
            title="④ 像不像被改过"
            error={L.tamper?.error}
            empty={susp == null ? '没判出来' : null}
          >
            {/* 🔴 只说"可疑度"不说"是不是"。四条判据支撑不了鉴定结论 */}
            <p className="syn-lane-big">{susp != null ? `${Math.round(susp * 100)}%` : '—'}</p>
            <p className="syn-lane-sub">
              可疑度 —— 这是<strong>初筛不是鉴定</strong>，高不代表一定改过，低也不代表一定没改
            </p>
            {L.tamper?.note && <p className="syn-lane-note">{L.tamper.note}</p>}
          </Lane>
        </div>
      )}

      {/* B2 三家反查。**单独一块、要手动点** —— Yandex 和 Lens 都要借
          桌面端的浏览器去撞它们的结果页，而且很容易招来人机验证。
          默认就去撞是拿用户的 IP 冒险，所以做成显式动作 */}
      {picked && (
        <div className="syn-multi">
          <div className="syn-lanes-bar">
            <button type="button" className="btn" onClick={() => void runMulti()} disabled={multiBusy}>
              {multiBusy ? (
                <Loader2 size={15} className="spin" aria-hidden />
              ) : (
                <Globe size={15} aria-hidden />
              )}
              换三家一起反查（Bing + Yandex + Lens）
            </button>
            <span className="syn-lanes-hint">
              三家索引重合度很低，一家没结果不代表网上没有
            </span>
          </div>

          {multi && (
            <>
              <div className="syn-lanes-grid">
                {Object.entries(multi.providers).map(([name, r]) => (
                  <section className="syn-lane" key={name}>
                    <h4 className="syn-lane-title">{PROVIDER_LABEL[name] ?? name}</h4>
                    {r.error ? (
                      // 🔴 原样显示。里面区分了「解析不出条目」和「被验证码挡下」，
                      // 概括成"没找到"会让用户拿一次失败当成"这图是原创的"证据
                      <p className="syn-lane-err">{r.error}</p>
                    ) : (
                      <>
                        <p className="syn-lane-big">{r.pagesIncluding?.length ?? 0}</p>
                        <p className="syn-lane-sub">处出现</p>
                        <ul className="syn-multi-list">
                          {(r.pagesIncluding ?? []).slice(0, 6).map((h) => (
                            <li key={h.pageUrl}>
                              <a
                                href={h.pageUrl}
                                onClick={(e) => {
                                  e.preventDefault();
                                  void window.synorive.sys.openExternal(h.pageUrl);
                                }}
                              >
                                {h.title || h.pageUrl}
                              </a>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </section>
                ))}
              </div>
              <p className="syn-lanes-note">{multi.note}</p>
            </>
          )}
        </div>
      )}

      {data && <p className="syn-lanes-note">{data.note}</p>}
    </div>
  );
}

const PROVIDER_LABEL: Record<string, string> = {
  bing: 'Bing',
  yandex: 'Yandex',
  lens: 'Google Lens',
};

function Lane({
  title,
  error,
  empty,
  children,
}: {
  title: string;
  error?: string;
  /** 非 null 就显示这句话代替内容 —— 空结果也是结果，不该显示成一片空白 */
  empty: string | null;
  children: React.ReactNode;
}) {
  return (
    <section className="syn-lane">
      <h4 className="syn-lane-title">{title}</h4>
      {error ? (
        <p className="syn-lane-err">
          <AlertTriangle size={13} aria-hidden /> 这一路没跑成：{error}
          <br />
          <span className="syn-lane-sub">其他几路的结果不受影响。</span>
        </p>
      ) : empty ? (
        <p className="syn-lane-empty">{empty}</p>
      ) : (
        children
      )}
    </section>
  );
}
