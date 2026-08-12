-- ============================================================
-- tickers 股票代號主檔
-- 規格依據：04_API_v2.md §4.3、03_SD.md §1.4
-- 用途：搜尋自動完成（打「台積」查到 2330）
-- 資料來源：FinMind TaiwanStockInfo，一次性灌入約 1800 檔
-- 搜尋條件：name ILIKE '%q%' OR ticker LIKE 'q%'
-- ============================================================

CREATE TABLE tickers (
    ticker      VARCHAR(10)  NOT NULL,              -- 例 2330
    name        VARCHAR(100) NOT NULL,              -- 例 台積電
    aliases     JSONB,                              -- 別名 ["台積","TSMC"]（§7 todo，先靠 name 亦可）
    market      VARCHAR(10),                        -- 上市 | 上櫃（可選）
    industry    VARCHAR(32),                        -- 法定產業別（半導體業/航運業…）；來源 FinMind industry_category
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),-- 灌檔時間

    CONSTRAINT pk_tickers PRIMARY KEY (ticker)
);

-- 搜尋用索引（MVP 約 1800 筆，一般 B-tree 索引即可）
CREATE INDEX ix_tickers_name ON tickers (name);

-- 若之後 name ILIKE '%q%' 模糊搜尋變慢，可啟用 trigram 索引：
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- CREATE INDEX ix_tickers_name_trgm ON tickers USING gin (name gin_trgm_ops);

COMMENT ON TABLE  tickers            IS '股票代號主檔，供 /tickers/search 自動完成';
COMMENT ON COLUMN tickers.ticker     IS '台股代號，例 2330';
COMMENT ON COLUMN tickers.name       IS '公司名稱，例 台積電';
COMMENT ON COLUMN tickers.aliases    IS '別名陣列，供模糊搜尋';
COMMENT ON COLUMN tickers.industry   IS '法定產業別；事件依成員個股在此聚合出「產業每日狀況」';
COMMENT ON COLUMN tickers.market     IS '上市 | 上櫃';
