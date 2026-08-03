/**
 * F7 全局唤起 ＋ A4 截图直搜
 * ====================================================================
 * 两个都是「不用先找到窗口就能用」的入口，所以放一起。
 *
 * **F7 Alt+空格**：像 Everything / Alfred 那样，任何时候按一下就弹搜索条。
 * 这是把一个检索工具从"我得先切到那个窗口"变成"随手就能查"的分界线。
 *
 * 🔴 **注册失败必须报出来，不能静默。** `globalShortcut.register()`
 * 在快捷键被别的软件占了时返回 `false` 而**不抛异常** ——
 * 不检查返回值的话，症状是用户按了没反应，而且永远查不出为什么
 * （日志里干干净净，代码看起来也对）。这是静默失败的典型形态。
 *
 * 🔴 **Alt+空格在 Windows 上是系统窗口菜单的默认键。** 抢得到，
 * 但抢了之后用户在**所有**窗口上都按不出那个菜单了。所以：
 *   ① 默认注册的是 `Alt+Space`，用户可以在设置里改
 *   ② 抢注失败时**自动退到 `Ctrl+Alt+Space`**，并把这件事告诉界面
 *   ③ 两个都失败就如实报告，不假装成功
 *
 * **A4 截图直搜**：按一下 → 拉起系统截图 → 截完的图自动进投喂条。
 * 现在的路径是"截图 → 存文件 → 找到文件 → 拖进来"，四步里有三步
 * 是纯粹的搬运。Windows 的 `ms-screenclip:` 协议截完直接进剪贴板，
 * 而剪贴板哨兵本来就在盯着 —— 两边接上就少了三步。
 */

import { globalShortcut, shell } from 'electron';

export interface HotkeyBinding {
  /** 逻辑动作名，界面据此显示"这个键干什么" */
  id: 'focus-search' | 'screenshot-search' | 'toggle-window';
  /** 想要的键 */
  accelerator: string;
  /** 抢不到时退而求其次的键。**空数组表示不退**，抢不到就是没有 */
  fallbacks: string[];
  label: string;
  run: () => void;
}

export interface HotkeyReport {
  id: string;
  label: string;
  /** 最终真正生效的键。`null` = 一个都没抢到 */
  active: string | null;
  /** 用的是不是备选键 —— 界面上要提示用户"你想要的那个被占了" */
  usedFallback: boolean;
  tried: string[];
}

/**
 * 逐个尝试注册，返回**真实结果**。
 *
 * `register()` 返回 false 就是没抢到；这里不吞、不重试、不假装。
 * `isRegistered()` 也查一遍 —— 某些平台上 register 返回 true
 * 但实际没生效过，两个都问一次能少掉一类"看着注册成功了却没反应"。
 */
function tryRegister(keys: string[], run: () => void): { active: string | null; tried: string[] } {
  const tried: string[] = [];
  for (const key of keys) {
    tried.push(key);
    if (globalShortcut.isRegistered(key)) continue;
    let ok = false;
    try {
      ok = globalShortcut.register(key, run);
    } catch {
      ok = false;
    }
    if (ok && globalShortcut.isRegistered(key)) {
      return { active: key, tried };
    }
    // 抢了一半的要还回去，否则会占着一个自己都用不了的键
    try {
      globalShortcut.unregister(key);
    } catch {
      /* 本来就没注册上，忽略 */
    }
  }
  return { active: null, tried };
}

export function registerHotkeys(bindings: HotkeyBinding[]): HotkeyReport[] {
  return bindings.map((b) => {
    const { active, tried } = tryRegister([b.accelerator, ...b.fallbacks], b.run);
    return {
      id: b.id,
      label: b.label,
      active,
      usedFallback: active != null && active !== b.accelerator,
      tried,
    };
  });
}

export function unregisterAllHotkeys(): void {
  globalShortcut.unregisterAll();
}

/**
 * A4 —— 拉起系统截图工具。
 *
 * Windows 走 `ms-screenclip:`（Win+Shift+S 背后就是它）；
 * macOS 走 `screencapture -i -c`（截到剪贴板）；
 * Linux 没有统一的，返回 false 让调用方告诉用户"这台机器上用不了"。
 *
 * 🔴 **截完不主动去读剪贴板**。剪贴板哨兵已经在轮询了，
 * 这里再读一次会和它抢，还可能读到上一张（截图工具是异步的，
 * 用户可能几秒后才框完）。让哨兵按自己的节奏发现新图，
 * 慢半秒但绝不会拿到错的那张。
 */
export async function launchScreenCapture(): Promise<{ ok: boolean; note: string }> {
  if (process.platform === 'win32') {
    try {
      await shell.openExternal('ms-screenclip:');
      return { ok: true, note: '框选完就会自动进投喂条' };
    } catch (e) {
      return { ok: false, note: `拉不起系统截图：${(e as Error).message}` };
    }
  }
  if (process.platform === 'darwin') {
    try {
      const { spawn } = await import('node:child_process');
      spawn('screencapture', ['-i', '-c'], { detached: true, stdio: 'ignore' }).unref();
      return { ok: true, note: '框选完就会自动进投喂条' };
    } catch (e) {
      return { ok: false, note: `拉不起系统截图：${(e as Error).message}` };
    }
  }
  return {
    ok: false,
    note: 'Linux 上没有统一的系统截图接口。自己用惯用的工具截到剪贴板，一样会被接住',
  };
}
