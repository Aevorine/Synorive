import { useState } from 'react';
import { Check, Columns2, Copy, FileDown, FileText, Loader2, X } from 'lucide-react';
import { applyRedactions, findSensitive, summarize } from '../lib/redact';
import { compose, composeHtml, type CiteStyle, type ComposeFormat } from '../lib/compose';
import { MAX_SELECTION, useSelection } from '../lib/useSelection';
import { useApp } from '../lib/store';

/**
 * A4 成稿条 / D3 并排对比入口 —— 选中若干条之后浮在底部
 * ====================================================================
 * 只在**真的选中了东西**时出现。常驻一条工具栏的话，
 * 它 99% 的时间在占位置，而剩下 1% 的时间用户已经忘了它能干嘛。
 *
 * 🔴 **「取消」和「清空选择」不是一回事，必须分开。**
 *    合成一个按钮的话，用户想收起这条栏结果把挑了十分钟的选择全丢了 ——
 *    而这个操作没有撤销。所以这里只有「清空」一个按钮且写明它清的是什么。
 */

const FORMATS: { id: ComposeFormat; label: string; ext: string; hint: string }[] = [
  { id: 'markdown', label: 'Markdown', ext: 'md', hint: '带引用锚点，粘到笔记软件/GitHub 直接可用' },
  { id: 'plain', label: '纯文本', ext: 'txt', hint: '没有任何标记，粘到哪都不会乱' },
];

const CITES: { id: CiteStyle; label: string; hint: string }[] = [
  { id: 'gb', label: '国标', hint: 'GB/T 7714 简化形态。没有作者和出版信息时不硬凑，标 [Z]' },
  { id: 'apa', label: 'APA', hint: '作者-年份体例的简化形态' },
  { id: 'plain', label: '就写路径', hint: '不套任何体例，直接写标题和文件位置' },
];

export function ComposeBar({ onCompare }: { onCompare?: (ids: string[]) => void }) {
  const picked = useSelection((s) => s.picked);
  const warnedFull = useSelection((s) => s.warnedFull);
  const clear = useSelection((s) => s.clear);

  const [format, setFormat] = useState<ComposeFormat>('markdown');
  const [cite, setCite] = useState<CiteStyle>('gb');
  const [withSnippets, setWithSnippets] = useState(true);
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  // A5：归在某个项目下时，稿件默认就叫那个项目的名字。
  // 用户十有八九正是在为那个项目整理材料，让他每次重打一遍是没道理的
  const project = useApp((s) => s.activeProjectName);

  if (picked.length === 0) return null;

  const docTitle =
    title.trim() ||
    (project
      ? `${project} · 资料整理 ${new Date().toLocaleDateString('zh-CN')}`
      : `资料整理 ${new Date().toLocaleDateString('zh-CN')}`);
  const opts = { title: docTitle, format, cite, includeSnippets: withSnippets };

  /** 三个动作共用的收尾：给一句话反馈，2.5 秒后自己消失 */
  /** 出稿前是否自动打码。默认开 —— 漏遮一个身份证号的代价比多遮大得多 */
  const [redactOn, setRedactOn] = useState(true);
  /** 上一次出稿遮了什么。必须显示出来，见 guard() 的说明 */
  const [lastRedaction, setLastRedaction] = useState<string | null>(null);

  const finish = (msg: string) => {
    setDone(msg);
    window.setTimeout(() => setDone(null), 2500);
  };

  /**
   * 出稿前打码。
   *
   * 🔴 **三条出口都要走这一道**（复制 / 存文件 / 导 PDF）。只在其中一条上做，
   *    等于没做 —— 用户会从没做的那条把东西发出去，而他以为都保护着。
   * 🔴 **遮了什么必须说出来。** 一个悄悄改写你要发出去的内容的功能，
   *    哪怕改得对也不能接受：他得知道自己发出去的到底是什么。
   */
  const guard = (text: string): string => {
    if (!redactOn) return text;
    const hits = findSensitive(text);
    if (hits.length === 0) return text;
    setLastRedaction(summarize(hits));
    return applyRedactions(text, hits);
  };

  const doCopy = async () => {
    setBusy('copy');
    try {
      await navigator.clipboard.writeText(guard(compose(picked, opts)));
      finish('已复制到剪贴板');
    } catch (e) {
      finish(e instanceof Error ? `复制失败：${e.message}` : '复制失败');
    } finally {
      setBusy(null);
    }
  };

  const doSave = async () => {
    setBusy('save');
    const ext = FORMATS.find((f) => f.id === format)?.ext ?? 'md';
    const r = await window.synorive.doc.saveText(guard(compose(picked, opts)), docTitle, ext);
    setBusy(null);
    // 🔴 `ok:false` 且没有 error = 用户在保存对话框点了取消。
    //    **那是正常操作，不能报成失败** —— 弹一句"保存失败"会让人以为出了问题
    if (r.ok) finish(`已存到 ${r.path}`);
    else if (r.error) finish(`保存失败：${r.error}`);
  };

  const doPdf = async () => {
    setBusy('pdf');
    const r = await window.synorive.doc.exportPdf(guard(composeHtml(picked, opts)), docTitle);
    setBusy(null);
    if (r.ok) finish(`已导出 ${r.path}`);
    else if (r.error) finish(`导出失败：${r.error}`);
  };

  return (
    <div className="compose" role="region" aria-label="已选内容">
      <div className="compose__head">
        <Check size={15} strokeWidth={2} className="compose__tick" />
        <span className="compose__count">
          已选 <b>{picked.length}</b> 条
        </span>
        {warnedFull && (
          <span className="compose__warn">最多 {MAX_SELECTION} 条，再多就不是"挑几条"了</span>
        )}
        <span className="compose__spacer" />
        {done && <span className="compose__done">{done}</span>}
        <button className="compose__x" onClick={clear} title="清空已选的全部内容（不可撤销）">
          <X size={14} strokeWidth={1.9} />
          清空选择
        </button>
      </div>

      {/* 已选清单：**必须能单条移除**。只给"全清"的话，
          挑了 20 条发现有一条不要，就得从头再挑一遍 */}
      <div className="compose__chips">
        {picked.map((h, i) => (
          <span key={h.item.id} className="compose__chip" title={h.item.locator}>
            <span className="compose__chipn">{i + 1}</span>
            <span className="compose__chiptext">{h.item.title || h.item.locator}</span>
            <button
              className="compose__chipx"
              onClick={() => useSelection.getState().remove(h.item.id)}
              aria-label={`从已选里移除 ${h.item.title}`}
            >
              <X size={11} strokeWidth={2} />
            </button>
          </span>
        ))}
      </div>

      <div className="compose__opts">
        <input
          className="textinput compose__title"
          value={title}
          placeholder={docTitle}
          onChange={(e) => setTitle(e.target.value)}
          aria-label="稿件标题"
        />

        <div className="compose__seg" role="group" aria-label="格式">
          {FORMATS.map((f) => (
            <button
              key={f.id}
              className={`chip${format === f.id ? ' chip--on' : ''}`}
              onClick={() => setFormat(f.id)}
              title={f.hint}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="compose__seg" role="group" aria-label="引用体例">
          {CITES.map((c) => (
            <button
              key={c.id}
              className={`chip${cite === c.id ? ' chip--on' : ''}`}
              onClick={() => setCite(c.id)}
              title={c.hint}
            >
              {c.label}
            </button>
          ))}
        </div>

        <label className="compose__check" title="关掉就只出一份「我引用了这些资料」的清单">
          <input
            type="checkbox"
            checked={withSnippets}
            onChange={(e) => setWithSnippets(e.target.checked)}
          />
          <span>带原文摘录</span>
        </label>

        {/* 🔴 默认开。漏遮一个身份证号的代价，比多遮一个订单号大得多 ——
            而多遮的那些，用户看一眼摘要就知道该不该关掉重来 */}
        <label
          className="compose__check"
          title="导出、复制、打 PDF 之前自动遮掉身份证号、手机号、银行卡、密钥这类东西。只影响出稿，不动库里的原文"
        >
          <input
            type="checkbox"
            checked={redactOn}
            onChange={(e) => {
              setRedactOn(e.target.checked);
              setLastRedaction(null);
            }}
          />
          <span>出稿前遮敏感内容</span>
        </label>
      </div>

      {/* 遮了什么必须说出来 —— 悄悄改写用户要发出去的内容是不能接受的 */}
      {lastRedaction && (
        <p className="compose__redacted">
          这次出稿遮掉了：{lastRedaction}。不想遮就关掉上面那个勾再来一次。
        </p>
      )}

      <div className="compose__actions">
        <button className="btn" onClick={() => void doCopy()} disabled={busy !== null}>
          {busy === 'copy' ? <Loader2 size={14} className="spin" /> : <Copy size={14} strokeWidth={1.8} />}
          复制
        </button>
        <button className="btn" onClick={() => void doSave()} disabled={busy !== null}>
          {busy === 'save' ? <Loader2 size={14} className="spin" /> : <FileText size={14} strokeWidth={1.8} />}
          存成文件
        </button>
        <button className="btn btn--primary" onClick={() => void doPdf()} disabled={busy !== null}>
          {busy === 'pdf' ? <Loader2 size={14} className="spin" /> : <FileDown size={14} strokeWidth={1.8} />}
          导出 PDF
        </button>
        {/* D3：并排对比。**2~4 条才给** —— 1 条没得比，5 条以上并排列宽
            会窄到读不了，那时候该用的是上面的成稿而不是对比 */}
        {onCompare && picked.length >= 2 && picked.length <= 4 && (
          <button
            className="btn"
            onClick={() => onCompare(picked.map((h) => h.item.id))}
            title="把选中的这几条并排放，逐段看差异"
          >
            <Columns2 size={14} strokeWidth={1.8} />
            并排对比
          </button>
        )}
      </div>
    </div>
  );
}
