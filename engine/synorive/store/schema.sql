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

PRAGMA journal_mode = WAL;          -- 崩溃不损库（A14）；读写不互相阻塞
PRAGMA synchronous = NORMAL;        -- WAL 下 NORMAL 已足够安全，比 FULL 快很多
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;       -- 256MB 内存映射，读放大明显下降
PRAGMA cache_size = -65536;         -- 64MB 页缓存

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
    token_count INTEGER,
    UNIQUE (item_id, chunk_index, channel)
);

CREATE INDEX IF NOT EXISTS idx_chunks_item ON chunks (item_id);
CREATE INDEX IF NOT EXISTS idx_chunks_channel ON chunks (channel);

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

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5 (
    title,      -- 存的是分词后的空格分隔序列，不是原文
    snippet,
    locator,
    content = '',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
    text,       -- 同上，分词后的
    content = '',
    tokenize = 'unicode61 remove_diacritics 2'
);

-- 子串兜底：只索引标题和路径的**原文**，用于短查询和词内匹配
CREATE VIRTUAL TABLE IF NOT EXISTS items_tri USING fts5 (
    title,
    locator,
    content = '',
    tokenize = 'trigram'
);

-- FTS 的 rowid 要能映射回业务主键
CREATE TABLE IF NOT EXISTS fts_item_map (
    rowid   INTEGER PRIMARY KEY,
    item_id TEXT NOT NULL UNIQUE REFERENCES items (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fts_chunk_map (
    rowid    INTEGER PRIMARY KEY,
    chunk_id TEXT NOT NULL UNIQUE REFERENCES chunks (id) ON DELETE CASCADE
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
