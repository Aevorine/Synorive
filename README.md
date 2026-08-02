# Synorive

多模态并发分析与极速内容检索平台。桌面端（Windows）+ 安卓端，可被 Claude Code 直接调用。

把本地文件、网页链接、导出的聊天记录统一进一个索引；上传任意图片/文字/链接/视频，
并发分析后在毫秒级找到相关内容；支持跨模态互搜（用图找文、搜到视频的第 3 分 24 秒）。

---

## 现在能跑到哪一步

一到三期 + 五期已完成。**现在这个应用是真的能用的**：

- 拖一个混着文档、代码、图片、视频的文件夹进去 → 后台并发索引，界面不卡
- 中文语义搜文档（描述内容也能搜到，不用记文件名）
- `type:pdf date:最近7天 -草稿 "精确短语"` 这类语法直接写在搜索框里
- 搜图片里的文字（OCR 实测字符覆盖率 100%）
- 用一张图找相似的图，或者**找它出自哪个视频的第几秒**
- 搜一句台词，直接定位到视频的第 3 分 24 秒
- `claude mcp add synorive` 之后，Claude Code 能直接检索你的库

完整进度与实测数据见 `task-progress.md`，技术方案与 76 项功能菜单见 `docs/00-技术方案.md`。

### 实测数据（都是量出来的，不是估的）

| 项 | 实测 | 目标 |
|---|---|---|
| 冷启动到可搜索 | **1.30s** | ≤2.0s ✅ |
| 搜索首屏 @10.2 万块 | P50 **45ms** / P95 **186ms** | ≤80 / ≤200 ✅ |
| 完整检索 @10.2 万块 | P95 **373ms** | ≤500 ✅ |
| 滚动帧率 | **59.9 fps** | ≥55 ✅ |
| 10 万块磁盘占用 | **374 MB** | ≤3 GB ✅ |
| 断点续跑 | 54/54 全跳过，快 1067 倍 | ✅ |
| 图片入库（跳过 OCR） | **19.35 张/秒** | — |
| 图片 OCR（后台补跑） | 1.2~1.5 张/秒 | ← 受限于 Python GIL |
| 视频快速通道 | **88.6 倍速** | — |
| 视频含转写 | 5.97 倍速 | ≥6 ⚠️ |
| 文本向量化 | 47 块/秒（本机天花板） | ⚠️ 见下 |

⚠️ 两条指标在这台机器（i5-1155G7 / 无独显）上定不到，原始目标需要约 3 倍算力。
详见 `task-progress.md` 的「待你拍板清单」。

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

### 接进 Claude Code

```bash
npm run build --workspace=@synorive/mcp
node scripts/install-claude-integration.mjs
```

装完新开一个 Claude Code 会话，问「我之前存过关于 X 的东西吗」就会自动触发检索。
八个工具：`search` / `ingest` / `analyze` / `get_content` / `similar` / `timeline` / `graph` / `status`。

引擎地址是自动发现的（读 `data/engine.json`）：桌面端开着就连同一个引擎，
没开就自己起一个。也可以用 `SYNORIVE_ENGINE_URL` 显式指定。

### 命令行

```bash
node cli/dist/index.js search "中文分词" -n 10
node cli/dist/index.js search "type:pdf date:最近7天 预算"
node cli/dist/index.js add D:\项目\文档 --tag 重要
node cli/dist/index.js status
node cli/dist/index.js doctor --install embed-image
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
