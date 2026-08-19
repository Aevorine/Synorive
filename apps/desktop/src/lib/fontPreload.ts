/**
 * 标题字体分片预取
 * ============================================================
 * 打包的思源宋体按 unicode-range 切成 202 片，界面只会命中其中少数几片。
 * 按需加载本身是对的，问题出在**时机**：
 *
 *   浏览器要先解析 CSS → 布局出第一批文字 → 才知道命中了哪几片 → 再去读。
 *   而这几片声明的是 `font-display: block`，读回来之前标题是**看不见的**。
 *   于是首屏会出现"标题先空着，几十毫秒后蹦出来"这一跳。
 *
 * 这里把那几片提前到和 JS 同时开始读，等布局走到标题时字体已经在手上了。
 *
 * ── 这 8 片是怎么挑的（不是拍脑袋）────────────────────────
 * 扫了 `apps/desktop/src` 全部源码里出现过的中日韩字符（1197 个），
 * 逐个对照 fonts.css 里每一片的 unicode-range，按命中字数排序，
 * 取到累计覆盖 95% 为止 —— 正好是第 115~118 片的 400/600 两档，共 8 个文件、274 KB。
 * 剩下 5% 是极少用的生僻字，仍然按需加载，不值得为它们多读 6 MB。
 *
 * 🔴 **必须写成 `import` 而不是拼字符串路径。** 打包后文件名带内容哈希
 *    （`noto-serif-sc-118-400-normal-XXXX.woff2`），手写路径在开发环境
 *    能对上、打完包 404 —— 而 404 的表现只是"预取没生效"，
 *    首屏照常显示，没有任何报错。典型的静默失败。
 *
 * 🔴 **`crossOrigin` 必须设。** 字体请求即使同源也按 CORS 规则走，
 *    预取时不带这个属性的话，浏览器认为预取到的和真正要用的不是同一个
 *    资源，会**再下一次** —— 预取变成纯浪费，且同样不报错。
 */

import u115n from '../styles/fonts/noto-serif-sc-115-400-normal.woff2?url';
import u115b from '../styles/fonts/noto-serif-sc-115-600-normal.woff2?url';
import u116n from '../styles/fonts/noto-serif-sc-116-400-normal.woff2?url';
import u116b from '../styles/fonts/noto-serif-sc-116-600-normal.woff2?url';
import u117n from '../styles/fonts/noto-serif-sc-117-400-normal.woff2?url';
import u117b from '../styles/fonts/noto-serif-sc-117-600-normal.woff2?url';
import u118n from '../styles/fonts/noto-serif-sc-118-400-normal.woff2?url';
import u118b from '../styles/fonts/noto-serif-sc-118-600-normal.woff2?url';

const SHARDS = [u115n, u115b, u116n, u116b, u117n, u117b, u118n, u118b];

let done = false;

/** 在 React 挂载之前调用一次。重复调用无效，不会重复插入。 */
export function preloadDisplayFont(): void {
  if (done || typeof document === 'undefined') return;
  done = true;
  const frag = document.createDocumentFragment();
  for (const href of SHARDS) {
    const link = document.createElement('link');
    link.rel = 'preload';
    link.as = 'font';
    link.type = 'font/woff2';
    link.crossOrigin = 'anonymous';
    link.href = href;
    frag.appendChild(link);
  }
  document.head.appendChild(frag);
}
