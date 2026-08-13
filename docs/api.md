# API 契約

只讀 API，全部是 `GET`。base URL `/api/v1`。互動式文件在 `/docs`（FastAPI 自動產生，
以實作為準；本檔說的是「為什麼有這些端點」）。

啟動：`uv run python -m backend api`

## 端點

### `/demo/*` —— 前端 `frontend/` 實際使用的那組

一次載入式契約：首頁進來只打 `bootstrap` 與 `market/*`，之後的分頁、篩選、排序都在前端做。

| 端點 | 回什麼 | 備註 |
|---|---|---|
| `GET /demo/bootstrap` | 最近兩個收盤日的全部可渲染事件 + meta + 分類清單 | `limit` 只是防爆上限（預設 120） |
| `GET /demo/day?date=` | 指定收盤日的全部事件 | 輕量版：不含 timeline / market_reaction |
| `GET /demo/events/{id}` | 單一事件詳情（含時間軸與市場反應） | 可深連結；搜尋結果點進來走這支 |
| `GET /demo/search?q=` | 關鍵字搜尋 | 比對標題、摘要、股號、公司名 |

`bootstrap` 與 `events/{id}` 共用同一份欄位定義（`EVENT_COLUMNS`）——
否則「從首頁點進詳情」與「從搜尋點進詳情」會渲染出不同結果。

### `/market/*` —— 首頁「今日大局」

| 端點 | 回什麼 |
|---|---|
| `GET /market/overview` | 加權指數脈搏：1/5/20 交易日報酬、漲跌家數 |
| `GET /market/global` | 八國大盤指數（GLOBAL PULSE 面板） |
| `GET /market/breadth` | 台股盤面廣度、產業表現、成交值前 12 大 |

### `/events`、`/tickers` —— 通用分離式契約

| 端點 | 回什麼 |
|---|---|
| `GET /events/today` | 今日事件摘要列表 |
| `GET /events/{id}` | 單一事件 |
| `GET /events/{id}/timeline` | 事件時間軸 |
| `GET /tickers/search?q=` | 個股搜尋提示 |
| `GET /tickers/{ticker}/events` | 某檔股票相關的事件 |

### 其他

| 端點 | 用途 |
|---|---|
| `GET /health` | 服務 + DB readiness。DB 連不上回 **503**，不是 200 |
| `/static/images/*` | 事件示意圖。靜態資源，刻意不在 `/api/v1` 底下 |

## 三件會影響串接的事

**時區**：連線一律 `timezone=Asia/Taipei`。Neon 預設 session timezone 是 GMT，
少了這個設定，`CURRENT_DATE` 在台北 00:00–08:00 之間會慢一天，「今日事件」那 8 小時查錯日。
連線池與單次連線都要設，漏一邊等於沒設。

**快取**：`bootstrap` 與 `market/global` 有進程內 TTL 快取（預設 60 秒，
`API_CACHE_TTL_SECONDS`）。資料每日盤後才更新，TTL 內直接回上次結果、完全不碰 DB。
副作用：`meta.as_of` 最多落後 TTL 秒。demo 時想立刻看到 DB 改動就設成 `0`。
⚠️ 這個變數必須是真正的環境變數，寫在 `.env` 太晚 —— 快取物件在模組 import 時就建好了。

**CORS**：預設清單是本專案自己的前端會出現的位置 —— 本機 5173／4173，加上
GitHub Pages 正式站 `https://our-best-project.github.io`。其他來源（預覽部署、
自訂網域）用 `CORS_EXTRA_ORIGINS` 逗號分隔補進來。只開 `GET`。

CORS 設錯的失敗方式特別安靜：API 回 200、log 乾淨、`curl` 也完全正常，
只有瀏覽器 console 會說話。所以正式站台的 origin 寫進程式碼預設值，
並由 `tests/test_cors.py` 守著。

## 誠實標註：目前吐不出來的欄位

不是遺漏，是資料鏈上真的沒有：

- `market_reaction` —— 邏輯實作了，但需要 `chip_data` 有該檔該日的資料；缺就回 `null`。
- `market_validation` / `validation_breakdown` —— 需要事件發生後 D0→D+5 的籌碼才算得出來，
  期間一律顯示「觀察中」。

前端對這些欄位都有 null 處理，**不以模擬值替代**。
