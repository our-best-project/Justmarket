-- 各國大盤指數（前端「今日大局」GLOBAL PULSE 面板的資料地基）
-- 資料源：Yahoo chart API（query1.finance.yahoo.com/v8/finance/chart）；symbol 經 kill-test 驗證
-- 慣例對齊主流三表：TIMESTAMPTZ DEFAULT now()、pk_/ix_/ck_ 命名、每欄中文註解、冪等建表
CREATE TABLE IF NOT EXISTS market_indices (
    index_code    VARCHAR(16)  NOT NULL,   -- 前端代號（對齊 GlobalMarket.index）：TAIEX/NDX/SPX...
    yahoo_symbol  VARCHAR(16)  NOT NULL,   -- Yahoo chart API symbol，如 ^TWII、000300.SS
    name          VARCHAR(64)  NOT NULL,   -- 顯示中文名，如「臺灣加權」
    country       VARCHAR(32)  NOT NULL,   -- 國家/地區，如「台灣」
    currency      VARCHAR(8)   NOT NULL,   -- 計價幣別 TWD/USD/JPY...
    timezone      VARCHAR(32)  NOT NULL,   -- IANA 時區 Asia/Taipei（前端算 session 用）
    map_x         NUMERIC(5,2) NOT NULL,   -- 世界地圖 X 座標 %（前端 mapX）
    map_y         NUMERIC(5,2) NOT NULL,   -- 世界地圖 Y 座標 %（前端 mapY）
    display_order SMALLINT     NOT NULL DEFAULT 0,   -- 顯示排序
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_market_indices PRIMARY KEY (index_code)
);

COMMENT ON TABLE market_indices IS '各國大盤指數主檔：靜態屬性（symbol/名稱/幣別/時區/地圖座標）';

-- 每個 (指數, 日期) 存一列；供每日盤後 upsert
CREATE TABLE IF NOT EXISTS market_index_daily (
    index_code    VARCHAR(16)      NOT NULL,   -- → market_indices.index_code
    date          DATE             NOT NULL,   -- 該指數當地交易日
    close         DOUBLE PRECISION NOT NULL,   -- 收盤點數（前端 series20 的 value）
    change_pct    DOUBLE PRECISION,            -- 當日漲跌 %（vs 前一交易日；首列可能為 null）
    session_state VARCHAR(8)       NOT NULL DEFAULT 'closed',  -- closed/open/preopen
    updated_at    TIMESTAMPTZ      NOT NULL DEFAULT now(),     -- 批次寫入/更新時間
    -- 複合主鍵 (index_code, date)：讓每日批次能 upsert（ON CONFLICT 不重複插入）
    CONSTRAINT pk_market_index_daily PRIMARY KEY (index_code, date),
    CONSTRAINT ck_market_index_daily_session
        CHECK (session_state IN ('closed', 'open', 'preopen'))
);

CREATE INDEX IF NOT EXISTS ix_market_index_daily_date ON market_index_daily (date);

COMMENT ON TABLE market_index_daily IS '各國大盤指數每日收盤快照；return5d/20d 與 series20 由 API 依交易日即時算，不落地';

-- 主檔種子（8 指數；靜態屬性取自前端 dashboard.mock.ts，symbol 經 kill-test 驗證）
-- 冪等：重跑只更新屬性、不重複插入
INSERT INTO market_indices
    (index_code, yahoo_symbol, name, country, currency, timezone, map_x, map_y, display_order)
VALUES
    ('TAIEX',  '^TWII',      '臺灣加權',      '台灣',   'TWD', 'Asia/Taipei',      79, 54, 1),
    ('NDX',    '^NDX',       'NASDAQ 100',    '美國',   'USD', 'America/New_York', 19, 43, 2),
    ('SPX',    '^GSPC',      'S&P 500',       '美國',   'USD', 'America/New_York', 25, 49, 3),
    ('N225',   '^N225',      '日經 225',      '日本',   'JPY', 'Asia/Tokyo',       88, 42, 4),
    ('KOSPI',  '^KS11',      '韓國綜合',      '韓國',   'KRW', 'Asia/Seoul',       81, 39, 5),
    ('HSI',    '^HSI',       '香港恆生',      '香港',   'HKD', 'Asia/Hong_Kong',   74, 53, 6),
    ('CSI300', '000300.SS',  '滬深 300',      '中國',   'CNY', 'Asia/Shanghai',    70, 45, 7),
    ('SX5E',   '^STOXX50E',  'EURO STOXX 50', '歐元區', 'EUR', 'Europe/Paris',     50, 35, 8)
ON CONFLICT (index_code) DO UPDATE SET
    yahoo_symbol  = excluded.yahoo_symbol,
    name          = excluded.name,
    country       = excluded.country,
    currency      = excluded.currency,
    timezone      = excluded.timezone,
    map_x         = excluded.map_x,
    map_y         = excluded.map_y,
    display_order = excluded.display_order,
    updated_at    = now();
