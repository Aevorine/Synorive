import { useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  FileText,
  Film,
  HelpCircle,
  Image as ImageIcon,
  Link2,
  MessageSquare,
  Music,
} from 'lucide-react';
import type { Modality, MatchExplain, SearchHit } from '@synorive/shared-types';
import { layout } from '@synorive/design-tokens';
import { useApp } from '../lib/store';
import { useKeyNav } from '../lib/keynav';
import { api } from '../lib/api';
import { useSelection } from '../lib/useSelection';

const MODALITY_ICON: Record<Modality, typeof FileText> = {
  text: FileText,
  image: ImageIcon,
  video: Film,
  audio: Music,
  link: Link2,
  message: MessageSquare,
};

/**
 * D6 可解释性详情。**只能走 title 原生 tooltip，不能做成展开面板** ——
 * 这个列表是虚拟滚动，行高必须固定（见文件顶部说明），任何会撑高单行的
 * UI 都会让滚动重新变卡。tooltip 不占文档流的位置，是唯一不违反这条
 * 约束的展示方式。
 */
function buildExplainTooltip(explain: MatchExplain): string {
  const lines: string[] = [explain.reason];

  if (explain.matchedTerms.length > 0) {
    lines.push(`命中词：${explain.matchedTerms.join('、')}`);
  }

  const scoreLabels: [keyof MatchExplain['scores'], string][] = [
    ['semantic', '语义相似'],
    ['keyword', '关键词(BM25)'],
    ['recency', '时间新鲜度'],
    ['popularity', '打开热度'],
    ['sourceTrust', '来源权重'],
    ['titleBoost', '标题命中'],
    ['lengthPenalty', '短片段扣分'],
    ['diversity', '多样性系数'],
  ];
  const scoreBits = scoreLabels
    .filter(([k]) => explain.scores[k] != null)
    .map(([k, label]) => `${label} ${explain.scores[k]!.toFixed(2)}`);
  if (scoreBits.length > 0) lines.push(scoreBits.join(' · '));

  if (explain.routes.length > 0) {
    const routeLabel: Record<string, string> = {
      keyword: '关键词精确匹配', vector: '语义理解', trigram: '文件名子串',
    };
    lines.push(`召回方式：${explain.routes.map((r) => routeLabel[r] ?? r).join('、')}`);
  }

  return lines.join('\n');
}

/** 内容通道 → 小徽标文案。body（正文）是最常见情况，不值得占一个徽标的视觉重量，不显示 */
const CHANNEL_BADGE: Record<string, string> = {
  ocr: '图片文字',
  transcript: '字幕',
  description: '图片描述',
  filename: '文件名',
};

function explainChannelBadge(explain: MatchExplain | undefined): string | null {
  if (!explain) return null;
  // 🔴 必须按 textChannel（下面摘录/高亮实际来自哪个通道）取徽标，不能
  // 扫 matchedVia 整个集合——一条结果可能关键词命中了正文、语义命中了
  // 它的 OCR 块，matchedVia 里 body 和 ocr 都在，扫集合会挑出"图片文字"
  // 徽标，但眼前显示的摘录其实是正文，徽标和摘录对不上
  return CHANNEL_BADGE[explain.textChannel] ?? null;
}

/**
 * 结果列表 —— 虚拟滚动（F6）
 *
 * 「不卡顿」这条要求在这里是架构问题不是优化问题：
 * 1 万条结果全渲染成 DOM 必然掉帧，无论代码写得多好。
 * 虚拟滚动只渲染视口内的十几行，所以 1 万条和 10 条一样流畅。
 *
 * 行高必须固定（从设计令牌取），变高行会让虚拟滚动每次都要测量，
 * 滚动时反而更卡。
 */
export function SearchResults({
  hits,
  onAsk,
  onScenes,
  onPreview,
  selectable,
}: {
  hits: SearchHit[];
  /** N6：给每条文档一个「能回答什么」入口。不传就不显示这个按钮 */
  onAsk?: (itemId: string, title: string) => void;
  /** N3：给每条视频一个「看镜头」入口，带上命中的秒数直接定位过去 */
  onScenes?: (itemId: string, locator: string, title: string, sec?: number) => void;
  /** F8：Space 预览。不传就只是不响应 Space，其余键照常 */
  onPreview?: (hit: SearchHit) => void;
  /**
   * A4/D3：显示勾选框。**默认 false** ——
   * 「读了这几条」那种展示性列表不该出现复选框，
   * 出现了用户会以为勾了能干什么，而那里什么都没接
   */
  selectable?: boolean;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const density = useApp((s) => s.settings?.density ?? 'standard');
  const rowHeight = layout.resultRowHeight[density];

  const virtualizer = useVirtualizer({
    count: hits.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    // 视口外多渲染 6 行，快速滚动时不会出现白条
    overscan: 6,
  });

  /**
   * F8 键盘全流程可达。
   *
   * 🔴 **滚动必须交给 virtualizer 而不是 `scrollIntoView`。**
   * 用 ↓ 一路按下去，选中项会走出可视区，而虚拟列表里它**根本不在 DOM 里** ——
   * 这时 `scrollIntoView` 是个空操作，症状是"按了没反应"，
   * 而且只在列表长的时候出现。这正是静默失败的形状。
   */
  const openHit = (i: number): void => {
    const hit = hits[i];
    if (!hit) return;
    void api.recordOpen(hit.item.id);
    if (hit.item.source === 'link') void window.synorive.sys.openExternal(hit.item.locator);
    else void window.synorive.sys.openPath(hit.item.locator);
  };

  const { index: active } = useKeyNav({
    count: hits.length,
    onOpen: openHit,
    onPreview: (i) => {
      const hit = hits[i];
      if (hit) onPreview?.(hit);
    },
    onScrollTo: (i) => virtualizer.scrollToIndex(i, { align: 'auto' }),
  });

  return (
    <div ref={parentRef} className="results" role="listbox" aria-label="搜索结果">
      <div className="results__inner" style={{ height: `${virtualizer.getTotalSize()}px` }}>
        {virtualizer.getVirtualItems().map((v) => {
          const hit = hits[v.index];
          if (!hit) return null;
          return (
            <div
              key={hit.item.id}
              className="results__row"
              style={{ height: `${v.size}px`, transform: `translateY(${v.start}px)` }}
            >
              <ResultCard
                hit={hit}
                rank={v.index + 1}
                active={v.index === active}
                onAsk={onAsk}
                onScenes={onScenes}
                selectable={selectable}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ResultCard({
  hit,
  rank,
  active,
  onAsk,
  onScenes,
  selectable,
}: {
  hit: SearchHit;
  rank: number;
  /** F8：键盘选中态。**必须同时写 aria-selected**——读屏软件看不到 CSS 高亮 */
  active?: boolean;
  onAsk?: (itemId: string, title: string) => void;
  onScenes?: (itemId: string, locator: string, title: string, sec?: number) => void;
  selectable?: boolean;
}) {
  const { item, highlight, explain, location } = hit;
  const Icon = MODALITY_ICON[item.modality] ?? FileText;
  // 只订阅"这一条选没选"，不订阅整个 picked 数组 ——
  // 订阅数组的话，勾任意一条都会让**视口内所有卡片**重渲染，
  // 一屏十几张 × 每次勾选，正好是虚拟滚动想避免的那种开销
  const checked = useSelection((s) => s.ids.has(item.id));
  const toggle = useSelection((s) => s.toggle);

  const open = () => {
    void api.recordOpen(item.id);
    if (item.source === 'link') void window.synorive.sys.openExternal(item.locator);
    else void window.synorive.sys.openPath(item.locator);
  };

  const reveal = (e: React.MouseEvent) => {
    e.stopPropagation();
    void window.synorive.sys.reveal(item.locator);
  };

  return (
    <article
      className={`card${active ? ' card--active' : ''}`}
      role="option"
      aria-selected={!!active}
      tabIndex={active ? 0 : -1}
      onDoubleClick={open}
      onKeyDown={(e) => {
        if (e.key === 'Enter') open();
      }}
      title="双击打开　↑↓ 选　Enter 打开　Space 预览"
    >
      {selectable ? (
        // 勾选框替掉序号，不是加在序号旁边 —— 卡片左侧那一列宽度是固定的，
        // 两个都塞进去会把标题挤窄，而序号在能勾选的时候本来也没什么用
        <label
          className="card__pick"
          onClick={(e) => e.stopPropagation()}
          title={checked ? '取消选中' : '选中它，用于出稿或并排对比'}
        >
          <input
            type="checkbox"
            checked={checked}
            onChange={() => toggle(hit)}
            aria-label={`选中 ${item.title}`}
          />
        </label>
      ) : (
        <span className="card__rank">{rank}</span>
      )}
      <Icon className="card__icon" size={17} strokeWidth={1.6} />

      <div className="card__main">
        <div className="card__head">
          <span className="card__title">{item.title}</span>
          {location?.page != null && <span className="card__loc">第 {location.page} 页</span>}
          {location?.startSec != null && (
            <span className="card__loc">{formatTime(location.startSec)}</span>
          )}
          {/* L3：命中的是论文哪一节，不用翻开就知道该看哪一段 */}
          {location?.section && <span className="card__loc card__loc--section">{location.section}</span>}
        </div>

        {highlight && (
          <p
            className="card__snippet syn-selectable"
            // 引擎侧已对原文做 HTML 转义、只留 <em> 标记安全（engine.py _highlight）
            dangerouslySetInnerHTML={{ __html: highlight }}
          />
        )}

        <div className="card__meta">
          <button className="card__path" onClick={reveal} title="在资源管理器中显示">
            {item.locator}
          </button>
          {item.sizeBytes != null && <span>{formatSize(item.sizeBytes)}</span>}
          {item.contentTime && <span>{formatDate(item.contentTime)}</span>}
          {explainChannelBadge(explain) && (
            <span className="card__channel" title="这条命中的内容来自哪里">
              {explainChannelBadge(explain)}
            </span>
          )}
          {explain && (
            <span className="card__why" title={buildExplainTooltip(explain)}>
              {explain.reason}
            </span>
          )}
          {/* N6：只对文档类给这个入口 —— 一张图片"能回答哪些问题"没有意义，
              给了只会让用户点开发现是空的 */}
          {/* N3：只对视频给这个入口。带上命中的秒数——从搜索结果点进来时
              用户要的是"那一秒"，让他自己在 20 格缩略图里找是本末倒置 */}
          {onScenes && item.modality === 'video' && (
            <button
              className="card__ask"
              onClick={(e) => {
                e.stopPropagation();
                onScenes(item.id, item.locator, item.title, location?.startSec);
              }}
              title="把这个视频切成一条能点的镜头带，点一格就地跳到那一秒"
            >
              <Film size={12} strokeWidth={1.8} /> 看镜头
            </button>
          )}
          {onAsk && (item.modality === 'text' || item.modality === 'link') && (
            <button
              className="card__ask"
              onClick={(e) => {
                e.stopPropagation();
                onAsk(item.id, item.title);
              }}
              title="从原文里读出它能回答的问题，点一条直接跳到那一段"
            >
              <HelpCircle size={12} strokeWidth={1.8} /> 能回答什么
            </button>
          )}
        </div>
      </div>

      <span className="card__score" title="综合相关度">
        {hit.score.toFixed(4)}
      </span>
    </article>
  );
}

function formatSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const days = Math.floor((now.getTime() - d.getTime()) / 86_400_000);
  if (days === 0) return '今天';
  if (days === 1) return '昨天';
  if (days < 30) return `${days} 天前`;
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
