import { useEffect, useState } from 'react';
import { Filter, Languages, Layers, ShieldCheck } from 'lucide-react';
import { webApi, type SourcePreset } from '../lib/webApi';

/**
 * 深挖的四个旋钮 —— S4 / S5 / S8 / V 档位
 * ============================================================
 * 四个旋钮各控一件事，**每一个都直接写明代价**：
 *   定向源（S8）— 只搜权威站。代价：搜不到别的
 *   挖几轮（S5）— 读完一轮再自动追问。代价：时间线性涨
 *   跨语言（S4）— 中文查询自动补英文变体。代价：多一个维基往返
 *   核查档（V） — 要不要主动去找反驳材料。代价：慢 1~20 秒不等
 *
 * 🔴 **不写代价的选项等于没有选项**。用户看不到"选了会失去什么"，
 * 就只能凭感觉乱点，然后为一个自己没意识到的取舍买单。
 */

export interface ResearchOptions {
  preset: string | null;
  rounds: number;
  expand: boolean;
  verifyLevel: 'annotate' | 'counter' | 'claim';
}

export const DEFAULT_RESEARCH_OPTIONS: ResearchOptions = {
  preset: null,
  rounds: 2,
  expand: true,
  verifyLevel: 'counter',
};

const ROUNDS: { n: number; label: string; cost: string }[] = [
  { n: 1, label: '一轮', cost: '最快，只回答你已经会问的问题' },
  { n: 2, label: '两轮', cost: '读完第一轮自己想出该追问什么，再搜一次（推荐）' },
  { n: 3, label: '三轮', cost: '再深一层，但第三轮新增的独立来源已经很少' },
];

const LEVELS: { id: ResearchOptions['verifyLevel']; label: string; cost: string }[] = [
  { id: 'annotate', label: '只标注', cost: '零延迟。只看来源等级和文风，不主动查证' },
  { id: 'counter', label: '反向检索', cost: '+1~2 秒。主动去搜辟谣/质疑 + 溯源 + 撤稿检查（推荐）' },
  { id: 'claim', label: '逐句核查', cost: '慢很多。每句断言都单独搜一轮，深挖会从 10 秒变 30 秒以上' },
];

export function ResearchControls({
  value,
  onChange,
  compact,
}: {
  value: ResearchOptions;
  onChange: (v: ResearchOptions) => void;
  compact?: boolean;
}) {
  const [presets, setPresets] = useState<SourcePreset[]>([]);
  const [open, setOpen] = useState(!compact);

  // 预设清单是纯静态的，拉一次就够
  useEffect(() => {
    if (presets.length) return;
    void webApi
      .presets()
      .then((r) => setPresets(r.presets))
      .catch(() => {
        /* 拉不到就只是少一个筛选器，不该弹错误打断搜索 */
      });
  }, [presets.length]);

  const set = <K extends keyof ResearchOptions>(k: K, v: ResearchOptions[K]) =>
    onChange({ ...value, [k]: v });

  const active = presets.find((p) => p.id === value.preset);

  if (compact && !open) {
    return (
      <button className="rc__summary" onClick={() => setOpen(true)}>
        <Layers size={14} aria-hidden />
        {value.rounds} 轮 · {LEVELS.find((l) => l.id === value.verifyLevel)?.label}
        {active ? ` · ${active.label}` : ''}
        {value.expand ? ' · 跨语言' : ''}
      </button>
    );
  }

  return (
    <div className="rc">
      {/* S8 定向源 */}
      <div className="rc__group">
        <span className="rc__label">
          <Filter size={14} aria-hidden /> 只搜
        </span>
        <div className="rc__opts">
          <button
            className={`rc__opt ${!value.preset ? 'is-on' : ''}`}
            onClick={() => set('preset', null)}
            title="不限来源，全网搜"
          >
            全网
          </button>
          {presets.map((p) => (
            <button
              key={p.id}
              className={`rc__opt ${value.preset === p.id ? 'is-on' : ''}`}
              onClick={() => set('preset', value.preset === p.id ? null : p.id)}
              title={`${p.why}\n代价：${p.caveat}`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      {/* 开了预设就必须把代价摆在明面上 —— 藏在 tooltip 里等于没说 */}
      {active && <p className="rc__caveat">⚠ {active.caveat}</p>}

      {/* S5 轮数 */}
      <div className="rc__group">
        <span className="rc__label">
          <Layers size={14} aria-hidden /> 挖几轮
        </span>
        <div className="rc__opts">
          {ROUNDS.map((r) => (
            <button
              key={r.n}
              className={`rc__opt ${value.rounds === r.n ? 'is-on' : ''}`}
              onClick={() => set('rounds', r.n)}
              title={r.cost}
            >
              {r.label}
            </button>
          ))}
        </div>
        <span className="rc__cost">{ROUNDS.find((r) => r.n === value.rounds)?.cost}</span>
      </div>

      {/* V 档位 */}
      <div className="rc__group">
        <span className="rc__label">
          <ShieldCheck size={14} aria-hidden /> 核查
        </span>
        <div className="rc__opts">
          {LEVELS.map((l) => (
            <button
              key={l.id}
              className={`rc__opt ${value.verifyLevel === l.id ? 'is-on' : ''}`}
              onClick={() => set('verifyLevel', l.id)}
              title={l.cost}
            >
              {l.label}
            </button>
          ))}
        </div>
        <span className="rc__cost">
          {LEVELS.find((l) => l.id === value.verifyLevel)?.cost}
        </span>
      </div>

      {/* S4 跨语言 */}
      <div className="rc__group">
        <span className="rc__label">
          <Languages size={14} aria-hidden /> 跨语言
        </span>
        <label className="rc__switch">
          <input
            type="checkbox"
            checked={value.expand}
            onChange={(e) => set('expand', e.target.checked)}
          />
          <span>
            中文查询自动补一个英文变体，分派给英文覆盖更好的引擎
            <em>（一手资料多半是英文的，不补等于漏掉半个世界）</em>
          </span>
        </label>
      </div>

      {compact && (
        <button className="rc__collapse" onClick={() => setOpen(false)}>
          收起
        </button>
      )}
    </div>
  );
}
