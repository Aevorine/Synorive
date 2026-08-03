import { Globe, Lock, ScanFace, ShieldOff, Cloud, Clipboard, Wifi } from 'lucide-react';
import type { AppSettings } from '@synorive/shared-types';

/**
 * 隐私围栏统一面板 —— E12 / U9
 * ============================================================
 * **要治的病**：这些开关一直都在，但散落在设置页的五个不同区块里。
 * 用户想回答一个很简单的问题 ——「这个软件现在会把我的什么东西发出去？」——
 * 得把整个设置页翻一遍，还未必翻全。
 *
 * 所以这一块把**所有会往外发数据的开关**收到一处，按"发出去的是什么"
 * 分成三档，每一档都写清楚**具体泄露什么**：
 *
 *   查询词  → 我在查什么      （联网搜索）
 *   资料原文 → 我有什么        （云端简报、图片描述）
 *   本机监听 → 我在这台机器上做什么（剪贴板、局域网配对）
 *
 * 🔴 **联网搜索和云端推理必须是两个开关**。前者发出去的是查询词，
 * 后者发出去的是你的资料原文。很多人愿意接受前者而绝不接受后者 ——
 * 合成一个"允许联网"就是逼他们二选一。这条在引擎侧也是同一个设计
 * （`allow_network` 和 `allow_cloud` 分开）。
 */

interface Row {
  key: keyof AppSettings | 'cloudEnabled';
  icon: typeof Globe;
  label: string;
  /** 具体泄露什么 —— 必须写死，不能笼统说"涉及隐私" */
  leaks: string;
  hint: string;
  danger?: boolean;
}

const GROUPS: { title: string; desc: string; rows: Row[] }[] = [
  {
    title: '会泄露「我在查什么」',
    desc: '发出去的是查询词，不是你的资料。',
    rows: [
      {
        key: 'allowNetwork',
        icon: Globe,
        label: '联网搜索总闸',
        leaks: '你输入的查询词会发给选中的搜索引擎',
        hint: '关掉之后整个研究工作台停用；本地检索完全不受影响，断网照样能搜自己的库',
      },
    ],
  },
  {
    title: '会泄露「我有什么」',
    desc: '发出去的是你自己的资料原文或图片。默认全关。',
    rows: [
      {
        key: 'cloudEnabled',
        icon: Cloud,
        label: '云端生成简报',
        leaks: '把摘录出来的原文片段发给模型厂商',
        hint: '关掉后深挖只出「摘录版」——每句话都逐字来自原文，断网也能用',
        danger: true,
      },
      {
        key: 'enableImageDescription',
        icon: Cloud,
        label: '图片详细描述（C4）',
        leaks: '把你库里的图片上传给云端视觉模型',
        hint: '还要上面那个云端开关也开着才生效',
        danger: true,
      },
      {
        key: 'enableAuthenticatedFetch',
        icon: Lock,
        label: '登录态抓取（C13）',
        leaks: '用你的浏览器登录状态去抓需要登录才能看的页面',
        hint: '抓回来的内容可能含你的账号信息，入库后会被全文检索到',
        danger: true,
      },
    ],
  },
  {
    title: '会记录「我在这台机器上做什么」',
    desc: '数据不出本机，但会被写进可全文检索、且 Claude Code 能通过 MCP 读到的库。',
    rows: [
      {
        key: 'clipboardSentinel',
        icon: Clipboard,
        label: '剪贴板哨兵（E4）',
        leaks: '复制的内容留在内存里最近 20 条',
        hint: '刻意**不自动入库** —— 密码、验证码、私钥都经过剪贴板。点了才存',
      },
      {
        key: 'clipboardAutoArchiveLinks',
        icon: Clipboard,
        label: '自动归档纯链接',
        leaks: '整段内容就是一个网址时自动抓取并入库',
        hint: '只对纯链接生效（链接里不夹带凭据），这是唯一一类能自动落盘还不出事的',
        danger: true,
      },
      {
        key: 'enableFaceClustering',
        icon: ScanFace,
        label: '人脸聚类（C5）',
        leaks: '在本机提取人脸特征并按人分组',
        hint: '不出网，但人脸数据是最敏感的一类，默认关',
        danger: true,
      },
      {
        key: 'lanPairingEnabled',
        icon: Wifi,
        label: '安卓配对（局域网）',
        leaks: '引擎从只听 127.0.0.1 改成监听 0.0.0.0，同网段设备能看到这个服务',
        hint: '非本机请求必须带配对令牌才放行；本机永远不受影响',
        danger: true,
      },
    ],
  },
];

export function PrivacyFence({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (patch: Partial<AppSettings>) => void;
}) {
  const valueOf = (k: Row['key']): boolean =>
    k === 'cloudEnabled' ? !!settings.cloud?.enabled : !!settings[k as keyof AppSettings];

  const toggle = (k: Row['key'], v: boolean) => {
    if (k === 'cloudEnabled') {
      onChange({ cloud: { ...settings.cloud, enabled: v } });
      return;
    }
    onChange({ [k]: v } as Partial<AppSettings>);
  };

  const outbound = GROUPS.flatMap((g) => g.rows).filter((r) => valueOf(r.key)).length;

  return (
    <section className="fence">
      <header className="fence__head">
        <ShieldOff size={16} aria-hidden />
        <h3>隐私围栏</h3>
        <span className="fence__count">
          当前有 <strong>{outbound}</strong> 项已开启
        </span>
      </header>

      <p className="fence__intro">
        这里收齐了<strong>所有会把数据往外发或往库里记的开关</strong>。
        它们原本散在设置页各处 —— 想回答「这个软件现在会发出去什么」，
        得把整页翻一遍。
      </p>

      <button
        className="fence__panic"
        onClick={() =>
          onChange({
            allowNetwork: false,
            cloud: { ...settings.cloud, enabled: false },
            enableImageDescription: false,
            enableAuthenticatedFetch: false,
            clipboardAutoArchiveLinks: false,
            lanPairingEnabled: false,
          })
        }
      >
        一键全断网（本地检索照常可用）
      </button>

      {GROUPS.map((g) => (
        <div className="fence__group" key={g.title}>
          <h4>{g.title}</h4>
          <p className="fence__desc">{g.desc}</p>
          {g.rows.map((r) => {
            const on = valueOf(r.key);
            const Icon = r.icon;
            return (
              <label
                key={String(r.key)}
                className={`fence__row ${on && r.danger ? 'is-hot' : ''}`}
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={(e) => toggle(r.key, e.target.checked)}
                />
                <Icon size={15} aria-hidden />
                <span className="fence__label">{r.label}</span>
                <span className="fence__leak">{on ? r.leaks : '关闭中，不会发生'}</span>
                <span className="fence__hint">{r.hint}</span>
              </label>
            );
          })}
        </div>
      ))}

      <p className="fence__foot">
        被自动折叠掉的搜索结果一律进「已排除」抽屉，随时能看为什么被排除、
        能一键放回 —— <strong>这个软件不会静默丢掉任何东西</strong>。
      </p>
    </section>
  );
}
