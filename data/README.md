# data — 管線用的資料檔

| 檔案 | 用途 | 誰在讀 |
|---|---|---|
| `cnyes_news_2026.json` | 凍結新聞語料（鉅亨 Anue，5,409 篇 → 去重 4,486 篇）。管線離線模式的資料來源，也是聚類結果能跨版本比對的基準 | `eventsignal/embedding/bge_m3.py --source json`、`eventsignal/llm/golden_set.py` |
| `ticker_stoplist.json` | 公司名停用清單：名字剛好是常用詞的公司（大陸、聯合、卓越…），不濾掉會大量誤標 | `eventsignal/crawler_legacy/base.py` 的 `related_tickers` 標註 |

## 注意

- `ticker_stoplist.json` 是**資料驅動產出，勿手改**。要更新就重跑
  `scripts/build_ticker_stoplist.py`（判別式是代號共現率，見 `crawler_legacy/README.md`）。
- BGE-M3 的模型權重不放這裡，由 sentence-transformers 自己快取到家目錄。
- 這個目錄不進 Docker 映像檔，只有 `ticker_stoplist.json` 例外 —— 它是 ingestion 的
  執行期依賴，凍結語料只有離線工具會用。
