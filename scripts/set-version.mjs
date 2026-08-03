#!/usr/bin/env node
/**
 * 版本号同步 —— 一条命令改完所有地方
 * ====================================================================
 * 自更新的整条链路都挂在版本号上，而版本号散落在四个文件里。
 * 手动改必然会漏一个，而漏掉的那一个**不会报错**：
 *
 *   · 漏了 apps/desktop/package.json → 打出来的包版本还是旧的，
 *     用户装上新版，更新器仍然认为有更新，无限提示
 *   · 漏了 apps/mobile 的 versionCode → 手机端永远查不到新版，
 *     而且它显示的是「已是最新」，不是报错
 *   · Release 的 tag 和 package.json 对不上 → electron-updater 404
 *
 * 所以：**永远用这个脚本改版本号，不要手动编辑。**
 *
 * 用法：
 *   node scripts/set-version.mjs 0.1.2
 *   node scripts/set-version.mjs --check     只检查各处是否一致，不改
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const ROOT_PKG = join(ROOT, 'package.json');
const DESKTOP_PKG = join(ROOT, 'apps', 'desktop', 'package.json');
const MOBILE_GRADLE = join(ROOT, 'apps', 'mobile', 'app', 'build.gradle.kts');
// 🔴 **引擎这两处 2026-08-03 才被加进来，在那之前一直漏着。**
// 后果不是"少改一个数"：`synorive.__version__` 会被 `/health` 返回，
// 桌面端状态栏显示的就是它 —— 发了 v0.1.1 的包，应用里却写着 0.1.0，
// 用户没法判断自己装的到底是不是新版。**版本号散在几处就一定会漏，
// 所以判据是"全仓 grep 得到的每一处都在这个脚本里"。**
const ENGINE_PYPROJECT = join(ROOT, 'engine', 'pyproject.toml');
const ENGINE_INIT = join(ROOT, 'engine', 'synorive', '__init__.py');

/**
 * versionName → versionCode。
 * 和 UpdateModels.kt 里的 versionCodeFromTag() **必须是同一个公式**，
 * 否则手机端算出来的远端版本号和 APK 里烧进去的对不上，
 * 症状是「明明发了新版，手机说已是最新」或者「装完还提示更新」。
 */
function versionCode(v) {
  const [major = 0, minor = 0, patch = 0] = v.split('.').map((x) => parseInt(x, 10) || 0);
  return major * 10000 + minor * 100 + patch;
}

function readJson(p) {
  return JSON.parse(readFileSync(p, 'utf8'));
}

function currentVersions() {
  const gradle = readFileSync(MOBILE_GRADLE, 'utf8');
  return {
    root: readJson(ROOT_PKG).version,
    desktop: readJson(DESKTOP_PKG).version,
    androidName: gradle.match(/versionName\s*=\s*"([^"]+)"/)?.[1] ?? '(没找到)',
    androidCode: gradle.match(/versionCode\s*=\s*(\d+)/)?.[1] ?? '(没找到)',
    enginePyproject:
      readFileSync(ENGINE_PYPROJECT, 'utf8').match(/^version\s*=\s*"([^"]+)"/m)?.[1] ?? '(没找到)',
    engineInit:
      readFileSync(ENGINE_INIT, 'utf8').match(/__version__\s*=\s*"([^"]+)"/)?.[1] ?? '(没找到)',
  };
}

function check() {
  const v = currentVersions();
  const want = v.root;
  const wantCode = String(versionCode(want));
  const problems = [];
  if (v.desktop !== want) problems.push(`apps/desktop/package.json = ${v.desktop}，应为 ${want}`);
  if (v.androidName !== want) problems.push(`安卓 versionName = ${v.androidName}，应为 ${want}`);
  if (v.androidCode !== wantCode) {
    problems.push(`安卓 versionCode = ${v.androidCode}，应为 ${wantCode}`);
  }
  if (v.enginePyproject !== want) {
    problems.push(`engine/pyproject.toml = ${v.enginePyproject}，应为 ${want}`);
  }
  if (v.engineInit !== want) {
    problems.push(`engine/synorive/__init__.py __version__ = ${v.engineInit}，应为 ${want}`
      + '（它会被 /health 返回，桌面端状态栏显示的就是它）');
  }

  console.log('当前版本：');
  console.log(`  根 package.json      ${v.root}`);
  console.log(`  桌面 package.json    ${v.desktop}`);
  console.log(`  安卓 versionName     ${v.androidName}`);
  console.log(`  安卓 versionCode     ${v.androidCode}（由 versionName 推导，应为 ${wantCode}）`);
  console.log(`  引擎 pyproject       ${v.enginePyproject}`);
  console.log(`  引擎 __version__     ${v.engineInit}  ← /health 报的就是它`);

  if (problems.length) {
    console.error('\n✗ 版本号不一致：');
    for (const p of problems) console.error(`  · ${p}`);
    console.error(`\n跑 node scripts/set-version.mjs ${want} 修好它。`);
    process.exit(1);
  }
  console.log('\n✓ 四处版本号一致。');
}

function setVersion(next) {
  if (!/^\d+\.\d+\.\d+$/.test(next)) {
    console.error(`✗ 版本号要写成 x.y.z，收到的是「${next}」`);
    process.exit(1);
  }
  const code = versionCode(next);

  for (const p of [ROOT_PKG, DESKTOP_PKG]) {
    const pkg = readJson(p);
    pkg.version = next;
    // 保留结尾换行，避免每次改版本都在 diff 里多出一行噪声
    writeFileSync(p, `${JSON.stringify(pkg, null, 2)}\n`, 'utf8');
  }

  let gradle = readFileSync(MOBILE_GRADLE, 'utf8');
  // 🔴 判据是**正则有没有命中**，不是"字符串变没变"。
  //    原来写的是 `if (gradle === before) 报错` —— 那在
  //    「本来就已经是目标版本」时会误报"没找到"并退出 1，
  //    而那恰恰是重复执行同一条命令的正常情况。
  //    replace 没匹配到时静默返回原串，所以必须显式 test。
  for (const [re, label] of [
    [/versionName\s*=\s*"[^"]+"/, 'versionName'],
    [/versionCode\s*=\s*\d+/, 'versionCode'],
  ]) {
    if (!re.test(gradle)) {
      console.error(`✗ 在 app/build.gradle.kts 里没找到 ${label}，没有改动它`);
      process.exit(1);
    }
  }
  gradle = gradle.replace(/versionName\s*=\s*"[^"]+"/, `versionName = "${next}"`);
  gradle = gradle.replace(/versionCode\s*=\s*\d+/, `versionCode = ${code}`);
  writeFileSync(MOBILE_GRADLE, gradle, 'utf8');

  // 引擎两处。同样要检查 replace 有没有真的命中 —— replace 没匹配到时
  // **静默返回原字符串**，不检查的话脚本会报"已同步"而引擎版本一个字没改
  for (const [path, re, make] of [
    [ENGINE_PYPROJECT, /^version\s*=\s*"[^"]+"/m, () => `version = "${next}"`],
    [ENGINE_INIT, /__version__\s*=\s*"[^"]+"/, () => `__version__ = "${next}"`],
  ]) {
    const before = readFileSync(path, 'utf8');
    // 同上：判正则命中，不判字符串变没变 —— 否则"本来就对"会被误报成"没找到"
    if (!re.test(before)) {
      console.error(`✗ 在 ${path} 里没找到版本号，没有改动它`);
      process.exit(1);
    }
    writeFileSync(path, before.replace(re, make()), 'utf8');
  }

  console.log(`✓ 版本号已同步为 ${next}（安卓 versionCode = ${code}）`);
  console.log('  改到了：package.json / apps/desktop/package.json /');
  console.log('          apps/mobile/app/build.gradle.kts /');
  console.log('          engine/pyproject.toml / engine/synorive/__init__.py');
  console.log(`\n下一步：git commit 后打 tag —— git tag v${next}`);
}

const arg = process.argv[2];
if (!arg || arg === '--check') check();
else setVersion(arg);
