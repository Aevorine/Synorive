/**
 * 设置项索引 —— C2 命令面板要能搜到**每一个**设置
 * ============================================================
 * 「命令面板覆盖全部设置项」这条要求，最笨也最常见的做法是让面板
 * `import` 设置页再去读它的结构。**这里不能那么做**：`SettingsPage` 是
 * `lazy()` 切出去的分片（见 `App.tsx` 的路由注释），而命令面板在主包里 ——
 * 一 import 就把整个设置页拽回首屏，启动直接变慢。
 *
 * 所以索引单独放在这个只有数据、零依赖的模块里，两边都引它。
 *
 * 🔴 **它会漂。** 设置页加了一个 Field 而这里忘了加，那一条就永远搜不到，
 *    不报错、不告警 —— 和 `pinyinMatch.ts` 文件头说的"手写 py 会被忘"
 *    是同一种病。所以 `SettingsPage` 挂载时会拿这份索引和真实 DOM 对一遍，
 *    对不上的在控制台喊出来（见那边的 `useSettingsFocus`）。
 *    喊在控制台是刻意的：这是给开发看的一致性检查，不该打扰用户。
 */

export interface SettingsEntry {
  /** 和设置页里那个 `<Field label=…>` 一字不差；分区条目则是 `<h2 class="panel__title">` 的文字 */
  label: string;
  /** 归属分区，显示在命令面板的副标题里 */
  section: string;
  /** true = 这条本身就是一个分区标题，不是具体设置项 */
  isSection?: boolean;
  /** 补一句能被搜到的别名/说明（比如「暗色」能搜到「主题」） */
  hint?: string;
}

const SECTIONS = [
  '外观',
  '性能',
  '跑得多快（实测）',
  '应用更新',
  '手机同步（端到端加密）',
  '全局快捷键',
  '后台行为',
  '库',
  '监听的目录',
  '联网搜索',
  '隐私（单项）',
  '云端增强（可选）',
  '安卓配对',
];

const FIELDS: SettingsEntry[] = [
  { label: '主题', section: '外观', hint: '浅色 深色 纸感 跟随系统 暗色 夜间' },
  { label: '打开软件先看哪一页', section: '外观', hint: '启动页 今日 搜索' },
  { label: '输入框默认干什么', section: '外观', hint: '问一句 找东西 默认意图' },
  { label: '字体方案', section: '外观', hint: '宋体 思源宋体 SimSun' },
  { label: '界面缩放', section: '外观', hint: '放大 100% 125% 150% 看不清' },
  { label: '列表密度', section: '外观', hint: '紧凑 标准 宽松' },
  { label: '护眼色温（在主题之上再叠一层暖色）', section: '外观', hint: '暖色 夜间 蓝光' },

  { label: '启用核显加速', section: '性能', hint: 'GPU 显卡' },
  { label: '把重活丢到后台线程', section: '性能', hint: 'Worker 卡顿 掉帧' },

  { label: '启动后自动检查一次更新', section: '应用更新', hint: '升级 新版本' },

  { label: '托盘常驻', section: '后台行为', hint: '关闭到托盘 后台' },
  { label: '开机自启（引擎提前热好）', section: '后台行为', hint: '自动启动 开机' },
  { label: '结果精排', section: '后台行为', hint: 'rerank 重排序' },
  { label: '剪贴板哨兵', section: '后台行为', hint: '复制 监听剪贴板' },
  { label: '自动归档纯链接', section: '后台行为', hint: '网址 URL 入库' },
  { label: '随手研究浮窗', section: '后台行为', hint: 'peek 浮窗' },
  { label: '浮窗也查网上', section: '后台行为', hint: 'peek 联网' },

  { label: '每轮派几家引擎', section: '联网搜索', hint: '排班 并发 lineup' },
  { label: '核查力度', section: '联网搜索', hint: '反驳 verify' },
  { label: '自建 SearXNG 地址', section: '联网搜索', hint: 'searx 自建实例' },
  { label: '引擎 API Key', section: '联网搜索', hint: 'serper brave tavily exa key' },

  { label: '资料库整库加密', section: '隐私（单项）', hint: '口令 密码 加密' },
  { label: '我自己的同义词', section: '隐私（单项）', hint: '黑话 缩写 别名' },
  { label: '库的快照', section: '隐私（单项）', hint: '备份 还原' },
  { label: '一次搜多个库', section: '隐私（单项）', hint: '联邦 多库' },
  { label: '人脸检测与聚类', section: '隐私（单项）', hint: '人脸 相册' },
  { label: '用浏览器登录态抓取网页', section: '隐私（单项）', hint: 'cookie 登录' },
  { label: '投喂目录时自动跳过敏感文件', section: '隐私（单项）', hint: '密钥 隐私围栏' },
  { label: '数据位置', section: '隐私（单项）', hint: '数据目录 备份 搬家 dataDir' },

  { label: '启用云端增强', section: '云端增强（可选）', hint: '大模型 改写 生成' },
  { label: '通道', section: '云端增强（可选）', hint: 'provider openai anthropic' },
  { label: '接口地址', section: '云端增强（可选）', hint: 'baseUrl 中转' },
  { label: '模型名', section: '云端增强（可选）', hint: 'model gpt claude' },
  { label: '视觉模型（可选）', section: '云端增强（可选）', hint: '看图 多模态' },
  { label: 'API Key', section: '云端增强（可选）', hint: '密钥 令牌' },
  { label: '图片详细描述（C4）', section: '云端增强（可选）', hint: '图片描述 caption' },

  { label: '允许局域网设备连接', section: '安卓配对', hint: '手机 同步 局域网' },
  { label: '扫码配对（推荐）', section: '安卓配对', hint: '二维码 手机' },
  { label: '地址', section: '安卓配对', hint: 'IP 局域网地址' },
  { label: '配对令牌', section: '安卓配对', hint: 'token 手机配对' },
];

/** 分区 + 具体设置项，命令面板直接铺开这一整份 */
export const SETTINGS_INDEX: SettingsEntry[] = [
  ...SECTIONS.map((s) => ({ label: s, section: s, isSection: true })),
  ...FIELDS,
];

// ────────────────────────────────────────────────────────────
// 「跳到某一条设置」的交接
// ────────────────────────────────────────────────────────────
// 设置页是 lazy 分片：从命令面板跳过去的那一刻，它**还没挂载**，
// 这时候派事件没人接。所以先把目标存在这里，设置页挂载时自己来取。
// 已经挂载的情况走事件，两条路都要有 —— 只做一条的话，
// "第一次跳没反应、第二次就好了"这种最难查的毛病就出来了。

let pending: string | null = null;

export const SETTINGS_FOCUS_EVENT = 'syn:settings-focus';

export function requestSettingsFocus(label: string): void {
  pending = label;
  window.dispatchEvent(new CustomEvent(SETTINGS_FOCUS_EVENT, { detail: { label } }));
}

/** 取一次就清掉 —— 留着的话切走再切回设置页会莫名其妙又滚一次 */
export function takeSettingsFocus(): string | null {
  const v = pending;
  pending = null;
  return v;
}
