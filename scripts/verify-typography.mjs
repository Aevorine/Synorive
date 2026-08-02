/**
 * B3 西文/中文实际渲染字体 + 令牌落地核对
 * ====================================================================
 * 分工：
 *   contrast-audit.mjs  → 令牌层面的 WCAG 判定（62 对，唯一真相源）
 *   本脚本              → ① 字符实际落在哪个字体面上　② 令牌有没有真的进到运行中的应用
 * 两边不重复：那边算"色值合不合格"，这边验"合格的色值有没有送到用户眼前"。
 *
 * B3 的关键是"实际渲染"而不是"声明"。font-family 里写了 Times New Roman
 * 不代表浏览器真用它渲染 —— 字体没装、名字被模糊匹配到别的面、被后面的
 * 字体抢走，都会导致声明对而渲染错，**而且不报任何错**。
 * CDP 的 CSS.getPlatformFontsForNode 报的是真正上屏的字体面，这才是证据。
 *
 * ⚠️ 两个必须记住的实现细节（都栽过）：
 *   ① getPlatformFontsForNode 只有节点**画过一遍之后**才有数据。插入后立刻查
 *      会返回空数组，看起来像"这个组合没匹配到字体"，其实只是还没绘制。
 *      → 全部探针建完 → 等绘制 → 再统一查。
 *   ② 🔴 **字体名不能用来判字重。** CDP 报的是字体文件内部名表里的名字，而
 *      @fontsource/noto-serif-sc 5.2.8 的子集文件 name[1] 一律写成
 *      「Noto Serif SC ExtraLight」——上游命名 bug，OS/2 usWeightClass 其实是
 *      正确的 400/600。我曾据此误判"标题发虚"并把字族顺序改反，那才是真 bug。
 *      → 字重一律用**画布墨量**判（B3-2），名字只作参考打印。
 *
 * 用法：node scripts/verify-typography.mjs [端口]
 */

import { fontFamily, palette } from '../packages/design-tokens/dist/index.js';

const port = Number(process.argv[2] ?? 9270);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.pending = new Map(); }
  async connect() {
    this.ws = new WebSocket(this.url);
    await new Promise((res, rej) => {
      this.ws.addEventListener('open', res, { once: true });
      this.ws.addEventListener('error', () => rej(new Error('CDP 连接失败')), { once: true });
    });
    this.ws.addEventListener('message', (ev) => {
      const m = JSON.parse(String(ev.data));
      const p = this.pending.get(m.id);
      if (p) { this.pending.delete(m.id); m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result); }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => { if (this.pending.delete(id)) rej(new Error(`CDP ${method} 超时`)); }, 20_000);
    });
  }
  async js(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? 'JS 出错');
    return r.result.value;
  }
  close() { this.ws?.close(); }
}

async function findTarget() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(2000) });
      const t = (await r.json()).find((x) => x.type === 'page' && x.webSocketDebuggerUrl);
      if (t) return t;
    } catch { /* 还没起来 */ }
    await sleep(400);
  }
  throw new Error('30 秒内没找到页面（应用起了吗？带 --remote-debugging-port 了吗？）');
}

const target = await findTarget();
const cdp = new Cdp(target.webSocketDebuggerUrl);
await cdp.connect();
await cdp.send('Page.enable');
await cdp.send('Runtime.enable');
await cdp.send('DOM.enable');
await cdp.send('CSS.enable');
await sleep(2500);

const problems = [];
const line = (n = 78) => '━'.repeat(n);

// ══ ① 字符落在哪个字体面上 ══════════════════════════════════
console.log(line());
console.log('  B3-1 实际渲染字体 —— 看 CDP 报的上屏字体面，不看声明');
console.log(line());

const CASES = [
  { id: 'latin', text: 'Synorive Search Engine', ff: 'body', label: '西文字母（正文）', want: /Times New Roman/i },
  { id: 'digits', text: '0123456789', ff: 'body', label: '数字（正文）', want: /Times New Roman/i },
  { id: 'punct', text: '.,;:!?()[]"\'', ff: 'body', label: '西文标点（正文）', want: /Times New Roman/i },
  { id: 'cjk', text: '中文分词与向量检索', ff: 'body', label: '汉字（正文）', want: /SimSun|宋体/i },
  { id: 'cjkpunct', text: '，。、；：！？「」', ff: 'body', label: '中文标点（正文）', want: /SimSun|宋体/i },
  { id: 'tlatin', text: 'Synorive', ff: 'display', label: '西文字母（标题）', want: /Times New Roman/i },
  // 思源宋体和 Noto Serif CJK 是同一套字的两个发行名，命中哪个都算对。
  // 名字里带不带 ExtraLight **不作判据**（上游命名 bug，见文件头 ②），字重交给 B3-2 量。
  { id: 'tcjk', text: '文件管理器', ff: 'display', label: '汉字（标题）', want: /Noto Serif SC|Source Han Serif/i },
];

await cdp.js(`(() => {
  document.getElementById('__fp')?.remove();
  const box = document.createElement('div');
  box.id = '__fp';
  box.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;background:#fff;color:#000;';
  const cs = getComputedStyle(document.documentElement);
  for (const c of ${JSON.stringify(CASES)}) {
    const el = document.createElement('div');
    el.id = '__fp_' + c.id;
    el.textContent = c.text;
    el.style.fontFamily = cs.getPropertyValue('--syn-ff-' + c.ff);
    el.style.fontSize = c.ff === 'display' ? '24px' : '16px';
    box.appendChild(el);
  }
  document.body.appendChild(box);
  return box.childElementCount;
})()`);
await sleep(1500);   // 必须等真的画一遍，否则 getPlatformFontsForNode 全空

const { root } = await cdp.send('DOM.getDocument', { depth: 1 });
for (const c of CASES) {
  const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector: `#__fp_${c.id}` });
  const { fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId });
  if (!fonts.length) {
    problems.push(`B3 ${c.label}：拿不到实际渲染字体（节点没画？）`);
    console.log(`  ✗ ${c.label.padEnd(18)} 拿不到`);
    continue;
  }
  const main = [...fonts].sort((a, b) => b.glyphCount - a.glyphCount)[0];
  const nameOk = c.want.test(main.familyName);
  const others = fonts.length > 1 ? `　（另有 ${fonts.slice(1).map((f) => f.familyName).join('、')}）` : '';
  console.log(`  ${nameOk ? '✓' : '✗'} ${c.label.padEnd(18)} 「${main.familyName}」渲染 ${main.glyphCount} 字形${others}`);
  if (!nameOk) problems.push(`B3 ${c.label}：期望匹配 ${c.want}，实际 ${main.familyName}`);
}
await cdp.js(`document.getElementById('__fp')?.remove()`);

// ══ ② 自带字体优先级 + 打包链路 ══════════════════════════════
//
// 要守的不变量：display 字族里 "Source Han Serif SC"（= 自带打包的那份）
// 必须排在 "Noto Serif SC"（= 系统那份）**前面**。排反了就会优先用系统字体，
// 等于白打包；没装思源宋体的机器上标题会掉回 SimSun，F1-b 方案落空。
//
// 🔴 这条为什么用静态断言，而不是"跑起来看看"——两种行为探针都试过，都不管用：
//   ① 墨量：思源宋体和系统 Noto Serif SC 本来就是同一套字，48px 墨量
//      2497 vs 2496。**区分不出用的是哪一份。** 注入已知坏版本，守卫报全绿。
//   ② 数已加载的 @font-face 分片：坏版本下也有 6 片被加载 —— 系统 Noto 有些
//      字形不覆盖，Chromium 逐字符回退顺带把自带的几片拉下来了。同样报全绿。
// 顺序是字符串里的**确定性不变量**，静态断言就是最合适的工具，绕成行为探针
// 反而引入了本来不存在的不确定性。
console.log();
console.log(line());
console.log('  B3-2 自带字体优先级（静态）+ 打包链路（运行时冒烟）');
console.log(line());

const shsIdx = fontFamily.display.indexOf('Source Han Serif SC');
const notoIdx = fontFamily.display.indexOf('Noto Serif SC');
console.log(`  display 字族：${fontFamily.display}`);
if (shsIdx < 0) {
  problems.push('B3 display 字族里没有 "Source Han Serif SC" —— 自带打包的思源宋体不会被用到');
  console.log('  ✗ 字族里根本没有自带字体的家族名');
} else if (notoIdx >= 0 && notoIdx < shsIdx) {
  problems.push('B3 display 字族顺序反了："Noto Serif SC"（系统）排在 "Source Han Serif SC"（自带）前面，自带字体白打包');
  console.log('  ✗ 系统字体排在自带字体前面 —— 顺序反了');
} else {
  console.log('  ✓ 自带 "Source Han Serif SC" 排在系统字体之前');
}

// 运行时冒烟：只证明"打包字体这条链路是通的"（@font-face 注册得上、woff2 取得到）。
// **它证明不了标题优先用的是自带字体**，那件事由上面的静态断言负责。
const wf = await cdp.js(`(() => {
  const faces = [...document.fonts].filter((f) => f.family === 'Source Han Serif SC');
  const by = {};
  for (const f of faces) by[f.status] = (by[f.status] || 0) + 1;
  return { total: faces.length, byStatus: by, loaded: by.loaded || 0 };
})()`);
console.log(`  @font-face 分片 ${wf.total} 个　状态 ${JSON.stringify(wf.byStatus)}`);
if (wf.total === 0) {
  problems.push('B3 自带思源宋体一个 @font-face 都没注册 —— fonts.css 没被 import？跑过 build_fonts.py 吗？');
  console.log('  ✗ 一个 @font-face 都没注册');
} else if (wf.loaded === 0) {
  problems.push('B3 自带思源宋体一个分片都没下载成功 —— woff2 路径断了？（打包后是 file:// 协议，别写绝对路径）');
  console.log('  ✗ 注册了但一片都没下下来');
} else {
  console.log(`  ✓ 打包链路通：${wf.loaded} 片已下载`);
}

// ══ ③ 字重客观测量：画布墨量 ════════════════════════════════
// 墨量 = 笔画覆盖的像素按灰度加权求和。它是能直接量的物理量，
// 而字体名只是个字符串 —— 上游写错名字的时候，只有墨量说得清真相。
console.log();
console.log(line());
console.log('  B3-3 字重客观测量 —— 画布墨量（名字不可信，量出来的才可信）');
console.log(line());

// 用短 id 索引，不用中文标签当键 —— 标签是给人看的，改一个字就查不到，
// 而查不到得到的是 undefined，undefined 参与比较又恰好是 false，守卫会**静默通过**。
// 这个坑刚在本脚本上栽过一次（"自带@400 / SimSun = NaN" 却报了全绿）。
const INK = [
  ['tok', 'display 令牌栈 @400', `"Times New Roman","Source Han Serif SC","Noto Serif SC","SimSun",serif`, 400],
  ['own400', '自带思源宋体 @400', `"Source Han Serif SC",serif`, 400],
  ['own600', '自带思源宋体 @600', `"Source Han Serif SC",serif`, 600],
  ['sysReg', '系统 Noto 常规 @400', `"Noto Serif SC",serif`, 400],
  ['sysThin', '系统 ExtraLight（细基准）', `"Noto Serif SC ExtraLight",serif`, 400],
  ['simsun', '系统 SimSun @400（更细）', `"SimSun",serif`, 400],
];

const ink = await cdp.js(`(async () => {
  const CASES = ${JSON.stringify(INK)};
  // 画布不会自动等 webfont，先显式 load，否则量到的是回退字体
  for (const [, , fam, w] of CASES) { try { await document.fonts.load(w + ' 48px ' + fam, '文件管理器'); } catch {} }
  const measure = (fam, w) => {
    const cv = document.createElement('canvas');
    cv.width = 320; cv.height = 80;
    const g = cv.getContext('2d', { willReadFrequently: true });
    g.fillStyle = '#fff'; g.fillRect(0, 0, cv.width, cv.height);
    g.fillStyle = '#000';
    g.font = w + ' 48px ' + fam;
    g.textBaseline = 'middle';
    g.fillText('文件管理器', 4, 40);
    const d = g.getImageData(0, 0, cv.width, cv.height).data;
    let s = 0;
    for (let i = 0; i < d.length; i += 4) s += (255 - d[i]) / 255;
    return Math.round(s);
  };
  const o = {};
  for (const [id, , fam, w] of CASES) o[id] = measure(fam, w);
  return o;
})()`);

for (const [, label, , w] of INK) {
  const id = INK.find((x) => x[1] === label)[0];
  console.log(`  ${label.padEnd(26)} ${String(ink[id]).padStart(6)}`);
}

const { tok, own400, own600, sysReg, sysThin, simsun } = ink;

/**
 * 比值守卫。
 * ⚠️ 必须显式挡住 NaN / undefined —— `NaN < 1.15` 求值是 false，
 *    直接写 `if (a / b < 门槛)` 的话，取不到数的时候守卫会**静默放行**。
 *    "不报错" 和 "通过" 是两回事。
 */
function ratio(label, a, b, cmp, bound, why) {
  const r = a / b;
  const dead = !Number.isFinite(r);
  console.log(`  ${label.padEnd(22)} = ${dead ? '取不到数' : r.toFixed(3)}　（${why}）`);
  if (dead) {
    problems.push(`B3 ${label}：取不到墨量（a=${a} b=${b}）—— 守卫没跑，不能算通过`);
    return;
  }
  if (!cmp(r, bound)) problems.push(`B3 ${label}：实测 ${r.toFixed(3)}，不满足 ${why}`);
}

console.log();
// 硬门槛①：令牌栈渲染出来的字重必须和自带字体一致。
//   ⚠️ 这条**不能**用来判"用的是自带的还是系统的"——两者是同一套字，墨量一样。
//      那件事归 B3-2 管。这条只保证令牌栈没掉到 SimSun 或别的字体上。
ratio('令牌栈 / 自带@400', tok, own400, (r) => Math.abs(r - 1) <= 0.02, 1,
  '应 ≈1.000：令牌栈渲染出的字重和自带字体一致');

// 硬门槛②：自带的 600 档必须真的比 400 粗（不是同一个文件打了两遍）
ratio('自带@600 / 自带@400', own600, own400, (r) => r > 1.10, 1.10,
  '应 >1.10：600 那档是真的更粗');

// 硬门槛③：标题字必须比正文宋体饱满。SimSun 系统必装，可当兜底基准
ratio('自带@400 / SimSun', own400, simsun, (r) => r > 1.15, 1.15,
  '应 >1.15：标题比正文宋体更饱满');

// 参考项：装了系统 Noto 才有意义，不作判定门槛
if (sysReg && sysThin) {
  console.log(`  自带@400 / 系统常规    = ${(own400 / sysReg).toFixed(3)}　（参考：≈1.000 说明和系统常规同一档）`);
  console.log(`  自带@400 / 系统细基准  = ${(own400 / sysThin).toFixed(3)}　（参考：>1.15 说明不是 ExtraLight）`);
  console.log(`  ⓘ 字体内部名写着 ExtraLight 是 @fontsource 的上游命名 bug，墨量证明字重是对的`);
}

// ══ ③ 令牌有没有真的进到运行中的应用 ════════════════════════
// contrast-audit.mjs 保证"令牌里的色值合格"，这一段保证"合格的色值送到了眼前"。
// 少了这一段，改完令牌忘了重新构建，审计全绿但界面还是旧色 —— 典型静默失败。
console.log();
console.log(line());
console.log('  B3-4 令牌落地核对 —— 运行中的 CSS 变量 vs 源码令牌');
console.log(line());

const KEBAB = (k) => k.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase());
for (const theme of ['light', 'dark']) {
  await cdp.js(`document.documentElement.setAttribute('data-theme', ${JSON.stringify(theme)})`);
  await sleep(350);
  const keys = Object.keys(palette[theme]);
  const live = await cdp.js(`(() => {
    const cs = getComputedStyle(document.documentElement);
    const o = {};
    for (const k of ${JSON.stringify(keys.map(KEBAB))}) o[k] = cs.getPropertyValue('--syn-color-' + k).trim().toUpperCase();
    return o;
  })()`);
  let bad = 0;
  for (const k of keys) {
    const want = palette[theme][k].toUpperCase();
    const got = live[KEBAB(k)];
    if (got !== want) {
      bad++;
      problems.push(`令牌落地 ${theme}.${k}：源码 ${want}，运行中 ${got || '(空)'}`);
      console.log(`  ✗ ${theme}.${k.padEnd(18)} 源码 ${want} ≠ 运行中 ${got || '(空)'}`);
    }
  }
  console.log(`  ${bad ? '✗' : '✓'} ${theme === 'light' ? '浅色' : '深色'}主题 ${keys.length} 个色值令牌　对上 ${keys.length - bad} 个`);
}
await cdp.js(`document.documentElement.setAttribute('data-theme','light')`);

console.log();
console.log(line());
if (problems.length) {
  for (const p of problems) console.error(`✗ ${p}`);
  cdp.close();
  process.exit(1);
}
console.log('✓ B3 全部通过（字体面 + 字重墨量 + 令牌落地）');
cdp.close();
process.exit(0);
