import { AlertTriangle, X } from 'lucide-react';
import { useSearch } from '../lib/useSearch';

/**
 * D10 / L3-plus —— 「我把你这句话理解成了什么」
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
 * 点 × 就是把整条指令从查询串里删掉重搜 —— 不做"临时禁用"那种半状态，
 * 那会让搜索框里的文字和实际生效的筛选对不上，是更难查的一类困惑。
 */

export function QueryChips() {
  const parsed = useSearch((s) => s.parsed);
  const query = useSearch((s) => s.query);
  const setQuery = useSearch((s) => s.setQuery);

  if (!parsed || (parsed.filters.length === 0 && parsed.unknown.length === 0)) return null;

  return (
    <div className="qchips" role="status">
      <span className="qchips__lead">这句话被理解成：</span>

      {parsed.filters.map((f) => (
        <span className="qchips__chip" key={f}>
          {f}
        </span>
      ))}

      {parsed.filters.length > 0 && parsed.text !== query.trim() && (
        <span className="qchips__chip qchips__chip--text">
          搜索词：{parsed.text || '（只有筛选，没有关键词）'}
        </span>
      )}

      {parsed.unknown.map((u) => (
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
