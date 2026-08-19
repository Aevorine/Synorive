import { useEffect, useState } from 'react';
import { HelpCircle, Loader2, X } from 'lucide-react';
import { api, type ItemQuestions } from '../lib/api';

/**
 * 「这篇能回答哪些问题」抽屉 —— N6
 * ============================================================
 * **和搜索是相反的方向。** 搜索的前提是你已经知道要问什么；
 * 而一篇四十页的 PDF 躺在库里，难的恰恰是「我该问它什么」。
 * 于是每次都得重新打开、重新翻一遍。
 *
 * 这里把方向反过来：从原文里读出它能回答的问题，摆在你面前，
 * 点一条**直接展开那一段原文**，不用打开整篇。
 *
 * 🔴 **这些问题不是模型生成的**，是从章节标题和定义/结论/数字句式里
 * 读出来的。所以它们一定对应真实存在的段落 —— 界面上必须写明这一点，
 * 否则用户会按"AI 摘要"的预期来看它（那种东西可以编，这个不能）。
 * 代价是覆盖不全：一篇没有明显结构的散文可能一条都读不出来。
 * **那种情况要如实说"读不出来"，不能挤几条含糊的凑数。**
 */

const KIND_LABEL: Record<string, string> = {
  section: '章节',
  define: '定义',
  finding: '结论',
  method: '方法',
  compare: '对比',
  number: '数据',
};

export function QuestionsPanel({
  itemId,
  title,
  onClose,
}: {
  itemId: string;
  title?: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<ItemQuestions | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [openChunk, setOpenChunk] = useState<number | null>(null);
  const [chunkText, setChunkText] = useState<Record<number, string>>({});

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr(null);
    api
      .questions(itemId)
      .then((r) => alive && setData(r))
      .catch((e) => alive && setErr((e as Error).message));
    return () => {
      alive = false;
    };
  }, [itemId]);

  // Esc 关闭。抽屉类的东西不给 Esc，用户会下意识按了发现没反应
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  /**
   * 点一条问题 → 展开对应段落。
   *
   * 这里取的是**整篇正文再按块定位**：库里没有"按 rowid 取单块正文"
   * 这条接口，而为了一个抽屉去加一条接口不划算 —— 一篇文档的正文
   * 本来就要能整取（`/items/{id}/content` 早就有了）。
   * 预览文字已经在问题里带着，展开只是给更多上下文。
   */
  const toggle = async (rowid: number, preview: string) => {
    if (openChunk === rowid) {
      setOpenChunk(null);
      return;
    }
    setOpenChunk(rowid);
    if (chunkText[rowid]) return;
    try {
      const c = await api.content(itemId, 40000);
      const idx = c.text.indexOf(preview.slice(0, 40));
      // 找不到就退回预览本身 —— 正文可能被截断，或者预览来自
      // 已经被规范化过的文本。**不假装定位成功**
      const seg =
        idx >= 0
          ? c.text.slice(Math.max(0, idx - 200), idx + 1200)
          : `${preview}\n\n（在正文里没能精确定位到这一段 —— 可能是正文太长被截断了）`;
      setChunkText((m) => ({ ...m, [rowid]: seg }));
    } catch (e) {
      setChunkText((m) => ({ ...m, [rowid]: `读取正文失败：${(e as Error).message}` }));
    }
  };

  return (
    <aside className="qp" role="dialog" aria-label="这篇能回答哪些问题">
      <header className="qp__head">
        <HelpCircle size={16} aria-hidden />
        <h3>这篇能回答哪些问题</h3>
        <button className="qp__close" onClick={onClose} aria-label="关闭" title="关闭">
          <X size={16} />
        </button>
      </header>

      <p className="qp__doc">{data?.title || title || itemId}</p>

      {!data && !err && (
        <p className="qp__hint">
          <Loader2 size={14} className="spin" /> 正在从原文里读…
        </p>
      )}
      {err && <p className="qp__err">读不出来：{err}</p>}

      {data && (
        <>
          <p className="qp__note">{data.note}</p>

          {data.questions.length === 0 ? (
            <p className="qp__hint">
              这篇共 {data.chunkCount} 块正文，但读不出结构化的问题 ——
              多半是没有明显章节的散文。<strong>这不代表内容有问题</strong>，
              只是这个功能对它不适用。
            </p>
          ) : (
            <ul className="qp__list">
              {data.questions.map((q) => (
                <li key={`${q.chunkRowid}-${q.question}`} className="qp__item">
                  <button className="qp__q" onClick={() => void toggle(q.chunkRowid, q.preview)}>
                    <span className={`qp__kind qp__kind--${q.kind}`}>
                      {KIND_LABEL[q.kind] ?? q.kind}
                    </span>
                    <span className="qp__text">{q.question}</span>
                    <span className="qp__where">
                      {[q.section, q.page != null ? `第 ${q.page} 页` : ''].filter(Boolean).join(' · ')}
                    </span>
                  </button>
                  {openChunk === q.chunkRowid && (
                    <pre className="qp__body">{chunkText[q.chunkRowid] ?? q.preview}</pre>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </aside>
  );
}
