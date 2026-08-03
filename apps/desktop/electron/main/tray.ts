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

  constructor(private readonly cb: TrayCallbacks) {}

  create(clipboardEnabled: boolean): void {
    this.clipboardEnabled = clipboardEnabled;

    const img = nativeImage.createFromPath(trayIconPath());
    // Windows 托盘按 DPI 取 16/20/24，给一张 20 的让系统自己缩最稳
    this.tray = new Tray(img.isEmpty() ? nativeImage.createEmpty() : img);
    this.tray.setToolTip('Synorive');

    this.tray.on('click', () => this.cb.onShow());
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
    if (!u || !u.latestVersion) return null;
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
      { label: '快速搜索…', accelerator: 'CommandOrControl+Alt+Space', click: () => this.cb.onSearch() },
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

/** 开机自启：Windows 走登录项，不写注册表 Run 键（更规范、卸载时系统自己清） */
export function setLaunchAtLogin(enabled: boolean): void {
  app.setLoginItemSettings({
    openAtLogin: enabled,
    // 自启时静默进托盘，不弹窗口打扰
    args: enabled ? ['--tray-only'] : [],
  });
}

export function getLaunchAtLogin(): boolean {
  return app.getLoginItemSettings().openAtLogin;
}
