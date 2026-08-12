-- 市場驗證分數的資料地基
-- 每個 (股票, 日期) 存一列
CREATE TABLE chip_data (
    ticker                     VARCHAR(10)      NOT NULL,   -- 股票代號，例 "2330"
    date                       DATE             NOT NULL,   -- 交易日
    
    -- 三大法人買賣超淨額（net = buy - sell），單位：股，用 BIGINT 避免溢位
    foreign_net                BIGINT           NOT NULL,   -- 外資
    trust_net                  BIGINT           NOT NULL,   -- 投信
    dealer_net                 BIGINT           NOT NULL,   -- 自營商
    foreign_consecutive_days   INTEGER          NOT NULL,   -- 外資連續買/賣超天數（正=連買、負=連賣、0=無）
    net_vs_avg20_volume_pct    DOUBLE PRECISION,            -- 法人淨額 ÷ 近20日均量（暖機不足時 null）
    
    -- 股價量
    close                      DOUBLE PRECISION NOT NULL,   -- 收盤價（元）
    volume                     BIGINT           NOT NULL,   -- 成交量，單位：股
    volume_ratio_vs_avg20      DOUBLE PRECISION,            -- 最新量 ÷ 近20日均量（暖機不足時 null）
    
    -- 報酬（狀態機：未到 D+5 時為 null）
    return_1d                  DOUBLE PRECISION,            -- 事件後 1 交易日累積報酬（0.021 = +2.1%）
    return_3d                  DOUBLE PRECISION,            -- 事件後 3 交易日累積報酬
    return_5d                  DOUBLE PRECISION,            -- 事件後 5 交易日累積報酬
    sigma_20d                  DOUBLE PRECISION,            -- 近20日報酬標準差（供 σ 正規化）
    updated_at                 TIMESTAMP        NOT NULL DEFAULT now(),  -- 批次寫入/更新時間
    -- 複合主鍵 (ticker, date)：讓 T16 能做 upsert（ON CONFLICT 不重複插入）
    CONSTRAINT pk_chip_data PRIMARY KEY (ticker, date)
);