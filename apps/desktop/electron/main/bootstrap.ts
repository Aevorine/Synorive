/**
 * 首次运行自举：找到一个能用的 Python，建 venv，装引擎
 * ============================================================
 * 「引擎总是启动失败」这件事有两个根因，这个文件治的是第二个。
 *
 *   ① dev 侧：解释器路径写死了上溯层数，换一种启动方式就断
 *      → 已在 `engine.ts` 的 `pythonCandidates()` 修掉（多基准点多层上溯）
 *   ② **打包侧：那台机器上压根没有 venv，也没人负责建**
 *      → 就是这个文件
 *
 * 原来的设计是"引擎按需装，由依赖医生负责"。那对**依赖和模型**是对的
 * （加起来 800MB+，不该塞进安装包）。但依赖医生要用 pip 装东西，
 * 而 pip 得有一个 Python 来跑 —— **它自己就依赖一个还不存在的东西**。
 * 这个先有鸡还是先有蛋的缺口，导致打包版从来没有真正跑起来过。
 *
 * 所以这里做的是最前面那一步：**在系统里找一个够格的 Python**，
 * 用它建一个专属 venv，再把引擎装进去。之后依赖医生才有地方施展。
 *
 * 🔴 **这属于会改用户机器的操作**（建目录、装包），所以：
 *   · 绝不自动触发，必须用户在引导页上点了才跑
 *   · 每一步都往外报进度，不让人对着一个转圈等三分钟
 *   · 装到 `userData/engine-venv`，卸载应用时跟着走，不污染系统 Python
 */

import { app } from 'electron';
import { execFile, execFileSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

/** 引擎要求的最低 Python 版本。3.10 及以下没有 `datetime.UTC` 等 API */
const MIN_MINOR = 11;
/** 优先用哪个小版本。3.13 是当前实测跑通的那个 */
const PREFERRED = ['3.13', '3.12', '3.11'];

export interface FoundPython {
  path: string;
  version: string;
  /** 怎么找到的，报给用户看 —— 让他知道用的是哪个解释器 */
  via: string;
}

export interface BootstrapProgress {
  step: 'find' | 'venv' | 'pip' | 'verify' | 'done' | 'error';
  message: string;
  /** 0~1，只在 pip 阶段有意义（那一步最长） */
  ratio?: number;
}

function probeVersion(exe: string, args: string[] = []): string | null {
  try {
    const out = execFileSync(exe, [...args, '-c', 'import sys;print("%d.%d"%sys.version_info[:2])'], {
      encoding: 'utf8',
      timeout: 8000,
      windowsHide: true,
    }).trim();
    return /^\d+\.\d+$/.test(out) ? out : null;
  } catch {
    return null;
  }
}

function ok(version: string | null): boolean {
  if (!version) return false;
  const [maj, min] = version.split('.').map(Number);
  return (maj ?? 0) === 3 && (min ?? 0) >= MIN_MINOR;
}

/**
 * 随安装包分发的 Python 运行时在哪（`scripts/bundle-python.mjs` 产出，
 * 由 electron-builder 的 extraResources 搬到 `resources/pyruntime`）。
 *
 * 🔴 **这个函数是补一个真实的自相矛盾**：`electron-builder.yml` 已经把
 * 一份完整运行时（解释器 + 全部核心依赖）打进了安装包，README 上也写着
 * "No Python installation required"，可这个文件的 `findSystemPython()`
 * **从头到尾没看过它一眼** —— 一旦引擎因为别的原因没起来（端口占用、
 * 模型没下、配置坏了），引导页就会对一个装了完整运行时的用户说
 * "这台机器上没找到 Python，去 python.org 装一个 3.13"。
 * 那是一句**方向完全错的**指引：他照做也解决不了问题，
 * 还会以为这个应用的"免安装 Python"是假的。
 */
export function bundledRuntimePython(): string | null {
  if (!process.resourcesPath) return null;
  const exe = join(
    process.resourcesPath,
    'pyruntime',
    process.platform === 'win32' ? 'python.exe' : join('bin', 'python3'),
  );
  return existsSync(exe) ? exe : null;
}

/**
 * 随包运行时是不是**真的能用**（不只是文件在）。
 *
 * 判据是能不能 `import synorive` —— 这正是 `bundle-python.mjs` 结尾那道
 * 自检的同一条判据。文件在不在是最弱的信号：`._pth` 写错、
 * 依赖漏装、杀毒软件隔离了某个 .pyd，都会让一个看起来完整的目录跑不起来。
 */
export function bundledRuntimeUsable(): { ok: boolean; python: string | null; detail: string } {
  const exe = bundledRuntimePython();
  if (!exe) return { ok: false, python: null, detail: '这个版本里没有随包运行时（多半是从源码跑的）' };
  try {
    const v = execFileSync(exe, ['-c', 'import synorive;print(synorive.__version__)'], {
      encoding: 'utf8',
      timeout: 30_000,
      windowsHide: true,
    }).trim();
    return { ok: true, python: exe, detail: `随包运行时可用，引擎版本 ${v}` };
  } catch (e) {
    return {
      ok: false,
      python: exe,
      detail:
        `随包运行时在（${exe}）但 import synorive 失败：${(e as Error).message}。` +
        '常见原因：杀毒软件把 .pyd 隔离了、安装目录被清理工具动过、或者安装包本身打漏了依赖',
    };
  }
}

/**
 * 找一个够格的 Python。
 *
 * 顺序是**从最可靠到最不可靠**：
 *   ⓪ **随安装包分发的运行时** —— 装完即用，不联网不 pip，装机版的主路径
 *   ① Windows 的 py launcher 指定版本 —— 它读注册表，是唯一权威的答案
 *   ② PATH 里的 python / python3 —— 常见但很可能是个老版本
 *      （这台机器上 PATH 里就是 3.9.4，正是踩过的坑）
 *   ③ 常见安装目录扫描 —— 兜底，覆盖"装了但没进 PATH"的情况
 */
export function findSystemPython(): FoundPython | null {
  const win = process.platform === 'win32';

  // ⓪ 随包运行时。**必须排第一** —— 它是我们自己备的、版本确定、依赖齐全，
  //    比用户机器上任何一个来路不明的解释器都可靠
  const bundled = bundledRuntimePython();
  if (bundled) {
    const ver = probeVersion(bundled);
    if (ok(ver)) return { path: bundled, version: ver!, via: '随安装包分发的运行时' };
  }

  // ① py launcher，按偏好版本逐个问
  if (win) {
    for (const v of PREFERRED) {
      const ver = probeVersion('py', [`-${v}`]);
      if (ok(ver)) {
        try {
          const real = execFileSync('py', [`-${v}`, '-c', 'import sys;print(sys.executable)'], {
            encoding: 'utf8',
            timeout: 8000,
            windowsHide: true,
          }).trim();
          if (real && existsSync(real)) {
            return { path: real, version: ver!, via: `py launcher -${v}` };
          }
        } catch {
          /* 拿不到真实路径就跳过，下面还有别的路 */
        }
      }
    }
  }

  // ② PATH
  for (const name of win ? ['python.exe', 'python3.exe'] : ['python3', 'python']) {
    const ver = probeVersion(name);
    if (ok(ver)) return { path: name, version: ver!, via: '系统 PATH' };
  }

  // ③ 常见安装位置。**只扫固定几个**，不做全盘搜索 ——
  //    全盘搜索要几十秒，而用户此刻正盯着一个报错页面等答案
  const roots = win
    ? [
        'C:\\Python313', 'C:\\Python312', 'C:\\Python311',
        'D:\\APPS\\Python\\Python3137', 'D:\\APPS\\Python\\Python313',
        join(app.getPath('home'), 'AppData', 'Local', 'Programs', 'Python', 'Python313'),
        join(app.getPath('home'), 'AppData', 'Local', 'Programs', 'Python', 'Python312'),
        join(app.getPath('home'), 'AppData', 'Local', 'Programs', 'Python', 'Python311'),
      ]
    : ['/usr/local/bin', '/usr/bin', '/opt/homebrew/bin'];
  for (const r of roots) {
    const exe = win ? join(r, 'python.exe') : join(r, 'python3');
    if (!existsSync(exe)) continue;
    const ver = probeVersion(exe);
    if (ok(ver)) return { path: exe, version: ver!, via: `安装目录 ${r}` };
  }

  return null;
}

/** 自举出来的 venv 放哪。跟着 userData 走 —— 卸载应用时一起消失，不污染系统 */
export function bootstrapVenvPath(): string {
  return join(app.getPath('userData'), 'engine-venv');
}

export function bootstrapPythonPath(): string {
  const base = bootstrapVenvPath();
  return process.platform === 'win32'
    ? join(base, 'Scripts', 'python.exe')
    : join(base, 'bin', 'python');
}

/** 引擎源码在哪。打包后随包分发（extraResources），开发时在仓库里 */
export function engineSourceDir(): string {
  const packed = join(process.resourcesPath ?? '', 'engine');
  if (existsSync(join(packed, 'pyproject.toml'))) return packed;
  // 开发时：从 appPath 往上找
  const { resolve } = require('node:path') as typeof import('node:path');
  for (let up = 0; up <= 4; up++) {
    const c = resolve(app.getAppPath(), ...Array<string>(up).fill('..'), 'engine');
    if (existsSync(join(c, 'pyproject.toml'))) return c;
  }
  return packed;
}

function run(
  exe: string,
  args: string[],
  onLine: (s: string) => void,
  timeoutMs: number,
): Promise<void> {
  return new Promise((res, rej) => {
    const child = execFile(exe, args, { timeout: timeoutMs, windowsHide: true, maxBuffer: 1 << 24 });
    child.stdout?.on('data', (d: Buffer | string) => onLine(String(d)));
    child.stderr?.on('data', (d: Buffer | string) => onLine(String(d)));
    child.once('error', rej);
    child.once('close', (code) =>
      code === 0 ? res() : rej(new Error(`退出码 ${code}`)),
    );
  });
}

/**
 * 跑一次完整自举。**每一步都报进度** —— 装依赖要一两分钟，
 * 期间只有一个转圈图标的话，用户分不清"在装"和"卡死了"。
 * 这和深挖那个实时进度是同一条理由。
 */
export async function bootstrapEngine(
  onProgress: (p: BootstrapProgress) => void,
): Promise<{ ok: true; python: string } | { ok: false; error: string }> {
  // 🔴 **先看随包运行时，能用就直接返回，一步都不用做。**
  //
  // 装机版的正常情况就是这一条：解释器和依赖在安装包里已经备好了。
  // 原来这里上来就去系统里翻 Python，于是装机版用户在引擎因为**别的原因**
  // 没起来时，会被引导去装一个他根本不需要的 Python ——
  // 忙活半天，真正的故障（端口被占、模型没下、目录被杀软动过）一点没动。
  onProgress({ step: 'find', message: '先看随安装包分发的运行时能不能用…' });
  const bundled = bundledRuntimeUsable();
  if (bundled.ok && bundled.python) {
    onProgress({ step: 'done', message: bundled.detail });
    return { ok: true, python: bundled.python };
  }
  if (bundled.python) {
    // 运行时在但坏了。**这句要说出来** —— 它和"这台机器没有 Python"
    // 是完全不同的故障，修法也完全不同
    onProgress({ step: 'find', message: bundled.detail });
  }

  onProgress({ step: 'find', message: '正在找一个能用的 Python…' });
  const found = findSystemPython();
  if (!found) {
    return {
      ok: false,
      error:
        `这台机器上没找到 Python ${MIN_MINOR}+，随安装包分发的运行时也没能用起来。\n\n` +
        `随包运行时的情况：${bundled.detail}\n\n` +
        '两条路，选一条：\n' +
        '① 如果你装的是安装包版：多半是杀毒软件隔离了运行时里的文件，' +
        '或者安装目录被清理工具动过 —— 把 Synorive 加进杀软白名单后重装一次最省事。\n' +
        '② 去 python.org 装一个 3.13（安装时勾上 "Add python.exe to PATH"），' +
        '装完回来点重试；或者设环境变量 SYNORIVE_PYTHON 指向你已有的解释器。',
    };
  }
  onProgress({
    step: 'find',
    message: `找到 Python ${found.version}（${found.via}）：${found.path}`,
  });

  const venv = bootstrapVenvPath();
  const py = bootstrapPythonPath();
  const src = engineSourceDir();

  if (!existsSync(join(src, 'pyproject.toml'))) {
    return {
      ok: false,
      error:
        `找不到引擎源码（找的是 ${src}）。\n` +
        '如果你是从源码跑的，确认仓库里有 engine/ 目录；' +
        '如果是装的安装包，说明这个包打漏了引擎源码，属于打包问题。',
    };
  }

  try {
    if (!existsSync(py)) {
      onProgress({ step: 'venv', message: '正在建一个专属的 Python 环境…' });
      mkdirSync(venv, { recursive: true });
      // --upgrade-deps 会顺带把 pip 升到新版，省掉后面一次"pip 版本太老"的失败
      await run(found.path, ['-m', 'venv', '--upgrade-deps', venv], () => {}, 180_000);
    } else {
      onProgress({ step: 'venv', message: '已有专属环境，跳过创建' });
    }

    onProgress({
      step: 'pip',
      message: '正在安装引擎依赖（第一次要一两分钟，之后就不用了）…',
      ratio: 0,
    });
    let seen = 0;
    await run(
      py,
      ['-m', 'pip', 'install', '-e', src, '--disable-pip-version-check'],
      (chunk) => {
        // pip 不给百分比，只能按"收集/下载/安装"这些关键词粗略推进。
        // **给一个会动的东西比给一个准确的百分比重要** ——
        // 用户要的是"它还活着"这个信息，不是精确进度
        for (const kw of ['Collecting', 'Downloading', 'Installing', 'Successfully']) {
          if (chunk.includes(kw)) seen++;
        }
        onProgress({
          step: 'pip',
          message: chunk.trim().split('\n').pop()?.slice(0, 120) ?? '安装中…',
          ratio: Math.min(0.95, seen / 60),
        });
      },
      900_000,
    );

    onProgress({ step: 'verify', message: '正在验证装好了没…' });
    // 🔴 装完必须真 import 一次。pip 报成功不等于能用 ——
    // 这条是台账里反复出现的教训：「装完自检」和「装完了」是两回事
    execFileSync(py, ['-c', 'import synorive; print(synorive.__version__)'], {
      timeout: 60_000,
      windowsHide: true,
      stdio: 'ignore',
    });

    onProgress({ step: 'done', message: '装好了，正在启动引擎…' });
    return { ok: true, python: py };
  } catch (e) {
    const msg = (e as Error).message;
    return {
      ok: false,
      error:
        `自动配置失败：${msg}\n\n` +
        '可以手动来一遍（在仓库根目录）：\n' +
        `  "${found.path}" -m venv engine/.venv\n` +
        '  engine/.venv/Scripts/python.exe -m pip install -e engine',
    };
  }
}
