-- 2026-07-25：產業別標籤（前端「產業每日狀況」＋事件的個股/產業雙軌）
--
-- 為什麼：要在前端顯示各產業當日事件狀況，需要把事件依成員個股歸到法定產業。
-- 兩欄：
--   tickers.industry   個股 → 法定產業別（來源 FinMind industry_category，seed_tickers.py 灌）
--   events.industries  事件 → 相關產業陣列（LLM 段填，個股消息自動查表歸屬＝B 方案）
--
-- 已建好的資料庫跑這支；全新環境用 01/02 建表即含。冪等：重複執行安全。

ALTER TABLE tickers ADD COLUMN IF NOT EXISTS industry VARCHAR(32);
ALTER TABLE events  ADD COLUMN IF NOT EXISTS industries JSONB;

COMMENT ON COLUMN tickers.industry  IS '法定產業別；事件依成員個股在此聚合出「產業每日狀況」';
COMMENT ON COLUMN events.industries IS '相關產業別；個股消息自動歸屬所屬產業（B 方案），供產業每日狀況聚合';
