/**
 * 一键把 Synorive 接进 Claude Code
 * ====================================================================
 * 做两件事：
 *   ① 注册 MCP 服务器（走 `claude mcp add` CLI，**不手改 .claude.json**）
 *   ② 装 Skill 包到 ~/.claude/skills/synorive/
 *
 * ⚠️ 为什么不手改 .claude.json：多个 Claude Code 实例并存时，
 *    各自在内存里持有一份配置，**后写盘的会用自己的旧快照整体覆盖新的**。
 *    手改过的配置第二天就没了。一律走 CLI。
 *
 * 用法：node scripts/install-claude-integration.mjs [--dry-run]
 */

import { execFileSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dryRun = process.argv.includes('--dry-run');

function run(cmd, args) {
  if (dryRun) {
    console.log(`  [dry-run] ${cmd} ${args.join(' ')}`);
    return '';
  }
  return execFileSync(cmd, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
}

function hasClaude() {
  try {
    execFileSync('claude', ['--version'], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

// ── ① MCP 服务器 ────────────────────────────────────────────
console.log('① 注册 MCP 服务器');

const serverJs = join(ROOT, 'mcp', 'dist', 'index.js');
if (!existsSync(serverJs)) {
  console.error(`  ✗ 还没构建：${serverJs}`);
  console.error('    先跑 npm run build --workspace=@synorive/mcp');
  process.exit(1);
}

if (!hasClaude()) {
  console.log('  · 没找到 claude 命令，跳过自动注册。手动执行：');
  console.log(`    claude mcp add synorive -- node "${serverJs}"`);
} else {
  // 先看现状 —— 改配置前先读一眼，别闭着眼睛动
  let existing = '';
  try {
    existing = run('claude', ['mcp', 'list']);
  } catch {
    /* 没配过任何 MCP 时会报错，正常 */
  }
  if (existing.includes('synorive')) {
    console.log('  · synorive 已经注册过了，先移除再重加（保证指向当前路径）');
    try {
      run('claude', ['mcp', 'remove', 'synorive']);
    } catch {
      /* 移除失败不致命 */
    }
  }
  try {
    run('claude', ['mcp', 'add', 'synorive', '--', 'node', serverJs]);
    console.log(`  ✓ 已注册：node ${serverJs}`);
  } catch (e) {
    console.error(`  ✗ 注册失败：${e.message ?? e}`);
    console.error(`    手动执行：claude mcp add synorive -- node "${serverJs}"`);
  }
}

// ── ② Skill 包 ──────────────────────────────────────────────
console.log('');
console.log('② 安装 Skill 包');

const src = join(ROOT, 'mcp', 'skill');
const dst = join(homedir(), '.claude', 'skills', 'synorive');

if (!existsSync(src)) {
  console.error(`  ✗ 源目录不存在：${src}`);
  process.exit(1);
}

if (dryRun) {
  console.log(`  [dry-run] ${src} → ${dst}`);
} else {
  mkdirSync(dst, { recursive: true });
  let n = 0;
  for (const f of readdirSync(src)) {
    copyFileSync(join(src, f), join(dst, f));
    n++;
  }
  console.log(`  ✓ ${n} 个文件 → ${dst}`);
}

console.log('');
console.log('完成。新开一个 Claude Code 会话就能用了：');
console.log('  · 问「我之前存过关于 X 的东西吗」会自动触发检索');
console.log('  · 也可以直接说「用 synorive 搜 X」');
console.log('');
console.log('命令行版本：');
console.log(`  node "${join(ROOT, 'cli', 'dist', 'index.js')}" search "关键词"`);
