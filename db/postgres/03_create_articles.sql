-- ============================================================
-- articles 文章表（爬蟲產出）
-- 規格依據：04_API_v2.md §4.1、§2（Enum 表）、03_SD.md §1.1
--
-- ★ status 是管線交接棒，絕對不能漏：
--     pending → vectorized → clustered → summarized → scored
--   爬蟲寫入時 status='pending'；每階段只處理上一格狀態的資料，
--   做完把 status 推進下一格。好處：當機可重跑、單段可重跑。
--
--   另有一個終端狀態 periodic：聚類段的週期性過濾會把「每日固定欄目」
--   （外幣日報、盤勢評論等，約佔兩成）挑掉，它們不屬於任何事件。
--   不給它一個終端狀態的話，這些文章會永遠卡在 vectorized 被每輪重撈。
--
-- ⚠️ 注意：articles.status 與 events.status 是完全不同的 enum，勿混
--
-- ⚠️ event_id 是否採 FK 尚未定案（§7 todo：正規關聯 vs 只靠 events.members）
--   本版先用「普通欄位 + 可 null + index」，保留彈性。
--   若團隊決定採 FK，取消下方 FK 約束的註解即可。
-- ============================================================

CREATE TABLE articles (
    article_id      VARCHAR(128) NOT NULL,          -- 來源縮寫 + 原站ID/URL雜湊
    event_id        VARCHAR(64),                    -- 聚類後回填（可 null）

    -- 來源資訊
    source          VARCHAR(64)  NOT NULL,          -- 工商時報 / MOPS / Fed
    source_type     VARCHAR(16)  NOT NULL,          -- official|media|analyst|gov
    stance_flag     VARCHAR(16)  NOT NULL,          -- neutral|biased
    language        VARCHAR(8)   NOT NULL,          -- zh-TW|en
    content_scope   VARCHAR(16)  NOT NULL,          -- full|summary_only

    -- 內容
    title           TEXT         NOT NULL,
    content         TEXT,                           -- media 類僅存導言/摘要，不存全文
    url             TEXT         NOT NULL,          -- 原始連結（供導流）
    related_tickers JSONB,                          -- ["2330"]

    -- 時間
    published_at    TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- ★ 管線交接棒
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',

    -- 稽核
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_articles PRIMARY KEY (article_id),

    -- Enum 約束（單一真實來源：04_API_v2.md §2）
    CONSTRAINT ck_articles_source_type
        CHECK (source_type IN ('official','media','analyst','gov')),
    CONSTRAINT ck_articles_stance_flag
        CHECK (stance_flag IN ('neutral','biased')),
    CONSTRAINT ck_articles_language
        CHECK (language IN ('zh-TW','en')),
    CONSTRAINT ck_articles_content_scope
        CHECK (content_scope IN ('full','summary_only')),
    CONSTRAINT ck_articles_status
        CHECK (status IN ('pending','vectorized','clustered','summarized','scored',
                          'periodic','filtered','deferred'))

    -- 若團隊決定 event_id 採 FK，取消下行註解（需先建好 events 表）：
    -- , CONSTRAINT fk_articles_event FOREIGN KEY (event_id) REFERENCES events(event_id)
);

-- 管線每階段都要「只撿某個 status 的資料」，加索引加速
CREATE INDEX ix_articles_status_fetched ON articles (status, fetched_at);

-- 聚類後依 event_id 反查成員文章
CREATE INDEX ix_articles_event_id ON articles (event_id);

COMMENT ON TABLE  articles                 IS '爬蟲產出的標準化文章，靠 status 在管線中交接';
COMMENT ON COLUMN articles.article_id      IS '來源縮寫 + 原站ID或URL雜湊';
COMMENT ON COLUMN articles.status          IS '★管線交接棒 pending→vectorized→clustered→summarized→scored；另有終端狀態 periodic＝被週期性過濾挑掉、不屬於任何事件（與 events.status 不同）';
COMMENT ON COLUMN articles.event_id        IS '聚類後回填的事件 ID';
COMMENT ON COLUMN articles.content         IS 'media 類僅存導言/摘要，不存全文（合規原則）';
COMMENT ON COLUMN articles.content_scope   IS '官方=full，媒體=summary_only';
