import { AlertTriangle, X } from 'lucide-react';
import type { SearchFilters } from '@synorive/shared-types';
import { useSearch } from '../lib/useSearch';

/**
 * D10 / L3-plus / D2 —— 「现在到底有哪些条件在起作用」
 * ============================================================
 * 用户在搜索框里敲 `section:方法 注意力机制`，引擎把它拆成
 * 「章节筛选」+「注意力机制」两部分。这个组件把拆的结果摆出来。
 *
 * 🔴 **这一步以前是缺的，而缺它是个真问题。** 引擎一直在算 `parsedQuery`，
 * 但界面从来没读过。后果是：加了一条筛选，结果集悄悄少了一大半，
 * 用户完全不知道是那条指令干的 —— 他只会觉得"这库里东西怎么这么少"。
 * **看不见的筛选比没有筛选糟得多。**
 *
 * 🔴 **看不懂的指令要单独用警告色列出来。** `date:去年夏天` 解析不了，
 * 按宽容原则它会退化成普通查询词继续参与搜索 —— 这是对的，
 * 但**必须说出来**：否则用户以为自己筛掉了时间范围，其实"去年夏天"
 * 四个字正在被当成关键词匹配，搜出一堆莫名其妙的东西。
 *
 * ── D2 这一轮补的 ────────────────────────────────────────
 * 以前只显示**查询串里打出来的**那种筛选（`type:pdf`），而从
 * 文件管理器侧栏点选的**结构化筛选**（`filters` 对象）一条都不显示。
 * 于是出现最难查的一种困惑：用户在别的页面勾了「只看图片」，
 * 回到搜索页搜文档，一条都搜不到，而**界面上没有任何东西指向那个勾**。
 * 现在两类条件摆在同一行、长得一样、都能点掉 ——
 * **对用户来说它们本来就是同一件事：「有什么在限制我的结果」。**
 */

/** 一条可点掉的结构化筛选 */
interface StructChip {
  key: keyof SearchFilters;
  text: string;
}

const MODALITY_CN: Record<string, string> = {
  text: '文档',
  image: '图片',
  video: '视频',
  audio: '音频',
  link: '网页',
  message: '聊天',
};

const SOURCE_CN: Record<string, string> = {
  file: '本机文件',
  web: '网页',
  clipboard: '剪贴板',
  mail: '邮件',
  'chat-export': '聊天记录',
};

function fmtDate(s: string): string {
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleDateString('zh-CN');
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

/**
 * 把 filters 对象翻译成人话芯片。
 *
 * 🔴 **必须显式列出每一个键，不能 `Object.entries` 泛化。**
 *    泛化的话，将来给 SearchFilters 加了新字段而忘了在这里加翻译，
 *    界面会显示成 `sizeMinBytes: 10485760` 这种给程序员看的东西 ——
 *    或者更糟：什么都不显示，于是又回到"看不见的筛选"那个问题。
 *    显式列出至少让漏掉的那一条在代码审查时看得出来。
 */
function structChips(f: SearchFilters): StructChip[] {
  const out: StructChip[] = [];
  if (f.modalities?.length) {
    out.push({
      key: 'modalities',
      text: `只看 ${f.modalities.map((m) => MODALITY_CN[m] ?? m).join('、')}`,
    });
  }
  if (f.sources?.length) {
    out.push({ key: 'sources', text: `来源 ${f.sources.map((s) => SOURCE_CN[s] ?? s).join('、')}` });
  }
  if (f.tags?.length) out.push({ key: 'tags', text: `标签 ${f.tags.join('、')}` });
  if (f.timeFrom || f.timeTo) {
    const a = f.timeFrom ? fmtDate(f.timeFrom) : '最早';
    const b = f.timeTo ? fmtDate(f.timeTo) : '现在';
    out.push({ key: 'timeFrom', text: `时间 ${a} ~ ${b}` });
  }
  if (f.sizeMinBytes != null || f.sizeMaxBytes != null) {
    const a = f.sizeMinBytes != null ? fmtSize(f.sizeMinBytes) : '0';
    const b = f.sizeMaxBytes != null ? fmtSize(f.sizeMaxBytes) : '不限';
    out.push({ key: 'sizeMinBytes', text: `大小 ${a} ~ ${b}` });
  }
  if (f.scopes?.length) out.push({ key: 'scopes', text: `只在 ${f.scopes.join('、')}` });
  return out;
}

export function QueryChips() {
  const parsed = useSearch((s) => s.parsed);
  const query = useSearch((s) => s.query);
  const filters = useSearch((s) => s.filters);
  const setQuery = useSearch((s) => s.setQuery);
  const setFilters = useSearch((s) => s.setFilters);

  const struct = structChips(filters);
  const hasParsed = !!parsed && (parsed.filters.length > 0 || parsed.unknown.length > 0);

  if (!hasParsed && struct.length === 0) return null;

  /**
   * 点掉一条结构化筛选。
   *
   * 时间和大小是**成对**的（from/to、min/max），必须一起删 ——
   * 只删一半会留下一个"最早 ~ 2024-01-01"的半截条件，
   * 而芯片上已经不显示它了，那正是"看不见的筛选"的翻版。
   */
  const dropStruct = (key: keyof SearchFilters) => {
    const next: SearchFilters = { ...filters };
    if (key === 'timeFrom') {
      delete next.timeFrom;
      delete next.timeTo;
    } else if (key === 'sizeMinBytes') {
      delete next.sizeMinBytes;
      delete next.sizeMaxBytes;
    } else {
      delete next[key];
    }
    setFilters(next);
  };

  return (
    <div className="qchips" role="status">
      <span className="qchips__lead">现在有这些条件在起作用：</span>

      {/* 查询串里打出来的筛选（`type:pdf`）—— 删它要改查询串本身 */}
      {parsed?.filters.map((f) => (
        <span className="qchips__chip" key={f}>
          {f}
        </span>
      ))}

      {/* D2：从别处勾选的结构化筛选。和上面长得一样是刻意的 ——
          对用户来说这两类本来就是同一件事 */}
      {struct.map((c) => (
        <span className="qchips__chip qchips__chip--struct" key={String(c.key)}>
          {c.text}
          <button
            type="button"
            aria-label={`去掉筛选：${c.text}`}
            title="去掉这个条件再搜一次"
            onClick={() => dropStruct(c.key)}
          >
            <X size={11} aria-hidden />
          </button>
        </span>
      ))}

      {struct.length > 1 && (
        <button
          type="button"
          className="qchips__clearall"
          onClick={() => setFilters({})}
          title="把上面所有筛选条件一次去掉"
        >
          全部去掉
        </button>
      )}

      {parsed && parsed.filters.length > 0 && parsed.text !== query.trim() && (
        <span className="qchips__chip qchips__chip--text">
          搜索词：{parsed.text || '（只有筛选，没有关键词）'}
        </span>
      )}

      {parsed?.unknown.map((u) => (
        <span className="qchips__chip qchips__chip--unknown" key={u}>
          <AlertTriangle size={12} aria-hidden />
          没看懂 <code>{u}</code>，当普通词搜了
          <button
            type="button"
            aria-label={`把 ${u} 从查询里删掉`}
            title="从查询里删掉它再搜一次"
            onClick={() => setQuery(query.replace(u, '').replace(/\s+/g, ' ').trim())}
          >
            <X size={11} aria-hidden />
          </button>
        </span>
      ))}
    </div>
  );
}
