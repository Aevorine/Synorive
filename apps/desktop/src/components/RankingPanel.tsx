import { useState } from 'react';
import { Check, Plus, RotateCcw, SlidersHorizontal, Trash2 } from 'lucide-react';
import type { RankingWeights, SavedRankingPreset } from '@synorive/shared-types';
import { DEFAULT_WEIGHTS } from '@synorive/shared-types';
import { useApp } from '../lib/store';
import { useSearch, type Preset } from '../lib/useSearch';

/**
 * D1 多指标可调排序 —— 用户原话「功能有更多可选择的指标」
 * ====================================================================
 * 每个滑块旁边都写清楚"调高会怎样"。**没有解释的滑块等于没有** ——
 * 用户不知道该往哪边拉，只会一个都不动。
 *
 * ── 这一轮加了什么 ──────────────────────────────────────
 * ① 两个真的会改变结果的新指标（不是把已有的换个名字）：
 *    · **结果多样性** 治"一个文件夹里二十个沾边的文件霸占整个首屏"
 *    · **忽略短片段** 治"目录行/页眉撞词冲到前排"
 *    两个都在引擎的 `apply_signals` 里真实参与打分，不是摆设 ——
 *    `tests/test_progressive_and_ranking.py` 的 D1①② 逐条量过：
 *    多样性 0→1.5 让前三条从 1 个目录变成 3 个目录；
 *    长度惩罚 0→1.0 让短片段掉 24% 而正文只掉 5.2%（4.6 倍）。
 *
 *    🔴 多样性**第一版是死代码**：写的是"同一份资料的第 2、3 段降权"，
 *       而召回和融合都已按 item 去重，那个分支永远进不去。
 *       是上面那个测试当场抓出来的 —— 滑块拖满，排序一个字不变。
 * ② **预设可以自己存**。以前调好一套只活到关窗口为止 ——
 *    那等于每次都要重调，这些滑块也就白给了。
 * ③ 每个滑块拖动时**立刻重搜**（useSearch.setWeights 自带防抖），
 *    调的效果当场看得见，不用记住"我刚才调了什么"。
 */

const METRICS: { key: keyof RankingWeights; label: string; hint: string; max?: number }[] = [
  { key: 'semantic', label: '语义相关', hint: '调高：理解意思，能搜到同义近义的说法' },
  { key: 'keyword', label: '关键词', hint: '调高：认准原词，适合搜型号、人名、代码符号' },
  { key: 'recency', label: '时间新鲜度', hint: '调高：越新的越靠前' },
  { key: 'sourceTrust', label: '来源权重', hint: '调高：本机文件比网页抓来的更靠前' },
  { key: 'popularity', label: '打开热度', hint: '调高：你常打开的排前面' },
  { key: 'titleBoost', label: '标题命中', hint: '调高：标题里有查询词的排前面' },
  {
    key: 'diversity',
    label: '结果多样性',
    hint: '调高：同一个文件夹（或同一个网站）只露头一两条，一屏里能看到更多不同来源。\n调到 0：允许一个文件夹铺满整屏——已经知道东西在哪个目录里、要一次看全时用。',
    max: 1.5,
  },
  {
    key: 'lengthPenalty',
    label: '忽略短片段',
    hint: '调高：目录行、页眉、单句标题这类很短的片段往后排。\n它们天然含查询词，是"搜出来一堆没用的"最常见的来源。',
    max: 1,
  },
];

/**
 * 时间取向的三个档。值就是 `recency` 权重，和「看最近」那个预设用的是同一根。
 * 分档而不是让用户拧滑块，是因为"我想先看新的"是个**意图**，
 * 而 0~2 的数字是**实现**——中间那层换算不该由用户来做。
 */
const TIME_BIAS: { id: string; label: string; hint: string; value: number }[] = [
  { id: 'relevance', label: '相关优先', hint: '几乎不看时间，只看内容有多对得上', value: 0.05 },
  { id: 'balanced', label: '均衡', hint: '内容为主，同样相关时新的排前面', value: 0.3 },
  { id: 'recent', label: '最近优先', hint: '时间权重拉满，先给你最新的', value: 1.5 },
];

/** 实时说清当前这个数值意味着什么。数字本身对用户没有意义。 */
function describeTimeBias(v: number): string {
  if (v < 0.15) return '现在几乎不看时间——十年前的资料只要更对得上，就排在今天的前面。';
  if (v < 0.7) return '现在以内容为主，两条同样相关时新的排前面。';
  if (v < 1.2) return '现在明显偏向新的，稍微差一点但更新的会被提上来。';
  return '现在时间压过内容——半年前那份更对的可能被压到很后面。找旧资料时记得调回来。';
}

const PRESETS: { id: Preset; label: string; hint: string }[] = [
  {
    id: 'auto',
    label: '自动',
    hint: '按这次搜的内容自己判断该用哪套权重——精确查找/模糊探索/求证/对比各不一样，'
      + '不用你先猜该调哪个预设。判得不合适随时点别的预设或拖滑块覆盖，默认档。',
  },
  { id: 'balanced', label: '均衡', hint: '语义和关键词并重，日常用这个' },
  { id: 'precise', label: '求准', hint: '关键词为主，短片段重罚，适合找确切的东西' },
  { id: 'semantic', label: '求全', hint: '语义为主，多样性拉高（更多来源露头），只记得大概意思时用' },
  { id: 'recent', label: '看最近', hint: '时间权重拉满' },
  { id: 'deep', label: '深读一处', hint: '关掉多样性，把同一个文件夹里所有相关的一次看全' },
];

/** autoIntent 是引擎判完之后返回的英文标识，这里换成用户看得懂的话 */
const INTENT_LABEL: Record<string, string> = {
  precise: '精确查找',
  explore: '模糊探索',
  factcheck: '求证核实',
  compare: '对比分析',
  balanced: '均衡',
};

export function RankingPanel() {
  const weights = useSearch((s) => s.weights);
  const preset = useSearch((s) => s.preset);
  const autoIntent = useSearch((s) => s.autoIntent);
  const setWeights = useSearch((s) => s.setWeights);
  const setPreset = useSearch((s) => s.setPreset);
  const explain = useSearch((s) => s.explain);
  const toggleExplain = useSearch((s) => s.toggleExplain);
  const settings = useApp((s) => s.settings);

  const [naming, setNaming] = useState(false);
  const [draftName, setDraftName] = useState('');

  const saved: SavedRankingPreset[] = settings?.savedPresets ?? [];

  const savePreset = () => {
    const name = draftName.trim();
    if (!name) return;
    // 同名覆盖，不新建第二条 —— 存两个同名预设，用户永远分不清点哪个
    const next = [
      ...saved.filter((p) => p.name !== name),
      { id: `p${Date.now().toString(36)}`, name, weights: { ...weights } },
    ];
    void window.synorive.settings.patch({ savedPresets: next });
    setNaming(false);
    setDraftName('');
  };

  const removePreset = (id: string) => {
    void window.synorive.settings.patch({ savedPresets: saved.filter((p) => p.id !== id) });
  };

  return (
    <aside className="ranking" aria-label="排序指标">
      <div className="ranking__head">
        <SlidersHorizontal size={15} strokeWidth={1.7} />
        <span className="ranking__title">排序指标</span>
        <button
          className="ranking__reset"
          title="恢复默认权重"
          onClick={() => {
            setWeights({ ...DEFAULT_WEIGHTS });
            // setWeights 会把 preset 置成 'custom'，所以恢复默认要在它之后
            // 再设一次 —— 顺序反了的话面板上会显示"自定"而值其实是默认值。
            // 恢复到 'auto' 而不是 'balanced'：那才是这个面板真正的默认档
            setPreset('auto');
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

      {/* 自动档判完了，告诉用户判成了什么——不然"自动"就是个黑箱，
          判错了用户也不知道该往哪个方向手动纠正 */}
      {preset === 'auto' && autoIntent && (
        <p className="ranking__autohint">
          自动识别为：{INTENT_LABEL[autoIntent] ?? autoIntent}
          <span className="ranking__autohint-sub">（不合适就点别的预设或拖滑块）</span>
        </p>
      )}

      {/* 自存的预设。**和内置的分开一行** —— 混在一起的话用户分不清
          哪些是能删的、哪些是删不掉的，而删按钮只挂在能删的那些上 */}
      {saved.length > 0 && (
        <div className="ranking__saved">
          {saved.map((p) => (
            <span key={p.id} className="ranking__savedchip">
              <button
                className="ranking__savedname"
                onClick={() => setWeights({ ...p.weights })}
                title="套用这组权重"
              >
                {p.name}
              </button>
              <button
                className="ranking__savedx"
                onClick={() => removePreset(p.id)}
                title="删掉这个预设"
                aria-label={`删除预设 ${p.name}`}
              >
                <Trash2 size={11} strokeWidth={1.8} />
              </button>
            </span>
          ))}
        </div>
      )}

      {/* 时间取向。
          六个 0~2 的滑块对"我想先看最近的"这个念头来说门槛太高 ——
          用户得先知道是 recency 这一根、再猜该拧到几。这里给三个有名字的档，
          下面那行实时写清"现在是什么效果"，拧完不用去猜。
          它就是 recency 那根滑块的另一个入口，不是新机制。 */}
      <div className="ranking__timebias">
        <span className="slider__label">时间取向</span>
        <div className="ranking__presets">
          {TIME_BIAS.map((t) => {
            const on = Math.abs(weights.recency - t.value) < 0.05;
            return (
              <button
                key={t.id}
                className={`chip${on ? ' chip--on' : ''}`}
                onClick={() => setWeights({ recency: t.value })}
                title={t.hint}
              >
                {t.label}
              </button>
            );
          })}
        </div>
        <p className="ranking__autohint">{describeTimeBias(weights.recency)}</p>
      </div>

      <div className="ranking__list">
        {METRICS.map((m) => (
          <label key={m.key} className="slider" title={m.hint}>
            <span className="slider__label">{m.label}</span>
            <input
              className="slider__input"
              type="range"
              min={0}
              max={m.max ?? 2}
              step={0.1}
              value={weights[m.key]}
              onChange={(e) => setWeights({ [m.key]: Number(e.target.value) })}
              aria-label={`${m.label}权重`}
            />
            <span className="slider__value">{weights[m.key].toFixed(1)}</span>
          </label>
        ))}
      </div>

      {/* 存当前这一组。只在调成"自定"之后才有意义 ——
          原样存一份内置预设是纯噪声，所以那时候按钮置灰并说明原因 */}
      {naming ? (
        <div className="ranking__save">
          <input
            className="textinput"
            autoFocus
            value={draftName}
            placeholder="给这组权重起个名，比如「找代码」"
            onChange={(e) => setDraftName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') savePreset();
              if (e.key === 'Escape') {
                setNaming(false);
                setDraftName('');
              }
            }}
          />
          <button className="btn btn--primary" onClick={savePreset} disabled={!draftName.trim()}>
            <Check size={13} strokeWidth={2} />
            存
          </button>
        </div>
      ) : (
        <button
          className="ranking__addpreset"
          onClick={() => setNaming(true)}
          disabled={preset !== 'custom'}
          title={
            preset === 'custom'
              ? '把当前这组权重存下来，下次一键套用'
              : '先拖动上面任意一个滑块调出你要的组合，再存'
          }
        >
          <Plus size={13} strokeWidth={1.8} />
          存成我的预设
        </button>
      )}

      <label className="ranking__toggle" title="在每条结果下显示它为什么能匹配上">
        <input type="checkbox" checked={explain} onChange={toggleExplain} />
        <span>显示匹配原因</span>
      </label>
    </aside>
  );
}
