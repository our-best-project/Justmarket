# scripts — 一次性維運腳本

手動觸發的工具。**不進 Docker 映像檔、不進 Prefect 排程** —— 排程要跑的東西一律
收在 `python -m eventsignal <子命令>` 底下。

都要在裝好套件的環境執行：

```bash
uv run python scripts/peek_db.py
```

| 腳本 | 做什麼 |
|---|---|
| `peek_db.py` | 印出各表筆數與最新資料時間，看一眼管線有沒有在動 |
| `watch_db.py` | 同上，但持續刷新（Ctrl+C 結束） |
| `audit_llm_output.py` | 稽核近期 LLM 產出：格式、分類分佈、異常值 |
| `seed_articles.py` | 把凍結語料灌進 `articles`（`--dry-run` / `--limit` / `--retag`） |
| `build_ticker_stoplist.py` | 由語料重算 `data/ticker_stoplist.json` |
| `t24_sensitivity.py` | 市場驗證分數的參數敏感度掃描（全市場 in-memory） |
| `repair_cluster_dups.py` | 修復重複的聚類結果 |
| `repair_event_members.py` | 修復事件成員關聯 |
| `repair_event_tickers.py` | 重算 `events.related_tickers`（＝成員文章的聯集） |
| `repair_fake_zeros.py` | 修復籌碼資料裡的假零值 |
| `repair_official_status.py` | 修復官方公告事件的 status |
| `run_daily_gce.sh` | GCE VM 上跑每日一輪的殼腳本 |
| `stack.ps1` | Windows 本機起停 compose 的便利腳本 |

## `repair_*` 是什麼

真實資料上出過的錯，各自對應一次事故。它們都支援 `--dry-run`（預設）與 `--apply`：
先看要改什麼，確認了再改。

修復腳本存在本身是訊號：同一支被跑第三次，該修的是產生錯誤的那一段，不是繼續修資料。
