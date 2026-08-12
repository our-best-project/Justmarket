"""各國大盤指數模組：Yahoo chart API 抓取 → market_index_daily 入庫 → /market/global 供前端。

前端「今日大局」GLOBAL PULSE 面板的資料地基。資料源為 Yahoo chart API（免費、免 key）。
- client.py     : Yahoo chart API 取數（標準庫 urllib，零第三方依賴）
- daily_batch.py: 抓 8 指數日線 → 算當日漲跌 % → upsert market_index_daily（冪等）
- jobs.py       : APScheduler 盤後 job（沿用 finmind_Bright/jobs.py 範式）
- 建表 SQL      : DATABASE/postgres/05_market_index.sql
"""
