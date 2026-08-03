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
import type { Modality, SearchHit } from '@synorive/shared-types';
import { layout } from '@synorive/design-tokens';
import { useApp } from '../lib/store';
import { api } from '../lib/api';

const MODALITY_ICON: Record<Modality, typeof FileText> = {
  text: FileText,
  image: ImageIcon,
  video: Film,
  audio: Music,
  link: Link2,
  message: MessageSquare,
};

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
}: {
  hits: SearchHit[];
  /** N6：给每条文档一个「能回答什么」入口。不传就不显示这个按钮 */
  onAsk?: (itemId: string, title: string) => void;
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

  return (
    <div ref={parentRef} className="results" role="list">
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
              <ResultCard hit={hit} rank={v.index + 1} onAsk={onAsk} />
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
  onAsk,
}: {
  hit: SearchHit;
  rank: number;
  onAsk?: (itemId: string, title: string) => void;
}) {
  const { item, highlight, explain, location } = hit;
  const Icon = MODALITY_ICON[item.modality] ?? FileText;

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
      className="card"
      role="listitem"
      tabIndex={0}
      onDoubleClick={open}
      onKeyDown={(e) => {
        if (e.key === 'Enter') open();
      }}
      title="双击打开　Enter 打开"
    >
      <span className="card__rank">{rank}</span>
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
            // 高亮标记由引擎生成，只含 <em>，没有用户可控的 HTML
            dangerouslySetInnerHTML={{ __html: highlight }}
          />
        )}

        <div className="card__meta">
          <button className="card__path" onClick={reveal} title="在资源管理器中显示">
            {item.locator}
          </button>
          {item.sizeBytes != null && <span>{formatSize(item.sizeBytes)}</span>}
          {item.contentTime && <span>{formatDate(item.contentTime)}</span>}
          {explain && <span className="card__why">{explain.reason}</span>}
          {/* N6：只对文档类给这个入口 —— 一张图片"能回答哪些问题"没有意义，
              给了只会让用户点开发现是空的 */}
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
