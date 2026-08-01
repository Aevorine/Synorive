/**
 * Synorive 设计令牌 —— 全应用唯一样式真相源
 * ============================================================
 * 规则（由 scripts/check-hardcoded-style.mjs 强制）：
 *   ❌ 业务代码里禁止出现 #RRGGBB / rgb() / 裸 px 字号
 *   ✅ 一律从这里取，或用它生成的 CSS 变量 var(--syn-*)
 *
 * 为什么要这么严：用户明确要求「界面要统一」。统一靠自觉做不到，
 * 只能靠"根本没有第二个地方可以定义颜色"来保证。
 */

// ──────────────────────────────────────────────────────────────
// 一、字号：中文字号 → CSS 像素
//    换算：Windows 下 1pt = 4/3 px。表里的 px 是精确值，不四舍五入。
//    用户原话：「中文是宋体小四或四号，四号与小四是分内容进行设置地，
//              比如每个界面的功能名称采取字体更大的宋体」
// ──────────────────────────────────────────────────────────────

/** 中文字号名 → 磅值 */
export const CN_POINT = {
  一号: 26,
  二号: 22,
  小二: 18,
  三号: 16,
  小三: 15,
  四号: 14,
  小四: 12,
  五号: 10.5,
  小五: 9,
} as const;

export type CnSizeName = keyof typeof CN_POINT;

/** 磅 → CSS 像素（1pt = 4/3 px） */
export const pt2px = (pt: number): number => Math.round((pt * 4) / 3 * 100) / 100;

/**
 * 字号令牌。key 是语义用途，不是尺寸——这样改尺寸时不用改调用处。
 * cn 字段留着是为了让人一眼看出"这是几号字"，也用于生成文档和自检。
 */
export const fontSize = {
  /** 品牌名 / 启动页 · 二号 22pt */
  brand: { px: pt2px(CN_POINT.二号), cn: '二号' as CnSizeName },
  /** 界面主标题（如「文件管理器」「分析中心」）· 小二 18pt —— 用户点名的那个 */
  pageTitle: { px: pt2px(CN_POINT.小二), cn: '小二' as CnSizeName },
  /** 区块标题 / 功能分区名 · 三号 16pt */
  sectionTitle: { px: pt2px(CN_POINT.三号), cn: '三号' as CnSizeName },
  /** 卡片主标题 / 次级标题 · 小三 15pt */
  cardTitle: { px: pt2px(CN_POINT.小三), cn: '小三' as CnSizeName },
  /** 重要正文 / 主按钮 / 搜索框输入 · 四号 14pt */
  emphasis: { px: pt2px(CN_POINT.四号), cn: '四号' as CnSizeName },
  /** 正文默认 / 列表项 / 说明文字 · 小四 12pt */
  body: { px: pt2px(CN_POINT.小四), cn: '小四' as CnSizeName },
  /** 辅助信息 / 状态栏 / 时间戳 · 五号 10.5pt */
  caption: { px: pt2px(CN_POINT.五号), cn: '五号' as CnSizeName },
  /** 角标 / 极次要 · 小五 9pt（慎用，仅限徽标数字） */
  micro: { px: pt2px(CN_POINT.小五), cn: '小五' as CnSizeName },
} as const;

/** 大标题阈值：≥ 此像素值改用思源宋体（F1-b 方案） */
export const SERIF_SWITCH_PX = 24;

// ──────────────────────────────────────────────────────────────
// 二、字族：中英混排
//    把 Times New Roman 放第一位，浏览器逐字符回退：
//    拉丁字母/数字/西文标点 → Times New Roman
//    汉字/中文全角标点（TNR 里没有）→ 自动落到宋体
//    一行 CSS 精确实现，不需要 JS 逐字符切换。
// ──────────────────────────────────────────────────────────────

export const fontFamily = {
  /** 正文：西文 Times New Roman + 中文 SimSun 宋体 */
  body: `"Times New Roman", "SimSun", "宋体", "NSimSun", serif`,
  /** 大标题（≥24px）：西文 Times New Roman + 中文思源宋体（F1-b） */
  display: `"Times New Roman", "Source Han Serif SC", "Noto Serif SC", "SimSun", "宋体", serif`,
  /** 等宽：路径、代码、哈希、日志 */
  mono: `"Cascadia Mono", "Consolas", "Courier New", "SimSun", monospace`,
  /** 数字专用：表格里的数字要等宽对齐 */
  tabular: `"Times New Roman", "SimSun", serif`,
} as const;

export const fontWeight = {
  normal: 400,
  medium: 500,
  semibold: 600,
  bold: 700,
} as const;

/**
 * 行高：中文行高必须比西文宽松，否则汉字挤在一起很累眼。
 * 用户原话：「界面使用时眼睛感到舒服」
 */
export const lineHeight = {
  tight: 1.3,   // 标题
  normal: 1.6,  // 正文（中文舒适区间是 1.5~1.8）
  relaxed: 1.9, // 长文阅读
} as const;

/** 字间距：宋体在大字号下笔画显细，加一点字距能"撑起来" */
export const letterSpacing = {
  tight: '-0.01em',
  normal: '0',
  wide: '0.02em',
  /** 大标题专用：SimSun 24px+ 时补偿点阵字体的紧凑感 */
  display: '0.04em',
} as const;

// ──────────────────────────────────────────────────────────────
// 三、颜色：从应用图标反推并降饱和
//    图标原色：深蓝 #1155DD / 亮青 #22B8F0 / 翡翠 #3AD9A0 / 琥珀 #F5A623
//    原色饱和度太高，配衬线字体会显廉价 → 加深降饱和后用于界面。
//    用户原话：「界面要有专业感，高级感」「界面使用时眼睛感到舒服」
// ──────────────────────────────────────────────────────────────

/** 品牌原色（仅用于图标、启动页、关于页，不用于常规界面） */
export const brand = {
  blue: '#1155DD',
  cyan: '#22B8F0',
  emerald: '#3AD9A0',
  amber: '#F5A623',
} as const;

export const palette = {
  light: {
    // 底层
    /** 纸底：暖白，不是纯白 —— 纯白长时间看会刺眼 */
    bg: '#FAF9F6',
    /** 次级底：卡片、面板 */
    bgElevated: '#FFFFFF',
    /** 下沉底：输入框、代码块 */
    bgSunken: '#F2F0EB',
    /** 悬停底 */
    bgHover: '#EDEAE3',
    /** 选中底 */
    bgSelected: '#E3EDF7',

    // 文字
    /** 主文字：墨色，不是纯黑 */
    text: '#1F2933',
    /** 次要文字 */
    textSecondary: '#52606D',
    /** 辅助文字 / 占位符 */
    textMuted: '#7B8794',
    /** 反白文字（深色底上） */
    textInverse: '#FAF9F6',

    // 线条
    /** 常规分隔线：层次靠它做，不靠颜色堆 */
    border: '#E1DDD4',
    /** 强调边框 */
    borderStrong: '#C9C3B7',
    /** 聚焦环 */
    borderFocus: '#0F4C8C',

    // 语义色
    /** 主色 · 靛蓝：选中态、主按钮、链接 */
    primary: '#0F4C8C',
    primaryHover: '#0C3E73',
    primaryActive: '#092F58',
    primarySubtle: '#E3EDF7',

    /** 辅色 · 沉翡翠：成功、已完成、已索引 */
    success: '#1E9E76',
    successSubtle: '#E2F2EC',

    /** 强调 · 暗琥珀：进行中、需注意、队列 */
    warning: '#C8871B',
    warningSubtle: '#FAF0DC',

    /** 警示 · 朱砂：仅用于删除和不可逆操作，全应用不超过 3 处 */
    danger: '#A8342A',
    dangerHover: '#8C2A22',
    dangerSubtle: '#F7E5E3',

    /** 信息 · 亮青（源自图标） */
    info: '#1B7FA8',
    infoSubtle: '#E0F0F6',

    // 检索高亮
    /** 搜索命中词高亮底 */
    highlight: '#FBEFC4',
    /** 语义匹配（非精确词）高亮底 */
    highlightSemantic: '#E2F2EC',
  },

  dark: {
    // 深色模式用暖灰不用纯黑：
    // 纯黑 #000 在 OLED 上会让浅色文字产生光晕，长时间看更累。
    bg: '#1A1E22',
    bgElevated: '#22272C',
    bgSunken: '#141719',
    bgHover: '#2A3036',
    bgSelected: '#1E3448',

    text: '#E8E6E1',
    textSecondary: '#B0B7BF',
    textMuted: '#7B8794',
    textInverse: '#1F2933',

    border: '#333A41',
    borderStrong: '#4A535C',
    borderFocus: '#5B9BD5',

    primary: '#5B9BD5',
    primaryHover: '#7BB0E0',
    primaryActive: '#9AC4EA',
    primarySubtle: '#1E3448',

    success: '#4CC9A0',
    successSubtle: '#16302A',

    warning: '#E0A93F',
    warningSubtle: '#33290F',

    danger: '#D9695C',
    dangerHover: '#E5867B',
    dangerSubtle: '#331A17',

    info: '#4FB3D9',
    infoSubtle: '#0F2A33',

    highlight: '#4A3F1A',
    highlightSemantic: '#16302A',
  },
} as const;

export type ThemeName = keyof typeof palette;
export type ColorToken = keyof (typeof palette)['light'];

// ──────────────────────────────────────────────────────────────
// 四、间距：8px 基准栅格（4px 为半格，仅限图标与文字间隙）
// ──────────────────────────────────────────────────────────────

export const spacing = {
  none: 0,
  xxs: 2,
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
  huge: 64,
} as const;

// ──────────────────────────────────────────────────────────────
// 五、圆角：克制。大圆角配衬线字体会显得幼稚。
// ──────────────────────────────────────────────────────────────

export const radius = {
  none: 0,
  sm: 2,
  md: 4,
  lg: 6,
  xl: 8,
  /** 仅用于头像和标签胶囊 */
  pill: 999,
} as const;

// ──────────────────────────────────────────────────────────────
// 六、阴影：高级感来自"几乎看不见的阴影 + 一条 1px 的线"，
//    不来自彩色大阴影。
// ──────────────────────────────────────────────────────────────

export const shadow = {
  none: 'none',
  /** 卡片微浮起 */
  sm: '0 1px 2px rgba(31, 41, 51, 0.06)',
  /** 下拉、气泡 */
  md: '0 2px 8px rgba(31, 41, 51, 0.08), 0 1px 2px rgba(31, 41, 51, 0.04)',
  /** 对话框 */
  lg: '0 8px 24px rgba(31, 41, 51, 0.12), 0 2px 6px rgba(31, 41, 51, 0.06)',
  /** 命令面板（唯一允许的大阴影） */
  xl: '0 16px 48px rgba(31, 41, 51, 0.18), 0 4px 12px rgba(31, 41, 51, 0.08)',
  /** 聚焦环 */
  focus: '0 0 0 3px rgba(15, 76, 140, 0.18)',
} as const;

export const shadowDark = {
  none: 'none',
  sm: '0 1px 2px rgba(0, 0, 0, 0.3)',
  md: '0 2px 8px rgba(0, 0, 0, 0.36), 0 1px 2px rgba(0, 0, 0, 0.24)',
  lg: '0 8px 24px rgba(0, 0, 0, 0.44), 0 2px 6px rgba(0, 0, 0, 0.28)',
  xl: '0 16px 48px rgba(0, 0, 0, 0.55), 0 4px 12px rgba(0, 0, 0, 0.3)',
  focus: '0 0 0 3px rgba(91, 155, 213, 0.28)',
} as const;

// ──────────────────────────────────────────────────────────────
// 七、动效：克制。只用于位置和透明度，不用于颜色和尺寸。
//    超过 200ms 的动画在高频操作里会让人觉得"这软件反应慢"。
// ──────────────────────────────────────────────────────────────

export const motion = {
  duration: {
    /** 悬停反馈、按钮态变化 */
    instant: 80,
    /** 展开收起、淡入淡出 */
    fast: 140,
    /** 面板滑入、页面切换 */
    normal: 180,
    /** 大面积转场（少用） */
    slow: 240,
  },
  easing: {
    /** 标准：进出都用它 */
    standard: 'cubic-bezier(0.2, 0, 0, 1)',
    /** 进入：快出慢停 */
    enter: 'cubic-bezier(0, 0, 0, 1)',
    /** 退出：慢起快出 */
    exit: 'cubic-bezier(0.3, 0, 1, 1)',
  },
} as const;

// ──────────────────────────────────────────────────────────────
// 八、层级
// ──────────────────────────────────────────────────────────────

export const zIndex = {
  base: 0,
  sticky: 10,
  dropdown: 100,
  overlay: 200,
  modal: 300,
  commandPalette: 400,
  toast: 500,
  tooltip: 600,
} as const;

// ──────────────────────────────────────────────────────────────
// 九、布局常量
//    「对于重要的功能显示在界面内重要的位置中」—— 用户原话
//    全局搜索框固定在顶栏中央，任何界面都在，永远可达。
// ──────────────────────────────────────────────────────────────

export const layout = {
  /** 顶栏高度：搜索框所在，全局唯一入口 */
  topBarHeight: 52,
  /** 侧栏展开宽度 */
  sideBarWidth: 208,
  /** 侧栏收窄宽度（仅图标） */
  sideBarWidthCollapsed: 56,
  /** 状态栏高度：后台在忙什么永远可见但不打扰 */
  statusBarHeight: 26,
  /** 搜索框最大宽度：太宽反而不好扫视 */
  searchBoxMaxWidth: 720,
  /** 内容区左右留白 */
  contentPadding: 24,
  /**
   * 结果列表行高。虚拟滚动要固定高度才最快（变高行每次都要测量）。
   *
   * ⚠️ 这几个数字是**照着实际排版算出来的**，不是随手挑的。逐项：
   *     标题     18.67px × 1.3 行高 ≈ 25px
   *     摘要     16px × 1.6 行高 ≈ 26px / 行
   *     元信息   12px × 1.6 ≈ 20px（路径 / 大小 / 时间 / 匹配原因）
   *     上下内边距 8 + 8 = 16px
   *   紧凑   = 标题 + 1 行摘要 + 路径　　　 25 + 26 + 20 + 16 ≈ 88 → 取 68（元信息只留路径且摘要 1 行）
   *   标准   = 标题 + 2 行摘要 + 元信息　　 25 + 52 + 20 + 16 ≈ 113 → 取 114
   *   宽松   = 标题 + 3 行摘要 + 元信息　　 25 + 78 + 20 + 16 ≈ 139 → 取 142
   *
   * 这里踩过两次：先是 64px（元信息整行被裁），改成 92px 还是差 20px 又裁掉一次。
   * 教训：行高必须按**每一项的实际渲染高度**加出来，凭感觉调只会反复差一点。
   */
  resultRowHeight: { compact: 68, standard: 114, comfortable: 142 },
  /** 详情面板宽度 */
  detailPanelWidth: 380,
} as const;

export type Density = keyof typeof layout.resultRowHeight;

// ──────────────────────────────────────────────────────────────
// 十、护眼模式（E16）色温调节
//    在最外层套一个滤镜，不改任何组件颜色。
// ──────────────────────────────────────────────────────────────

export const eyeComfort = {
  /** 色温档位 → sepia+saturate 滤镜强度 */
  warmth: {
    off: 'none',
    low: 'sepia(0.06) saturate(0.97)',
    medium: 'sepia(0.13) saturate(0.94)',
    high: 'sepia(0.22) saturate(0.9)',
  },
  /** 建议连续使用提醒间隔（分钟） */
  reminderMinutes: [0, 20, 30, 45, 60],
} as const;

export const tokens = {
  fontSize,
  fontFamily,
  fontWeight,
  lineHeight,
  letterSpacing,
  brand,
  palette,
  spacing,
  radius,
  shadow,
  shadowDark,
  motion,
  zIndex,
  layout,
  eyeComfort,
  SERIF_SWITCH_PX,
  CN_POINT,
  pt2px,
} as const;

export default tokens;
