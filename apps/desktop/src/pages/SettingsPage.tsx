import { FolderPlus, X } from 'lucide-react';
import type { AppSettings, Density, EyeComfortLevel, FontScheme } from '@synorive/shared-types';
import { PAGE_TITLES, useApp } from '../lib/store';

/**
 * 设置
 *
 * 每一项都写清楚"调了会怎样"。没有解释的开关等于没有 ——
 * 用户不知道该开还是该关，只会保持默认，那这个开关就是白做的。
 */

const FONT_SCHEMES: { id: FontScheme; label: string; hint: string }[] = [
  { id: 'a', label: '全宋体', hint: '正文和标题都用系统自带的 SimSun 宋体' },
  {
    id: 'b',
    label: '正文宋体 + 标题思源',
    hint: 'SimSun 是点阵老字体，24px 以上会显得又细又硬。大标题换思源宋体，同骨架但质感高一档',
  },
  { id: 'c', label: '全思源宋体', hint: '观感最现代统一；小字号下不如 SimSun 的点阵锐利' },
];

const DENSITIES: { id: Density; label: string; hint: string }[] = [
  { id: 'compact', label: '紧凑', hint: '一屏看更多条，摘要只显示一行' },
  { id: 'standard', label: '标准', hint: '标题 + 两行摘要 + 路径时间' },
  { id: 'comfortable', label: '宽松', hint: '摘要三行，看得最清楚' },
];

const EYE_LEVELS: { id: EyeComfortLevel; label: string; hint: string }[] = [
  { id: 'off', label: '关', hint: '不做色温调节' },
  { id: 'low', label: '弱', hint: '略微偏暖，长时间看不容易累' },
  { id: 'medium', label: '中', hint: '明显偏暖，夜里用' },
  { id: 'high', label: '强', hint: '最暖，纯文字阅读时用；看图会偏色' },
];

export function SettingsPage() {
  const settings = useApp((s) => s.settings);
  if (!settings) return <div className="page"><div className="page__body">加载中…</div></div>;

  const patch = (p: Partial<AppSettings>) => void window.synorive.settings.patch(p);

  const addFolders = async () => {
    const dirs = await window.synorive.sys.pickFolders();
    if (!dirs.length) return;
    const merged = [...new Set([...settings.watchedFolders, ...dirs])];
    patch({ watchedFolders: merged });
  };

  return (
    <div className="page">
      <div className="page__header">
        <h1 className="page__title">{PAGE_TITLES.settings}</h1>
      </div>

      <div className="page__body">
        {/* ── 外观 ────────────────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">外观</h2>

          <Field label="主题" hint="深色模式用暖灰不用纯黑——纯黑在 OLED 上会让浅色文字产生光晕">
            <Segmented
              options={[
                { id: 'system', label: '跟随系统' },
                { id: 'light', label: '浅色' },
                { id: 'dark', label: '深色' },
              ]}
              value={settings.theme}
              onChange={(v) => patch({ theme: v as AppSettings['theme'] })}
            />
          </Field>

          <Field
            label="字体方案"
            hint={FONT_SCHEMES.find((f) => f.id === settings.fontScheme)?.hint ?? ''}
          >
            <Segmented
              options={FONT_SCHEMES.map((f) => ({ id: f.id, label: f.label, title: f.hint }))}
              value={settings.fontScheme}
              onChange={(v) => patch({ fontScheme: v as FontScheme })}
            />
          </Field>

          <Field
            label="列表密度"
            hint={DENSITIES.find((d) => d.id === settings.density)?.hint ?? ''}
          >
            <Segmented
              options={DENSITIES.map((d) => ({ id: d.id, label: d.label, title: d.hint }))}
              value={settings.density}
              onChange={(v) => patch({ density: v as Density })}
            />
          </Field>

          <Field
            label="护眼色温"
            hint={EYE_LEVELS.find((e) => e.id === settings.eyeComfort)?.hint ?? ''}
          >
            <Segmented
              options={EYE_LEVELS.map((e) => ({ id: e.id, label: e.label, title: e.hint }))}
              value={settings.eyeComfort}
              onChange={(v) => patch({ eyeComfort: v as EyeComfortLevel })}
            />
          </Field>
        </section>

        {/* ── 性能 ────────────────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">性能</h2>

          <Field
            label={`分析并发度：${settings.concurrency}`}
            hint="同时处理几个文件。调高不一定更快——本机是 4 物理核，
                  超过之后线程互相抢核反而变慢。分析全程在独立进程里跑，调多高界面都不会卡。"
          >
            <input
              className="slider__input"
              type="range"
              min={1}
              max={16}
              step={1}
              value={settings.concurrency}
              onChange={(e) => patch({ concurrency: Number(e.target.value) })}
              style={{ maxWidth: 320 }}
            />
          </Field>

          <Toggle
            label="启用核显加速"
            hint="用 Intel/AMD 核显跑推理，图片分析可能快 2~3 倍。开关会切换 onnxruntime 的版本，
                  切完要重启引擎。没装 DirectML 版时这个开关不起作用（分析中心里可以装）。"
            checked={settings.enableGpuAcceleration}
            onChange={(v) => patch({ enableGpuAcceleration: v })}
          />
        </section>

        {/* ── 后台行为 ────────────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">后台行为</h2>

          <Toggle
            label="托盘常驻"
            hint="关掉窗口后引擎继续在后台跑。目录监听、剪贴板哨兵、订阅通知都需要它。
                  关掉的话这三项只能在应用打开时工作。"
            checked={settings.runInTray}
            onChange={(v) => patch({ runInTray: v })}
          />

          <Toggle
            label="开机自启"
            hint="开机后静默进托盘，不弹窗口。"
            checked={settings.launchAtLogin}
            onChange={(v) => patch({ launchAtLogin: v })}
          />

          <Toggle
            label="剪贴板哨兵"
            hint="后台监听剪贴板，复制的图片和链接自动静默入库——你什么都不用做，库自己在长。
                  介意的话关掉，需要时手动投喂。"
            checked={settings.clipboardSentinel}
            onChange={(v) => patch({ clipboardSentinel: v })}
          />
        </section>

        {/* ── 索引目录 ────────────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">监听的目录</h2>
          <p className="panel__hint">
            这些目录里的文件变化会被自动索引。新增和修改都认，删除的会从库里移除。
          </p>

          {settings.watchedFolders.length === 0 ? (
            <p className="panel__hint">还没有添加。</p>
          ) : (
            <ul className="pathlist">
              {settings.watchedFolders.map((p) => (
                <li key={p} className="pathlist__item">
                  <span className="pathlist__path">{p}</span>
                  <button
                    className="pathlist__remove"
                    title="不再监听（已索引的内容不会删）"
                    onClick={() =>
                      patch({ watchedFolders: settings.watchedFolders.filter((x) => x !== p) })
                    }
                  >
                    <X size={13} strokeWidth={2} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button className="btn" onClick={addFolders}>
            <FolderPlus size={15} strokeWidth={1.7} /> 添加目录
          </button>
        </section>

        {/* ── 隐私 ────────────────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">隐私</h2>

          <Toggle
            label="人脸检测与聚类"
            hint="把同一个人的照片归到一起（不做身份识别，只是聚类）。
                  这项默认关闭——人脸数据是最敏感的一类，开之前请确认你接受。"
            checked={settings.enableFaceClustering}
            onChange={(v) => patch({ enableFaceClustering: v })}
            danger
          />

          <Toggle
            label="用浏览器登录态抓取网页"
            hint="抓需要登录才能看的页面时复用 Chrome 的 Cookie。
                  默认关闭——这意味着应用能读你的浏览器凭据。"
            checked={settings.enableAuthenticatedFetch}
            onChange={(v) => patch({ enableAuthenticatedFetch: v })}
            danger
          />

          <Field label="数据位置" hint="索引库和模型都在这里。整个目录拷走就是完整备份。">
            <div className="pathlist">
              <div className="pathlist__item">
                <span className="pathlist__path">{settings.dataDir}</span>
                <button
                  className="pathlist__remove"
                  title="在资源管理器中打开"
                  onClick={() => void window.synorive.sys.reveal(settings.dataDir)}
                >
                  <FolderPlus size={13} strokeWidth={2} />
                </button>
              </div>
            </div>
          </Field>
        </section>

        {/* ── 云端 ────────────────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">云端增强（可选）</h2>
          <p className="panel__hint">
            向量化、OCR、语音转写全部在本机离线完成，永远不上云。
            只有「图片详细描述」「视频剧情摘要」「复杂问答」这类深度理解才会调云端，
            而且必须你在这里明确打开。每个目录还可以单独设「禁止上云」。
          </p>
          <Toggle
            label="启用云端增强"
            hint="打开后会出现接口配置。密钥存在系统凭据管理器里，不写进配置文件。"
            checked={settings.cloud.enabled}
            onChange={(v) => patch({ cloud: { ...settings.cloud, enabled: v } })}
          />
          {settings.cloud.enabled && (
            <p className="panel__hint">
              接口配置界面还在做（五期）。当前状态：
              {settings.cloud.provider === 'none' ? '未接入任何厂商' : settings.cloud.provider}。
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

// ── 小组件 ──────────────────────────────────────────────────

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <div className="field__head">
        <span className="field__label">{label}</span>
      </div>
      {hint && <p className="field__hint">{hint}</p>}
      <div className="field__control">{children}</div>
    </div>
  );
}

function Segmented({
  options,
  value,
  onChange,
}: {
  options: { id: string; label: string; title?: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="segmented">
      {options.map((o) => (
        <button
          key={o.id}
          className={`segmented__btn${value === o.id ? ' segmented__btn--on' : ''}`}
          onClick={() => onChange(o.id)}
          title={o.title}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
  danger,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  danger?: boolean;
}) {
  return (
    <label className={`toggle${danger ? ' toggle--danger' : ''}`}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle__body">
        <span className="toggle__label">{label}</span>
        <span className="toggle__hint">{hint}</span>
      </span>
    </label>
  );
}
