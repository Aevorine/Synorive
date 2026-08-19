/**
 * 云端 API Key 存储（H3）
 * ============================================================
 * 用 Electron `safeStorage`（Windows 走 DPAPI，绑定当前系统用户账号）
 * 加密后存成一个二进制文件，**不写进 settings.json**——那份是明文 JSON，
 * `AppSettings.cloud.credentialKey` 只存一个固定引用名，真正的密钥
 * 只活在这个文件里，且只有当前 Windows 账号能解出来。
 *
 * `safeStorage.isEncryptionAvailable()` 在某些系统配置下会是 false
 * （比如没有登录态的服务账号）——这种情况下**宁可不存**，
 * 也不退化成明文落盘。用户会看到"没连接"，比"以为存了其实是明文"安全。
 */
import { app, safeStorage } from 'electron';
import { existsSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

function keyFile(): string {
  return join(app.getPath('userData'), 'cloud-credential.bin');
}

export function encryptionAvailable(): boolean {
  return safeStorage.isEncryptionAvailable();
}

/** 存下 Key，加密失败（含系统不支持加密）返回 false，调用方必须据此提示用户。 */
export function saveCloudKey(apiKey: string): boolean {
  if (!apiKey) {
    clearCloudKey();
    return true;
  }
  if (!encryptionAvailable()) return false;
  try {
    const enc = safeStorage.encryptString(apiKey);
    const p = keyFile();
    const tmp = `${p}.tmp`;
    writeFileSync(tmp, enc);
    renameSync(tmp, p);
    return true;
  } catch (err) {
    console.error('[cloud-keys] 加密写入失败：', err);
    return false;
  }
}

/** 读不到 / 解不开都返回 null——上层据此把云端功能当"未配置"处理，不是报错崩溃。 */
export function loadCloudKey(): string | null {
  const p = keyFile();
  if (!existsSync(p)) return null;
  if (!encryptionAvailable()) return null;
  try {
    return safeStorage.decryptString(readFileSync(p));
  } catch (err) {
    console.error('[cloud-keys] 解密失败（可能是换了系统账号）：', err);
    return null;
  }
}

export function clearCloudKey(): void {
  const p = keyFile();
  if (existsSync(p)) {
    try {
      unlinkSync(p);
    } catch {
      /* 文件被占用之类，下次启动再清 */
    }
  }
}

export function hasCloudKey(): boolean {
  return existsSync(keyFile());
}

// ────────────────────────────────────────────────────────────
// 搜索引擎的 API Key（S3：Brave / Serper / Tavily / Exa）
//
// 和云端 Key 走**同一条加密路线**，但分开存成一个文件：
// 云端 Key 是单个字符串，引擎 Key 是一张表（可能同时配好几家）。
// 塞进同一个文件要么改格式（老用户的云端 Key 就读不出来了），
// 要么在里面再套一层编码 —— 两种都不如多一个文件干净。
//
// 🔴 **自建 SearXNG 的地址不算密钥**，它存在 settings.json 的
// `webEndpoints` 里。把一个本机地址当秘密加密存放，只会让用户
// 想改的时候找不到地方改。
// ────────────────────────────────────────────────────────────
function engineKeyFile(): string {
  return join(app.getPath('userData'), 'engine-credentials.bin');
}

export function saveEngineKeys(keys: Record<string, string>): boolean {
  const clean = Object.fromEntries(
    Object.entries(keys).filter(([, v]) => typeof v === 'string' && v.trim()),
  );
  const p = engineKeyFile();
  if (!Object.keys(clean).length) {
    if (existsSync(p)) {
      try {
        unlinkSync(p);
      } catch {
        /* 被占用就下次再清 */
      }
    }
    return true;
  }
  if (!encryptionAvailable()) return false;
  try {
    const enc = safeStorage.encryptString(JSON.stringify(clean));
    const tmp = `${p}.tmp`;
    writeFileSync(tmp, enc);
    renameSync(tmp, p);
    return true;
  } catch (err) {
    console.error('[engine-keys] 加密写入失败：', err);
    return false;
  }
}

export function loadEngineKeys(): Record<string, string> {
  const p = engineKeyFile();
  if (!existsSync(p) || !encryptionAvailable()) return {};
  try {
    const raw = safeStorage.decryptString(readFileSync(p));
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return {};
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .filter(([, v]) => typeof v === 'string')
        .map(([k, v]) => [k, v as string]),
    );
  } catch (err) {
    // 解不开就当没配（换了系统账号会走到这里），不让引擎起不来
    console.error('[engine-keys] 解密失败：', err);
    return {};
  }
}

/** 只回哪几家配了 Key，**不回 Key 本身** —— 设置页只需要知道有没有 */
export function engineKeyStatus(): Record<string, boolean> {
  return Object.fromEntries(Object.keys(loadEngineKeys()).map((k) => [k, true]));
}

// ── 整库加密口令 ────────────────────────────────────────────

function dbKeyFile(): string {
  return join(app.getPath('userData'), 'db.key');
}

/**
 * 存整库加密口令。
 *
 * 🔴 **它和云端 API Key 不是一个量级的东西。** API Key 丢了换一个就行；
 *    这个口令丢了，**整个资料库永远打不开** —— 没有后门、没有找回。
 *    所以界面上开启加密之前必须强制用户确认已经把口令记在别处，
 *    而这里只负责"这台机器上下次启动能自动解开"。
 *
 * 🔴 safeStorage 用的是当前**系统账号**的密钥。换了 Windows 账号、
 *    重装系统、把 userData 拷到另一台机器 —— 这份文件都解不开了。
 *    那时候用户必须手动重新输一次口令，所以口令本身必须由他自己保管。
 */
export function saveDbKey(pw: string): boolean {
  if (!pw) {
    clearDbKey();
    return true;
  }
  if (!encryptionAvailable()) return false;
  try {
    const enc = safeStorage.encryptString(pw);
    const p = dbKeyFile();
    const tmp = `${p}.tmp`;
    writeFileSync(tmp, enc);
    renameSync(tmp, p);
    return true;
  } catch (err) {
    console.error('[db-key] 加密写入失败：', err);
    return false;
  }
}

/** 读不到 / 解不开都返回 null —— 上层据此提示用户手动输一次，不是崩溃 */
export function loadDbKey(): string | null {
  const p = dbKeyFile();
  if (!existsSync(p)) return null;
  if (!encryptionAvailable()) return null;
  try {
    return safeStorage.decryptString(readFileSync(p));
  } catch (err) {
    console.error('[db-key] 解密失败（可能是换了系统账号）：', err);
    return null;
  }
}

export function hasDbKey(): boolean {
  return existsSync(dbKeyFile());
}

export function clearDbKey(): void {
  try {
    if (existsSync(dbKeyFile())) unlinkSync(dbKeyFile());
  } catch (err) {
    console.error('[db-key] 删除失败：', err);
  }
}
