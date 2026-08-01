# Synorive

多模态并发分析与极速内容检索平台。桌面端（Windows）+ 安卓端，可被 Claude Code 直接调用。

把本地文件、网页链接、导出的聊天记录统一进一个索引；上传任意图片/文字/链接/视频，
并发分析后在毫秒级找到相关内容；支持跨模态互搜（用图找文、搜到视频的第 3 分 24 秒）。

---

## 现在能跑到哪一步

**阶段 1「地基」已完成并验证。** 应用可启动、引擎可启动、界面可交互，功能层从二期开始填。
完整进度见 `task-progress.md`，技术方案与 76 项功能菜单见 `docs/00-技术方案.md`。

---

## 怎么跑起来

### 一次性准备

```bash
# 1. Node 依赖（Node ≥20）
npm install

# 2. 生成字体子集（6MB，不进仓库，必须自己生成一次）
python scripts/build_fonts.py

# 3. 生成图标（已在仓库里，改了源图才需要重跑）
python scripts/build_icons.py

# 4. Python 引擎环境（Python ≥3.11）
py -3.13 -m venv engine/.venv
engine/.venv/Scripts/python.exe -m pip install -e engine
```

### 开发

```bash
npm run dev              # 起 Electron + Vite 热更新，引擎自动拉起
```

### 构建

```bash
npm run build            # 全部工作区
npm run build:desktop    # 只构建桌面端
npm run pack:win         # 打 Windows 安装包
```

### 单独跑引擎（调试 / 给 CLI 和 MCP 用）

```bash
engine/.venv/Scripts/python.exe -m synorive.main --port 8731 --data-dir ./data
# 接口文档 http://127.0.0.1:8731/docs
```

### 检查

```bash
node scripts/check-hardcoded-style.mjs   # 界面统一性：零硬编码色值与字号
npm run typecheck                        # TypeScript
```

---

## 目录

```
Synorive/
├─ apps/desktop/          Electron 41 + React 19 + TypeScript
│  ├─ electron/main/      主进程：窗口、托盘、引擎托管、IPC
│  ├─ electron/preload/   contextBridge 白名单（渲染层只能碰这些）
│  └─ src/                渲染层，零计算，保证 60fps
├─ apps/mobile/           安卓端（Kotlin + Compose），六期
├─ engine/                Python 引擎：摄取、分析、检索、依赖医生
├─ packages/
│  ├─ design-tokens/      全应用唯一样式真相源
│  └─ shared-types/       四方通信契约
├─ mcp/                   MCP 服务器，给 Claude Code 调
├─ cli/                   synorive 命令行
├─ scripts/               图标、字体、样式检查
└─ data/                  索引库与模型（不进仓库）
```

---

## 几个不显然的设计决定

**引擎是独立进程，不是线程。**
「使用时不卡顿」唯一可靠的实现方式。分析再重，Electron 主线程一帧都不参与。

**中文检索必须预分词。**
实测 SQLite 3.50.4：trigram 分词器下「搜索」「视频」这类两字词命中率为 **0**
（trigram 要求查询 ≥3 字符）；unicode61 不分词更糟，整句算一个词。
唯一可用的是入库和查询两侧都过 jieba，存空格分隔序列。另建一张标题 trigram
表兜底词内子串查询。

**检索是三级瀑布，不是一次返回。**
15ms 出最近打开项 → 50ms 出关键词召回 → 150ms 出向量召回并重排。
结果是"长出来的"不是"等出来的"，永不转圈。

**样式统一靠机制不靠自觉。**
业务代码里禁止出现色值和字号字面量，`scripts/check-hardcoded-style.mjs` 扫到就失败。

**字体是逐字符回退的，不是靠 JS 切换。**
`font-family: "Times New Roman", "SimSun", serif` —— 西文字母数字标点从
Times New Roman 取，汉字它没有就自动落到宋体。24px 以上的标题换思源宋体
（Windows 自带的 SimSun 是点阵字体，大字号下笔画又细又硬）。

---

## 许可

私有项目。字体 Noto Serif SC 采用 SIL OFL 1.1，可商用。
