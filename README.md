<div align="center">

# Synorive

**Local-first multimodal search for everything you own — and a fact-checked web researcher.**
**本地优先的多模态检索 + 会自己找反驳材料的联网研究工作台**

Search your documents, code, images, videos and web archives by **meaning**, not filename.
Then search the open web across multiple engines, cross-check every claim, and get a briefing
where **every sentence is verbatim from a real source**.

Runs fully offline. Your files never leave your machine.
Exposes 24 tools to **Claude Code** over MCP.

[![Status](https://img.shields.io/badge/status-alpha%20v0.1.0-C8871B)](task-progress.md)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Android-0F4C8C)](#怎么跑起来)
[![Engine](https://img.shields.io/badge/engine-Python%203.13%20%2B%20FastAPI-1E9E76)](engine)
[![Desktop](https://img.shields.io/badge/desktop-Electron%2041%20%2B%20React%2019-0F4C8C)](apps/desktop)
[![Offline](https://img.shields.io/badge/works-fully%20offline-1E9E76)](#几个不显然的设计决定)
[![MCP](https://img.shields.io/badge/MCP-24%20tools-C8871B)](mcp)

</div>

---

## What it does / 它做什么

|  | English | 中文 |
|---|---|---|
| 🔍 | **Semantic search over your own files** — documents, source code, PDFs (split by section), images (OCR), video (down to the second), web archives | **搜自己的东西**：文档、代码、PDF（按章节）、图片（OCR）、视频（定位到秒）、网页存档 |
| 🖼 | **Cross-modal**: find an image by describing it, or find *which video a frame came from and at what second* | **跨模态互搜**：以图搜图、以图搜视频镜头并定位到秒 |
| 🌐 | **Multi-engine web search** — Bing / Baidu / 360 / Mojeek / Wikipedia, plus Google & DuckDuckGo via a self-hosted SearXNG | **多引擎联网搜索**，自建 SearXNG 后 Google 和 DuckDuckGo 也能用 |
| 🛡 | **Actively hunts for counter-evidence** — searches for debunkings, traces a claim to its earliest source, flags retracted papers | **主动找打脸证据**：反向搜辟谣、溯源到最早出处、标出已撤稿文献 |
| 📋 | **Extract-only briefings** — every line is a verbatim quote with its source. Conflicting claims are shown **side by side, undecided** | **只摘录不改写的简报**：每句都逐字来自原文并挂着出处；有分歧就并排放，不替你选 |
| 🔌 | **24 MCP tools for Claude Code** — let your agent search your library and verify claims for you | **24 个 MCP 工具**，Claude Code 能直接检索你的库、替你核查说法 |
| 🔒 | **Privacy fence** — network search and cloud inference are two *separate* switches, because one leaks *what you ask*, the other leaks *what you have* | **隐私围栏**：联网搜索和云端推理是两个开关，前者泄露"我在查什么"，后者泄露"我有什么" |

**Keywords:** local semantic search · multimodal RAG · offline search engine · personal knowledge base ·
fact checking · misinformation detection · MCP server · Claude Code · vector search · SQLite FTS5 ·
sqlite-vec · HNSW · OCR · video search · Chinese NLP · Electron · privacy-first

---

## Why another search tool? / 为什么还要再造一个

Most "search your files" tools stop at keyword matching, and most "AI research" tools
hand you a fluent summary you cannot verify. Synorive refuses both:

- **Retrieval is measured, not claimed.** Every performance number in this README was
  benchmarked on real data, including the two that **did not** hit their targets — they're
  listed with the reason instead of being quietly dropped.
- **Nothing is silently discarded.** Results filtered out as low-quality go into an
  "excluded" drawer with the reason, one click to bring back.
- **It never tells you something is false.** It finds who disputes a claim and shows you
  both sides — judging truth is not a capability it has, and pretending otherwise
  would be the most dangerous thing it could do.

---

## 现在能跑到哪一步

一到三期、五期、八期已完成。**现在这个应用是真的能用的**：

**搜自己的东西**
- 拖一个混着文档、代码、图片、视频的文件夹进去 → 后台并发索引，界面不卡
- 中文语义搜文档（描述内容也能搜到，不用记文件名）
- `type:pdf date:最近7天 -草稿 "精确短语"` 这类语法直接写在搜索框里
- 搜图片里的文字（OCR 实测字符覆盖率 100%）
- 用一张图找相似的图，或者**找它出自哪个视频的第几秒**
- 搜一句台词，直接定位到视频的第 3 分 24 秒
- 论文按 Abstract/Method/Results 分节索引，搜到直接标着「第 2 页 · Background」
- 问一篇 PDF **「你能回答哪些问题」**，点一条直接展开那一段原文

**搜全网并判真假**
- 多引擎并发（cn.bing / 百度 / 360 / Mojeek / 维基），自建 SearXNG 后 **Google 与 DuckDuckGo 也能用**
- 深挖会**读完第一轮再自己想出该追问什么**，然后再搜一轮
- 中文查询自动补一个英文变体，派给英文覆盖更好的引擎（一手资料多半是英文的）
- 主动反向搜「辟谣 / 质疑 / 争议 / debunked」，把打脸材料摆出来
- 把一条信息**追溯到最早出处**，十几个站两天内发同一件事会被标成「转载爆发」
- 引用的论文**被撤稿了会红字标出来**（走 OpenAlex）
- 五家学术源按 DOI 合并，带被引数和 PDF 链接

**给 Claude Code 用**
- `claude mcp add synorive` 之后，Claude Code 能直接检索你的库、核查一个说法、
  同时对比「你自己的资料」和「网上说的」

完整进度与实测数据见 [`task-progress.md`](task-progress.md)，
技术方案与 76 项功能菜单见 `docs/00-技术方案.md`。

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

### 发版与自动更新

桌面端和安卓端都能自己检查更新，更新源是这个仓库的 **GitHub Releases**。

```bash
npm run version:check      # 四处版本号是不是一致（根/桌面/安卓 name/安卓 code）
npm run version:set 0.1.2  # 一条命令改完四处，别手动改
npm run android:keystore   # 首次：生成安卓 release 签名密钥库（放仓库外）
npm run release            # 出两端产物，**不上传**
npm run release:publish    # 出产物并创建 GitHub Release（要 gh 已登录）
```

发版链路上有三个"漏了也不报错"的坑，`scripts/release.mjs` 会逐个挡住：

| 漏了什么 | 用户那边看到的现象 |
|---|---|
| `latest.yml` 没传 | 桌面端显示**「已是最新」**，不是报错，更新永远到不了 |
| tag 和 `package.json` 版本对不上 | 更新器 404 |
| APK 没传 | 手机端查得到新版但下不了 |
| 安卓 `versionCode` 没 +1 | 手机端显示**「已是最新」** |

两个已知限制，都是设计上的、不是 bug：

- **便携版（portable exe）不能自动更新** —— 单文件自解压包运行时在临时目录里，替换不了正在运行的自己。应用里会明说这一点并给出下载页链接，而不是报错让你反复重试。
- **安卓端要手动确认安装** —— 非应用商店分发只能调系统安装器，首次会要你打开「允许安装未知应用」。应用会检测这个权限并直接把你送到那一页。

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

**24 个工具**：
- 本地库：`search` / `ingest` / `analyze` / `get_content` / `similar` / `timeline` / `graph` / `status` / `questions`
- 联网：`web_search` / `research` / `scholar` / `read_url` / `web_engines` / `verify` / `unified_search`
- 文献：`scholar_review`（分主题综述，只摘录不改写）/ `scholar_table`（同一指标横向抽表）/
  `citations`（共被引找奠基论文）/ `harvest`（批量下开放全文并入库，默认干跑）
- 核对与记忆：`check_numbers`（数字回原文逐个核对）/ `memory`（这个话题以前查过什么）
- 本地媒体：`compare`（两个文件哪里不一样）/ `chapters`（长视频章节目录）

给 Claude 的返回**强制带可信度分项和出处**，工具描述里写死了能力边界
（"判断不了这句话本身是不是事实"、"逐字摘录不是改写"、"有反驳材料 ≠ 原说法是假的"）——
不写的话 Claude 会把内容农场的说法和官方文档等同看待，再用同样自信的语气转述。

引擎地址是自动发现的（读 `data/engine.json`）：桌面端开着就连同一个引擎，
没开就自己起一个。也可以用 `SYNORIVE_ENGINE_URL` 显式指定。

### 让 Google 和 DuckDuckGo 也能用（可选，但强烈建议）

2026-08 实测：Google 已强制 JavaScript（纯 HTTP 只拿到跳转页）、DuckDuckGo 的 html
端点改成了 JS 落地页、Yandex 直接给验证码、**七个 SearXNG 公共实例全部 429/403**。
免费拿到这几家结果，现实里只剩一条路：**自己跑一个 SearXNG**。

```bash
node scripts/setup-searxng.mjs            # 先看它打算做什么（默认干跑，什么都不动）
node scripts/setup-searxng.mjs --apply    # 确认后再真装（要 Docker）
node scripts/setup-searxng.mjs --status   # 看还活着没
```

装完引擎会在冷启动时**自动发现并启用**它，不用去设置里勾。
实测装完后 `google cse` 单独贡献 20 条、DuckDuckGo 10 条 —— 这两家直连时都是零。

> 脚本替你填掉了最容易踩的坑：SearXNG **默认只开放 HTML 格式**，
> 不在 `settings.yml` 里加 `json` 的话，现象是"实例起来了却一条结果都没有"。

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

## 许可 / License

**[GNU AGPL-3.0](LICENSE)** — Copyright © 2026 Aevorine

你可以自由使用、修改、分发这个项目。但有一条硬约束：
**任何基于它的修改版，只要被别人用到（包括做成网络服务），源码就必须一并公开。**
选 AGPL 而不是 MIT，是为了防止有人把它闭源拿去商用。

> You may use, modify and redistribute this project freely, with one hard condition:
> **any modified version that other people can interact with — including over a network —
> must have its complete source code made available.**

第三方资源：
- 字体 Noto Serif SC —— SIL OFL 1.1，可商用
- 模型（BGE / CLIP / RapidOCR / SenseVoice / BGE-reranker）各自遵循原始许可，
  由依赖医生按需下载，**不随仓库分发**
