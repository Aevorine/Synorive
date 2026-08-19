import { useEffect, useState } from 'react';
import { CheckCircle2, FolderPlus, Loader2, Trash2, XCircle, X } from 'lucide-react';
import type {
  AppSettings,
  CloudConfig,
  Density,
  EyeComfortLevel,
  FontScheme,
  LibraryEntry,
} from '@synorive/shared-types';
import { PrivacyFence } from '../components/PrivacyFence';
import { HotkeyReport } from '../components/HotkeyReport';
import { ModelPanel } from '../components/ModelPanel';
import { PerfPanel } from '../components/PerfPanel';
import { SyncPanel } from '../components/SyncPanel';
import { UpdatePanel } from '../components/UpdatePanel';
import { useApp } from '../lib/store';
import { clearQueryHistory, historySize } from '../lib/queryHistory';

const CLOUD_PROVIDERS: { id: CloudConfig['provider']; label: string; hint: string }[] = [
  { id: 'none', label: '不用', hint: '右栏生成版简报不可用，左栏摘录版不受影响' },
  {
    id: 'openai-compatible',
    label: 'OpenAI 兼容',
    hint: '官方 OpenAI，或任何兼容 /chat/completions 协议的端点（国内中转、自建 vLLM/Ollama 等）',
  },
  { id: 'anthropic', label: 'Claude 原生', hint: 'Anthropic 官方 /v1/messages 协议' },
];

const DEFAULT_BASE_URL: Record<string, string> = {
  'openai-compatible': 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
};

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
  { id: 'off', label: '关', hint: '不做色温调节。想要暖色优先选上面的「纸感」主题——那个不花性能' },
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
      <div className="page__body">
        {/* ── 外观 ────────────────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">外观</h2>

          <Field
            label="主题"
            hint="深色用暖灰不用纯黑（纯黑在 OLED 上会让浅色文字产生光晕）。
                  「纸感」是纸黄底 + 棕墨字，长时间读文字最省眼——
                  它是一套独立配色，不是加滤镜，所以不影响滚动流畅度。"
          >
            <Segmented
              options={[
                { id: 'system', label: '跟随系统' },
                { id: 'light', label: '浅色' },
                { id: 'dark', label: '深色' },
                { id: 'paper', label: '纸感', title: '纸黄底 + 棕墨字，长时间阅读用' },
              ]}
              value={settings.theme}
              onChange={(v) => patch({ theme: v as AppSettings['theme'] })}
            />
          </Field>

          <Field
            label="打开软件先看哪一页"
            hint="「今日」会把到期的订阅、刚进库的内容、没结的研究一起摆出来，
                  打开就有东西看；「搜索」直接进大输入区。"
          >
            <Segmented
              options={[
                { id: 'today', label: '今日', title: '有什么新东西、有什么没读完' },
                { id: 'search', label: '搜索', title: '直接进大输入区' },
              ]}
              value={settings.startPage}
              onChange={(v) => patch({ startPage: v as AppSettings['startPage'] })}
            />
          </Field>

          <Field
            label="输入框默认干什么"
            hint="「问一句」回一段带出处的答案（按 Enter 才发，打字过程中不搜）；
                  「找东西」回结果列表（边打边搜）。两者随时一键切换，输入的字不会丢。"
          >
            <Segmented
              options={[
                { id: 'ask', label: '问一句', title: '回一段带出处的答案' },
                { id: 'find', label: '找东西', title: '回一个结果列表' },
              ]}
              value={settings.defaultInputMode}
              onChange={(v) => patch({ defaultInputMode: v as AppSettings['defaultInputMode'] })}
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

          {/* ⚠️ 这一档是**整层滤镜**，开着会让浏览器把整个页面提成独立合成层，
              滚动时每帧重做一遍。所以提示里明写"要暖色优先用纸感主题"——
              不写的话用户会以为这两条路等价，然后为一点暖色付掉滚动流畅度 */}
          <Field
            label="护眼色温（在主题之上再叠一层暖色）"
            hint={`${EYE_LEVELS.find((e) => e.id === settings.eyeComfort)?.hint ?? ''}${
              settings.eyeComfort !== 'off'
                ? '　⚠️ 这一层是整页滤镜，长列表滚动会略微变涩；只想要暖色的话「纸感」主题零开销。'
                : ''
            }`}
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
            hint="用 Intel/AMD 核显跑推理，图片分析可能快 2~3 倍。没装 DirectML 版 onnxruntime 时
                  拿不到核显会自动退回 CPU（不报错）。改完点下面的「重载」当场生效，不用重启引擎。"
            checked={settings.enableGpuAcceleration}
            onChange={(v) => patch({ enableGpuAcceleration: v })}
          />

          <Toggle
            label="把重活丢到后台线程"
            hint="搜索结果高亮、文本比对、图谱布局这些计算放进 Worker，
                  界面线程只管画画面——长列表滚动和打字不会再被卡住。
                  只有在怀疑 Worker 本身出问题时才关掉它排查。"
            checked={settings.offloadHeavyWork}
            onChange={(v) => patch({ offloadHeavyWork: v })}
          />

          {/* E15 模型热插拔。放在核显开关正下方 ——
              它就是那个开关的「立即生效」按钮，隔远了没人会去点 */}
          <ModelPanel preferGpu={settings.enableGpuAcceleration} />
        </section>

        {/* ── C6 性能看板 ──────────────────────────────
            紧跟在「性能」后面：上面那几个开关是"调什么"，
            这里是"调完到底有没有变快"。隔开的话没人会把两者对上 */}
        <section className="panel">
          <h2 className="panel__title">跑得多快（实测）</h2>
          <p className="panel__hint">
            八项技术指标在你平时用的过程中自动采样。
            <strong>没采到样本的显示「还没测」，不会拿 0 冒充"很快"</strong>；
            样本不够的也不给达标结论。
          </p>
          <PerfPanel />
        </section>

        {/* ── U 组 应用更新 ───────────────────────────────
            放在第三位（外观、性能之后）：用户来设置页找"我是哪个版本 /
            有没有新版"的频率，远高于往下那些一次配好就不再动的开关。 */}
        <section className="panel">
          <h2 className="panel__title">应用更新</h2>
          <p className="panel__hint">
            更新包来自项目的 GitHub Releases。<strong>检查是自动的，下载和安装永远要你点</strong>——
            不会在你干活的时候自己下东西或者重启应用。
          </p>

          <UpdatePanel />

          <Toggle
            label="启动后自动检查一次更新"
            hint="只发一个「最新版本号是多少」的请求，不含任何你的内容，也不受「联网搜索总闸」管。
                  关掉之后一个字节都不会发，要更新就自己点上面的「检查更新」。"
            checked={settings.autoCheckUpdate ?? true}
            onChange={(v) => patch({ autoCheckUpdate: v })}
          />
        </section>

        {/* ── 后台行为 ────────────────────────────────── */}
        {/* F7：全局快捷键的**真实**注册结果。
            `globalShortcut.register()` 抢不到时返回 false 而不抛异常 ——
            失败是静默的，所以只能靠这块界面把它喊出来 */}
        {/* E17/6.5：端到端加密同步。放在快捷键前面 ——
            它涉及密钥和隐私，属于用户会主动来找的那一类设置 */}
        <section className="panel">
          <h2 className="panel__title">手机同步（端到端加密）</h2>
          <SyncPanel />
        </section>

        <section className="panel">
          <h2 className="panel__title">全局快捷键</h2>
          <HotkeyReport />
        </section>

        <section className="panel">
          <h2 className="panel__title">后台行为</h2>

          <Toggle
            label="托盘常驻"
            hint="关掉窗口后引擎继续在后台跑。目录监听、剪贴板哨兵、订阅通知都需要它。
                  关掉的话这三项只能在应用打开时工作。"
            checked={settings.runInTray}
            onChange={(v) =>
              // 关掉托盘常驻时，开机自启必须跟着关：静默启动 + 没有托盘图标
              // = 一个既没窗口也没图标的进程，用户根本回不来
              patch(v ? { runInTray: true } : { runInTray: false, launchAtLogin: false })
            }
          />

          <Toggle
            label="开机自启（引擎提前热好）"
            hint="开机后静默进托盘把引擎先起起来，你点图标时直接就能搜，不用等冷启动。
                  托盘图标上悬停能看到引擎状态和这次启动花了多久。
                  打开它会自动把上面的「托盘常驻」也打开——没有托盘图标的静默启动
                  等于一个你找不回来的后台进程。"
            checked={settings.launchAtLogin}
            onChange={(v) =>
              patch(v ? { launchAtLogin: true, runInTray: true } : { launchAtLogin: false })
            }
          />

          <Toggle
            label="结果精排"
            hint="搜完之后再用一个更懂中文的模型把前几条重排一遍，明显更准（实测 100 题里
                  排第一的从 94 提到 97）。它不挡首屏——结果照常秒出，精排完再悄悄换个顺序，
                  大约晚 0.8 秒。要先在分析中心装 279MB 的精排模型。"
            checked={settings.rerankResults}
            onChange={(v) => patch({ rerankResults: v })}
          />

          <Toggle
            label="剪贴板哨兵"
            hint="盯着剪贴板，把你复制过的文字、链接、截图攒在搜索页上方，点一下才存进库。
                  内容只在内存里，关掉哨兵或退出应用就清空——密码、验证码、私钥这类东西
                  会被识别出来直接丢掉，连预览都不留。"
            checked={settings.clipboardSentinel}
            onChange={(v) => patch({ clipboardSentinel: v })}
          />

          <Toggle
            label="自动归档纯链接"
            hint="只对「复制的整段内容就是一个网址」生效，自动抓正文存档，不用点。
                  其他内容一律还是要你点一下——链接里不会夹带密码，别的说不准。"
            checked={settings.clipboardAutoArchiveLinks}
            onChange={(v) => patch({ clipboardAutoArchiveLinks: v })}
            disabled={!settings.clipboardSentinel}
          />

          <Toggle
            label="随手研究浮窗"
            hint="复制文字后右下角浮出三条最相关的，不抢键盘焦点，12 秒自动消失。"
            checked={settings.clipboardPeek ?? false}
            onChange={(v) => patch({ clipboardPeek: v })}
            disabled={!settings.clipboardSentinel}
          />

          <Toggle
            label="浮窗也查网上"
            hint="默认只查本地库（几十毫秒、不出网、不花钱）。打开后浮窗还会联网搜一次，
                  多等几秒，而且会把你复制的这段话作为查询词发给搜索引擎。
                  和「联网搜索总闸」是两道闸，两个都开才生效。"
            checked={settings.clipboardPeekWeb ?? false}
            onChange={(v) => patch({ clipboardPeekWeb: v })}
            disabled={!settings.clipboardPeek || !(settings.allowNetwork ?? true)}
            danger
          />
        </section>

        {/* ── 多库支持 ─────────────────────────────────
            每个库有自己独立的索引数据 + 隐私策略 + 排序预设，互不影响。
            引擎是"一个进程绑一个数据目录"的架构，没法同时管理多个库——
            "切库"实际是换一次 dataDir、重启一次引擎子进程，不是瞬间切换，
            所以切换按钮点下去之前必须先把这件事说清楚。 */}
        <section className="panel">
          <h2 className="panel__title">库</h2>
          <LibraryPanel settings={settings} />
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

        {/* ── 隐私围栏（E12/U9）──────────────────────────
            所有会把数据往外发或往库里记的开关收在这一处。
            它们原本散在这一页的五个不同区块里 —— 想回答
            「这个软件现在会发出去什么」得把整页翻一遍，还未必翻全。
            下面那些单项开关保留着（改起来更细），这一块是总览。 */}
        <section className="panel">
          <PrivacyFence settings={settings} onChange={patch} />
        </section>

        {/* ── 联网搜索（S1 / V 档位 / S3 Key）───────────── */}
        <section className="panel">
          <h2 className="panel__title">联网搜索</h2>

          <Field
            label="每轮派几家引擎"
            hint="按每家最近的成功率和耗时自动排班，再固定留一个「探索位」给最久没试过的那家——
                  没有探索位的话，一家暂时失败的引擎会永远没机会翻身。0 = 全部派出。"
          >
            <input
              type="range"
              min={0}
              max={8}
              value={settings.webLineupSize ?? 0}
              onChange={(e) => patch({ webLineupSize: Number(e.target.value) })}
            />
            <span className="field__value">
              {(settings.webLineupSize ?? 0) === 0 ? '全部派出' : `${settings.webLineupSize} 家 + 探索位`}
            </span>
          </Field>

          <Field
            label="核查力度"
            hint="决定要不要主动去找反驳材料。越深越准，也越慢。"
          >
            <select
              value={settings.verifyLevel ?? 'counter'}
              onChange={(e) =>
                patch({ verifyLevel: e.target.value as 'annotate' | 'counter' | 'claim' })
              }
            >
              <option value="annotate">只标注 —— 零延迟，不额外出网</option>
              <option value="counter">反向检索 —— +1~2 秒，主动搜辟谣/溯源/撤稿（推荐）</option>
              <option value="claim">逐句核查 —— 慢很多，每条断言单独搜一轮</option>
            </select>
          </Field>

          <Field
            label="自建 SearXNG 地址"
            hint="实测 Google/DuckDuckGo/Yandex 直连全被挡、公共 SearXNG 实例全部 429。
                  自建一个是免费拿到这些引擎结果的唯一现实路径。
                  跑 node scripts/setup-searxng.mjs 看它打算怎么装（默认干跑，加 --apply 才真装）。"
          >
            <input
              type="text"
              value={settings.webEndpoints?.searxng ?? ''}
              placeholder="http://127.0.0.1:8888"
              onChange={(e) =>
                patch({
                  webEndpoints: { ...(settings.webEndpoints ?? {}), searxng: e.target.value },
                })
              }
            />
          </Field>

          <EngineKeys />
        </section>

        {/* ── 隐私（单项细调）─────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">隐私（单项）</h2>

          {/* 检索词比文件列表更能反映一个人在想什么。默认开是为了好用，
              但"我不想留痕"必须一次点击就能做到，而且要立刻生效。 */}
          <QueryHistoryControl />

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

          <Toggle
            label="投喂目录时自动跳过敏感文件"
            hint="投喂整个文件夹时，.env、私钥、credentials.json 这类看起来像密钥/
                  凭据的文件默认不会被索引（不影响其它正常文件）。默认开启——
                  关掉之后这类文件会像普通文档一样被搜索库收录。"
            checked={settings.sensitiveGuardEnabled}
            onChange={(v) => patch({ sensitiveGuardEnabled: v })}
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
            这里配的是研究工作台「右栏生成版简报」专用的通道——
            把左栏的原文摘录改写得更通顺，且每句仍挂着真实出处链接，不会凭空编来源。
            不配也完全能用，左栏摘录版不需要它。
          </p>
          <Toggle
            label="启用云端增强"
            hint="打开后下面会出现通道配置。密钥经系统凭据管理器加密存放，不写进配置文件。"
            checked={settings.cloud.enabled}
            onChange={(v) => patch({ cloud: { ...settings.cloud, enabled: v } })}
          />
          {settings.cloud.enabled && <CloudProviderConfig settings={settings} patch={patch} />}
        </section>

        {/* ── 安卓配对（A16） ──────────────────────────── */}
        <section className="panel">
          <h2 className="panel__title">安卓配对</h2>
          <p className="panel__hint">
            手机上的 Synorive 精简客户端通过局域网连这台机器：搜索、投稿、以图搜图
            都转发给桌面端这台引擎处理，手机自己只存一份最近结果的轻量缓存。
          </p>
          <Toggle
            label="允许局域网设备连接"
            hint="打开后引擎从只听本机（127.0.0.1）改成监听局域网（0.0.0.0）——
                  同一局域网内的其他设备能看到这台机器在跑这个服务。默认关。
                  没有下面的配对令牌，光知道地址也连不进来。"
            checked={settings.lanPairingEnabled}
            onChange={(v) => patch({ lanPairingEnabled: v })}
            danger
          />
          {settings.lanPairingEnabled && <AndroidPairingInfo settings={settings} patch={patch} />}
        </section>
      </div>
    </div>
  );
}

/**
 * 库：多个互相隔离的索引库，各自的监听目录/隐私策略/排序预设都不共享，
 * 只共享模型文件（体积以 GB 计，没有谁想每个库拷一份）。
 *
 * 🔴 「切换」不是一次点击就瞬间完成的操作——引擎是"一个进程绑一个数据目录"
 * 的架构，切库落地成"换个 dataDir、重启一次引擎子进程"，实测要几秒钟，
 * 期间当前搜索状态会清空。这不符合直觉，必须在点下去之前说清楚，
 * 而不是让用户自己发现"怎么点一下东西全没了"。
 */
function LibraryPanel({ settings }: { settings: AppSettings }) {
  // 不维护一份本地库列表快照——`settings` 这个 prop 本身就是响应式的
  // （主进程每次改完 libraries/activeLibraryId 都会广播 settings:changed，
  // App.tsx 订阅后整棵树重渲染），自己再存一份容易和它对不上
  const libraries = settings.libraries;

  const [busyId, setBusyId] = useState('');
  const [creating, setCreating] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [error, setError] = useState('');

  const switchTo = async (lib: LibraryEntry) => {
    if (
      !window.confirm(
        `切换到「${lib.name}」？\n\n` +
          '引擎子进程要重启才能换到这个库的数据，大约几秒钟，' +
          '期间当前的搜索结果和输入框内容会清空。',
      )
    ) {
      return;
    }
    setBusyId(lib.id);
    setError('');
    const r = await window.synorive.library.switchTo(lib.id);
    if (!r.ok) setError(r.error ?? '切换失败');
    setBusyId('');
  };

  const startRename = (lib: LibraryEntry) => {
    setRenamingId(lib.id);
    setRenameDraft(lib.name);
    setError('');
  };

  const commitRename = async (id: string) => {
    const name = renameDraft.trim();
    if (!name) {
      setRenamingId(null);
      return;
    }
    setBusyId(id);
    await window.synorive.library.rename(id, name);
    setRenamingId(null);
    setBusyId('');
  };

  const remove = async (lib: LibraryEntry) => {
    if (
      !window.confirm(
        `移除「${lib.name}」？\n\n只是不再管理这个库——硬盘上 ${lib.dataDir} 里的数据不会被删除，以后仍能手动找到。`,
      )
    ) {
      return;
    }
    setBusyId(lib.id);
    setError('');
    const r = await window.synorive.library.remove(lib.id);
    if (!r.ok) setError(r.error ?? '移除失败');
    setBusyId('');
  };

  const createWithFolder = async () => {
    const name = nameDraft.trim();
    if (!name) {
      setError('先填个库名');
      return;
    }
    const dirs = await window.synorive.sys.pickFolders();
    if (!dirs.length) return;
    setBusyId('__create__');
    setError('');
    await window.synorive.library.create(name, dirs[0]);
    setNameDraft('');
    setCreating(false);
    setBusyId('');
  };

  const createAuto = async () => {
    const name = nameDraft.trim();
    if (!name) {
      setError('先填个库名');
      return;
    }
    setBusyId('__create__');
    setError('');
    await window.synorive.library.create(name);
    setNameDraft('');
    setCreating(false);
    setBusyId('');
  };

  return (
    <>
      <p className="panel__hint">
        每个库有自己独立的监听目录、隐私开关（联网/图片描述/人脸聚类等）和排序预设，
        互不影响；模型文件是全部库共享的一份，不会每个库拷贝一份。
        新建库默认不会自动切换过去，确认要用了再手动切。
      </p>

      <ul className="pathlist libpanel__list">
        {libraries.map((lib) => {
          const active = lib.id === settings.activeLibraryId;
          const busy = busyId === lib.id;
          return (
            <li key={lib.id} className="pathlist__item libpanel__item">
              <div className="libpanel__main">
                {renamingId === lib.id ? (
                  <div className="libpanel__renamerow">
                    <input
                      className="textinput libpanel__nameinput"
                      value={renameDraft}
                      autoFocus
                      onChange={(e) => setRenameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') void commitRename(lib.id);
                        if (e.key === 'Escape') setRenamingId(null);
                      }}
                    />
                    <button className="btn btn--sm" disabled={busy} onClick={() => void commitRename(lib.id)}>
                      确定
                    </button>
                    <button className="btn btn--sm" onClick={() => setRenamingId(null)}>
                      取消
                    </button>
                  </div>
                ) : (
                  <>
                    <span className="libpanel__name">{lib.name}</span>
                    {active && <span className="badge badge--time">当前使用</span>}
                  </>
                )}
              </div>
              <span className="pathlist__path">{lib.dataDir}</span>
              {renamingId !== lib.id && (
                <div className="libpanel__actions">
                  {!active && (
                    <button className="btn btn--sm" disabled={busy} onClick={() => void switchTo(lib)}>
                      {busy ? <Loader2 size={13} className="spin" strokeWidth={2} /> : '切换'}
                    </button>
                  )}
                  <button className="btn btn--sm" disabled={busy} onClick={() => startRename(lib)}>
                    重命名
                  </button>
                  <button
                    className="btn btn--sm"
                    disabled={busy || active}
                    title={active ? '不能移除当前激活的库，先切到别的库再移除' : '只从列表移除，不删硬盘上的数据'}
                    onClick={() => void remove(lib)}
                  >
                    移除
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {error && <p className="libpanel__error">{error}</p>}

      {creating ? (
        <div className="libpanel__create">
          <input
            className="textinput"
            placeholder="库名，比如「工作」"
            value={nameDraft}
            autoFocus
            onChange={(e) => setNameDraft(e.target.value)}
          />
          <div className="panel__row">
            <button className="btn btn--sm" disabled={busyId === '__create__'} onClick={() => void createWithFolder()}>
              选择目录并新建
            </button>
            <button className="btn btn--sm" disabled={busyId === '__create__'} onClick={() => void createAuto()}>
              自动生成目录并新建
            </button>
            <button
              className="btn btn--sm"
              onClick={() => {
                setCreating(false);
                setError('');
              }}
            >
              取消
            </button>
          </div>
        </div>
      ) : (
        <button className="btn" onClick={() => setCreating(true)}>
          <FolderPlus size={15} strokeWidth={1.7} /> 新建库
        </button>
      )}
    </>
  );
}

/** 配对面板：地址 + 端口 + 令牌，手机端"手动配对"界面照着填这三样。 */
function AndroidPairingInfo({
  settings,
  patch,
}: {
  settings: AppSettings;
  patch: (p: Partial<AppSettings>) => void;
}) {
  const engine = useApp((s) => s.engine);
  const [addrs, setAddrs] = useState<string[]>([]);

  useEffect(() => {
    void window.synorive.sys.getLanAddresses().then(setAddrs);
  }, []);

  const port = engine?.port;
  const regenerateToken = () => {
    // 32 个十六进制字符的新令牌——旧手机端会立刻配对失败，得重新填一遍，
    // 这是有意的："怀疑令牌泄露了就点一下，逼所有旧连接失效"
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    const token = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
    patch({ pairingToken: token });
  };

  return (
    <div className="pairing-info">
      <Field label="地址" hint="手机端「配对设置」里填这几个之一——具体哪个是本机地址，看手机连的是哪个 Wi-Fi/网段。">
        {addrs.length === 0 ? (
          <p className="field__hint">没探测到局域网 IPv4 地址——检查一下这台机器是不是连着 Wi-Fi/网线。</p>
        ) : (
          <ul className="pairing-info__addrs">
            {addrs.map((a) => (
              <li key={a}>
                <code>{a}{port ? `:${port}` : ''}</code>
              </li>
            ))}
          </ul>
        )}
      </Field>

      <Field label="配对令牌" hint="手机端「配对设置」里的令牌栏填这个，一字不差（区分大小写）。">
        <div className="pairing-info__token">
          <code>{settings.pairingToken}</code>
          <button className="btn btn--sm" onClick={regenerateToken} title="旧令牌立刻失效，所有已配对的手机要重新填">
            换一个
          </button>
        </div>
      </Field>

      {!port && (
        <p className="field__hint">引擎还没就绪，端口拿不到——等状态栏显示"就绪"再回来看这里。</p>
      )}
    </div>
  );
}

/** 独立成组件是因为它自己管一份"还没保存"的草稿状态（密钥输入框不能受控成明文回显）。 */
function CloudProviderConfig({
  settings,
  patch,
}: {
  settings: AppSettings;
  patch: (p: Partial<AppSettings>) => void;
}) {
  const cloud = settings.cloud;
  const [hasKey, setHasKey] = useState(false);
  const [keyDraft, setKeyDraft] = useState('');
  const [testState, setTestState] = useState<
    { kind: 'idle' } | { kind: 'testing' } | { kind: 'ok'; reply?: string } | { kind: 'error'; msg: string }
  >({ kind: 'idle' });

  useEffect(() => {
    void window.synorive.cloud.hasKey().then(setHasKey);
  }, []);

  const patchCloud = (p: Partial<CloudConfig>) => patch({ cloud: { ...cloud, ...p } });

  const onProviderChange = (provider: CloudConfig['provider']) => {
    patchCloud({
      provider,
      baseUrl: cloud.baseUrl || DEFAULT_BASE_URL[provider] || '',
    });
  };

  const saveKey = async () => {
    if (!keyDraft.trim()) return;
    const ok = await window.synorive.cloud.setKey(keyDraft.trim());
    if (ok) {
      setHasKey(true);
      setKeyDraft('');
    } else {
      setTestState({
        kind: 'error',
        msg: '这台机器的系统加密不可用，Key 没能安全存下来，已放弃保存（不会退化成明文存储）',
      });
    }
  };

  const clearKey = async () => {
    await window.synorive.cloud.clearKey();
    setHasKey(false);
  };

  const test = async () => {
    setTestState({ kind: 'testing' });
    const r = await window.synorive.cloud.test({
      provider: cloud.provider,
      baseUrl: cloud.baseUrl || DEFAULT_BASE_URL[cloud.provider] || '',
      chatModel: cloud.chatModel || '',
      // 输入框里还没保存的草稿优先用来测；已保存过的话传空串，
      // 主进程那边测试逻辑会退回去用已存的 Key（见 index.ts 的 cloud:test 处理）
      apiKey: keyDraft.trim(),
    });
    setTestState(r.ok ? { kind: 'ok', reply: r.reply } : { kind: 'error', msg: r.error ?? '未知错误' });
  };

  return (
    <div className="cloudcfg">
      <Field label="通道" hint={CLOUD_PROVIDERS.find((p) => p.id === cloud.provider)?.hint ?? ''}>
        <Segmented
          options={CLOUD_PROVIDERS.map((p) => ({ id: p.id, label: p.label, title: p.hint }))}
          value={cloud.provider}
          onChange={(v) => onProviderChange(v as CloudConfig['provider'])}
        />
      </Field>

      {cloud.provider !== 'none' && (
        <>
          <Field label="接口地址" hint="默认已经填好官方地址，只有走中转/自建端点时才需要改">
            <input
              className="textinput"
              value={cloud.baseUrl ?? ''}
              placeholder={DEFAULT_BASE_URL[cloud.provider]}
              onChange={(e) => patchCloud({ baseUrl: e.target.value })}
            />
          </Field>

          <Field label="模型名" hint="填服务商那边定义的模型标识，比如 gpt-4o-mini、claude-opus-5">
            <input
              className="textinput"
              value={cloud.chatModel ?? ''}
              placeholder="例如 gpt-4o-mini"
              onChange={(e) => patchCloud({ chatModel: e.target.value })}
            />
          </Field>

          <Field
            label="视觉模型（可选）"
            hint="给「图片详细描述」用的模型标识。很多厂商的纯文本模型不能读图，
                  要单独填一个支持视觉输入的型号，比如 gpt-4o、claude-opus-5。不填就沿用上面的模型名试试看。"
          >
            <input
              className="textinput"
              value={cloud.visionModel ?? ''}
              placeholder="例如 gpt-4o"
              onChange={(e) => patchCloud({ visionModel: e.target.value })}
            />
          </Field>

          <Field
            label="API Key"
            hint={
              hasKey
                ? '已保存（加密存放，这里不会显示明文）。要换一把就直接填新的再点保存。'
                : '还没配置。粘贴进来后点保存——保存前可以先点"测试连接"验证填得对不对。'
            }
          >
            <div className="keyrow">
              <input
                className="textinput"
                type="password"
                value={keyDraft}
                placeholder={hasKey ? '●●●●●●●●●●●●' : '粘贴 API Key'}
                onChange={(e) => setKeyDraft(e.target.value)}
                autoComplete="off"
              />
              <button className="btn btn--sm" onClick={() => void saveKey()} disabled={!keyDraft.trim()}>
                保存
              </button>
              {hasKey && (
                <button className="btn btn--sm" onClick={() => void clearKey()}>
                  清除
                </button>
              )}
            </div>
          </Field>

          <div className="panel__row">
            <button
              className="btn btn--sm"
              onClick={() => void test()}
              disabled={testState.kind === 'testing' || (!hasKey && !keyDraft.trim()) || !cloud.chatModel}
            >
              {testState.kind === 'testing' ? (
                <>
                  <Loader2 size={13} className="spin" strokeWidth={2} /> 测试中…
                </>
              ) : (
                '测试连接'
              )}
            </button>
            {testState.kind === 'ok' && (
              <span className="testresult testresult--ok">
                <CheckCircle2 size={14} strokeWidth={2} /> 连通了，模型回复了「{testState.reply}」
              </span>
            )}
            {testState.kind === 'error' && (
              <span className="testresult testresult--error">
                <XCircle size={14} strokeWidth={2} /> {testState.msg}
              </span>
            )}
          </div>

          <Toggle
            label="图片详细描述（C4）"
            hint="投喂图片时顺带把它发给上面配置的视觉模型，生成一段中文描述并入索引——
                  用来搜「有猫的照片」这类光靠 OCR 找不到的图。默认关：这意味着图片内容会被发送到云端。"
            checked={settings.enableImageDescription}
            onChange={(v) => patch({ enableImageDescription: v })}
            danger
          />
        </>
      )}
    </div>
  );
}

// ── 小组件 ──────────────────────────────────────────────────

/**
 * S3 —— 联网搜索引擎的 API Key。
 *
 * 🔴 **这一块以前是缺的**，而引擎侧一直在提示用户"去设置里填一个 Key"。
 * 后端其实早就写好了（`cloud-keys.ts` 的 `saveEngineKeys` 用 safeStorage
 * 加密落盘），只是没有任何东西调用它 —— IPC、preload、界面三层全断。
 * 于是 Semantic Scholar 每次撞 429 都给出一条**用户无法执行的建议**。
 *
 * 和云端 Key 一样：这里只发得出去、查得到"设没设"，**读不回明文**。
 */
function EngineKeys() {
  const [status, setStatus] = useState<Record<string, boolean>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');

  useEffect(() => {
    void window.synorive.engineKeys.status().then(setStatus);
  }, []);

  const save = async (id: string, value: string) => {
    setBusy(id);
    const ok = await window.synorive.engineKeys.set(id, value);
    if (ok) {
      setStatus(await window.synorive.engineKeys.status());
      setDraft((d) => ({ ...d, [id]: '' }));
    }
    setBusy('');
  };

  return (
    <>
      <Field
        label="引擎 API Key"
        hint="填了就存在系统凭据里加密保管，界面上再也读不出明文。
              改完会自动重启一次引擎让它生效（几秒钟，不用手动点）。
              一个都不填也能用，只是下面这几家会受额度限制或干脆用不了。"
      >
        <div className="enginekeys">
          {ENGINE_KEY_SLOTS.map((slot) => (
            <div className="enginekeys__item" key={slot.id}>
              <div className="enginekeys__head">
                <span className="enginekeys__name">{slot.label}</span>
                <span className="enginekeys__state">
                  {status[slot.id] ? '已保存' : '未配置'}
                </span>
              </div>
              <p className="field__hint">{slot.hint}</p>
              <div className="keyrow">
                <input
                  className="textinput"
                  type="password"
                  autoComplete="off"
                  value={draft[slot.id] ?? ''}
                  placeholder={status[slot.id] ? '●●●●●●●●●●●●' : slot.placeholder}
                  onChange={(e) => setDraft((d) => ({ ...d, [slot.id]: e.target.value }))}
                />
                <button
                  className="btn btn--sm"
                  disabled={busy === slot.id || !(draft[slot.id] ?? '').trim()}
                  onClick={() => void save(slot.id, (draft[slot.id] ?? '').trim())}
                >
                  {busy === slot.id ? <Loader2 size={13} className="spin" strokeWidth={2} /> : '保存'}
                </button>
                {status[slot.id] && (
                  <button
                    className="btn btn--sm"
                    disabled={busy === slot.id}
                    onClick={() => void save(slot.id, '')}
                  >
                    清除
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </Field>
    </>
  );
}

/**
 * 能填 Key 的几家，**按"填了能解决什么问题"排序**，不按字母也不按厂商。
 *
 * 排第一的是 Semantic Scholar：它免 Key 也能用，但额度是全世界共享的
 * 1 RPS，撞 429 是常态而不是异常 —— 这是唯一一家"填个免费 Key 就从
 * 基本不可用变成基本不失败"的。其余四家不填就完全用不了。
 */
const ENGINE_KEY_SLOTS: { id: string; label: string; hint: string; placeholder: string }[] = [
  {
    id: 'semanticscholar',
    label: 'Semantic Scholar（免费）',
    hint: '免 Key 也能用，但额度全世界共用，实测几乎每次都撞 429。'
      + '去 semanticscholar.org/product/api 申请一个免费 Key，这一家就基本不会再失败。',
    placeholder: '免费申请，几分钟到邮箱',
  },
  {
    id: 'serper',
    label: 'Serper（拿 Google 结果）',
    hint: '转发 Google 的真实结果，不用浏览器也不会碰到验证码——'
      + '实测 Google 直连和浏览器渲染都会被判异常流量，这是目前最可靠的一条路。有免费额度。',
    placeholder: 'google.serper.dev 申请',
  },
  {
    id: 'brave',
    label: 'Brave Search API',
    hint: '独立索引的官方接口，稳定不被封，有免费额度。',
    placeholder: 'brave.com/search/api 申请',
  },
  {
    id: 'tavily',
    label: 'Tavily（直接带正文）',
    hint: '专为 AI 检索做的接口，结果里直接带正文，深挖时能省掉一次抓取往返。有免费额度。',
    placeholder: 'tavily.com 申请',
  },
  {
    id: 'exa',
    label: 'Exa（语义检索）',
    hint: '按意思检索而不是按关键词，用一句话描述要找什么。关键词搜不到的长尾资料它常能找到。',
    placeholder: 'exa.ai 申请',
  },
];

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
  disabled,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  danger?: boolean;
  /** 依赖别的开关时用它置灰，而不是把整项藏起来 —— 藏起来你会以为没这功能 */
  disabled?: boolean;
}) {
  return (
    <label className={`toggle${danger ? ' toggle--danger' : ''}${disabled ? ' toggle--disabled' : ''}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="toggle__body">
        <span className="toggle__label">{label}</span>
        <span className="toggle__hint">{hint}</span>
      </span>
    </label>
  );
}

/**
 * 搜过什么 —— 看一眼、清掉。
 *
 * 🔴 **清空必须立刻生效，不能只是不显示。** "隐私开关只是把列表藏起来、
 *    数据还躺在磁盘上"是隐私功能里最恶劣的一类失败：用户以为清干净了，
 *    实际什么都没清，而且他永远不会发现。
 */
function QueryHistoryControl() {
  const [n, setN] = useState(() => historySize());
  const [done, setDone] = useState(false);

  return (
    <Field
      label={`搜过什么（本机，共 ${n} 条）`}
      hint="打头几个字会浮出你以前搜过的整句话，少打很多字。只存在这台电脑上，不上传。"
    >
      <div className="panel__row">
        <button
          className="btn btn--sm"
          disabled={n === 0}
          onClick={() => {
            clearQueryHistory();
            setN(historySize());
            setDone(true);
          }}
          title="立刻从本机删掉全部搜索记录"
        >
          <Trash2 size={13} strokeWidth={1.8} />
          清空搜索记录
        </button>
        {done && <span className="syn-t-caption">已清空，共删掉之前那 {0 === n ? '全部' : n} 条</span>}
      </div>
    </Field>
  );
}
