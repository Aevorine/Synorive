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
  /**
   * 大标题（≥24px）：西文 Times New Roman + 中文思源宋体（F1-b）
   *
   * ⚠️ "Source Han Serif SC" 必须排第一 —— 它是 fonts.css 里**自带打包**的那份
   *    （build_fonts.py 生成，@font-face 家族名就叫这个）。排到后面去，就会优先用
   *    系统字体，等于白打包：没装思源宋体的机器上标题会掉回 SimSun，F1-b 方案落空。
   *
   * 🔴 别被 CDP 报的字体名骗了（我栽过一次，差点把上面的顺序改反）：
   *    CSS.getPlatformFontsForNode 报的是字体文件**内部名表**里的名字，而
   *    @fontsource/noto-serif-sc 5.2.8 的子集文件，name[1] 一律写成
   *    「Noto Serif SC ExtraLight」/「Noto Serif SC ExtraLight SemiBold」——
   *    **这是上游的命名 bug，字重数据本身是对的**：
   *      *-400-*.woff2  OS/2 usWeightClass = 400
   *      *-600-*.woff2  OS/2 usWeightClass = 600
   *    48px「文件管理器」墨量实测：自带@400 = 2497，系统 Noto 常规@400 = 2496
   *    （比值 1.000，完全同一档），真 ExtraLight 只有 2030。**标题没有发虚。**
   *    → 判字重要量墨量，不能读名字。守卫见 scripts/verify-typography.mjs B3-2。
   */
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
    /**
     * 辅助文字 / 占位符
     * 原值 #7B8794 在纸底上只有 3.48:1、在输入框底上 3.21:1，达不到 WCAG AA。
     * 占位符和状态栏文字都是 12px 小字，**不在无障碍豁免范围内**（豁免的是禁用态控件）。
     * 现值对 bgSunken 4.56:1，是三种底色里卡得最紧的那个。
     */
    textMuted: '#636E7A',
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

    /**
     * 辅色 · 沉翡翠：成功、已完成、已索引
     * 这三个语义色都**当文字用**（.badge--* 的 color、.dep__degrade 的 12px 小字），
     * 所以按 4.5:1 卡，不能按"装饰色 3:1"放行。压暗后当状态圆点用也只会更清楚。
     * 卡得最紧的是"同色系浅底上的徽章文字"这一对，不是纸底。
     */
    success: '#177A5B',
    successSubtle: '#E2F2EC',

    /** 强调 · 暗琥珀：进行中、需注意、队列 */
    warning: '#936314',
    warningSubtle: '#FAF0DC',

    /** 警示 · 朱砂：仅用于删除和不可逆操作，全应用不超过 3 处 */
    danger: '#A8342A',
    dangerHover: '#8C2A22',
    dangerSubtle: '#F7E5E3',

    /** 信息 · 亮青（源自图标） */
    info: '#187397',
    infoSubtle: '#E0F0F6',

    // 检索高亮
    /** 搜索命中词高亮底 */
    highlight: '#FBEFC4',
    /** 语义匹配（非精确词）高亮底 */
    highlightSemantic: '#E2F2EC',

    /**
     * 遮罩：命令面板、对话框背后压暗的那一层。
     * 浅色下压 32%，深色下要更重（52%）—— 深色界面本来就暗，
     * 用同样的透明度会看不出"背后那层被压住了"，弹层像是浮在半空。
     */
    scrim: 'rgb(0 0 0 / 32%)',
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
    /** 原值 #7B8794 压在卡片底 #22272C 上只有 4.11:1（状态栏就是这个组合） */
    textMuted: '#838F9B',
    /** 原值 #1F2933 压在危险按钮 #D9695C 上只有 4.30:1 */
    textInverse: '#1C242D',

    border: '#333A41',
    borderStrong: '#4A535C',
    /** 跟 primary 保持同值，改一个必须改另一个 */
    borderFocus: '#62A0D7',

    /** 原值 #5B9BD5 压在 primarySubtle #1E3448 上只有 4.33:1（徽章文字） */
    primary: '#62A0D7',
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

    scrim: 'rgb(0 0 0 / 52%)',
  },

  /**
   * 纸感（B4 第三档）—— 长时间阅读专用
   * ============================================================
   * 🔴 **它存在的技术理由，不是"多一个好看的皮肤"**：
   *
   * 原来的护眼是给 <body> 挂一层 `filter: sepia(...)`。整层 filter 会
   * 把整个页面提升成一个独立合成层，并且**每一帧都要重新做一遍像素级
   * 滤镜**——滚动长列表时这条是实打实的掉帧来源，而且它和"卡顿"的
   * 关联完全不直观（用户只会觉得"这软件滚起来涩"，绝不会怀疑到护眼开关上）。
   *
   * 换成一套独立色板之后：颜色是静态变量，合成层该怎么走怎么走，
   * **护眼和流畅第一次不再互相打架**。滤镜路径保留但默认 off，
   * 想要更暖的人还能在纸感基础上再叠一档。
   *
   * 配色取向：纸黄底 + 棕墨字，对比度仍然按 WCAG AA 卡（正文 ≥4.5:1）。
   * 实测最紧的一对是 textMuted(#6B6157) 压在 bgSunken(#EBE6D9) 上 = 4.84:1。
   */
  paper: {
    bg: '#F5F1E8',
    bgElevated: '#FBF8F1',
    bgSunken: '#EBE6D9',
    bgHover: '#E5DFD0',
    bgSelected: '#E2E8ED',

    text: '#2B2620',
    textSecondary: '#5A5147',
    textMuted: '#6B6157',
    textInverse: '#FBF8F1',

    border: '#DDD5C4',
    borderStrong: '#C4B9A2',
    borderFocus: '#2F5169',

    primary: '#2F5169',
    primaryHover: '#264257',
    primaryActive: '#1D3344',
    primarySubtle: '#E2E8ED',

    success: '#3D6B4E',
    successSubtle: '#E4EDE4',

    warning: '#8A6218',
    warningSubtle: '#F4EAD3',

    danger: '#9C3327',
    dangerHover: '#82291F',
    dangerSubtle: '#F2E2DE',

    info: '#2A6B80',
    infoSubtle: '#DEEAEE',

    highlight: '#F2E4B8',
    highlightSemantic: '#E4EDE4',

    scrim: 'rgb(43 38 32 / 34%)',
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

  /**
   * B1 主舞台（大输入区）—— 用户原话：
   * 「如果是主要使用的功能，而且输入的内容很多，则显示的位置的界面要很大」
   *
   * 🔴 **在此之前，全应用唯一的输入口是顶栏里一个高 32px 的单行 input。**
   *    它连换行都做不到，更别说"输入的内容很多"。搜索页自己还得写一句
   *    「在上面的搜索框里敲字就能搜」来告诉用户主功能在哪 ——
   *    **一个软件需要用文案指路，就说明那个功能不在它该在的位置上。**
   *
   * 两态设计（同一个组件，不是两个）：
   *   舞台态 = 没有结果时，居中、大、多行、可拖高、可粘图、可拖文件
   *   收窄态 = 有结果后收进顶部一条，点一下 / 按 Ctrl+K 重新展开
   * 两态之间只做高度和位置过渡，**输入内容永不丢失**。
   */
  stage: {
    /** 舞台态最小宽度：低于这个值多行输入就没有意义了 */
    minWidth: 720,
    /** 舞台态最大宽度：再宽横向扫视距离过长，读起来反而累 */
    maxWidth: 960,
    /** 舞台态输入框默认可视高度（约 6 行小四字） */
    inputMinHeight: 132,
    /** 手动拖高上限（约 16 行）；超过就该去研究工作台写长文了 */
    inputMaxHeight: 420,
    /** 默认行数 */
    inputRows: 6,
    /** 收窄态高度：和原来顶栏搜索框一致，切换时不跳版 */
    compactHeight: 40,
    /** 舞台垂直位置：距顶部的比例（0.32 比居中略高，视觉重心更稳） */
    verticalAnchor: 0.32,
  },
} as const;

export type Density = keyof typeof layout.resultRowHeight;

// ──────────────────────────────────────────────────────────────
// 九·b、密度标尺（B5）
//    「宽松 / 标准 / 紧凑」三档要真的改变界面，而不只是改一个属性。
//
//    🔴 **修的是一个静默失败**：`data-density` 属性一直在往 <html> 上写，
//       但全仓 8227 行 CSS 里只有 search.css 响应了它 3 次。
//       也就是说：设置页有这个开关、点了有反馈、属性也确实变了，
//       **而界面上除了搜索结果摘要的行数以外什么都不会变**。
//       开关不报错、不崩溃、看起来完全正常 —— 它只是什么都不做。
//
//    做法：把密度做成一组**变量倍率**，组件一律用 var(--syn-d-*)，
//    于是新写的组件自动就是响应密度的，不需要每个都记得加一条规则。
// ──────────────────────────────────────────────────────────────

export const densityScale = {
  compact: {
    /** 间距总倍率：所有 --syn-d-space-* 由它乘出来 */
    scale: 0.75,
    /** 列表项之间的缝 */
    gap: 4,
    /** 按钮 / 输入框 / 芯片的标准高度 */
    control: 26,
    /** 卡片内边距 */
    cardPad: 8,
    /** 正文行高：紧凑档也不能低于 1.45，中文低于这个就糊成一片 */
    lineHeight: 1.45,
    /** 页面内容区左右留白 */
    contentPad: 16,
  },
  standard: {
    scale: 1,
    gap: 8,
    control: 32,
    cardPad: 12,
    lineHeight: 1.6,
    contentPad: 24,
  },
  comfortable: {
    scale: 1.25,
    gap: 12,
    control: 38,
    cardPad: 16,
    lineHeight: 1.75,
    contentPad: 32,
  },
} as const;

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

// ──────────────────────────────────────────────────────────────
// 十一、打印样式表（A4 一键成稿 / PDF 导出）
//
// 🔴 **为什么它必须住在这个包里，而不是在用它的那个组件旁边。**
//    导出的 PDF 走 `webContents.printToPDF`，在一个**独立的、自包含的**
//    渲染窗口里加载 —— 那个窗口不加载 `tokens.css`，
//    写 `var(--syn-color-text)` 会解析成空值，打出来是一份没有颜色和
//    字号的 PDF。所以这份样式表的色值和字号**只能写死**。
//
//    而"允许写死字面量的地方"在这个仓库里只有一个，就是这个包
//    （`scripts/check-hardcoded-style.mjs` 的 ALLOW_PATHS 就是这么定的）。
//    把它放到别处 = 要么被守卫拦下，要么得去改守卫的范围 ——
//    **改守卫来迁就代码，是把"界面要统一"这条规则一点点掏空的开始。**
//
// 🔴 尺寸用 **pt 不用 px**：这是给纸张排版的。px 在不同 DPI 的
//    打印后端上会得到不同的实际字号，而 pt 是印刷单位，到哪都一样大。
//
// 色值取自上面 palette.light 的同一批墨色，保持"打出来的和屏幕上像一家"。
// ──────────────────────────────────────────────────────────────

export const printCss = `
  body { font-family: "Times New Roman", "SimSun", serif; font-size: 12pt; line-height: 1.7; color: #1F2933; margin: 2.2cm 2cm; }
  h1 { font-size: 20pt; margin: 0 0 0.4em; }
  h2 { font-size: 15pt; margin: 1.4em 0 0.5em; border-bottom: 1px solid #E1DDD4; padding-bottom: 0.2em; }
  h3 { font-size: 12.5pt; margin: 1em 0 0.3em; }
  .meta { color: #636E7A; font-size: 10.5pt; margin-bottom: 1.2em; }
  .loc { color: #636E7A; font-size: 10.5pt; margin: 0 0 0.3em; }
  blockquote { margin: 0.3em 0 0.4em; padding: 0.4em 0.9em; border-left: 3px solid #0F4C8C; background: #F2F0EB; }
  .cite { font-size: 10.5pt; margin: 0 0 1em; }
  a { color: #0F4C8C; text-decoration: none; }
  ol.refs { padding-left: 1.4em; }
  ol.refs li { margin-bottom: 0.4em; font-size: 11pt; }
  /* 一段摘录被分页切成两半读起来很难受，能不切就不切 */
  section { break-inside: avoid; }
`;

export const tokens = {
  fontSize,
  printCss,
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
  densityScale,
  eyeComfort,
  SERIF_SWITCH_PX,
  CN_POINT,
  pt2px,
} as const;

export default tokens;
