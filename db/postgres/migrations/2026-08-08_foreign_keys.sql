-- 補外鍵（工程審查 P3-04）。2026-08-08 Bright；請 Nash 審閱（DDL 是你的守備範圍）。
--
-- 品誠審查列了三個缺 FK 的關聯。動手前先對 production 稽核孤兒列，結論是
-- 「加兩個、明確不加一個」——第三個不是疏漏，是兩張表語意本來就不同：
--
--   articles.event_id  → events.event_id          孤兒 0     ✅ 加
--   market_index_daily.index_code → market_indices 孤兒 0     ✅ 加
--   chip_data.ticker   → tickers.ticker           孤兒 101,763（536 檔）❌ 不加
--
-- chip_data 不加的理由：孤兒全是 ETF／債券 ETF／槓反（00 開頭、B/L/R 結尾）。
-- tickers 是「可搜尋的普通股主檔」（load_tickers 灌入時刻意過濾），chip_data 是
-- FinMind 的事實記錄——後者本來就不是前者的子集。加 FK 會讓每日籌碼批次寫入
-- ETF 時直接炸掉。若未來要收斂，方向是擴充 tickers 涵蓋而非擋資料。
--
-- 手法：NOT VALID 先加（不掃既有列、不鎖表）、VALIDATE 隨後補（只拿共享鎖）。
-- ON DELETE SET NULL：repair 腳本（repair_cluster_dups）會刪被合併的事件，
-- SET NULL 讓其文章回到「無事件」狀態而非擋住刪除——repair 自己會改派。

BEGIN;

ALTER TABLE articles
  ADD CONSTRAINT fk_articles_event
  FOREIGN KEY (event_id) REFERENCES events(event_id)
  ON DELETE SET NULL
  NOT VALID;

ALTER TABLE articles VALIDATE CONSTRAINT fk_articles_event;

ALTER TABLE market_index_daily
  ADD CONSTRAINT fk_mid_index
  FOREIGN KEY (index_code) REFERENCES market_indices(index_code)
  ON DELETE CASCADE          -- 主檔移除指數時日線一起走，孤兒日線沒有意義
  NOT VALID;

ALTER TABLE market_index_daily VALIDATE CONSTRAINT fk_mid_index;

COMMIT;
