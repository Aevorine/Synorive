/**
 * 托盘常驻
 * ============================================================
 * 用户选了「托盘常驻 + 开机自启」。托盘的意义不是多一个图标，
 * 而是让这三件事在窗口关掉之后仍然活着：
 *   E4 剪贴板哨兵 · 目录监听增量索引 · E8 订阅监控
 * 所以托盘菜单里要能一眼看到引擎在干什么，并且能单独开关它们。
 */

import { Menu, Tray, app, nativeImage } from 'electron';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { EngineProcessState, UpdateState } from '../shared/ipc-contract.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

const LIFECYCLE_LABEL: Record<EngineProcessState['lifecycle'], string> = {
  stopped: '引擎已停止',
  starting: '引擎启动中…',
  ready: '引擎就绪',
  degraded: '引擎降级运行',
  restarting: '引擎重启中…',
  failed: '引擎启动失败',
};

function trayIconPath(): string {
  const candidates = [
    join(__dirname, '../../resources/icons/tray-20.png'),
    join(process.resourcesPath, 'icons', 'tray-20.png'),
    join(__dirname, '../../resources/icons/icon-32.png'),
  ];
  return candidates.find((p) => existsSync(p)) ?? candidates[0]!;
}

export interface TrayCallbacks {
  onShow: () => void;
  /**
   * 左键单击托盘图标 = 显示 / 收起来回切。
   * 🔴 和 `onShow` 分开是有意的：菜单里的「打开主窗口」永远只能是"打开"，
   *    点了却把窗口收掉，那条菜单项就是在骗人。
   */
  onToggle: () => void;
  onSearch: () => void;
  onQuit: () => void;
  onRestartEngine: () => void;
  onToggleClipboard: (enabled: boolean) => void;
  onOpenSettings: () => void;
}

export class TrayController {
  private tray: Tray | null = null;
  private engineState: EngineProcessState | null = null;
  private clipboardEnabled = true;
  /**
   * U 组：托盘常驻是默认行为，也就是说**多数时候主窗口是关着的**。
   * 只在侧栏挂角标的话，这些时候自动检查到的更新一个人也看不到。
   */
  private updateState: UpdateState | null = null;
  /**
   * 菜单里「快速搜索…」右边显示的键。
   * 🔴 **必须是真正抢到的那个键，不能写死。** 默认注册的是 Alt+Space，
   *    抢不到才退到 Ctrl+Alt+Space —— 菜单里印一个用户按了没反应的键，
   *    比什么都不印更糟。抢不到任何键时就一个也不印。
   */
  private searchAccelerator: string | null = null;

  constructor(private readonly cb: TrayCallbacks) {}

  /** 由 applyHotkeys() 把**真实**注册结果推进来 */
  setSearchAccelerator(a: string | null): void {
    this.searchAccelerator = a;
    this.rebuild();
  }

  create(clipboardEnabled: boolean): void {
    this.clipboardEnabled = clipboardEnabled;

    // 🔴 重复调用只更新状态，不再建一个。设置里来回拨「托盘常驻」会走到这里，
    //    每次都 new 一个 Tray 的话，系统托盘里会攒下一排点不动的僵尸图标，
    //    而且旧的那些永远收不回来（原来的 this.tray 引用被覆盖了）。
    if (this.tray && !this.tray.isDestroyed()) {
      this.rebuild();
      return;
    }

    const img = nativeImage.createFromPath(trayIconPath());
    // Windows 托盘按 DPI 取 16/20/24，给一张 20 的让系统自己缩最稳
    this.tray = new Tray(img.isEmpty() ? nativeImage.createEmpty() : img);
    this.tray.setToolTip('Synorive');

    // 单击 = 来回切（显示 ↔ 收起）；双击 = 一定显示。
    // Windows 上双击会先发两次 click，两次切换正好抵消，末尾这条
    // 'double-click' 兜底把窗口留在"显示"上，否则双击的结果是窗口一闪就没了。
    this.tray.on('click', () => this.cb.onToggle());
    this.tray.on('double-click', () => this.cb.onShow());

    this.rebuild();
  }

  setEngineState(s: EngineProcessState): void {
    this.engineState = s;
    this.rebuild();
  }

  setClipboardEnabled(v: boolean): void {
    this.clipboardEnabled = v;
    this.rebuild();
  }

  setUpdateState(s: UpdateState): void {
    this.updateState = s;
    this.rebuild();
  }

  /**
   * 有没有值得提一句的更新。
   * 跳过的版本不算 —— 用户说了不要这一版，托盘里还挂着就是没在听。
   */
  private updateLine(): string | null {
    const u = this.updateState;
    if (!u) return null;
    /**
     * 🔴 **失败也要显示，而且要排在"跳过的版本"判断前面。**
     *
     * 托盘常驻是默认行为，多数时候主窗口是关着的 —— 那正是自动检查
     * （启动后 20 秒那一次）跑的时候。原来 error 这一支直接 return null，
     * 于是"检查更新失败"在托盘上**没有任何痕迹**：用户以为自己一直是最新版，
     * 实际上更新链路已经断了几个月。
     * 失败时 `latestVersion` 通常是 null，所以这一支必须在下面那个
     * `!u.latestVersion` 早退之前。
     */
    if (u.lifecycle === 'error') return '检查更新失败，点开看原因';
    if (!u.latestVersion) return null;
    if (u.latestVersion === u.skippedVersion) return null;
    if (u.lifecycle === 'available') return `有新版本 v${u.latestVersion}，点这里去下载`;
    if (u.lifecycle === 'downloaded') return `v${u.latestVersion} 已下载，点这里去安装`;
    return null;
  }

  private statusLine(): string {
    const s = this.engineState;
    if (!s) return '引擎状态未知';
    const base = LIFECYCLE_LABEL[s.lifecycle];
    if (s.lifecycle === 'ready' && s.bootMs) return `${base}（启动 ${(s.bootMs / 1000).toFixed(1)}s）`;
    if (s.lifecycle === 'failed' && s.lastError) return `${base}：${s.lastError}`;
    if (s.lifecycle === 'restarting') return `${base}第 ${s.restartCount} 次`;
    return base;
  }

  private rebuild(): void {
    if (!this.tray) return;

    const upd = this.updateLine();

    const menu = Menu.buildFromTemplate([
      { label: 'Synorive', enabled: false },
      { label: this.statusLine(), enabled: false },
      // 🔴 有更新时排在最上面、且是可点的 —— 点了直接开到设置页那一区。
      //    托盘菜单里放一条不可点的"有新版本"通知，等于告诉用户
      //    "有事发生了，自己去找"，那还不如不说
      ...(upd
        ? [
            { type: 'separator' as const },
            { label: upd, click: () => this.cb.onOpenSettings() },
          ]
        : []),
      { type: 'separator' },
      { label: '打开主窗口', click: () => this.cb.onShow() },
      {
        label: '快速搜索…',
        ...(this.searchAccelerator ? { accelerator: this.searchAccelerator } : {}),
        click: () => this.cb.onSearch(),
      },
      { type: 'separator' },
      {
        label: '剪贴板哨兵',
        type: 'checkbox',
        checked: this.clipboardEnabled,
        click: (item) => this.cb.onToggleClipboard(item.checked),
      },
      { label: '设置…', click: () => this.cb.onOpenSettings() },
      { label: '重启引擎', click: () => this.cb.onRestartEngine() },
      { type: 'separator' },
      { label: '退出 Synorive', click: () => this.cb.onQuit() },
    ]);

    this.tray.setContextMenu(menu);
    // 悬停提示也带上 —— 有人从不点开右键菜单
    this.tray.setToolTip(
      upd ? `Synorive —— ${this.statusLine()}｜${upd}` : `Synorive —— ${this.statusLine()}`,
    );
  }

  destroy(): void {
    this.tray?.destroy();
    this.tray = null;
  }
}

/**
 * 登录项在任务管理器「启动」页里显示的名字。
 *
 * 🔴 **不写这一项，Electron 会用默认的 `electron.app.Synorive`。**
 * 用户在任务管理器里看到的就是这么一行 —— 不像个应用名，像残留的开发痕迹，
 * 而「这是什么？要不要禁用？」的判断就是在那一页做的。
 */
const LOGIN_ITEM_NAME = 'Synorive';

/** Electron 没指定 name 时用的默认键名。改名之后要把它清掉，否则旧的赖在注册表里。 */
const LEGACY_LOGIN_ITEM_NAME = `electron.app.${LOGIN_ITEM_NAME}`;

/**
 * 这一趟该注册哪个可执行文件。
 *
 * 🔴 **便携版不能注册 `process.execPath`。** portable 目标是单文件自解压：
 * 运行时先把自己摊到 `%TEMP%\<随机>\` 再从那儿启动，于是 `process.execPath`
 * 指向一个**临时目录**。拿它去写开机自启，下次开机那个路径早就没了 ——
 * 表现是「设置里开关明明开着，开机就是不启动」，而注册表里躺着一条
 * 指向 Temp 的死路径，没人会往那儿看。
 *
 * electron-builder 的 portable 目标会把用户真正双击的那个 exe 路径放进
 * `PORTABLE_EXECUTABLE_FILE`，那个是稳定的，注册它才有意义。
 */
function launchTarget(): string {
  return process.env.PORTABLE_EXECUTABLE_FILE || process.execPath;
}

/** 开机自启：Windows 走登录项，不手写注册表 Run 键（更规范、卸载时系统自己清） */
export function setLaunchAtLogin(enabled: boolean): void {
  // 早期版本用的是 Electron 默认键名，升级上来的用户注册表里还留着那一条。
  // 不清掉的话会变成两条登录项：旧的那条指向老路径，可能起第二个实例。
  app.setLoginItemSettings({ openAtLogin: false, name: LEGACY_LOGIN_ITEM_NAME });

  app.setLoginItemSettings({
    openAtLogin: enabled,
    name: LOGIN_ITEM_NAME,
    path: launchTarget(),
    // 自启时静默进托盘，不弹窗口打扰
    args: enabled ? ['--tray-only'] : [],
  });
}

/**
 * 界面上那个开关该显示"开"还是"关"。
 *
 * 🔴 **只看 `openAtLogin` 不够 —— 还要核对它指向的是不是这一个 exe。**
 * 用户把便携版挪了个位置、或者装过又重装到别的盘之后，注册表里那条路径
 * 就成了死的：系统照样报 `openAtLogin: true`，界面显示"已开启"，
 * 而实际开机什么也不会发生。这种"开关说开着、功能是死的"比直接显示关闭
 * 更难查，因为用户不会去怀疑一个看起来正常的开关。
 * 路径对不上就当没开 —— 用户重新拨一次开关，顺手就把路径修正了。
 */
export function getLaunchAtLogin(): boolean {
  const got = app.getLoginItemSettings({
    path: launchTarget(),
    args: ['--tray-only'],
  });
  if (!got.openAtLogin) return false;
  const registered = (got.launchItems ?? []).find((i) => i.name === LOGIN_ITEM_NAME);
  // 拿不到 launchItems（非 Windows）时就按系统说的算
  if (!registered) return true;
  return registered.path?.toLowerCase() === launchTarget().toLowerCase();
}
