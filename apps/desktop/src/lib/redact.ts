/**
 * 出稿前的敏感内容打码
 * ============================================================
 * 摘录出来的正文来自你自己的资料，而资料里什么都可能有：身份证号、手机号、
 * 银行卡、API Key、私钥。这些东西在库里躺着没问题（那是你自己的机器），
 * 但**导出、复制、打成 PDF 发给别人**的那一刻性质就变了。
 *
 * 🔴 **只在出稿路径上做，不动库里的原文。** 在入库时打码是另一件事，
 *    而且是错的 —— 那样你自己都搜不到自己的东西了。
 *
 * 🔴 **默认"宁可多遮"，但每一处都能单独取消。** 漏遮一个身份证号的代价，
 *    比多遮一个看起来像卡号的订单号大得多。而多遮的那些，用户看一眼就能放回来。
 *
 * 🔴 **打码之前必须让用户看见改了哪些地方。** 一个悄悄改写你要发出去的内容的
 *    功能，哪怕改得对，也是不能接受的 —— 他得知道自己发出去的是什么。
 */

export interface Redaction {
  /** 第几个字符开始 */
  start: number;
  end: number;
  /** 原文 */
  text: string;
  /** 这是什么（给用户看的） */
  kind: string;
  /** 遮成什么样 */
  masked: string;
}

/**
 * 规则表。
 *
 * 顺序有意义：**先匹配的赢**。所以把"更具体"的排在前面 ——
 * 一个 18 位身份证号同时也能被"长数字串"匹配上，先跑身份证那条才说得出
 * 它到底是什么。说不出是什么的打码，用户没法判断该不该放回来。
 */
const RULES: { kind: string; re: RegExp; mask: (m: string) => string }[] = [
  {
    kind: '私钥',
    re: /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
    mask: () => '［已移除：私钥］',
  },
  {
    // sk-、ghp_、AKIA 这类有明确前缀的，误报率极低
    kind: 'API 密钥',
    re: /\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})\b/g,
    mask: (m) => `${m.slice(0, 6)}…［已遮蔽］`,
  },
  {
    kind: '身份证号',
    // 18 位：6 位地址 + 8 位生日 + 3 位顺序 + 1 位校验（可能是 X）
    re: /\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b/g,
    mask: (m) => `${m.slice(0, 6)}********${m.slice(-1)}`,
  },
  {
    kind: '手机号',
    re: /(?<!\d)1[3-9]\d{9}(?!\d)/g,
    mask: (m) => `${m.slice(0, 3)}****${m.slice(-4)}`,
  },
  {
    kind: '银行卡号',
    re: /(?<!\d)\d{16,19}(?!\d)/g,
    mask: (m) => `${m.slice(0, 4)}${'*'.repeat(m.length - 8)}${m.slice(-4)}`,
  },
  {
    kind: '邮箱',
    re: /\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b/g,
    mask: (m) => {
      const [u, d] = m.split('@');
      return `${(u ?? '').slice(0, 2)}***@${d ?? ''}`;
    },
  },
  {
    kind: '网址里的令牌',
    re: /([?&](?:token|key|apikey|api_key|access_token|secret|password|pwd)=)[^&\s]+/gi,
    mask: (m) => `${m.split('=')[0]}=［已遮蔽］`,
  },
];

/** 扫一遍，返回所有命中（不改原文） */
export function findSensitive(text: string): Redaction[] {
  const out: Redaction[] = [];
  const taken: [number, number][] = [];
  const overlaps = (a: number, b: number) => taken.some(([s, e]) => a < e && b > s);

  for (const rule of RULES) {
    // 每次重新构造，避免 lastIndex 在多次调用之间残留 ——
    // 那会让第二次扫描漏掉开头的命中，而且是随机漏
    const re = new RegExp(rule.re.source, rule.re.flags);
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const start = m.index;
      const end = start + m[0].length;
      if (overlaps(start, end)) continue; // 更具体的规则已经占了这一段
      taken.push([start, end]);
      out.push({ start, end, text: m[0], kind: rule.kind, masked: rule.mask(m[0]) });
      if (m[0].length === 0) re.lastIndex++; // 防死循环
    }
  }
  return out.sort((a, b) => a.start - b.start);
}

/**
 * 按给定的命中列表打码。
 *
 * `skip` 里的下标表示"这一处用户说了不用遮" —— 所以是按**下标**跳过而不是
 * 按内容，同样的号码在不同位置可能一个该遮一个不该。
 */
export function applyRedactions(text: string, hits: Redaction[], skip: Set<number> = new Set()): string {
  let out = '';
  let at = 0;
  hits.forEach((h, i) => {
    if (skip.has(i)) return;
    out += text.slice(at, h.start) + h.masked;
    at = h.end;
  });
  return out + text.slice(at);
}

/** 一句话摘要，给按钮上的提示用 */
export function summarize(hits: Redaction[]): string {
  if (hits.length === 0) return '没扫到需要遮蔽的内容';
  const byKind = new Map<string, number>();
  for (const h of hits) byKind.set(h.kind, (byKind.get(h.kind) ?? 0) + 1);
  return [...byKind.entries()].map(([k, n]) => `${k} ${n} 处`).join('、');
}
