/**
 * 随手研究浮窗 —— N7
 * ============================================================
 * 复制一段话 → 屏幕角落浮出一个小窗，给三条最相关的（本地库 + 网上）。
 *
 * 剪贴板哨兵早就端到端跑通了，但它一直只是"攒着"—— 攒下来的东西
 * 除非你主动去托盘翻，否则一辈子不会被看到。这一层是把它用起来。
 *
 * 🔴 **三条硬约束，每一条都是这个功能能不能用的分水岭：**
 *
 * ① **绝不抢焦点。** 你复制东西十有八九是为了马上粘贴到别处 ——
 *    弹个窗把焦点抢走，等于直接毁掉你正在做的那件事。
 *    所以 `focusable: false` + `showInactive()`，键盘输入永远不会被它接走。
 *    这一条做不到的话，这个功能就是纯粹的骚扰。
 *
 * ② **默认关。** 每次复制都弹窗是一种非常具体的敌意。
 *    用户得先明确说"我要这个"。
 *
 * ③ **默认只查本地库。** 本地检索几十毫秒、不出网、不花钱；
 *    联网要几秒还会把"我在查什么"发出去。要联网得再单独开一个开关 ——
 *    和隐私围栏里那条"联网搜索和云端推理是两个开关"同一个道理。
 *
 * 位置放右下角而不是跟随鼠标：跟随鼠标意味着它会出现在你正在操作的地方，
 * 挡住你刚复制完要粘贴的那个输入框。
 */

import { BrowserWindow, screen, shell } from 'electron';
import { join } from 'node:path';

const isDev = !!process.env.ELECTRON_RENDERER_URL;

const WIDTH = 380;
const HEIGHT = 260;
/** 离屏幕边缘留多少。太贴边在任务栏上会被挡 */
const MARGIN = 16;
/** 自动消失。太短来不及看，太长会一直挂在那儿碍事 */
const AUTO_HIDE_MS = 12_000;

export interface PeekOptions {
  /** 联网搜索总闸（E12）。关掉时浮窗只查本地库 */
  allowNetwork: boolean;
  /** 用户是否额外允许浮窗联网。默认 false —— 见文件头约束③ */
  peekWeb: boolean;
}

export class PeekWindow {
  private win: BrowserWindow | null = null;
  private hideTimer: NodeJS.Timeout | null = null;
  private opts: PeekOptions = { allowNetwork: true, peekWeb: false };

  setOptions(o: Partial<PeekOptions>): void {
    this.opts = { ...this.opts, ...o };
  }

  /** 复制到一段值得查的文字时调这个 */
  show(query: string): void {
    const q = (query || '').trim();
    // 太短查不出东西，太长多半是整篇文章被复制（那不是"想查一下"）
    if (q.length < 4 || q.length > 400) return;

    const win = this.ensure();
    const payload = {
      query: q,
      web: this.opts.allowNetwork && this.opts.peekWeb,
    };
    // 用 hash 传参而不是 IPC：浮窗可能还在加载中，IPC 会丢；
    // hash 变化在页面就绪后仍然读得到
    const send = () => win.webContents.send('peek:query', payload);
    if (win.webContents.isLoading()) {
      win.webContents.once('did-finish-load', send);
    } else {
      send();
    }

    this.place(win);
    // 🔴 showInactive 而不是 show —— 后者会抢焦点
    win.showInactive();

    if (this.hideTimer) clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => this.hide(), AUTO_HIDE_MS);
  }

  hide(): void {
    if (this.hideTimer) {
      clearTimeout(this.hideTimer);
      this.hideTimer = null;
    }
    this.win?.hide();
  }

  destroy(): void {
    this.hide();
    if (this.win && !this.win.isDestroyed()) this.win.destroy();
    this.win = null;
  }

  private place(win: BrowserWindow): void {
    // 放在**鼠标所在那块屏**的右下角。多显示器时放主屏是错的 ——
    // 用户可能正在副屏上干活，浮窗弹到另一块屏上等于没弹
    const cursor = screen.getCursorScreenPoint();
    const display = screen.getDisplayNearestPoint(cursor);
    const wa = display.workArea;
    win.setBounds({
      x: wa.x + wa.width - WIDTH - MARGIN,
      y: wa.y + wa.height - HEIGHT - MARGIN,
      width: WIDTH,
      height: HEIGHT,
    });
  }

  private ensure(): BrowserWindow {
    if (this.win && !this.win.isDestroyed()) return this.win;

    const win = new BrowserWindow({
      width: WIDTH,
      height: HEIGHT,
      show: false,
      frame: false,
      resizable: false,
      minimizable: false,
      maximizable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      // 🔴 这一行是这个功能的地基：不可聚焦 = 永远不会把键盘从你手里抢走
      focusable: false,
      transparent: false,
      // allow-hardcoded：BrowserWindow 只认字面色值不认 CSS 变量，
      // 和 window.ts 里那两个值必须保持一致（改令牌时两处都要改）
      backgroundColor: '#FAF9F6',
      webPreferences: {
        preload: join(__dirname, '../preload/index.cjs'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false,
        spellcheck: false,
        backgroundThrottling: false,
      },
    });

    // 浮在全屏应用之上也要能看见 —— 否则你在看视频时复制一句台词，
    // 它会弹在视频后面，等于没弹
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

    win.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
      return { action: 'deny' };
    });

    const devUrl = process.env.ELECTRON_RENDERER_URL;
    if (isDev && devUrl) {
      void win.loadURL(`${devUrl}#peek`);
    } else {
      // 打包后是 file://，这里必须给绝对文件路径 + hash
      void win.loadFile(join(__dirname, '../renderer/index.html'), { hash: 'peek' });
    }

    this.win = win;
    return win;
  }
}
