# 架構

## 系統要解的問題

台股一天有幾百篇財經新聞，其中大量在講同一件事。使用者要的不是「今天有 567 篇報導」，
而是「今天有 21 件值得知道的事，每件事有幾家媒體報、市場信不信」。

系統做的就是這個收斂：**報導（article）→ 事件（event）→ 兩個可解釋的分數**。

## 四層

```
① 前端 web/            使用者看到的網頁（TypeScript + Vite），GitHub Pages 靜態部署
② API  src/eventsignal/api/    只讀 FastAPI，Cloud Run
③ 儲存 Neon PostgreSQL + pgvector
④ 處理 src/eventsignal/ 其餘   背景管線，Prefect 排程
```

方向是單向的：④ 算好寫進 ③，② 只從 ③ 讀，① 只從 ② 讀。
**API 不做任何運算** —— 分數穩定、可重現，前端重整不會看到不同的數字。

## 資料流與交接棒

```
crawler/（Scrapy）
   │  JSON feed
   ▼
ingestion      驗證契約、標 related_tickers、冪等寫入   → articles.status = pending
   ▼
embedding      標題(+前段) → BGE-M3 1024 維             → vectorized
   ▼
clustering     Agglomerative(average) + 日期/ticker 雙閘門 → clustered，並產生 events
   ▼
llm            一次呼叫產出 摘要 + 九類分類 + 方向 + status → summarized
   ▼
scoring        重要性 ★1–5、市場驗證 0–100              → scored
```

**段與段之間不傳資料，只傳狀態。** 每段只處理 `articles.status` 等於自己上一格的資料，
做完推進下一格。這帶來三個性質：

- 當機可重跑：中途死掉，重跑只會處理沒推進的那些。
- 單段可重跑：`--stages llm,scoring` 只補跑後半段。
- 不需要暫存檔：沒有 CSV、沒有 SQLite、沒有訊息佇列，狀態就在資料庫欄位裡。

代價是每段都要自己維護 status 轉移，漏推進就會累積 —— 2026-08-11 就發生過殼腳本漏掉
向量化那一步、pending 堆到 3,000+ 而每輪 log 照樣印「管線結束」。修法是把步驟清單收進
`eventsignal/daily.py` 並用 `tests/test_daily_steps.py` 守住，不再讓它活在殼腳本裡。

## 兩條旁支

**籌碼流（FinMind）**：三大法人、股價、成交量每日盤後批次入庫，不進向量化。
它只在 `scoring/market_validation.py` 那一步被讀進來當佐證。

**大盤指數**：八國指數日線（Yahoo chart API）每日盤後 upsert，供前端「今日大局」。
與事件管線完全無關，是獨立的一條。

## 模組邊界

切法依「會一起改變的理由」，不依技術類型。詳細對照表在
[`src/eventsignal/__init__.py`](../src/eventsignal/__init__.py) 的 docstring，那裡離程式碼最近。

三個獨立部署面：

| | 是什麼 | 邊界 |
|---|---|---|
| `src/eventsignal/` | 主套件，api 與 worker 兩個映像檔都由它出 | 不 import crawler，只用子程序呼叫 |
| `crawler/` | 正式 Scrapy 專案 | 不被任何人 import；也被 spider_forge 當沙盒容器 |
| `src/spider_forge/` | AI 爬蟲生成系統 | **不在任何 Prefect flow 內**；不 import `news_crawler`（由 `tests/test_architecture.py` 守著） |

`spider_forge` 是刻意的手動邊界：它產出「spider + 內部 CI 認證」，由人決定要不要搬進
`crawler/`。自動把生成的程式碼接上正式管線，等於讓模型直接改 production 爬蟲。

## 為什麼是這些選擇

- **事件而非文章當主體**：使用者的問題是「今天發生什麼」，不是「今天有誰寫了什麼」。
- **聚類用 Agglomerative(average) 而非 HDBSCAN**：實測 F1 0.914 勝出，且加了日期與 ticker
  雙閘門擋住「同主題但不同事件」。對照組與數據見 `src/eventsignal/clustering/REPORT.md`。
- **重要性與市場驗證分開**：「這件事重要」與「市場買不買單」是兩個問題，混成一個分數就
  再也拆不出理由。兩者都必須能列出計算依據。
- **只給證據與分數，不給買賣建議**：這是產品邊界，不是技術限制。

## 前端與 API 的兩套契約

`/events`、`/tickers` 是分離式契約（列表、詳情、時間軸各一支）；
`/demo/*` 是一次載入式（bootstrap 一支回傳首頁全部資料，之後純前端切換）。

前端 `web/` 用的是後者。兩套並存不互相取代 —— 前者是通用契約，後者是為「一次載入、
零等待切換」的展示體驗設計的。要收斂成一套的話該砍的是前者，但那會動到契約，屬難逆決定。

端點清單見 [`api.md`](api.md)。
