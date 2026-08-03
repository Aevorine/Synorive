import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Cpu, Loader2, RefreshCw } from 'lucide-react';
import { labApi, type ModelStatus } from '../lib/labApi';

/**
 * E15 —— 模型热插拔
 * ============================================================
 * 换执行器（CPU ↔ 核显）**不用重启引擎**了。
 *
 * 🔴 **能热换的只有「同一个模型换执行器」和「精排模型」。**
 * 文本向量模型不能在线换成另一个模型：库里几十万条向量都是旧模型算的，
 * 换了之后新查询的向量和旧向量根本不在同一个空间里 ——
 * **搜索不会报错**，只会开始返回一堆不相干的东西。
 * 这块界面把这条限制明写出来，因为它是用户**绝对推理不出来**的：
 * 从操作上看"换个模型"和"换个执行器"长得一模一样。
 *
 * 🔴 **「已安装」和「已加载」分开显示。** 模型是用到才加载的，
 * 「装了但没加载」是正常状态。合成一个字段的话，用户装完看到"未加载"
 * 会以为装失败了，跑去重装一遍。
 */

export function ModelPanel({ preferGpu }: { preferGpu: boolean }) {
  const [st, setSt] = useState<ModelStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await labApi.modelStatus());
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const reload = async () => {
    setBusy(true);
    setErr(null);
    setNote(null);
    try {
      const r = await labApi.reloadModels(preferGpu);
      setSt(r.status);
      setNote(`${r.changed.join('；')}。${r.note}`);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (err) {
    return (
      <p className="mp__err">
        <AlertTriangle size={14} aria-hidden /> 读不到模型状态：{err}
      </p>
    );
  }
  if (!st) return <p className="mp__hint">正在读模型状态…</p>;

  /** 引擎实际生效的偏好和设置页勾的不一致 = 还没热重载 */
  const stale = st.preferGpu !== preferGpu;

  return (
    <div className="mp">
      {/* 两个模型的字段不完全一样（只有向量器有 provider/dim），
          所以分开渲染而不是 map 一个异构数组 —— 合起来 map 会让
          `m.provider` 的类型退化成 unknown，然后被迫到处 as */}
      <ul className="mp__list">
        <ModelRow m={st.textEmbedder} provider={st.textEmbedder.provider} />
        <ModelRow m={st.reranker} />
      </ul>

      <p className="mp__hint">{st.note}</p>
      {/* 🔴 这句是这块界面最要紧的一句：它解释的是一个
          「换了不报错、只是结果开始变差」的陷阱 */}
      <p className="mp__warn">
        <AlertTriangle size={13} aria-hidden /> {st.textEmbedder.why}
      </p>

      <div className="mp__actions">
        <button type="button" className="btn" onClick={() => void reload()} disabled={busy}>
          {busy ? (
            <Loader2 size={14} className="spin" aria-hidden />
          ) : (
            <RefreshCw size={14} aria-hidden />
          )}
          {stale ? `按新设置重载（切到${preferGpu ? '核显' : 'CPU'}）` : '重新加载模型'}
        </button>
        <span className="mp__hint">
          引擎当前用的是 <b>{st.preferGpu ? '核显优先' : 'CPU'}</b>
          {stale && '，和上面的开关不一致 —— 点一下才生效，不用重启'}
        </span>
      </div>

      {note && <p className="mp__ok">{note}</p>}
    </div>
  );
}

function ModelRow({
  m,
  provider,
}: {
  m: { id: string; installed: boolean; loaded: boolean; hotSwappable: boolean };
  provider?: string | null;
}) {
  return (
    <li className="mp__row">
      <span className="mp__name">
        <Cpu size={13} aria-hidden /> {m.id}
      </span>
      <span className={`mp__tag${m.installed ? ' mp__tag--on' : ''}`}>
        {m.installed ? '已安装' : '未安装'}
      </span>
      {/* 🔴 未加载不是错误，所以用中性色不用警告色 */}
      <span className="mp__tag">{m.loaded ? '已加载' : '未加载（用到才加载）'}</span>
      {provider && <span className="mp__tag">{provider}</span>}
      {!m.hotSwappable && <span className="mp__tag mp__tag--warn">不能在线换模型</span>}
    </li>
  );
}
