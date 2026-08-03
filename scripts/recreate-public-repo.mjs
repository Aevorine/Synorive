#!/usr/bin/env node
/**
 * 重建公开仓库 + 发布 v0.1.1
 * ====================================================================
 * 为什么要重建而不是改可见性：
 *
 * `git filter-repo` + `force push` 能让隐私文件从**分支**上消失，
 * 但 GitHub **不会立刻回收失去引用的对象**。旧提交依然能按 SHA 直接读到 ——
 * 2026-08-03 实测：仓库转公开后
 *   GET /repos/.../contents/.claude/.pv-prompts.json?ref=5b45787 → 200，内容可读
 * 官方的说法是要找 Support 才能清缓存和悬空引用。
 *
 * **删库重建是唯一能立刻、确定地做到零残留的办法** —— 悬空对象跟着旧仓库
 * 一起没了。代价是丢掉 star 和创建日期。
 *
 * 前置：`gh auth refresh -h github.com -s delete_repo`（只有仓库主人能授权），
 *       然后手动删掉 Aevorine/Synorive，或者让这个脚本代删。
 *
 * 用法：
 *   node scripts/recreate-public-repo.mjs            演练，只打印要做什么
 *   node scripts/recreate-public-repo.mjs --confirm  真的执行
 */

import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const CONFIRM = process.argv.includes('--confirm');

const OWNER = 'Aevorine';
const NAME = 'Synorive';
const SLUG = `${OWNER}/${NAME}`;
const version = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')).version;
const tag = `v${version}`;

const DESCRIPTION =
  'Local-first multimodal search over your own files (docs/code/images/video) + ' +
  'multi-engine web research that actively hunts for counter-evidence. Fully offline-capable. ' +
  '16 MCP tools for Claude Code. 本地优先的多模态检索 + 会自己找反驳材料的联网研究工作台';

const TOPICS = [
  'chinese-nlp', 'claude-code', 'desktop-app', 'electron', 'fact-checking',
  'fastapi', 'full-text-search', 'knowledge-base', 'local-first', 'mcp-server',
  'misinformation-detection', 'multimodal', 'ocr', 'offline-first', 'privacy-first',
  'rag', 'semantic-search', 'sqlite-vec', 'vector-search', 'video-search',
];

const RELEASE_DIR = join(ROOT, 'release');
const ASSETS = [
  join(RELEASE_DIR, `Synorive-Setup-${version}.exe`),
  join(RELEASE_DIR, `Synorive-Setup-${version}.exe.blockmap`),
  join(RELEASE_DIR, `Synorive-${version}-portable.exe`),
  join(RELEASE_DIR, 'latest.yml'),
  join(ROOT, 'apps', 'mobile', 'app', 'build', 'outputs', 'apk', 'release', 'app-release.apk'),
];

function run(cmd, args) {
  console.log(`▶ ${cmd} ${args.join(' ')}`);
  if (!CONFIRM) return;
  const r = spawnSync(cmd, args, { cwd: ROOT, stdio: 'inherit', shell: true });
  if (r.status !== 0) {
    console.error(`✗ 失败（退出码 ${r.status}）`);
    process.exit(1);
  }
}

// ── 出发前检查 ────────────────────────────────────────────
console.log(`═══ 重建 ${SLUG} 为公开仓并发布 ${tag} ═══\n`);

// 🔴 产物必须先在，不然会推出一个空 Release，而空 Release 比没有 Release 更糟：
//    更新器会查到「有新版本」然后下载 404
let missing = false;
for (const a of ASSETS) {
  if (!existsSync(a)) {
    console.error(`✗ 缺产物：${a}`);
    missing = true;
  } else {
    console.log(`  ✓ ${a.replace(ROOT, '.')}`);
  }
}
if (missing) {
  console.error('\n先跑 npm run release 把产物出全，再来。');
  process.exit(1);
}

// latest.yml 指向的文件名必须真的存在 —— 对不上的话「检查更新」正常、点下载 404
const declared = readFileSync(join(RELEASE_DIR, 'latest.yml'), 'utf8').match(/^path:\s*(.+)$/m)?.[1]?.trim();
if (!declared || !existsSync(join(RELEASE_DIR, declared))) {
  console.error(`✗ latest.yml 指向「${declared}」，但 release/ 里没有这个文件`);
  process.exit(1);
}
console.log(`  ✓ latest.yml → ${declared}\n`);

// 本地历史必须是干净的
const dirty = execFileSync('git', ['status', '--porcelain'], { cwd: ROOT, encoding: 'utf8' })
  .split('\n')
  .filter((l) => l.trim() && !l.startsWith('??'));
if (dirty.length) {
  console.error('✗ 工作树有未提交改动，先提交：\n' + dirty.join('\n'));
  process.exit(1);
}

for (const f of ['.claude/.pv-prompts.json', '.claude/.pv-state.json', 'task-progress.md', '.claude/PROJECT-BRIEF.md']) {
  const hit = spawnSync('git', ['log', '--all', '--oneline', '--', f], { cwd: ROOT, encoding: 'utf8' });
  if (hit.stdout?.trim()) {
    console.error(`✗ 本地历史里还有 ${f} —— 先跑 git_filter_repo 清掉`);
    process.exit(1);
  }
}
console.log('  ✓ 本地历史里没有隐私文件\n');

if (!CONFIRM) {
  console.log('—— 以上是演练。下面这些动作加 --confirm 才会真的执行 ——\n');
}

// ── 执行 ──────────────────────────────────────────────────

// 仓库可能已经被手动删过了（`gh` 的 token 默认没有 delete_repo 权限，
// 网页上删是更省事的一条路）。已经没了就跳过，不要因为"删一个不存在的东西
// 失败了"把整个流程停住 —— 那是一个成功的前置条件，不是错误。
const exists = spawnSync('gh', ['repo', 'view', SLUG, '--json', 'name'], {
  cwd: ROOT, stdio: 'ignore', shell: true,
}).status === 0;

if (exists) {
  console.log('· 远端仓库还在，先删');
  run('gh', ['repo', 'delete', SLUG, '--yes']);
} else {
  console.log('· 远端仓库已不存在（你手动删过了），跳过删除这一步');
}

run('gh', ['repo', 'create', SLUG, '--public', '--description', `"${DESCRIPTION}"`]);
run('gh', ['repo', 'edit', SLUG, '--add-topic', TOPICS.join(',')]);
run('git', ['remote', 'set-url', 'origin', `https://github.com/${SLUG}.git`]);
run('git', ['push', '-u', 'origin', 'main']);
run('gh', [
  'release', 'create', tag,
  ...ASSETS.map((a) => `"${a}"`),
  '--title', `"Synorive ${tag}"`,
  '--notes', '"首个带应用内自更新的版本。桌面端装 Synorive-Setup-0.1.1.exe，安卓端装 app-release.apk。"',
]);

if (CONFIRM) {
  console.log(`\n✓ 完成：https://github.com/${SLUG}/releases/tag/${tag}`);
  console.log('  两端下一次「检查更新」就能看到它了。');
  console.log('\n🔴 收尾自查（务必跑一遍）：');
  console.log(`   curl -s -o /dev/null -w "%{http_code}\\n" https://api.github.com/repos/${SLUG}/contents/.claude/.pv-prompts.json?ref=5b45787`);
  console.log('   期望 404 —— 不是 404 就说明重建没起作用，立刻改回私有。');
}
