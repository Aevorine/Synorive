#!/usr/bin/env node
/**
 * 生成安卓 release 签名密钥库
 * ====================================================================
 * 🔴 **密钥库丢了 = 这个应用再也无法发布能覆盖安装的更新。**
 *    Android 靠签名判断"是不是同一个应用"。换了密钥的包在用户手机上
 *    只能先卸载再装，本地数据全丢。所以：
 *      · 默认放到**仓库外**（避免哪天 gitignore 写漏了就传上去）
 *      · 生成完立刻自己备份一份到别的地方
 *
 * 口令默认随机生成并写进 apps/mobile/keystore.properties（已 gitignore）。
 * 想自己定就传 --pass=<口令>。
 *
 * 用法：
 *   node scripts/make-android-keystore.mjs
 *   node scripts/make-android-keystore.mjs --out=D:/keys/synorive-release.jks --pass=xxxx
 */

import { spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

function arg(name, fallback) {
  const hit = process.argv.slice(2).find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
}

/** 默认放在用户目录下的 .synorive-keys，**不在仓库里** */
const outPath = resolve(arg('out', join(homedir(), '.synorive-keys', 'synorive-release.jks')));
const alias = arg('alias', 'synorive');
const pass = arg('pass', randomBytes(18).toString('base64url'));
const propsPath = join(ROOT, 'apps', 'mobile', 'keystore.properties');

if (existsSync(outPath)) {
  console.error(
    `✗ ${outPath} 已经存在。\n` +
      '  没有覆盖它 —— 覆盖等于把旧密钥销毁，用它签过的包从此无法覆盖更新。\n' +
      '  真要换一把，先自己把旧的挪走。',
  );
  process.exit(1);
}

/**
 * keytool 在 JDK 的 bin 里。JAVA_HOME 优先，其次在 PATH 上找。
 *
 * 🔴 **必须解析成绝对路径，因为下面 spawnSync 不能开 shell。**
 *    `-dname "CN=Synorive, OU=..."` 这个参数里有空格和逗号，
 *    走 shell 的话 Windows 会把它拆成好几个参数，keytool 报的是
 *    「非法选项」并打出整页用法 —— 完全看不出是引号被吃了。
 */
function resolveKeytool() {
  if (process.env.JAVA_HOME) {
    const p = join(process.env.JAVA_HOME, 'bin', 'keytool.exe');
    if (existsSync(p)) return p;
    const nix = join(process.env.JAVA_HOME, 'bin', 'keytool');
    if (existsSync(nix)) return nix;
  }
  const finder = process.platform === 'win32' ? 'where' : 'which';
  const found = spawnSync(finder, ['keytool'], { encoding: 'utf8' });
  const first = found.stdout?.split(/\r?\n/).find((l) => l.trim());
  if (first) return first.trim();
  return null;
}

const keytool = resolveKeytool();
if (!keytool) {
  console.error(
    '✗ 找不到 keytool。它在 JDK 里（JRE 里的那个不够用）。\n' +
      '  装一个：winget install Microsoft.OpenJDK.17\n' +
      '  装完把 JAVA_HOME 指到它，或者重开一个终端再跑。',
  );
  process.exit(1);
}

mkdirSync(dirname(outPath), { recursive: true });

console.log(`▶ 生成密钥库 ${outPath}`);
const r = spawnSync(
  keytool,
  [
    '-genkeypair',
    '-v',
    '-keystore', outPath,
    '-alias', alias,
    '-keyalg', 'RSA',
    '-keysize', '4096',
    // 10000 天 ≈ 27 年。Google Play 要求有效期至少到 2033 年，
    // 而且**证书到期后就没法再发更新了**，短有效期是给自己埋雷
    '-validity', '10000',
    '-storepass', pass,
    '-keypass', pass,
    '-dname', 'CN=Synorive, OU=Synorive, O=Synorive, C=CN',
  ],
  // 不开 shell —— 见 resolveKeytool 上面那段注释
  { stdio: 'inherit' },
);

if (r.status !== 0) {
  console.error(
    `\n✗ keytool 失败（退出码 ${r.status}）。\n` +
      '  最常见的原因是机器上没有 JDK，或 JAVA_HOME 指向的是 JRE。\n' +
      '  装一个 JDK 17：winget install Microsoft.OpenJDK.17',
  );
  process.exit(1);
}

// 🔴 只看退出码不够 —— keytool 参数不对时也可能退 0 而不产文件
if (!existsSync(outPath)) {
  console.error('✗ keytool 报成功，但文件没生成。别继续，先手动跑一遍 keytool 看它说什么。');
  process.exit(1);
}

writeFileSync(
  propsPath,
  [
    '# 由 scripts/make-android-keystore.mjs 生成',
    '# 🔴 这个文件在 .gitignore 里，永远不要提交，也不要贴到任何地方。',
    `storeFile=${outPath.replace(/\\/g, '/')}`,
    `storePassword=${pass}`,
    `keyAlias=${alias}`,
    `keyPassword=${pass}`,
    '',
  ].join('\n'),
  'utf8',
);

console.log(`\n✓ 密钥库已生成：${outPath}`);
console.log(`✓ 配置已写入：${propsPath}（已被 gitignore）`);
console.log('\n🔴 现在立刻做两件事：');
console.log(`   ① 把 ${outPath} 备份到另一个地方（U盘/网盘/密码管理器附件都行）`);
console.log(`   ② 把口令记下来：${pass}`);
console.log('   丢了这两样，这个应用就再也发不出能覆盖安装的更新了。');
