# SPEC · Rust/C++ 重构任务 DAG

> 配套文档：`TECH_ROADMAP.md`（选型理由与架构）。本文件是可执行的任务事实源，
> 供 `/compact` 压缩后或断线重开的会话直接从这里恢复，不依赖对话记忆。
> **状态：规划阶段，等待用户拍板；未动一行业务代码。**

## 全局规则

- 每个 Phase 完成的唯一判定是 **PASS 条件全部满足**，不是"代码写完"。
- 每个 Phase 开工前，先跑一次 `git status` 确认工作区干净，完成后单独提交一次 `git commit`（不 push），作为可回滚的快照。
- 新旧实现并存期间，`engine/data/synorive.db`（SQLite 文件格式与 `store/schema.sql`）保持不变，新旧引擎必须能读同一份数据库文件——这是整个迁移安全性的地基，任何 Phase 都不能破坏它。
- 任务不重叠原则同样用于本计划自身的执行：同一 Phase 内标了"可并行"的任务组，才允许多 worktree/多 agent 同时动手；标了"串行"的必须严格排队，因为它们写同一批文件或依赖上一步产物。

---

## Phase 0 — 地基与双轨验证台架（阻塞后续全部 Phase）

| 任务 | 内容 | 依赖 | 并行性 |
|---|---|---|---|
| 0.1 | 建 Cargo workspace：`engine-rs/`（`synorive-core` lib + `synorive-enginebin` bin） | 无 | 可与 0.2 并行 |
| 0.2 | 本机沙盒实测 Tauri 2.x 最小原型的冷启动/闲置内存，产出真实数字，回填 `TECH_ROADMAP.md` 第 7 节"待实测"标注 | 无 | 可与 0.1 并行 |
| 0.3 | 双轨对拍脚本：同一批 `engine/tests` 真实 fixture（含 `cn_corpus.py`、`eval_cn_retrieval.py` 的语料）分别喂给旧 Python 引擎和新 Rust 引擎，对比排序结果、分数误差、延迟 | 0.1 | 串行（是后续所有 Phase 判定 PASS 的公共设施） |
| 0.4 | tantivy 索引文件与 SQLCipher 加密库的落盘方案定稿（旁路文件 vs 库内） | 0.1 | 串行，先出 mini-design 再进 Phase 1 |

**PASS 条件**：0.3 的对拍脚本能一键跑通并输出 diff 报告（即使此时 Rust 侧还是空实现，跑出"全不通过"也算脚本本身就绪）；0.2 产出的实测数据已写回文档。

---

## Phase 1 — 存储与检索核心（对应账本 A1 / A2 / A4）

**串行**（同一批文件，避免多头写冲突）：

1. `store` 模块移植（`rusqlite` + bundled-sqlcipher，schema 原样）
2. `usearch` 原生集成（int8 量化 + mmap），替换现有 Python `usearch`/`sqlite-vec` 调用路径
3. `tantivy` 索引接入（BM25F 分场：标题/章节/正文加权），与 0.4 的落盘方案对齐
4. RRF 融合模块（`retrieval::fuse`），保留线性加权兜底开关

**PASS 条件**：
- Phase 0 对拍脚本跑 `eval_cn_retrieval.py` 同款锚点查询集，Top-K 命中率不低于现有 Python 实现
- 同硬件头对头延迟：ANN P50 ≤ 86ms（@17万向量），完整检索 P95 ≤ 373.5ms（@10.2万块）
- 内存峰值 ≤ 现有 493MB（@10万块，六轮查询无泄漏，需跑内存增长曲线确认）

---

## Phase 2 — 摄取与多模态分析管线

**可并行的任务组**（互不写同一文件，可分给不同 worktree/agent）：

- 2A：`ingest`（chunker/pipeline/sensitive/watcher/web）
- 2B：`analyze::embedder` + `reranker`（`ort` + `tokenizers`）
- 2C：`analyze::image/preview/chapters/compare/enrich/tamper`
- 2D：`analyze::video` + `transcribe`（`ffmpeg-next` + sherpa-onnx 绑定）
- 2E：文档解析（`mupdf`/`calamine`/`docx-rs`/`epub`，pptx/trafilatura 按 `TECH_ROADMAP.md` 缺陷 1 单独立项，允许延后）
- 2F：`analyze::face`（insightface 权重 + 自写 SCRFD/ArcFace 推理管线）——**风险最高，需单独排期，不与 2A-2E 抢时间线**
- 2G：`doctor`（依赖医生：下载器/清单校验/断点续传）

**PASS 条件（每个子组独立判定，互不阻塞）**：
- 2B/2C/2D/2F：对同一批测试图片/音视频样本，新旧实现输出的向量/转写文本/聚类结果做数值 diff（余弦相似度 ≥0.999 或转写 WER 差异在可接受范围）
- 2A/2E/2G：功能等价性用现有 `engine/tests` 里对应测试文件逐条重跑
- 2F 额外要求：人脸聚类结果与 `engine/tests` 里能找到的样本做像素级/向量级 diff，不能只看"跑起来不报错"（对应 `TECH_ROADMAP.md` 缺陷 2 的静默失效风险）

---

## Phase 3 — 并发调度核心（对应账本 B1 / B4 / B5 / B6）

**串行**（调度器是单一核心组件，逐步叠加特性）：

1. `ResourceGraph` 资源键调度器基础版（admission control，见 `TECH_ROADMAP.md` 4.2）
2. 两段式冷启动（B1）：mmap 挂载 + 后台模型加载 + 就绪探针
3. `CancellationToken` 取消陈旧查询（B4）
4. Rayon 优先级工作池（B5）
5. `PROCESS_MODE_BACKGROUND_BEGIN` 接入 + 设置页开关（B6）

**PASS 条件**：
- B1：冷启动到"关键词可搜索"≤300ms（本机实测，多次取中位数）
- B4：连续快速输入 10 次查询，只有最后一次的结果落到 UI，中间请求全部被取消（用日志/计数器断言，不是靠肉眼看）
- B5：前台交互任务与后台批量索引同时压测，前台 P95 延迟不因后台任务存在而劣化超过 10%
- B6：任务管理器/`Get-Process` 实测后台索引任务的优先级类确实被降到 `PROCESS_MODE_BACKGROUND_BEGIN`，且设置页关闭开关后确实恢复正常优先级

---

## Phase 4 — 网络面与同步/联邦/情报

**可并行**：
- 4A：`axum` 本地 IPC 服务（UDS/Named Pipe + 二进制协议），替换 FastAPI+uvicorn+WebSocket
- 4B：`sync::crypto` + `queue`（E2E 加密同步）
- 4C：`federation` + `evidence` + `snapshots` + `briefing` + `relations` + `render_broker` + `lan_tls`
- 4D：`websearch/*`（23 个文件，工作量最大但技术风险最低）
- 4E：`cloud::adapters` + `describe` + `synthesize`

**PASS 条件**：
- 4A：桌面壳能通过新 IPC 拿到与旧版 HTTP 接口语义一致的响应，延迟不高于原 HTTP+WebSocket 方案
- 4B：加密同步端到端联调（桌面↔安卓模拟）密文可被对端正确解密，且现有 `test_db_encryption.py`/`test_pairing_guard.py` 覆盖的场景全部有对应 Rust 测试重跑通过
- 4C/4D/4E：逐文件对拍，`engine/tests` 里同名测试的等价场景重跑通过

---

## Phase 5 — 桌面壳迁移（Electron → Tauri）

**串行**（同一个壳，改动互相依赖）：

1. Tauri 2.x 工程骨架 + 现有 `apps/desktop/src` 挂载（前端组件树基本不动）
2. IPC 调用层重写（`invoke()` 取代 `ipcRenderer`），`shared-types` 改由 `specta`/`ts-rs` 从 Rust 结构体生成
3. 托盘 + 全局热键 + 自动更新 + PDF 查看器 + "peek" 悬浮窗逐项对齐（`tauri-plugin-*`）
4. 打包链路：`electron-builder` → Tauri bundler（NSIS/MSI）

**PASS 条件**：
- 现有桌面端每一个手动可验证的交互（托盘单击/双击、全局热键、PDF 查看、peek 悬浮、设置页每个开关）逐项人工过一遍，行为与旧版一致
- 安装包体积、冷启动、闲置内存三项头对头不劣于 Phase 0.2 实测出的 Tauri 基线，且不劣于现有 Electron 版本（101.6MB / 1302ms / —）

---

## Phase 6 — 移动端核心共享

1. `cargo-ndk` 交叉编译 `synorive-core` 到 Android ABI（arm64-v8a / armeabi-v7a / x86_64）
2. `uniffi-bindgen` 生成 Kotlin 绑定
3. `apps/mobile` 现有配对/加密/本地索引逻辑切到调用共享核心

**PASS 条件**：现有安卓端已验证的场景（扫码配对 I2、分享投喂、拍照反查）在新绑定下逐项人工过一遍不退化；无需重新做 A16 未完成的真机测试之外的新增测试范围。

---

## Phase 7 — 切换与退役

1. 头对头重测 `task-progress.md` 全部 A1-A17 验收项，不允许任何一项退化
2. 移除 `engine/.venv` + `pyruntime` 打包脚本（`scripts/bundle-python.mjs`）
3. 更新 `task-progress.md`，把 Rust 版本的实测数字并列写入
4. 保留一个"回退开关"：允许临时切回 Python 引擎读同一份 `synorive.db`（直到确认无重大问题后再删除 Python 侧代码）

**PASS 条件**：A1-A17 全绿，且至少 A1（冷启动）/A10（内存）/A12（安装包）三项有可展示的改善数据。

---

## 未决问题（需要用户在 Phase 0 前拍板）

1. 是否接受 `TECH_ROADMAP.md` 缺陷 1（trafilatura/pptx 无成熟 Rust 平替）的过渡方案：自写简化版 vs 过渡期例外保留一个不常驻的 Python 子进程？
2. 是否接受人脸聚类（2F）单独排期、不卡其他模块进度？
3. Phase 4A 的二进制协议选 `prost`(Protobuf) 还是 `rkyv`(零拷贝)？两者都是成熟选择，前者跨语言生态更成熟，后者性能更高但主要服务 Rust-to-Rust（桌面 Tauri 侧最终也是 Rust，属于可行选项，需要一次小规模选型对比）。
