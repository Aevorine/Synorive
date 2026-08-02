/**
 * E4 端到端：真往系统剪贴板写东西，看哨兵收没收、界面显示对不对。
 * 光测纯函数不够 —— 轮询、去重、开关联动、界面渲染都在函数外面。
 */
import { execFileSync } from 'node:child_process';

const port = 9270;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * 用 PowerShell 写系统剪贴板（Electron 那边在另一个进程里读）。
 *
 * ⚠️ 必须走 base64，不能把字符串直接拼进命令行。
 *    第一版用 JSON.stringify 拼成双引号 PowerShell 字符串，结果 `p7$Kd2!...`
 *    里的 `$Kd2` 被当成变量插值掉了，真正进剪贴板的是 `p7!Nq9@Wz4#Rb`。
 *    而断言拿**原始字符串**去比对、没找到就判成"没收 ✓" —— 一条被收进去的
 *    密码被报成了通过。测试用例送不对，测多少遍都是零分。
 */
function setClip(text) {
  const b64 = Buffer.from(text, 'utf8').toString('base64');
  execFileSync('pwsh', ['-NoProfile', '-Command',
    `Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${b64}')))`],
    { stdio: 'ignore' });
}

/** 回读一次，确认真正进剪贴板的就是我想写的那串 */
function readClip() {
  const out = execFileSync('pwsh', ['-NoProfile', '-Command',
    '[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-Clipboard -Raw)))'],
    { encoding: 'utf8' });
  return Buffer.from(out.trim(), 'base64').toString('utf8');
}

let target;
for (let i = 0; i < 90; i++) {
  try {
    const l = await (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(1500) })).json();
    target = l.find((x) => x.type === 'page'); if (target) break;
  } catch { /* 还没起来 */ }
  await sleep(500);
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r, { once: true }));
let id = 0; const pend = new Map();
ws.addEventListener('message', (e) => {
  const m = JSON.parse(String(e.data)); const p = pend.get(m.id);
  if (p) { pend.delete(m.id); p(m); }
});
const send = (method, params = {}) => new Promise((res) => {
  const i = ++id; pend.set(i, res);
  ws.send(JSON.stringify({ id: i, method, params }));
});
const js = async (x) => (await send('Runtime.evaluate',
  { expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;
await send('Runtime.enable');
await sleep(6000);   // 等应用起完

const problems = [];
const line = (n = 74) => '─'.repeat(n);

console.log('确认哨兵开着：', await js(`window.synorive.settings.get().then(s => s.clipboardSentinel)`));
await js(`window.synorive.clip.clear()`);

// 剪贴板面板只挂在**搜索页**上，而且**故意**只在没搜东西时才显示
// （搜索时它会抢走结果的位置）。所以校验界面之前要做两件事：
//   ① 切回搜索页 —— 上一个测试可能把应用留在设置页，那时候组件压根没挂载
//   ② 清空搜索框 —— 留着查询词的话面板会隐藏
// 两条都踩过，报出来都是"面板没渲染"，看着像组件坏了。
await js(`(() => {
  const items = [...document.querySelectorAll('.sidebar__item')];
  items.find(e => e.textContent.trim() === '搜索')?.click();
  return true;
})()`);
await sleep(1000);
await js(`(() => {
  const el = document.querySelector('.searchbox__input');
  if (el) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, '');
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  return true;
})()`);
await sleep(1200);

const CASES = [
  ['普通文字', '明天下午三点和张工对一下多模态检索的进度', true, 'text'],
  ['网址', 'https://github.com/Fusheng201/Synorive/blob/main/README.md', true, 'link'],
  ['代码', 'const hits = await search({ query, limit: 30 });', true, 'text'],
  ['🔒 OpenAI 密钥', 'sk-proj-QwErTyUiOpAsDfGhJkLzXcVbNm0123456789', false, null],
  ['🔒 短信验证码', '739104', false, null],
  ['🔒 随机密码', 'p7$Kd2!Nq9@Wz4#Rb', false, null],
  ['🔒 私钥', '-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk', false, null],
];

console.log('\n' + line());
console.log('  往系统剪贴板真写一遍，看哨兵怎么处理');
console.log(line());
for (const [name, text, shouldCapture, kind] of CASES) {
  setClip(text);
  // 送进去的和想送的必须一致，否则后面的断言全是空转
  const actual = readClip().split(String.fromCharCode(13)).join('').trim();
  if (actual !== text.trim()) {
    problems.push(`${name}：写进剪贴板的内容和预期不符（写入通道有问题，不是被测功能的锅）`);
    console.log(`  ✗ ${name.padEnd(16)} 剪贴板实际是 ${JSON.stringify(actual.slice(0, 40))}`);
    continue;
  }
  await sleep(1600);   // 轮询是 800ms，给两轮
  const list = await js(`window.synorive.clip.list()`);
  const got = list.find((x) => x.content === text.trim());
  const ok = shouldCapture ? !!got : !got;
  console.log(`  ${ok ? '✓' : '✗'} ${name.padEnd(16)} ${shouldCapture ? '应收' : '应丢弃'}　实际${got ? `收了(${got.kind})` : '没收'}`);
  if (!ok) problems.push(`${name}：期望${shouldCapture ? '收' : '丢'}，实际${got ? '收了' : '没收'}`);
  if (got && kind && got.kind !== kind) problems.push(`${name}：类型应为 ${kind}，实际 ${got.kind}`);
}

// 界面上真的画出来了吗
await sleep(800);
const ui = await js(`(() => ({
  面板在: !!document.querySelector('.cliptray'),
  行数: document.querySelectorAll('.clip').length,
  第一行: document.querySelector('.clip__text')?.textContent?.slice(0,40) ?? null,
}))()`);
console.log(`\n界面：${JSON.stringify(ui, null, 0)}`);
if (!ui.面板在) problems.push('剪贴板面板没渲染出来');
if (ui.行数 !== 3) problems.push(`界面应显示 3 条（只有 3 条该收），实际 ${ui.行数} —— 多出来的是本该被丢弃的`);

// 开关真的能关掉吗 —— 这正是原来那个假开关的问题
console.log('\n' + line());
console.log('  关掉开关，看是不是真停了（原来这个开关是空的）');
console.log(line());
await js(`window.synorive.settings.patch({ clipboardSentinel: false })`);
await sleep(600);
const afterOff = await js(`window.synorive.clip.list()`);
console.log(`  关掉后内存里剩 ${afterOff.length} 条${afterOff.length === 0 ? '（已清空 ✓）' : ' ✗'}`);
if (afterOff.length !== 0) problems.push('关掉哨兵后内存没清空');

setClip('关掉之后复制的这句不应该被收进去');
await sleep(1800);
const afterOffList = await js(`window.synorive.clip.list()`);
console.log(`  关掉后再复制，收到 ${afterOffList.length} 条${afterOffList.length === 0 ? '（没收 ✓）' : ' ✗ 开关是假的'}`);
if (afterOffList.length !== 0) problems.push('关掉哨兵后仍在收集 —— 开关没生效');

const uiOff = await js(`document.querySelectorAll('.clip').length`);
console.log(`  关掉后界面剩 ${uiOff} 行${uiOff === 0 ? ' ✓' : ' ✗'}`);
if (uiOff !== 0) problems.push('关掉哨兵后界面没清空');

// 开回来
await js(`window.synorive.settings.patch({ clipboardSentinel: true })`);
await sleep(600);
setClip('重新打开之后这句应该能收到');
await sleep(1800);
const back = await js(`window.synorive.clip.list()`);
console.log(`  重新打开后收到 ${back.length} 条${back.length >= 1 ? ' ✓' : ' ✗'}`);
if (back.length < 1) problems.push('重新打开哨兵后收不到');

console.log('\n' + '='.repeat(74));
if (problems.length) {
  for (const p of problems) console.error(`✗ ${p}`);
  ws.close(); process.exit(1);
}
console.log('✓ E4 剪贴板哨兵端到端通过（收集 / 密钥丢弃 / 界面 / 开关真启停）');
ws.close(); process.exit(0);
