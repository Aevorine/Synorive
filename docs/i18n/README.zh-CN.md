<div align="center">

# Synorive

**本地优先的多模态语义检索 + 会自己找反驳材料的联网研究工作台**

用**意思**搜你自己的文档、代码、图片、视频和网页存档，而不是靠记文件名。
然后跨多个引擎搜公网、交叉核对每一条说法，出一份**每句话都逐字来自真实出处**的简报。

完全离线可用。你的文件永远不离开这台机器。
通过 MCP 向 **Claude Code** 暴露 24 个工具。

[English](../../README.md) · **简体中文** · [Français](README.fr.md) · [Español](README.es.md) · [Русский](README.ru.md) · [العربية](README.ar.md)

[![下载](https://img.shields.io/badge/下载-v0.1.1-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)
[![许可](https://img.shields.io/badge/许可-AGPL--3.0-1E9E76)](../../LICENSE)
[![平台](https://img.shields.io/badge/平台-Windows%20%7C%20Android-0F4C8C)](https://github.com/Aevorine/Synorive/releases/latest)

</div>

![Synorive](../screenshots/research-light.png)

---

## 它做什么

| | |
|---|---|
| 🔍 | **搜自己的东西**：文档、代码、PDF（按章节）、图片（OCR）、视频（定位到秒）、网页存档 |
| 🖼 | **跨模态互搜**：以图搜图；给一帧画面，找出它出自哪个视频的第几秒 |
| 🌐 | **多引擎联网搜索**：Bing / 百度 / 360 / Mojeek / 维基百科；自建 SearXNG 后 Google 和 DuckDuckGo 也能用 |
| 🛡 | **主动找打脸证据**：反向搜辟谣、把说法溯源到最早出处、标出已撤稿的文献 |
| 📋 | **只摘录不改写的简报**：每一句都逐字来自原文并挂着出处；有分歧就**并排放着，不替你下结论** |
| 🔌 | **24 个 MCP 工具**：Claude Code 能直接检索你的库、替你核查说法 |
| 🔒 | **隐私围栏**：联网搜索和云端推理是**两个**开关——前者泄露"我在查什么"，后者泄露"我有什么" |

---

## 为什么还要再造一个

多数「搜本地文件」的工具停在关键词匹配；多数「AI 研究」工具给你一段流畅但没法核实的总结。
这个项目两样都拒绝：

- **检索性能是量出来的，不是声称的。** README 里每个数字都在真实数据上跑过基准，
  **包括两个没达标的** —— 它们连同原因一起列着，而不是被悄悄拿掉。
- **不静默丢东西。** 被判为低质量而过滤掉的结果进「已排除」抽屉，写明理由，一键放回。
- **它从不告诉你某件事是假的。** 它找出谁在质疑这个说法，把两边摆给你看 ——
  判断真假不是它的能力，假装有才是最危险的。

---

## 装起来用

**Windows**：到 [Releases](https://github.com/Aevorine/Synorive/releases/latest) 下载

- `Synorive-Setup-0.1.1.exe` —— 安装版，**支持应用内自动更新**
- `Synorive-0.1.1-portable.exe` —— 免安装版（便携版不能自更新，要手动换新包）

> Python 运行时已经打进安装包里，**装完直接能用**，不需要你先装 Python。

**Android**：下载 `app-release.apk`，在手机上开「允许安装未知来源」后安装。
手机端是瘦客户端，需要在同一局域网内连到电脑上的引擎。

---

## 从源码跑

```bash
npm install                                   # Node ≥20
python scripts/build_fonts.py                 # 生成字体子集（6MB，不进仓库）
python -m venv engine/.venv                   # Python ≥3.11
engine/.venv/Scripts/pip install -e "engine[docs,media,sync,ann]"
npm run dev                                   # 起 Electron + Vite，引擎自动拉起
```

只做编译检查：`npm run typecheck` ｜ `node scripts/check-hardcoded-style.mjs`

---

## 接进 Claude Code

```bash
claude mcp add synorive -- node <仓库路径>/mcp/dist/index.js
```

接上之后可以直接说：「在我的库里搜『向量检索为什么慢』，把找到的说法交叉核对一遍」。

---

## 几个不显然的设计决定

- **三层瀑布检索**（关键词 → 语义 → 精排）：首屏先出，慢的那层出来了自己重排，不让你干等。
- **中文必须预分词**：不过 jieba 的话，「搜索」「视频」这类两字词命中率是 0。
- **摘录版和生成版简报分开**：左栏逐字摘录、右栏 AI 生成，随时能对照，
  永远不会把生成的句子伪装成原文。
- **联网总闸和云端推理分开**：很多人愿意接受前者而绝不接受后者，合成一个开关就是逼他们二选一。

---

## 许可

[AGPL-3.0-or-later](../../LICENSE)。完整文档（含实测数据、目录结构、已知缺口）见
[英文 README](../../README.md)。
