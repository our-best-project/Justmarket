-- 2026-07-22：articles.status 增加終端狀態 'periodic'
--
-- 為什麼：聚類段的週期性過濾會挑掉「每日固定欄目」（外幣日報、盤勢評論、
-- 營收速報等，實測 4,486 篇裡有 1,031 篇），這些文章不屬於任何事件。
-- 沒有終端狀態的話，它們會永遠停在 'vectorized'，被每一輪聚類重撈。
--
-- 已建好的資料庫要跑這支；全新環境直接用 03_create_articles.sql 即可（已含）。
-- 冪等：重複執行安全。

ALTER TABLE articles DROP CONSTRAINT IF EXISTS ck_articles_status;

ALTER TABLE articles ADD CONSTRAINT ck_articles_status
    CHECK (status IN ('pending','vectorized','clustered','summarized','scored','periodic'));

COMMENT ON COLUMN articles.status IS
    '★管線交接棒 pending→vectorized→clustered→summarized→scored；另有終端狀態 periodic＝被週期性過濾挑掉、不屬於任何事件（與 events.status 不同）';
