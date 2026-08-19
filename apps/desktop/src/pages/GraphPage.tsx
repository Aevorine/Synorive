import { useMemo, useState } from 'react';
import { ArrowLeft, Network } from 'lucide-react';
import type { Entity } from '@synorive/shared-types';
import { api } from '../lib/api';
import { useEngineData } from '../lib/useEngineData';
import { useApp } from '../lib/store';
import { useSearch } from '../lib/useSearch';

/**
 * 知识图谱 E6 —— 顺藤摸瓜
 *
 * 不画力导向图。那种图好看但没用：节点一多就是一团毛线，
 * 而且用户真正想干的是「从这个实体找出所有提到它的内容」，
 * 那是个跳转动作，不是个观赏动作。
 * 所以做成"实体列表 + 点进去看邻居 + 再点跳到搜索"。
 */

const KIND_LABEL: Record<string, string> = {
  person: '人物',
  place: '地点',
  org: '机构',
  product: '专名',
  event: '事件',
  concept: '概念',
  time: '时间',
  contact: '联系方式',
  link: '链接',
  money: '金额',
  version: '版本号',
};

const KIND_ORDER = ['org', 'contact', 'link', 'money', 'version', 'time', 'person', 'place', 'product'];

export function GraphPage() {
  const [focus, setFocus] = useState<Entity | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const setPage = useApp((s) => s.setPage);
  const setQuery = useSearch((s) => s.setQuery);

  const { data, loading, error } = useEngineData(
    () => api.graph({ entityId: focus?.id, kind: kind ?? undefined, limit: 80 }),
    [focus?.id, kind],
    { refreshOn: ['ingest.job'] },
  );

  const entities = data?.entities ?? [];
  const edges = data?.edges ?? [];

  const byKind = useMemo(() => {
    const m = new Map<string, Entity[]>();
    for (const e of entities) {
      const list = m.get(e.kind) ?? [];
      list.push(e);
      m.set(e.kind, list);
    }
    return [...m.entries()].sort(
      (a, b) => (KIND_ORDER.indexOf(a[0]) + 99) % 99 - ((KIND_ORDER.indexOf(b[0]) + 99) % 99),
    );
  }, [entities]);

  const neighbors = useMemo(() => {
    if (!focus) return [];
    const map = new Map(entities.map((e) => [e.id, e]));
    return edges
      .map((g) => {
        const other = map.get(g.from === focus.id ? g.to : g.from);
        return other && other.id !== focus.id ? { entity: other, weight: g.weight } : null;
      })
      .filter((x): x is { entity: Entity; weight: number } => !!x)
      .sort((a, b) => b.weight - a.weight);
  }, [focus, entities, edges]);

  const searchFor = (e: Entity) => {
    setQuery(e.name);
    setPage('search');
  };

  return (
    <div className="page">
      <div className="page__meta">
        <span className="page__subtitle">
          {focus
            ? `${focus.name} 的关联（${neighbors.length} 个）`
            : entities.length
              ? `${entities.length} 个实体 · ${edges.length} 条共现关系`
              : ''}
        </span>
      </div>

      <div className="filterbar">
        {focus ? (
          <button className="chip chip--on" onClick={() => setFocus(null)}>
            <ArrowLeft size={11} strokeWidth={2} /> 返回全部
          </button>
        ) : (
          <div className="filterbar__group">
            <span className="filterbar__label">类型</span>
            <div className="filterbar__chips">
              <button
                className={`chip${kind === null ? ' chip--on' : ''}`}
                onClick={() => setKind(null)}
              >
                全部
              </button>
              {KIND_ORDER.filter((k) => entities.some((e) => e.kind === k) || kind === k).map(
                (k) => (
                  <button
                    key={k}
                    className={`chip${kind === k ? ' chip--on' : ''}`}
                    onClick={() => setKind(k)}
                  >
                    {KIND_LABEL[k] ?? k}
                  </button>
                ),
              )}
            </div>
          </div>
        )}
      </div>

      <div className="page__body">
        {error && <div className="banner banner--error">{error}</div>}

        {loading && entities.length === 0 && (
          <div className="loadingstate">
            <span>加载中…</span>
          </div>
        )}

        {!loading && entities.length === 0 && (
          <div className="empty">
            <Network size={30} strokeWidth={1.2} className="empty__glyph" />
            <div className="empty__title">还没抽取到实体</div>
            <p className="empty__hint">
              索引一些文档之后，里面的邮箱、链接、金额、日期、版本号、机构名会自动被抽出来。
              人名和地名默认不抽——中文的词性标注在这上面误判率太高，
              与其给一堆假的不如不给。设置里可以打开。
            </p>
          </div>
        )}

        {focus && (
          <div className="entitydetail">
            <div className="entitydetail__head">
              <span className={`badge badge--${focus.kind}`}>{KIND_LABEL[focus.kind] ?? focus.kind}</span>
              <span className="entitydetail__name">{focus.name}</span>
              <span className="entitydetail__count">被提到 {focus.mentionCount} 次</span>
              <button className="btn btn--sm btn--primary" onClick={() => searchFor(focus)}>
                搜所有提到它的内容
              </button>
            </div>
            {neighbors.length === 0 ? (
              <p className="panel__hint">它没有和别的实体一起出现过。</p>
            ) : (
              <div className="entitygrid">
                {neighbors.map(({ entity, weight }) => (
                  <button key={entity.id} className="entity" onClick={() => setFocus(entity)}>
                    <span className={`badge badge--${entity.kind}`}>
                      {KIND_LABEL[entity.kind] ?? entity.kind}
                    </span>
                    <span className="entity__name">{entity.name}</span>
                    <span className="entity__count">同现 {weight}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {!focus &&
          byKind.map(([k, list]) => (
            <section key={k} className="panel">
              <h2 className="panel__subtitle">
                {KIND_LABEL[k] ?? k}　<span className="panel__hint">{list.length} 个</span>
              </h2>
              <div className="entitygrid">
                {list.map((e) => (
                  <button key={e.id} className="entity" onClick={() => setFocus(e)}>
                    <span className={`badge badge--${e.kind}`}>{KIND_LABEL[e.kind] ?? e.kind}</span>
                    <span className="entity__name">{e.name}</span>
                    <span className="entity__count">×{e.mentionCount}</span>
                  </button>
                ))}
              </div>
            </section>
          ))}
      </div>
    </div>
  );
}
