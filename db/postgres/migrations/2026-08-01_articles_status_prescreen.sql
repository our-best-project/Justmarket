-- 2026-08-01：articles.status 增加兩個終端狀態 'filtered' 與 'deferred'（篩選層 Prescreen）
--
-- 為什麼：爬蟲擴到 6 家來源後產能暴增（實測每小時 1,437 篇、24 小時 22,178 篇），
-- 遠超下游消化能力；待 LLM 的事件累積到 5,277 個，而 LLM 段有 3 秒節流＋免費層
-- RPM 上限，全跑要 4.4 小時且成本可觀。篩選層把 LLM 呼叫量收斂到約 1/4。
--
--   filtered＝關卡 1（向量化前）就判定不值得進管線：週期性欄目、內文過短。
--            與 'periodic' 的差別：periodic 是聚類段事後挑掉（已付 embedding 成本），
--            filtered 是進場前就擋下（省掉 embedding）。判定用同一份純函式
--            clustering_Timyo/dedup.py:periodic_type()，只是提前呼叫。
--   deferred＝關卡 3（TTL 回收）：超過 N 天仍未達到送 LLM 門檻的事件成員文章。
--            沒有這個終態的話，它們會永遠停在 'clustered' 被每輪重撈。
--            （與 'periodic' 當初要解的是同一類問題。）
--
-- 未達門檻但仍在 TTL 內的文章**維持 'clustered'**，不進終態——
-- 之後若有其他媒體跟進、source_count 上升，下一輪會自動符合條件被撈進 LLM。
--
-- 已建好的資料庫要跑這支；全新環境直接用 03_create_articles.sql 即可（已含）。
-- 冪等：重複執行安全。

ALTER TABLE articles DROP CONSTRAINT IF EXISTS ck_articles_status;

ALTER TABLE articles ADD CONSTRAINT ck_articles_status
    CHECK (status IN ('pending','vectorized','clustered','summarized','scored',
                      'periodic','filtered','deferred'));

COMMENT ON COLUMN articles.status IS
    '★管線交接棒 pending→vectorized→clustered→summarized→scored；終端狀態：periodic＝聚類段週期性過濾挑掉、filtered＝進場濾網在向量化前擋下、deferred＝逾期未達 LLM 門檻（與 events.status 不同）';
