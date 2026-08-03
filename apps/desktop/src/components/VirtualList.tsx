import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * F6 —— 虚拟滚动 + 图片懒解码
 * ============================================================
 * 几千条结果不掉帧的唯一可靠办法：**只把看得见的那十几行放进 DOM**。
 *
 * **为什么不用现成的虚拟滚动库**：那些库都要求你先告诉它每行多高。
 * 而这里的行高是变的 —— 一条网页结果可能一行标题，也可能标题换行 +
 * 三行摘要 + 一排来源标签。喂一个错的固定行高，症状是滚动条长度
 * 一直在跳、滚到底部时下面还空着一大块，比不做虚拟滚动更难受。
 *
 * 所以这里做的是**测量式虚拟滚动**：先用估算高度铺开，渲染出来的行
 * 用 `ResizeObserver` 量真实高度写回缓存，下一帧用真值重算偏移。
 * 代价是第一屏会有一次很小的位置修正；收益是行高怎么变都不会错位。
 *
 * 🔴 **`overscan` 不能省**。只渲染可视区的话，快速滚动时会先看到
 * 一片空白再看到内容 —— 那个空白比卡顿更像"坏了"。上下各多渲染
 * 几行，滚动过程中就永远有东西。
 */

export interface VirtualListProps<T> {
  items: T[];
  /** 估算行高，只影响首帧铺开，量到真值后就不用它了 */
  estimateHeight?: number;
  /** 可视区外上下各多渲染几行 */
  overscan?: number;
  /** 列表容器的额外 class */
  className?: string;
  /** 少于这个条数就不虚拟化，直接全渲染 —— 见下面的注释 */
  threshold?: number;
  children: (item: T, index: number) => React.ReactNode;
  /** 键：不给就用下标（下标做键在列表会重排时不安全，所以尽量给） */
  keyOf?: (item: T, index: number) => string;
}

export function VirtualList<T>({
  items,
  estimateHeight = 96,
  overscan = 6,
  className,
  threshold = 60,
  children,
  keyOf,
}: VirtualListProps<T>) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const heights = useRef<number[]>([]);
  const [, forceTick] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(0);

  // 条数少于阈值时**整个跳过虚拟化**。三十条结果做虚拟滚动没有任何收益，
  // 却引入了测量抖动和键盘导航要处理"目标行还没渲染"的复杂度
  const virtualize = items.length >= threshold;

  useLayoutEffect(() => {
    heights.current.length = items.length;
  }, [items.length]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setViewport(el.clientHeight));
    ro.observe(el);
    setViewport(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (el) setScrollTop(el.scrollTop);
  }, []);

  const measure = useCallback((index: number, h: number) => {
    if (h > 0 && Math.abs((heights.current[index] ?? 0) - h) > 0.5) {
      heights.current[index] = h;
      // 用 tick 而不是把 heights 放进 state：高度缓存每帧可能变几十次，
      // 放 state 会触发几十次重渲染，反倒把帧率打下去
      forceTick((n) => n + 1);
    }
  }, []);

  if (!virtualize) {
    return (
      <div ref={scrollRef} className={className} onScroll={onScroll}>
        {items.map((it, i) => (
          <div key={keyOf ? keyOf(it, i) : i}>{children(it, i)}</div>
        ))}
      </div>
    );
  }

  // 前缀和：第 i 行的顶部偏移。O(n) 每帧算一遍，n 是几千的量级，
  // 微秒级，比维护一棵增量更新的树简单得多也不容易出错
  // 用 Float64Array 而不是普通数组：一是索引访问在 TS 的
  // noUncheckedIndexedAccess 下不会变成 `number | undefined`（少一堆 `!`），
  // 二是几千项时它的分配和遍历都比稀疏数组稳
  const offsets = new Float64Array(items.length + 1);
  for (let i = 0; i < items.length; i++) {
    offsets[i + 1] = offsets[i]! + (heights.current[i] || estimateHeight);
  }
  const total = offsets[items.length]!;

  let start = 0;
  while (start < items.length && offsets[start + 1]! < scrollTop) start++;
  let end = start;
  while (end < items.length && offsets[end]! < scrollTop + viewport) end++;
  start = Math.max(0, start - overscan);
  end = Math.min(items.length, end + overscan);

  return (
    <div ref={scrollRef} className={className} onScroll={onScroll}>
      <div className="syn-vl-spacer" style={{ height: `${total}px` }}>
        <div className="syn-vl-window" style={{ transform: `translateY(${offsets[start]}px)` }}>
          {items.slice(start, end).map((it, k) => {
            const i = start + k;
            return (
              <Row key={keyOf ? keyOf(it, i) : i} index={i} onMeasure={measure}>
                {children(it, i)}
              </Row>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Row({
  index,
  onMeasure,
  children,
}: {
  index: number;
  onMeasure: (i: number, h: number) => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    onMeasure(index, el.offsetHeight);
    const ro = new ResizeObserver(() => onMeasure(index, el.offsetHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [index, onMeasure]);
  return <div ref={ref}>{children}</div>;
}

/**
 * F6 配套 —— 懒解码图片。
 *
 * `loading="lazy"` 只推迟**下载**，浏览器仍然会在图片进入内存后
 * 立刻解码；一屏几十张缩略图同时解码就是掉帧的直接原因。
 * `decoding="async"` 把解码挪出主线程，这两个属性要一起给才有意义。
 *
 * 另外必须占位：没有 `width/height` 或占位框的话，图片一张张加载完
 * 会把下面的内容一次次往下推（布局抖动），而虚拟滚动的高度缓存
 * 也会跟着一遍遍失效重算。
 */
export function LazyImage({
  src,
  alt,
  className,
  ratio = '16 / 9',
}: {
  src: string;
  alt: string;
  className?: string;
  ratio?: string;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={`syn-lazy-img ${className ?? ''}`} style={{ aspectRatio: ratio }}>
      {failed ? (
        <span className="syn-lazy-img-fail">图没加载出来</span>
      ) : (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}
