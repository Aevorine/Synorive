/**
 * 主窗口：创建 + 尺寸位置记忆 + 关闭到托盘
 */

import { BrowserWindow, app, nativeTheme, screen, shell } from 'electron';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const isDev = !app.isPackaged;

interface Bounds {
  x?: number;
  y?: number;
  width: number;
  height: number;
  isMaximized: boolean;
}

const DEFAULT_BOUNDS: Bounds = { width: 1360, height: 860, isMaximized: false };
/** 低于这个尺寸布局会挤坏：侧栏 208 + 内容最小 560 + 详情面板 380 */
const MIN_WIDTH = 1024;
const MIN_HEIGHT = 640;

function boundsPath(): string {
  return join(app.getPath('userData'), 'window-bounds.json');
}

function loadBounds(): Bounds {
  const p = boundsPath();
  if (!existsSync(p)) return DEFAULT_BOUNDS;
  try {
    const b = JSON.parse(readFileSync(p, 'utf8')) as Bounds;
    // 上次用的显示器可能已经拔掉了 —— 窗口会跑到屏幕外面，必须校验
    if (typeof b.x === 'number' && typeof b.y === 'number') {
      const visible = screen.getAllDisplays().some((d) => {
        const wa = d.workArea;
        return (
          b.x! < wa.x + wa.width && b.x! + b.width > wa.x && b.y! < wa.y + wa.height && b.y! + b.height > wa.y
        );
      });
      if (!visible) {
        delete b.x;
        delete b.y;
      }
    }
    return { ...DEFAULT_BOUNDS, ...b };
  } catch {
    return DEFAULT_BOUNDS;
  }
}

function saveBounds(win: BrowserWindow): void {
  if (win.isDestroyed()) return;
  const isMaximized = win.isMaximized();
  // 最大化时 getBounds 返回的是最大化后的尺寸，存了下次还原不回去
  const b = isMaximized ? win.getNormalBounds() : win.getBounds();
  const data: Bounds = { x: b.x, y: b.y, width: b.width, height: b.height, isMaximized };
  try {
    const p = boundsPath();
    mkdirSync(dirname(p), { recursive: true });
    const tmp = `${p}.tmp`;
    writeFileSync(tmp, JSON.stringify(data), 'utf8');
    renameSync(tmp, p);
  } catch {
    /* 记不住位置不是致命问题，不要因此弹错 */
  }
}

export interface CreateWindowOptions {
  /** 关窗口时是收进托盘还是真退出 */
  runInTray: boolean;
  onCloseToTray?: () => void;
}

export function createMainWindow(opts: CreateWindowOptions): BrowserWindow {
  const b = loadBounds();

  const win = new BrowserWindow({
    x: b.x,
    y: b.y,
    width: b.width,
    height: b.height,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    show: false,
    // 自绘标题栏：系统标题栏配不上这套宋体+衬线的视觉
    titleBarStyle: 'hidden',
    titleBarOverlay: false,
    frame: false,
    // allow-hardcoded：BrowserWindow 只认字面色值，不认 CSS 变量。
    // 这两个值必须和 design-tokens 里的 palette.dark.bg / palette.light.bg 保持一致，
    // 改令牌时别忘了改这里 —— 不一致的症状是启动瞬间闪一下别的颜色。
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#1A1E22' : '#FAF9F6',
    // 启动瞬间的白闪很掉档次，用主题色垫底
    webPreferences: {
      preload: join(__dirname, '../preload/index.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
    },
  });

  if (b.isMaximized) win.maximize();

  win.once('ready-to-show', () => {
    win.show();
  });

  let saveTimer: NodeJS.Timeout | null = null;
  const scheduleSave = () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveBounds(win), 400);
  };
  win.on('resize', scheduleSave);
  win.on('move', scheduleSave);

  // 外链一律用系统浏览器打开，绝不在应用内导航到外部站点
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    const devServer = process.env.ELECTRON_RENDERER_URL;
    if (devServer && url.startsWith(devServer)) return;
    e.preventDefault();
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
  });

  win.on('close', (e) => {
    saveBounds(win);
    if (opts.runInTray && !(app as { isQuitting?: boolean }).isQuitting) {
      e.preventDefault();
      win.hide();
      opts.onCloseToTray?.();
    }
  });

  const devUrl = process.env.ELECTRON_RENDERER_URL;
  if (isDev && devUrl) {
    void win.loadURL(devUrl);
    win.webContents.openDevTools({ mode: 'detach' });
  } else {
    // 注意：这里必须是相对路径解析出来的绝对文件路径。
    // 渲染层里引用资源也绝不能写 / 开头的绝对路径 —— 打包后页面是 file://，
    // /x/y.js 会解析到盘根而不是应用目录，开发能跑、装好的应用永远拿不到。
    void win.loadFile(join(__dirname, '../renderer/index.html'));
  }

  return win;
}

export { MIN_WIDTH, MIN_HEIGHT };
