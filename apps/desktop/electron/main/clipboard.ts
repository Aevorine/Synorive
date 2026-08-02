/**
 * E4 剪贴板哨兵
 * ============================================================
 * 盯着剪贴板，把你复制过的东西攒起来，随时可以一键存进库。
 *
 * 🔒 为什么**不自动入库**（这条是刻意的，别"优化"掉）：
 *   密码管理器的密码、短信验证码、私钥、Cookie —— 全都会经过剪贴板。
 *   自动归档等于把这些永久写进一个可全文检索、还能被 Claude Code
 *   通过 MCP 读到的库里。那是把一个便利功能变成一个泄密渠道。
 *   所以默认只在**内存**里留最近 N 条，你点了才落盘；
 *   例外是纯链接（不含凭据），可以单独打开自动归档。
 *
 * 另外：Electron 没有剪贴板变化事件，只能轮询。800ms 是权衡过的 ——
 * 再快对复制粘贴这种低频操作没有意义，只是白烧 CPU。
 */

import { clipboard, nativeImage } from 'electron';

/** 内存里最多留几条。不落盘，退出即清空。 */
const MAX_ENTRIES = 20;
const POLL_MS = 800;

export interface ClipEntry {
  id: string;
  kind: 'text' | 'link' | 'image';
  /** 文本内容，或图片的 data URL */
  content: string;
  /** 给界面看的一行预览 */
  preview: string;
  /** 图片才有 */
  width?: number;
  height?: number;
  capturedAt: string;
  /** 已经存进库了吗 */
  archived: boolean;
}

/**
 * 看起来像凭据的东西一律不收。
 *
 * 这里**宁可漏收也不能误收** —— 漏收的代价是你手动复制一次，
 * 误收的代价是密码进了一个可全文检索的库。两者不对等。
 */
const SECRET_PATTERNS: RegExp[] = [
  /\b(sk|pk)-[A-Za-z0-9_-]{16,}/,                       // API key
  /\bgh[pousr]_[A-Za-z0-9]{20,}/,                        // GitHub token
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,                  // 私钥
  /\bBearer\s+[A-Za-z0-9._-]{20,}/i,                     // Bearer token
  /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./,       // JWT
  /\b(?:password|passwd|pwd|密码)\s*[:=]\s*\S+/i,        // 显式写着密码
  // 整段就是一串 4~8 位数字 —— 多半是验证码。
  // ⚠️ 必须锚定整串。写成 `\b\d{6}\b` 会把「这个季度营收 482915 元」这种
  //    正常句子里的数字一起拦掉，实测误拦过。
  /^\d{4,8}$/,
];

/**
 * 结构化文本：网址和文件路径。
 * 这两类天然"没有空格、字符杂"，会被高熵判据误判成随机密码 —— 实测
 * `https://github.com/Fusheng201/Synorive` 就被拦过，而链接恰恰是最该收的一类。
 * 它们各有明确前缀，先认出来直接放行。
 */
const STRUCTURED = /^(?:https?:\/\/|ftp:\/\/|file:\/\/|[A-Za-z]:[\\/]|\\\\|[/~])/;

/**
 * 高熵短串判据：没有空格、大小写数字符号混杂、字符几乎不重复。
 * 密码管理器吐出来的随机密码就长这样，而正常复制的句子不会。
 *
 * ⚠️ 下限是 **12** 不是 16。一开始写的 16 漏掉了 `p7$Kd2!Nq9@Wz4#Rb` 这类
 *    十二到十五位的密码 —— 而那恰恰是最常见的手工密码长度区间。端到端实测抓到的。
 *
 * 12~15 位这一段容易误伤 `Synorive2026` 这种"词+数字"，所以这段额外要求
 * **必须含符号**：随机密码几乎都带符号，而人拼的标识符通常不带。
 */
function looksHighEntropy(s: string): boolean {
  const t = s.trim();
  if (t.length < 12 || t.length > 64 || /\s/.test(t)) return false;

  let classes = 0;
  if (/[a-z]/.test(t)) classes++;
  if (/[A-Z]/.test(t)) classes++;
  if (/\d/.test(t)) classes++;
  const hasSymbol = /[^A-Za-z0-9]/.test(t);
  if (hasSymbol) classes++;
  if (classes < 3) return false;

  // 短的那一段（12~15）门槛更严，否则会把 Synorive2026 这类误判成密码
  if (t.length < 16 && !hasSymbol) return false;

  // 字符种类占比高 = 更像随机串而不是单词
  return new Set(t).size / t.length > 0.6;
}

export function looksLikeSecret(s: string): boolean {
  const t = s.trim();
  // 显式特征（key 前缀、私钥头、JWT…）优先，它们比结构判断更硬 ——
  // 一个带 token 的 URL 该拦还是要拦。
  if (SECRET_PATTERNS.some((re) => re.test(t))) return true;
  if (STRUCTURED.test(t)) return false;
  return looksHighEntropy(t);
}

const URL_ONLY = /^https?:\/\/\S+$/i;

export interface ClipboardWatcherOptions {
  /** 攒到新东西时通知界面 */
  onEntry: (entry: ClipEntry) => void;
  /** 纯链接自动归档时调这个真正入库 */
  onAutoArchive?: (entry: ClipEntry) => void;
}

export class ClipboardWatcher {
  private timer: NodeJS.Timeout | null = null;
  private lastText = '';
  private lastImageHash = '';
  private entries: ClipEntry[] = [];
  private seq = 0;
  private autoArchiveLinks = false;

  constructor(private readonly opts: ClipboardWatcherOptions) {}

  start(): void {
    if (this.timer) return;
    // 开哨兵的那一刻剪贴板里已有的内容不收 —— 你开开关是为了"从现在开始"，
    // 而不是把开之前复制的东西（很可能就是刚粘过的密码）一起吞进来。
    this.lastText = clipboard.readText() ?? '';
    this.lastImageHash = this.imageHash();
    this.timer = setInterval(() => this.tick(), POLL_MS);
    this.timer.unref?.();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** 关掉哨兵时把攒的东西一并清掉，别留在内存里 */
  clear(): void {
    this.entries = [];
  }

  setAutoArchiveLinks(on: boolean): void {
    this.autoArchiveLinks = on;
  }

  list(): ClipEntry[] {
    return [...this.entries];
  }

  markArchived(id: string): void {
    const e = this.entries.find((x) => x.id === id);
    if (e) e.archived = true;
  }

  remove(id: string): void {
    this.entries = this.entries.filter((x) => x.id !== id);
  }

  private imageHash(): string {
    const img = clipboard.readImage();
    if (img.isEmpty()) return '';
    const s = img.getSize();
    // 缩到 8×8 再取字节做指纹：比整张图便宜几个数量级，
    // 对"是不是同一张图"这个问题足够了
    const tiny = img.resize({ width: 8, height: 8, quality: 'good' });
    return `${s.width}x${s.height}:${tiny.toBitmap().toString('base64')}`;
  }

  private tick(): void {
    try {
      const text = clipboard.readText() ?? '';
      if (text && text !== this.lastText) {
        this.lastText = text;
        this.onText(text);
        return;   // 一轮只处理一种，文本优先
      }
      const h = this.imageHash();
      if (h && h !== this.lastImageHash) {
        this.lastImageHash = h;
        this.onImage();
      }
    } catch {
      /* 剪贴板偶发被别的进程锁住，跳过这一轮就行，不值得报错 */
    }
  }

  private onText(text: string): void {
    const trimmed = text.trim();
    if (!trimmed || trimmed.length > 200_000) return;
    if (looksLikeSecret(trimmed)) return;   // 🔒 静默丢弃，连预览都不留

    const isLink = URL_ONLY.test(trimmed);
    const entry: ClipEntry = {
      id: `clip-${Date.now()}-${this.seq++}`,
      kind: isLink ? 'link' : 'text',
      content: trimmed,
      preview: trimmed.replace(/\s+/g, ' ').slice(0, 120),
      capturedAt: new Date().toISOString(),
      archived: false,
    };
    this.push(entry);

    if (isLink && this.autoArchiveLinks) {
      entry.archived = true;
      this.opts.onAutoArchive?.(entry);
    }
  }

  private onImage(): void {
    const img = clipboard.readImage();
    if (img.isEmpty()) return;
    const size = img.getSize();
    if (size.width < 16 || size.height < 16) return;   // 小图标之类的没意义

    // 存 data URL，界面能直接当缩略图显示。
    // 大图先缩到 480 宽再转，否则一张 4K 截图能撑到几十 MB。
    const thumb = size.width > 480
      ? img.resize({ width: 480, quality: 'good' })
      : img;
    const entry: ClipEntry = {
      id: `clip-${Date.now()}-${this.seq++}`,
      kind: 'image',
      content: thumb.toDataURL(),
      preview: `图片 ${size.width}×${size.height}`,
      width: size.width,
      height: size.height,
      capturedAt: new Date().toISOString(),
      archived: false,
    };
    this.push(entry);
  }

  private push(entry: ClipEntry): void {
    // 连续复制同一段内容不重复攒
    if (this.entries[0]?.content === entry.content) return;
    this.entries.unshift(entry);
    if (this.entries.length > MAX_ENTRIES) this.entries.length = MAX_ENTRIES;
    this.opts.onEntry(entry);
  }
}

export { nativeImage };
