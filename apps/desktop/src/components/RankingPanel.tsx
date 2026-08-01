import { RotateCcw, SlidersHorizontal } from 'lucide-react';
import type { RankingWeights } from '@synorive/shared-types';
import { DEFAULT_WEIGHTS } from '@synorive/shared-types';
import { useSearch, type Preset } from '../lib/useSearch';

/**
 * D4 多指标可调排序 —— 用户原话「功能有更多可选择的指标」
 *
 * 每个滑块旁边都写清楚"调高会怎样"。
 * 没有解释的滑块等于没有 —— 用户不知道该往哪边拉。
 */

const METRICS: { key: keyof RankingWeights; label: string; hint: string }[] = [
  { key: 'semantic', label: '语义相关', hint: '调高：理解意思，能搜到同义近义的说法' },
  { key: 'keyword', label: '关键词', hint: '调高：认准原词，适合搜型号、人名、代码符号' },
  { key: 'recency', label: '时间新鲜度', hint: '调高：越新的越靠前' },
  { key: 'sourceTrust', label: '来源权重', hint: '调高：本机文件比网页抓来的更靠前' },
  { key: 'popularity', label: '打开热度', hint: '调高：你常打开的排前面' },
  { key: 'titleBoost', label: '标题命中', hint: '调高：标题里有查询词的排前面' },
];

const PRESETS: { id: Preset; label: string; hint: string }[] = [
  { id: 'balanced', label: '均衡', hint: '语义和关键词并重，日常用这个' },
  { id: 'precise', label: '求准', hint: '关键词为主，适合找确切的东西' },
  { id: 'semantic', label: '求全', hint: '语义为主，只记得大概意思时用' },
  { id: 'recent', label: '看最近', hint: '时间权重拉满' },
];

export function RankingPanel() {
  const weights = useSearch((s) => s.weights);
  const preset = useSearch((s) => s.preset);
  const setWeights = useSearch((s) => s.setWeights);
  const setPreset = useSearch((s) => s.setPreset);
  const explain = useSearch((s) => s.explain);
  const toggleExplain = useSearch((s) => s.toggleExplain);

  return (
    <aside className="ranking" aria-label="排序指标">
      <div className="ranking__head">
        <SlidersHorizontal size={15} strokeWidth={1.7} />
        <span className="ranking__title">排序指标</span>
        <button
          className="ranking__reset"
          title="恢复默认权重"
          onClick={() => {
            setPreset('balanced');
            setWeights({ ...DEFAULT_WEIGHTS });
            setPreset('balanced');
          }}
        >
          <RotateCcw size={13} strokeWidth={1.7} />
        </button>
      </div>

      <div className="ranking__presets">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            className={`chip${preset === p.id ? ' chip--on' : ''}`}
            onClick={() => setPreset(p.id)}
            title={p.hint}
          >
            {p.label}
          </button>
        ))}
        {preset === 'custom' && <span className="chip chip--on">自定</span>}
      </div>

      <div className="ranking__list">
        {METRICS.map((m) => (
          <label key={m.key} className="slider" title={m.hint}>
            <span className="slider__label">{m.label}</span>
            <input
              className="slider__input"
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={weights[m.key]}
              onChange={(e) => setWeights({ [m.key]: Number(e.target.value) })}
              aria-label={`${m.label}权重`}
            />
            <span className="slider__value">{weights[m.key].toFixed(1)}</span>
          </label>
        ))}
      </div>

      <label className="ranking__toggle" title="在每条结果下显示它为什么能匹配上">
        <input type="checkbox" checked={explain} onChange={toggleExplain} />
        <span>显示匹配原因</span>
      </label>
    </aside>
  );
}
