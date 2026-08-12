# db — schema 與資料資產

正式環境以 **Neon PostgreSQL**（含 pgvector）為唯一資料庫。應用程式從 `.env` 讀
`DATABASE_URL`，開發機不需要另外常駐一個 PostgreSQL。

## 目錄

| 路徑 | 是什麼 |
|---|---|
| `postgres/*.sql` | 正式 DDL 與 pgvector 初始化。建立新環境時以此處為準 |
| `postgres/migrations/` | 已套用的 migration，檔名帶日期 |
| `docker-compose.yml`、`init.sql`、`chip_data.sql` | 本機 PostgreSQL 備援，供離線測試；**不是**正式資料來源 |

## 資料表

| 表 | 是什麼 | 主鍵 |
|---|---|---|
| `articles` | 單篇報導。管線的交接棒欄位 `status` 在這裡 | `article_id` |
| `events` | 收斂後的事件。摘要、分類、方向、兩個分數都在這裡 | `event_id` |
| `tickers` | 個股主檔（代號、名稱、產業） | `ticker` |
| `chip_data` | 每日籌碼與價量，以及評分要用的衍生欄位 | `(ticker, date)` |
| `market_indices` | 八國指數靜態主檔 | `index_code` |
| `market_index_daily` | 指數每日收盤 | `(index_code, date)` |

`articles.status` 是整條管線的狀態機：

```
pending → vectorized → clustered → summarized → scored
                    ↘ periodic（週期性欄目，不進事件）
```

## 建立新環境

```bash
psql "$DATABASE_URL" -f db/postgres/init.sql
```

`init.sql` 會 `CREATE EXTENSION vector` 並依序套用 `01_`–`05_` 的建表檔。
之後的 migration 逐支手動套用，檔名就是套用順序。

## 一條容易踩的設定

連線一律要帶 `options="-c timezone=Asia/Taipei"`。Neon 預設 session timezone 是 GMT，
`CURRENT_DATE` 在台北 00:00–08:00 之間會慢一天。程式端已經處理（`eventsignal/db/session.py`
的 `get_conn()` 與連線池都有設），但**手動用 psql 查資料時要自己注意**，
否則會覺得「API 查到的今日事件跟我查的不一樣」。

## 秘密

連線字串只放 `.env` 或部署平台的 secret store。不要寫進本目錄的任何檔案，
也不要寫進 notebook 或 markdown —— `.gitignore` 擋得掉 `.env`，擋不掉你貼在文件裡的密碼。
