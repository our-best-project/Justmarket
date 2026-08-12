-- ============================================================
-- events 事件主表
-- 規格依據：04_API_v2.md §3（事件主對照表）、§2（Enum 表）
-- 用途：事件物件是唯一真實來源，各模組只填自己負責的段
-- 前置：pgvector 擴充需已啟用（DATABASE/postgres/init.sql）
--
-- ⚠️ 兩組容易混淆的東西：
--   1) status(消息確認度) vs verify_state(市場驗證狀態) — 兩個不同狀態機，勿混
--   2) expected_direction(利多/利空) vs chip_evidence 內的 direction(看多/看空)
--      — 語意不同，字彙刻意不同（消息面 vs 動作面）
--
-- ⚠️ DB 名 ≠ API 名：
--   importance_stars → API 回傳 stars
--   members          → API 回傳 source_links（投影成 url 陣列）
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE events (
    -- ========== 去重聚類段（填寫模組：去重） ==========
    event_id              VARCHAR(64)  NOT NULL,     -- evt_日期_代號_序號
    representative_title  TEXT,                      -- 去重原始代表標題（內部用，不進 API）
    source_count          SMALLINT,                  -- 報導篇數（前端「N 篇報導」）
    members               JSONB,                     -- [{source,title,url,has_unique_detail}]
    related_tickers       JSONB,                     -- ["2330"]（聚類段先寫 regex 版；LLM 段以全文語境覆蓋成更準的版本）
    industries            JSONB,                     -- ["半導體業"]（LLM 段補：純產業消息由 LLM 判、個股消息由成員個股查 tickers.industry 帶出）

    -- ========== 向量化段（填寫模組：向量化） ==========
    embedding             VECTOR(1024),              -- BGE-M3 1024 維，僅去重用、不外露

    -- ========== LLM 段（填寫模組：LLM） ==========
    title                 TEXT,                      -- 事件標題（LLM 改寫版）
    summary               TEXT,                      -- 2–4 句白話摘要
    occurred_at_iso       TIMESTAMPTZ,               -- ★ 列表排序用此欄（非 published_at）
    occurred_at_text      TEXT,                      -- 人話時間「今天 14:00」
    status                VARCHAR(32),               -- 消息確認度 event_status
    categories            JSONB,                     -- 9 類多標籤 ["法說","財報"]
    expected_direction    VARCHAR(8),                -- 事件消息面方向：利多|利空|中性
    direction_confidence  VARCHAR(8),                -- high|low
    confidence_note       TEXT,                      -- 「多來源一致」

    -- ========== 評分段（填寫模組：評分） ==========
    importance_stars      SMALLINT,                  -- 1–5；★ API 回傳名為 stars
    importance_reasons    JSONB,                     -- 為何重要（字串陣列）
    market_validation     SMALLINT,                  -- 0–100
    validation_breakdown  JSONB,                     -- {foreign,investment_trust,volume,price,divergence}
    chip_evidence         JSONB,                     -- [{label,detail,direction}]，direction 用 chip_direction
    verify_state          VARCHAR(16),               -- observing|preliminary|verified

    -- ========== 稽核 ==========
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT pk_events PRIMARY KEY (event_id),

    -- Enum 約束（單一真實來源：04_API_v2.md §2）
    CONSTRAINT ck_events_status
        CHECK (status IN ('official_confirmed','rumor_unconfirmed','developing','market_reacting')),
    CONSTRAINT ck_events_verify_state
        CHECK (verify_state IN ('observing','preliminary','verified')),
    CONSTRAINT ck_events_expected_direction
        CHECK (expected_direction IN ('利多','利空','中性')),
    CONSTRAINT ck_events_direction_confidence
        CHECK (direction_confidence IN ('high','low')),

    -- 數值範圍防呆
    CONSTRAINT ck_events_importance_stars_range
        CHECK (importance_stars BETWEEN 1 AND 5),
    CONSTRAINT ck_events_market_validation_range
        CHECK (market_validation BETWEEN 0 AND 100)
);

-- 首頁排序：importance 降序，同星等比 market_validation（04_API_v2 §1 慣例）
CREATE INDEX ix_events_ranking ON events (importance_stars DESC, market_validation DESC);

-- 列表時間排序用
CREATE INDEX ix_events_occurred_at ON events (occurred_at_iso DESC);

-- 若之後去重的相似度搜尋變慢，可加向量索引（MVP 量小先不建）：
-- CREATE INDEX ix_events_embedding ON events
--     USING hnsw (embedding vector_cosine_ops);

COMMENT ON TABLE  events                     IS '事件主表，前端主角，各模組逐步補齊';
COMMENT ON COLUMN events.event_id            IS '事件主鍵 evt_日期_代號_序號';
COMMENT ON COLUMN events.embedding           IS 'BGE-M3 1024 維向量，僅去重用、不外露';
COMMENT ON COLUMN events.occurred_at_iso     IS '事件發生時間，列表排序用此欄';
COMMENT ON COLUMN events.status              IS '消息確認度（勿與 verify_state 混淆）';
COMMENT ON COLUMN events.verify_state        IS '市場驗證狀態（勿與 status 混淆）';
COMMENT ON COLUMN events.importance_stars    IS 'DB=importance_stars，API 回傳名=stars';
COMMENT ON COLUMN events.members             IS 'DB 存完整物件，API 投影成 source_links';
COMMENT ON COLUMN events.related_tickers     IS '相關個股；LLM 段以全文覆蓋聚類段的 regex 版';
COMMENT ON COLUMN events.industries          IS '相關產業別；個股消息自動歸屬所屬產業（B 方案），供產業每日狀況聚合';
COMMENT ON COLUMN events.expected_direction  IS '事件消息面方向；與 chip_evidence.direction 為不同 enum';
