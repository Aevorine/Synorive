-- ============================================================
-- Synorive 索引库 · 表结构
-- ============================================================
-- 设计原则：
--   ① 单文件。全部数据一个 .db，拷走就是完整备份。
--   ② 关键词（FTS5）和向量（sqlite-vec）在同一个事务里，
--      不会出现"文本索引进去了、向量没进去"这种半截状态。
--   ③ 每条内容有 fingerprint，重复投喂直接跳过 —— 这是断点续跑（A13）的地基。
--   ④ 每个分析阶段单独记状态，强杀进程重启后只补没做完的那些阶段。
-- ============================================================

-- journal_mode 是**写进库文件**的，设一次全局有效，所以留在这儿。
PRAGMA journal_mode = WAL;          -- 崩溃不损库（A14）；读写不互相阻塞

-- 🔴 其余那几条（synchronous / foreign_keys / temp_store / mmap_size / cache_size）
--    **已经从这里移走了，别再加回来。**
--
--    它们全是**每连接**生效的，不写进库文件。而这个脚本只在 `initialize()` 里
--    跑一次，用的是当时那一条连接。引擎是每线程一条连接的，于是结果是：
--      · 建库那条连接：拿到这里写的值
--      · 其余所有工作线程：这里写的值一个都没生效
--    也就是说"256MB 内存映射，读放大明显下降"这句注释，
--    在真正干活的那些线程上**一次都没成立过**，而且不报任何错。
--
--    更麻烦的是它还会**反向覆盖** `db.connect()` 里按本机内存算好的档位——
--    建库那条连接会被这里的固定值顶掉。
--
--    现在这几条的唯一出处是 `db.py` 的 `connect()`，每条连接都设一遍。

-- ── 内容项 ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    -- 内容指纹（SHA-256 前 16 字节的 hex）。同一份内容换个路径进来也认得出。
    fingerprint   TEXT NOT NULL,
    modality      TEXT NOT NULL,     -- text | image | video | audio | link | message
    source        TEXT NOT NULL,     -- file | link | clipboard | chat-export | mail | mobile | api
    status        TEXT NOT NULL DEFAULT 'queued',

    title         TEXT NOT NULL DEFAULT '',
    locator       TEXT NOT NULL,     -- 绝对路径 / URL / 消息定位符
    snippet       TEXT,
    mime          TEXT,
    size_bytes    INTEGER,

    -- 内容自身的时间（拍摄/发布/消息时间），优先于文件时间用于排序
    content_time  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,

    -- E11 热度学习：纯本地统计，设置里可一键清空
    last_opened_at TEXT,
    open_count    INTEGER NOT NULL DEFAULT 0,

    thumb_path    TEXT,
    -- 各模态特有的元数据，JSON
    meta_json     TEXT,
    -- 出错时的原因，给用户看的人话
    error         TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_items_fingerprint ON items (fingerprint);
CREATE INDEX IF NOT EXISTS idx_items_locator    ON items (locator);
CREATE INDEX IF NOT EXISTS idx_items_status     ON items (status);
CREATE INDEX IF NOT EXISTS idx_items_modality   ON items (modality);
CREATE INDEX IF NOT EXISTS idx_items_source     ON items (source);
-- 时间排序用倒序索引：绝大多数查询都是"最近的在前"
CREATE INDEX IF NOT EXISTS idx_items_ctime      ON items (content_time DESC);
CREATE INDEX IF NOT EXISTS idx_items_created    ON items (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_popularity ON items (open_count DESC, last_opened_at DESC);

-- ── 标签 ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags (tag_id);

-- ── 文本分块 ────────────────────────────────────────────────
-- C8 按语义边界切，不是按字数硬切。一块对应一个向量。

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    item_id     TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,
    -- 这块文字从哪来：body | ocr | transcript | title | caption
    channel     TEXT NOT NULL DEFAULT 'body',
    -- 文档：页码；视频/音频：起止秒；图片 OCR：归一化坐标框（JSON）
    page        INTEGER,
    start_sec   REAL,
    end_sec     REAL,
    bbox_json   TEXT,
    -- L3：论文分节（Abstract/Method/Results…），PDF 之外的内容一律 NULL
    section     TEXT,
    token_count INTEGER,
    UNIQUE (item_id, chunk_index, channel)
);

CREATE INDEX IF NOT EXISTS idx_chunks_item ON chunks (item_id);
CREATE INDEX IF NOT EXISTS idx_chunks_channel ON chunks (channel);

-- ── C5 人脸聚类（默认关）────────────────────────────────────
-- 没有走 sqlite-vec：一个人的库里聚出的"人物"最多几百个，
-- 暴力比对特征向量找最近的聚类中心快得可以忽略不计，
-- 犯不上为这点数据量另建一张向量表、多背一次维度/模型兼容性判断。

CREATE TABLE IF NOT EXISTS face_clusters (
    id          TEXT PRIMARY KEY,
    -- 用户自己起的名字，NULL = 还没命名（界面显示"未命名人物 N"）
    label       TEXT,
    face_count  INTEGER NOT NULL DEFAULT 0,
    -- 512 维 float32，这个聚类里所有人脸特征的运行平均，新脸进来时拿它比对
    centroid    BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faces (
    id          TEXT PRIMARY KEY,
    item_id     TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    cluster_id  TEXT REFERENCES face_clusters (id) ON DELETE SET NULL,
    -- 归一化坐标框 (x,y,w,h) 0~1，供界面画框和裁切缩略图
    bbox_json   TEXT NOT NULL,
    det_score   REAL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_faces_item ON faces (item_id);
CREATE INDEX IF NOT EXISTS idx_faces_cluster ON faces (cluster_id);

-- ── 全文检索（FTS5 + 中文分词）──────────────────────────────
-- ⚠️ 这里的选型是实测出来的，不是拍脑袋的。2026-08-02 在 SQLite 3.50.4 上量过：
--
--   分词方式               查「分词」(2字)   查「视频分析」(4字)
--   trigram                  0 命中            1 命中
--   unicode61 不预分词        0 命中            —
--   unicode61 + 预分词        1 命中            1 命中   ← 唯一可用
--
-- trigram 分词器要求查询串 ≥3 字符，而中文最常见的查询恰恰是两字词
-- （搜索/视频/文件/分析），全都搜不到。unicode61 不分词更糟，整句一个词。
--
-- 所以：**入库和查询两侧都过一遍 jieba 分词，存空格分隔的词序列**，
-- 用 unicode61 建正常的倒排索引，BM25 排序才有意义。
--
-- 另建一张只对标题的 trigram 表兜底：专治「我只记得文件名里那几个字」
-- 这类词内子串查询（jieba 分完词之后是匹配不到的）。标题短，索引很小。

-- 用 content='' + contentless_delete=1：
--   content=''            → FTS 只存索引不存正文（正文在 items/chunks 表里），省一半空间
--   contentless_delete=1  → 无内容表也支持 DELETE（SQLite 3.43+ 才有，本机 3.50.4）
-- 不加 contentless_delete 的话删一条内容会留下永久的幽灵索引项，
-- 搜索命中一个已经不存在的 id，界面上就是"点开报文件不存在"。
--
-- FTS 的 rowid 直接复用 items.rowid / chunks.rowid，不另建映射表。

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5 (
    title,      -- 存的是 jieba 分词后的空格分隔序列，不是原文
    snippet,
    locator,
    content = '',
    contentless_delete = 1,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
    text,       -- 同上，分词后的
    content = '',
    contentless_delete = 1,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- 子串兜底：只索引标题和路径的**原文**，用于短查询和词内匹配
CREATE VIRTUAL TABLE IF NOT EXISTS items_tri USING fts5 (
    title,
    locator,
    content = '',
    contentless_delete = 1,
    tokenize = 'trigram'
);

-- ── 分析阶段状态 ────────────────────────────────────────────
-- 断点续跑（A13）的核心：每个阶段单独记，重启只补没做完的。

CREATE TABLE IF NOT EXISTS item_stages (
    item_id    TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    stage      TEXT NOT NULL,  -- probe|extract|ocr|transcribe|chunk|embed|enrich|thumbnail|index
    status     TEXT NOT NULL,  -- pending|running|done|failed|skipped
    started_at TEXT,
    ended_at   TEXT,
    error      TEXT,
    -- 这个阶段用的模型和版本，换模型时靠它判断要不要重做（E15 热插拔）
    model_id   TEXT,
    PRIMARY KEY (item_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_stages_status ON item_stages (status, stage);

-- ── 摄取任务 ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    status        TEXT NOT NULL,  -- queued|running|paused|done|failed|cancelled
    source        TEXT NOT NULL,
    priority      TEXT NOT NULL DEFAULT 'normal',
    total_items   INTEGER NOT NULL DEFAULT 0,
    done_items    INTEGER NOT NULL DEFAULT 0,
    failed_items  INTEGER NOT NULL DEFAULT 0,
    skipped_items INTEGER NOT NULL DEFAULT 0,
    targets_json  TEXT NOT NULL,
    allow_cloud   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS job_items (
    job_id  TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    item_id TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, item_id)
);

-- ── 视频场景（E2 片段级定位）────────────────────────────────

CREATE TABLE IF NOT EXISTS video_scenes (
    item_id       TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    scene_index   INTEGER NOT NULL,
    start_sec     REAL NOT NULL,
    end_sec       REAL NOT NULL,
    keyframe_path TEXT,
    transcript    TEXT,
    PRIMARY KEY (item_id, scene_index)
);

CREATE INDEX IF NOT EXISTS idx_scenes_time ON video_scenes (item_id, start_sec);

-- ── 实体图谱（E6）───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS entities (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,  -- person|place|org|product|event|concept|time
    name          TEXT NOT NULL,
    aliases_json  TEXT NOT NULL DEFAULT '[]',
    mention_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, name)
);

CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id TEXT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    item_id   TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    chunk_id  TEXT REFERENCES chunks (id) ON DELETE CASCADE,
    PRIMARY KEY (entity_id, item_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_mentions_item ON entity_mentions (item_id);

CREATE TABLE IF NOT EXISTS entity_edges (
    from_id  TEXT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    to_id    TEXT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    weight   INTEGER NOT NULL DEFAULT 1,
    relation TEXT,
    PRIMARY KEY (from_id, to_id)
);

-- ── E12 隐私围栏 ────────────────────────────────────────────
-- 选了 B3 混合模式就必须有这个：没有目录级"禁止上云"开关的混合模式
-- 是不负责任的。

CREATE TABLE IF NOT EXISTS privacy_fences (
    id         TEXT PRIMARY KEY,
    pattern    TEXT NOT NULL,   -- 目录绝对路径 / 域名 / glob
    action     TEXT NOT NULL,   -- never-index | index-no-cloud | allow-all
    note       TEXT,
    created_at TEXT NOT NULL
);

-- ── H2 出站审计 ─────────────────────────────────────────────
-- 「你能查到到底什么被发出去过」。只记摘要不记原文。

CREATE TABLE IF NOT EXISTS outbound_log (
    id              TEXT PRIMARY KEY,
    at              TEXT NOT NULL,
    provider        TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    item_id         TEXT,
    content_summary TEXT NOT NULL,
    bytes_sent      INTEGER NOT NULL,
    purpose         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outbound_at ON outbound_log (at DESC);

-- ── E7 搜索配方 / E8 订阅监控 ───────────────────────────────

CREATE TABLE IF NOT EXISTS recipes (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    request_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_run_at  TEXT,
    schedule     TEXT,
    pinned       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS watches (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    request_json      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    last_checked_at   TEXT,
    notified_ids_json TEXT NOT NULL DEFAULT '[]',
    enabled           INTEGER NOT NULL DEFAULT 1
);

-- ── E9 近重复检测 ───────────────────────────────────────────
-- 感知哈希按 16 位分段存，查相似时先按段做等值匹配再算汉明距离，
-- 比全表扫快两个数量级。

CREATE TABLE IF NOT EXISTS phash_buckets (
    item_id TEXT NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    seg     INTEGER NOT NULL,   -- 第几段（0~3）
    value   INTEGER NOT NULL,   -- 该段的 16 位值
    PRIMARY KEY (item_id, seg)
);

CREATE INDEX IF NOT EXISTS idx_phash_lookup ON phash_buckets (seg, value);

-- ── 键值杂项（schema 版本、模型版本、统计缓存）───────────────

CREATE TABLE IF NOT EXISTS meta_kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── 回收站 ──────────────────────────────────────────────────
-- 删除时索引记录（items/chunks/FTS/向量）照常立刻清掉——不留半个
-- "搜不到但还在库里"的幽灵状态。这张表存的只是"删过这么一条，
-- 原来在哪、叫什么"，30 天内可以按 locator 重新投喂一次（= 恢复），
-- 过期自动从这张表清掉（并不会去动硬盘上的原文件，那从来不是这个
-- 软件删除操作的范围）。

CREATE TABLE IF NOT EXISTS trash (
    id          TEXT PRIMARY KEY,
    item_id     TEXT NOT NULL,   -- 原 item 的 id，仅供追溯，item 本身已经被删了
    title       TEXT NOT NULL DEFAULT '',
    locator     TEXT NOT NULL,
    modality    TEXT NOT NULL,
    source      TEXT NOT NULL,
    size_bytes  INTEGER,
    deleted_at  TEXT NOT NULL,
    purge_at    TEXT NOT NULL     -- deleted_at + 30 天，后台到点自动清
);

CREATE INDEX IF NOT EXISTS idx_trash_purge_at ON trash (purge_at);

-- ── P4 研究项目持久化 ───────────────────────────────────────
-- 深挖一次要十几秒、要发几十个请求、抓十几篇正文。关掉窗口就全没了，
-- 等于每次想接着挖都得从头付一遍这个成本。
--
-- 分三张表而不是一张大 JSON：
--   projects 是"这个研究是关于什么的"，要能按标题/时间列出来
--   runs     是"每一次搜索的完整结果"，只按 project 取，整份 JSON 存着最省事
--   sources  是"这个项目累计见过哪些来源"，跨轮次去重，且能被单独钉住/加备注
-- 全塞进一张表的话，"列出我的研究项目"这个最高频的操作要反序列化几 MB JSON。

CREATE TABLE IF NOT EXISTS research_projects (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    query         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',   -- open / done / archived
    notes         TEXT,
    settings_json TEXT                            -- 引擎/预设/档位，续做时原样复用
);

CREATE INDEX IF NOT EXISTS idx_research_projects_updated
    ON research_projects (updated_at DESC);

CREATE TABLE IF NOT EXISTS research_runs (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES research_projects (id) ON DELETE CASCADE,
    query        TEXT NOT NULL,
    mode         TEXT NOT NULL,                   -- quick / deep / scholar
    created_at   TEXT NOT NULL,
    elapsed_ms   INTEGER,
    payload_json TEXT NOT NULL                    -- 整份响应，原样存
);

CREATE INDEX IF NOT EXISTS idx_research_runs_project
    ON research_runs (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS research_sources (
    project_id  TEXT NOT NULL REFERENCES research_projects (id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    title       TEXT,
    site        TEXT,
    tier        TEXT,
    trust_score REAL,
    first_seen  TEXT NOT NULL,
    pinned      INTEGER NOT NULL DEFAULT 0,       -- 用户钉住的，导出时排最前
    note        TEXT,
    PRIMARY KEY (project_id, url)
);
