import { useEffect, useState } from 'react';
import { ArrowRight, FolderPlus, Search, ShieldCheck, X } from 'lucide-react';

/**
 * F5 —— 首次使用三步引导
 * ============================================================
 * **只有三步，而且每一步都是「做一件事」不是「读一段话」。**
 *
 * 大多数引导的问题在于它们在介绍功能。用户第一次打开一个软件时
 * 记不住任何介绍 —— 他能记住的只有自己刚做过的动作。所以这三步是：
 *   ① 真的拖一个文件夹进来（不是"你可以拖文件夹进来"）
 *   ② 真的搜一次（用他自己刚加的东西）
 *   ③ 看一眼隐私开关在哪（这是唯一必须提前知道的事）
 *
 * 🔴 **随时能跳过，跳过之后不再出现。** 引导最讨厌的形态是拦在
 * 界面前面不让走。这里 Esc 和右上角的叉都能立刻关掉，
 * 而且**关掉就永久关掉** —— 每次启动都弹一次的引导是纯粹的骚扰。
 *
 * 🔴 **判断"首次"用的是库里有没有内容，不是有没有配置文件。**
 * 配置文件被删了但库里有一万条内容的用户，不该再看一遍引导。
 */

const KEY = 'synorive.onboarded.v1';

export function Onboarding({
  itemCount,
  onAddFolder,
  onGoSearch,
  onGoPrivacy,
}: {
  /** 库里有多少条内容。> 0 就不算首次 */
  itemCount: number | null;
  onAddFolder: () => void;
  onGoSearch: () => void;
  onGoPrivacy: () => void;
}) {
  const [step, setStep] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (itemCount == null) return; // 还没问出来，先不判断
    const done = localStorage.getItem(KEY) === '1';
    setOpen(!done && itemCount === 0);
  }, [itemCount]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  function close(): void {
    localStorage.setItem(KEY, '1');
    setOpen(false);
  }

  if (!open) return null;

  const steps = [
    {
      icon: FolderPlus,
      title: '先给它一点东西',
      body: '拖一个文件夹进来 —— 文档、代码、图片、视频混在一起也行，它会自己分类。索引在后台跑，界面不会卡。',
      action: '选一个文件夹',
      run: onAddFolder,
    },
    {
      icon: Search,
      title: '用你自己的话搜',
      body: '不用记文件名。描述内容就行：「上次那个讲预算的表格」「有猫的那张图」。视频能定位到第几秒。',
      action: '去搜一下',
      run: onGoSearch,
    },
    {
      icon: ShieldCheck,
      title: '这两个开关要知道在哪',
      body: '「联网搜索」和「云端推理」是分开的两个闸：前者泄露你在查什么，后者泄露你有什么。默认都关着，要开自己开。',
      action: '看一眼隐私开关',
      run: onGoPrivacy,
    },
  ];
  // `steps[step]` 在 TS 的 noUncheckedIndexedAccess 下是可能 undefined 的。
  // 兜到第 0 步而不是加 `!` —— 真出现越界时显示第一步，
  // 总好过整个引导白屏而没人知道为什么
  const cur = steps[step] ?? steps[0]!;

  return (
    <div className="syn-onb-mask" role="dialog" aria-modal="true" aria-label="首次使用引导">
      <section className="syn-onb">
        <button type="button" className="syn-onb-x" onClick={close} aria-label="跳过引导" title="跳过引导">
          <X size={16} aria-hidden />
        </button>

        <p className="syn-onb-step">
          第 {step + 1} 步 / 共 3 步
        </p>
        <h2 className="syn-onb-title">
          <cur.icon size={20} aria-hidden /> {cur.title}
        </h2>
        <p className="syn-onb-body">{cur.body}</p>

        <div className="syn-onb-actions">
          <button
            type="button"
            className="syn-onb-primary"
            onClick={() => {
              cur.run();
              if (step < 2) setStep(step + 1);
              else close();
            }}
          >
            {cur.action} <ArrowRight size={14} aria-hidden />
          </button>
          {step < 2 ? (
            <button type="button" className="syn-onb-ghost" onClick={() => setStep(step + 1)}>
              先看下一步
            </button>
          ) : (
            <button type="button" className="syn-onb-ghost" onClick={close}>
              知道了
            </button>
          )}
          <button type="button" className="syn-onb-ghost" onClick={close}>
            跳过，我自己摸索
          </button>
        </div>

        <ol className="syn-onb-dots" aria-hidden>
          {steps.map((s, i) => (
            <li key={s.title} className={i === step ? 'is-on' : ''} />
          ))}
        </ol>
      </section>
    </div>
  );
}
