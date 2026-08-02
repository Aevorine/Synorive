/**
 * E13 命令面板端到端
 * ====================================================================
 * 验的是"键盘一路能不能走通"：快捷键唤起 → 敲拼音 → 上下选 → 回车执行。
 * 只测组件渲染没意义 —— 命令面板的全部价值就在于**不用鼠标**。
 *
 * 用法：node scripts/test-palette.mjs [端口]
 */

const port = Number(process.argv[2] ?? 9270);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let target;
for (let i = 0; i < 90; i++) {
  try {
    const l = await (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(1500) })).json();
    target = l.find((x) => x.type === 'page');
    if (target) break;
  } catch { /* 还没起来 */ }
  await sleep(500);
}
if (!target) { console.error('✗ 找不到页面'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0;
const pend = new Map();
ws.addEventListener('message', (e) => {
  const m = JSON.parse(String(e.data));
  const p = pend.get(m.id);
  if (p) { pend.delete(m.id); p(m); }
});
const send = (method, params = {}) => new Promise((res, rej) => {
  const i = ++id;
  pend.set(i, res);
  ws.send(JSON.stringify({ id: i, method, params }));
  setTimeout(() => { if (pend.delete(i)) rej(new Error('timeout ' + method)); }, 20000);
});
const js = async (x) => (await send('Runtime.evaluate',
  { expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;
await send('Runtime.enable');
await send('Input.enable').catch(() => {});
await sleep(5000);

const problems = [];
const line = (n = 72) => '─'.repeat(n);

/** 真按键，不走 JS 直接改 state —— 快捷键有没有接上只有这样才测得出来 */
async function key(k, mods = 0) {
  const codes = {
    P: { code: 'KeyP', key: 'P', vk: 80 },
    ArrowDown: { code: 'ArrowDown', key: 'ArrowDown', vk: 40 },
    ArrowUp: { code: 'ArrowUp', key: 'ArrowUp', vk: 38 },
    Enter: { code: 'Enter', key: 'Enter', vk: 13 },
    Escape: { code: 'Escape', key: 'Escape', vk: 27 },
  }[k];
  for (const type of ['keyDown', 'keyUp']) {
    await send('Input.dispatchKeyEvent', {
      type, modifiers: mods, code: codes.code, key: codes.key,
      windowsVirtualKeyCode: codes.vk, nativeVirtualKeyCode: codes.vk,
    });
  }
}
const CTRL = 2, SHIFT = 8;

const isOpen = () => js(`!!document.querySelector('.palette')`);
const rows = () => js(`[...document.querySelectorAll('.palette__item')].map(e => e.textContent.trim())`);
const selected = () => js(`document.querySelector('.palette__item--sel')?.textContent?.trim() ?? null`);

// ── ① 快捷键唤起 ────────────────────────────────────────
console.log(line());
console.log('  ① Ctrl+Shift+P 唤起');
console.log(line());
await js(`document.activeElement?.blur()`);
await key('P', CTRL | SHIFT);
await sleep(700);
const opened = await isOpen();
const all = await rows();
console.log(`  面板打开=${opened}　命令 ${all.length} 条`);
if (!opened) problems.push('Ctrl+Shift+P 没能唤起面板');
if (all.length < 15) problems.push(`命令只有 ${all.length} 条，太少了`);

// ── ② 拼音首字母过滤 ───────────────────────────────────
console.log();
console.log(line());
console.log('  ② 拼音首字母过滤（中文标签的关键：不用切输入法）');
console.log(line());
for (const [q, expect] of [['wjglq', '文件管理器'], ['sz', '设置'], ['cqyq', '重启引擎'], ['qkjtb', '剪贴板']]) {
  await js(`(() => {
    const el = document.querySelector('.palette__input');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, ${JSON.stringify(q)});
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  })()`);
  await sleep(350);
  const top = await js(`document.querySelector('.palette__item')?.textContent?.trim() ?? null`);
  const ok = !!top && top.includes(expect);
  console.log(`  ${ok ? '✓' : '✗'} 打「${q}」→ 榜首「${top}」（应含「${expect}」）`);
  if (!ok) problems.push(`拼音「${q}」没把「${expect}」排到第一`);
}

// ── ③ 键盘上下选 ───────────────────────────────────────
console.log();
console.log(line());
console.log('  ③ 上下键选择');
console.log(line());
await js(`(() => {
  const el = document.querySelector('.palette__input');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, ''); el.dispatchEvent(new Event('input', { bubbles: true })); return true;
})()`);
await sleep(300);
const first = await selected();
await key('ArrowDown');
await sleep(200);
const second = await selected();
await key('ArrowUp');
await sleep(200);
const back = await selected();
console.log(`  初始「${first}」→ ↓「${second}」→ ↑「${back}」`);
if (first === second) problems.push('按 ↓ 之后选中项没变');
if (back !== first) problems.push('按 ↑ 没回到原来那条');

// ── ④ 回车真执行 ───────────────────────────────────────
console.log();
console.log(line());
console.log('  ④ 回车执行（选「转到设置」，看页面真的换了没）');
console.log(line());
await js(`(() => {
  const el = document.querySelector('.palette__input');
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, 'zdsz'); el.dispatchEvent(new Event('input', { bubbles: true })); return true;
})()`);
await sleep(400);
console.log(`  榜首：${await js(`document.querySelector('.palette__item')?.textContent?.trim()`)}`);
await key('Enter');
await sleep(900);
const afterTitle = await js(`document.querySelector('.page__title')?.textContent ?? null`);
const stillOpen = await isOpen();
console.log(`  执行后主标题「${afterTitle}」　面板已关=${!stillOpen}`);
if (afterTitle !== '设置') problems.push(`回车没跳到设置页，当前是「${afterTitle}」`);
if (stillOpen) problems.push('执行后面板没关掉');

// ── ⑤ Esc 关闭 ────────────────────────────────────────
console.log();
console.log(line());
console.log('  ⑤ Esc 关闭');
console.log(line());
await key('P', CTRL | SHIFT);
await sleep(600);
const reopened = await isOpen();
await key('Escape');
await sleep(500);
const closed = !(await isOpen());
console.log(`  再次打开=${reopened}　Esc 关闭=${closed}`);
if (!reopened) problems.push('第二次按快捷键打不开');
if (!closed) problems.push('Esc 没关掉面板');

// ── ⑥ 打开时不应残留上次的搜索词 ────────────────────────
await key('P', CTRL | SHIFT);
await sleep(600);
const leftover = await js(`document.querySelector('.palette__input')?.value ?? ''`);
console.log(`  重新打开后输入框内容 = ${JSON.stringify(leftover)}${leftover === '' ? ' ✓' : ' ✗'}`);
if (leftover !== '') problems.push('重新打开时残留了上次的搜索词，用户会以为命令变少了');
await key('Escape');

// 收尾：回到搜索页，别把应用留在设置页影响后续测试
await js(`(() => {
  [...document.querySelectorAll('.sidebar__item')].find(e => e.textContent.trim() === '搜索')?.click();
  return true;
})()`);

console.log();
console.log('='.repeat(72));
if (problems.length) {
  for (const p of problems) console.error(`✗ ${p}`);
  ws.close(); process.exit(1);
}
console.log('✓ E13 命令面板通过（快捷键 / 拼音 / 上下选 / 回车执行 / Esc / 不残留）');
ws.close(); process.exit(0);
