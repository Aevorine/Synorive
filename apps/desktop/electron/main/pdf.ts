import { BrowserWindow, dialog, shell } from 'electron';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/**
 * E5 —— 导出**引用可点**的 PDF
 * ============================================================
 * 为什么必须放主进程，而不是在渲染层调 `window.print()`：
 *
 * 🔴 `window.print()` 走的是**系统打印驱动**。走到那一层时页面已经被
 * 光栅化成打印页了，`<a href="#s3">` 只剩下字形，PDF 里点它毫无反应。
 * 而 `webContents.printToPDF()` 是 Chromium 自己的 PDF 后端，
 * 它会把 `<a>` 写成 PDF 的 link annotation —— **内部锚点和外部网址都保留**。
 * 这是 E5「可点引用锚点」唯一能落地的路径，不是两种写法的口味之争。
 *
 * 🔴 **必须落成临时文件再 `loadFile`，不能用 `data:` URL。**
 * `data:` URL 的文档没有正常的 base URL，Chromium 对 `#anchor` 的解析
 * 在这种文档里不稳；而且长 HTML 塞进 URL 会被长度限制截断 ——
 * 截断的表现是"PDF 出来了但后半篇没了"，不报错。
 *
 * 🔴 **打印窗口必须 `show: false` 且 `offscreen` 不开。**
 * 开了离屏渲染反而拿不到完整分页；不 `show:false` 会闪一个空白窗口出来。
 */

export interface PdfResult {
  ok: boolean;
  /** 存到哪了。用户取消保存对话框时为空 */
  path?: string;
  /** 失败原因。**照实说，不吞** —— 静默失败的表现是"点了没反应" */
  error?: string;
}

export async function exportPdf(html: string, defaultName: string): Promise<PdfResult> {
  if (!html || !html.trim()) {
    return { ok: false, error: '没有可导出的内容（引擎返回的 HTML 是空的）' };
  }

  const save = await dialog.showSaveDialog({
    title: '导出 PDF',
    defaultPath: sanitize(defaultName) + '.pdf',
    filters: [{ name: 'PDF', extensions: ['pdf'] }],
  });
  if (save.canceled || !save.filePath) return { ok: false };

  let dir: string | null = null;
  let printer: BrowserWindow | null = null;
  try {
    // 临时目录而不是单个临时文件：将来如果要嵌本地图片，
    // 相对路径能在同一个目录里解析得到
    dir = await mkdtemp(join(tmpdir(), 'synorive-pdf-'));
    const srcPath = join(dir, 'doc.html');
    await writeFile(srcPath, withPrintCss(html), 'utf8');

    printer = new BrowserWindow({
      show: false,
      webPreferences: {
        // 打印的是我们自己引擎生成的 HTML，但仍然**一律不给 Node**：
        // 这份 HTML 里嵌着从公网抓来的摘录文本，把它当可信内容是错的
        nodeIntegration: false,
        contextIsolation: true,
        javascript: false,
        images: true,
        sandbox: true,
      },
    });

    await printer.loadFile(srcPath);
    // 字体还在下载时就打印，会得到一份回退字体排版的 PDF（中文变方块）。
    // `did-finish-load` 只保证 DOM 好了，不保证 webfont 好了，所以额外等一次
    await waitForFonts();

    const buf = await printer.webContents.printToPDF({
      printBackground: true, // 引用块的底色是靠背景画的，不开就全白
      pageSize: 'A4',
      margins: { marginType: 'default' },
      // 页眉页脚默认关：开了会在每页顶上印一行文件路径，
      // 而这个路径是我们的临时目录，泄露本机目录结构还难看
      displayHeaderFooter: false,
      generateTaggedPDF: true, // 带结构标签，锚点链接和无障碍读屏都靠它
      generateDocumentOutline: true, // 左侧目录树，直接由 h1~h3 生成
    });

    if (buf.length === 0) {
      return { ok: false, error: 'Chromium 返回了 0 字节的 PDF（内容可能全被打印样式隐藏了）' };
    }
    await writeFile(save.filePath, buf);
    // 存完直接在文件管理器里选中它。用户刚在保存对话框里挑过位置，
    // 这时候弹出来不算打扰，反而省掉「刚才存哪了」那一步
    revealPdf(save.filePath);
    return { ok: true, path: save.filePath };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  } finally {
    printer?.destroy();
    if (dir) await rm(dir, { recursive: true, force: true }).catch(() => undefined);
  }
}

/** 导出完直接在文件管理器里选中它 —— 少一步"文件存哪了" */
export function revealPdf(path: string): void {
  shell.showItemInFolder(path);
}

/**
 * 给打印加一层**只管分页**的补丁。
 *
 * 🔴 这里一个颜色、一个字号都不写。
 * 配色和字体是引擎生成 single-html 时一起带出来的（`export.py` 的
 * `_SINGLE_EXTRA_CSS`）—— 那份文档是自包含的，**不加载设计令牌样式表**，
 * 所以在这里写 `var(--syn-color-primary)` 会解析成空值，
 * 而写死十六进制又会绕开硬编码守卫。两边都不对，
 * 正确做法是让打印用的配色和它的正文配色在同一个文件里定义。
 *
 * 留在这一侧的只有分页规则：这是**打印介质独有**的，
 * 屏幕上的那份文档没有"页"的概念，写进 export.py 反而是噪音。
 */
function withPrintCss(html: string): string {
  const css = `
<style>
@media print {
  li, blockquote, figure, tr { break-inside: avoid; }
  h1, h2, h3 { break-after: avoid; }
}
</style>`;
  // 插在 </head> 前；没有 head（引擎给的是片段）就直接前置，
  // Chromium 会自己补全文档结构
  return html.includes('</head>') ? html.replace('</head>', css + '</head>') : css + html;
}

/**
 * 等字体真正就绪。
 *
 * 🔴 `document.fonts.ready` 要在页面里执行 JS 才拿得到，而我们**故意关掉了
 * javascript** —— 打印一份含公网摘录的文档时开 JS 是纯风险。所以退而求其次：
 * `did-finish-load` 之后再给一小段固定时间。这是个**已知的折中**，
 * 不是没想到：宁可每次多等 400ms，也不为省这点时间给外来 HTML 开脚本执行。
 */
async function waitForFonts(): Promise<void> {
  await new Promise((r) => setTimeout(r, 400));
}

/** 文件名里的非法字符换成下划线。不换的话保存对话框会直接报一个看不懂的系统错 */
function sanitize(name: string): string {
  const cleaned = name.replace(/[\\/:*?"<>|]/g, '_').trim();
  return cleaned.slice(0, 80) || 'synorive-研究简报';
}
