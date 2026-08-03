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

function run(cmd, cmdArgs, cwd, env) {
  console.log(`\n▶ ${cmd} ${cmdArgs.join(' ')}`);
  const r = spawnSync(cmd, cmdArgs, {
    cwd: cwd ?? ROOT,
    stdio: 'inherit',
    shell: true,
    ...(env ? { env: { ...process.env, ...env } } : {}),
  });
  if (r.status !== 0) {
    console.error(`✗ 失败（退出码 ${r.status}）：${cmd} ${cmdArgs.join(' ')}`);
    process.exit(1);
  }
}

/**
 * 找一个 JDK 17+ 给 Gradle 用。
 *
 * 🔴 **不能直接指望 PATH 里的 `java`。** 这台机器上 `java -version` 报的是
 * 1.8.0_481 —— 而 Android Gradle Plugin 要 17+。用 Java 8 跑 `assembleRelease`
 * 的报错是一句 `Unsupported class file major version` 或者
 * `Could not initialize class org.codehaus.groovy...`，**两句都不提 JDK 版本**，
 * 看到的人只会以为 Gradle 配置坏了，然后去翻 build.gradle.kts。
 *
 * 🔴 **也不能把路径写进 `gradle.properties` 的 `org.gradle.java.home`。**
 * 那个文件是要进仓库的，写死一个 `D:\APPS\JDK17\...` 等于把本机路径
 * 提交给所有人 —— 别人克隆下来构建，报错会指向一个他机器上不存在的目录。
 *
 * 所以在这里探测，只对这一次 Gradle 调用设 `JAVA_HOME`。
 * 顺序：`JAVA_HOME` 已经够新就直接用 → 常见安装位置扫一遍 → 都没有就明确报错。
 */
function findJdk17() {
  const probe = (home) => {
    const exe = join(home, 'bin', process.platform === 'win32' ? 'java.exe' : 'java');
    if (!existsSync(exe)) return null;
    const r = spawnSync(exe, ['-version'], { encoding: 'utf8', shell: false });
    // `java -version` 走的是 stderr，这是它几十年的老行为，不是 bug
    const text = `${r.stderr ?? ''}${r.stdout ?? ''}`;
    const m = text.match(/version "(\d+)[.\d_]*"/);
    const major = m ? parseInt(m[1], 10) : 0;
    return major >= 17 ? { home, major } : null;
  };

  if (process.env.JAVA_HOME) {
    const got = probe(process.env.JAVA_HOME);
    if (got) return got;
  }

  const roots = [
    'C:\\Program Files\\Java',
    'C:\\Program Files\\Eclipse Adoptium',
    'C:\\Program Files\\Microsoft',
    'C:\\Program Files\\Amazon Corretto',
    'C:\\Program Files\\Zulu',
    'C:\\Program Files\\Android\\Android Studio\\jbr',
    'D:\\APPS\\JDK17',
    '/usr/lib/jvm',
    '/Library/Java/JavaVirtualMachines',
  ];
  for (const root of roots) {
    if (!existsSync(root)) continue;
    // 目录本身可能就是一个 JDK（Android Studio 的 jbr 就是这样）
    const direct = probe(root);
    if (direct) return direct;
    let entries = [];
    try {
      entries = readdirSync(root);
    } catch {
      continue;
    }
    for (const e of entries) {
      const got = probe(join(root, e))
        // macOS 的 .jdk 包多一层 Contents/Home
        ?? probe(join(root, e, 'Contents', 'Home'));
      if (got) return got;
    }
  }
  return null;
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

  const jdk = findJdk17();
  if (!jdk) {
    console.error(
      '✗ 没找到 JDK 17 或更高版本，Android Gradle Plugin 跑不起来。\n' +
        '  （PATH 里的 java 可能是 8/11 —— 那个版本报的错完全不提 JDK，\n' +
        '   会让你去翻 build.gradle.kts，方向就跑偏了）\n' +
        '  装一个：winget install EclipseAdoptium.Temurin.17.JDK\n' +
        '  或者已经装了就设 JAVA_HOME 指向它，再重跑。',
    );
    process.exit(1);
  }
  console.log(`  ✓ Gradle 用 JDK ${jdk.major}：${jdk.home}`);

  // 🔴 **必须用绝对路径调 gradlew，不能靠"反正 cwd 是它所在的目录"。**
  //
  // 实测（2026-08-04，本机）：`spawnSync('gradlew.bat', …, {cwd: mobileDir, shell:true})`
  // 报 `'gradlew.bat' is not recognized as an internal or external command`，
  // 而同一个文件用绝对路径调就正常打印 Gradle 8.9。
  // 原因是 cmd.exe 的"先搜当前目录"这条行为**并不是永远成立的**
  // （`NoDefaultCurrentDirectoryInExePath` 一类的环境/组策略会关掉它）。
  //
  // 这个错最坑的地方是它长得像"你没装 Gradle"，而文件明明就在那儿 ——
  // 于是排查方向会一路跑到"是不是 wrapper 没提交""是不是要先 gradle wrapper"，
  // 而真正的问题只是**调用方式**。绝对路径在所有平台都成立，没有理由不用。
  const gradlew = join(mobileDir, process.platform === 'win32' ? 'gradlew.bat' : 'gradlew');
  if (!existsSync(gradlew)) {
    console.error(
      `✗ 找不到 Gradle wrapper：${gradlew}\n` +
        '  它应该是随仓库一起提交的。没有的话在 apps/mobile 里跑一次 `gradle wrapper`。',
    );
    process.exit(1);
  }
  run(`"${gradlew}"`, ['assembleRelease', '--no-daemon'], mobileDir, { JAVA_HOME: jdk.home });

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
