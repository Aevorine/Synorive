#!/usr/bin/env node
/**
 * 发版 —— 出两端产物并（可选）发到 GitHub Releases
 * ====================================================================
 * 自更新链路上有三个"漏了也不报错"的坑，这个脚本存在就是为了堵它们：
 *
 *  ① **latest.yml 没上传** → 桌面端更新器查到的是「已是最新」而不是报错，
 *     用户永远收不到更新，你也永远不会收到反馈。
 *  ② **tag 和 package.json 版本对不上** → electron-updater 404。
 *  ③ **APK 没传** → 手机端能查到新版但下不了。
 *
 * 用法：
 *   node scripts/release.mjs            出两端产物，不上传（默认，安全）
 *   node scripts/release.mjs --publish  出产物并创建 GitHub Release 上传
 *   node scripts/release.mjs --desktop-only / --android-only
 *
 * 🔴 --publish 会往公网发东西，是不可逆的。它需要 `gh` 已登录。
 */

import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const args = new Set(process.argv.slice(2));
const PUBLISH = args.has('--publish');
const DESKTOP = !args.has('--android-only');
const ANDROID = !args.has('--desktop-only');

const version = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')).version;
const tag = `v${version}`;

function run(cmd, cmdArgs, cwd) {
  console.log(`\n▶ ${cmd} ${cmdArgs.join(' ')}`);
  const r = spawnSync(cmd, cmdArgs, { cwd: cwd ?? ROOT, stdio: 'inherit', shell: true });
  if (r.status !== 0) {
    console.error(`✗ 失败（退出码 ${r.status}）：${cmd} ${cmdArgs.join(' ')}`);
    process.exit(1);
  }
}

/**
 * 🔴 只看退出码是不够的 —— 构建工具在"什么都没产出"的情况下照样能退 0。
 *    这里必须核对**文件真的存在且字节数合理**。
 */
function mustExist(path, minBytes, why) {
  if (!existsSync(path)) {
    console.error(`✗ 缺少产物：${path}\n  ${why}`);
    process.exit(1);
  }
  const size = statSync(path).size;
  if (size < minBytes) {
    console.error(`✗ 产物太小，几乎肯定是坏的：${path}（${size} 字节）\n  ${why}`);
    process.exit(1);
  }
  console.log(`  ✓ ${path}（${(size / 1024 / 1024).toFixed(1)} MB）`);
}

console.log(`═══ Synorive 发版 ${tag} ═══`);

// 版本号四处一致是前提，不一致就别往下走了
run('node', ['scripts/set-version.mjs', '--check']);

const artifacts = [];

// ── 桌面端 ────────────────────────────────────────────────
if (DESKTOP) {
  console.log('\n── 桌面端 ──');
  run('npm', ['run', 'pack:win'], join(ROOT, 'apps', 'desktop'));

  const releaseDir = join(ROOT, 'release');
  const setup = join(releaseDir, `Synorive-Setup-${version}.exe`);
  const portable = join(releaseDir, `Synorive-${version}-portable.exe`);
  const latestYml = join(releaseDir, 'latest.yml');

  mustExist(setup, 50 * 1024 * 1024, 'NSIS 安装包没出来。注意：NSIS 报错不影响 portable 产出，日志里的「构建成功」可能只是 portable 成了');
  mustExist(portable, 50 * 1024 * 1024, 'portable 包没出来');
  mustExist(
    latestYml,
    100,
    'latest.yml 没生成 —— 说明 electron-builder.yml 里的 publish 配置没生效。没有它，自动更新整条链路是死的',
  );

  // 🔴 latest.yml 里写的文件名必须和磁盘上真实存在的文件对得上。
  //    对不上时「检查更新」一切正常，只有真的点了下载才 404 ——
  //    是这条链路上最晚才暴露、最难联想到根因的一个错。
  const declared = readFileSync(latestYml, 'utf8').match(/^path:\s*(.+)$/m)?.[1]?.trim();
  if (!declared) {
    console.error('✗ latest.yml 里没有 path 字段，格式不对');
    process.exit(1);
  }
  if (!existsSync(join(releaseDir, declared))) {
    console.error(
      `✗ latest.yml 说安装包叫「${declared}」，但 release/ 里没有这个文件。\n` +
        '  更新器会按这个名字去 GitHub 下载，下不到 → 404。\n' +
        '  多半是 nsis.artifactName 改了但没重新打包，或者文件名里带了空格。',
    );
    process.exit(1);
  }
  console.log(`  ✓ latest.yml 指向的 ${declared} 确实存在`);

  artifacts.push(setup, latestYml);
  // portable 也传：便携版用户虽然不能自动更新，但要能下到新版
  artifacts.push(portable);
}

// ── 安卓端 ────────────────────────────────────────────────
if (ANDROID) {
  console.log('\n── 安卓端 ──');
  const mobileDir = join(ROOT, 'apps', 'mobile');

  if (!existsSync(join(mobileDir, 'keystore.properties'))) {
    console.error(
      '✗ apps/mobile/keystore.properties 不存在 —— 打出来的 release APK 不会有正式签名，\n' +
        '  装到手机上会和已装的正式版冲突（Android 靠签名判断"是不是同一个应用"）。\n' +
        '  照 apps/mobile/keystore.properties.example 建一个，或跑 node scripts/make-android-keystore.mjs',
    );
    process.exit(1);
  }

  const gradlew = process.platform === 'win32' ? 'gradlew.bat' : './gradlew';
  run(gradlew, ['assembleRelease', '--no-daemon'], mobileDir);

  const apkDir = join(mobileDir, 'app', 'build', 'outputs', 'apk', 'release');
  const apk = readdirSync(apkDir).find((f) => f.endsWith('.apk'));
  if (!apk) {
    console.error(`✗ ${apkDir} 里没有 APK`);
    process.exit(1);
  }
  const apkPath = join(apkDir, apk);
  mustExist(apkPath, 3 * 1024 * 1024, 'APK 太小，多半是资源没打进去');
  artifacts.push(apkPath);
}

// ── 上传 ──────────────────────────────────────────────────
console.log('\n── 产物清单 ──');
for (const a of artifacts) console.log(`  ${a}`);

if (!PUBLISH) {
  console.log(
    `\n✓ 产物已出，**没有上传**。\n` +
      `  确认无误后跑：node scripts/release.mjs --publish\n` +
      `  或手动：gh release create ${tag} ${artifacts.map((a) => `"${a}"`).join(' ')}`,
  );
  process.exit(0);
}

try {
  execFileSync('gh', ['auth', 'status'], { stdio: 'ignore', shell: true });
} catch {
  console.error('✗ gh 没登录。先跑 gh auth login。');
  process.exit(1);
}

console.log(`\n▶ 创建 GitHub Release ${tag}`);
run('gh', [
  'release',
  'create',
  tag,
  ...artifacts.map((a) => `"${a}"`),
  '--title',
  `"Synorive ${tag}"`,
  '--generate-notes',
]);

console.log(`\n✓ 发布完成：${tag}`);
console.log('  桌面端和安卓端下一次检查更新就能看到它了。');
