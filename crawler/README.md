# crawler — 正式 Scrapy 執行面

獨立的 Scrapy 專案。它只接受 spider 名稱與 runtime 設定，輸出 JSON feed；
**不 import Spider Forge、不 import 模型、不直接寫 Neon**。

寫入資料庫是 `backend/ingestion.py` 的事：它以子程序執行這裡的 spider，
驗證輸出契約、標 `related_tickers`、冪等寫入 `articles`。這條邊界讓「抓得到」與
「存得對」可以分開驗證，也讓爬蟲容器可以完全不持有資料庫憑證。

## 怎麼跑

```bash
uv sync --extra crawler
```

```bash
cd crawler && uv run python -m scrapy list
```

正式路徑不直接呼叫 scrapy，走 ingestion：

```bash
uv run python -m backend ingest --all
```

只有 `APPROVED_CRAWLERS` 裡的 spider 會被執行 —— 名單寫在 `ingestion.py`，
不是誰在這個目錄放一個檔案就會被排程跑。

## Docker

```bash
docker compose --profile manual build crawler
```

```bash
docker compose --profile manual run --rm crawler list
```

這個映像檔同時是 Spider Forge 的沙盒。跑候選 spider 時只掛唯讀程式、傳入網域
allowlist，JSON 由 stdout 回到控制層 —— host 的輸出目錄不交給候選程式：

```bash
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 1g --cpus 1 --tmpfs /tmp:rw,nosuid,size=256m \
  -e SPIDERFORGE_ALLOWED_DOMAINS=example.com \
  -v "$PWD/candidate.py:/candidate/spider.py:ro" \
  justmarket-crawler:local runspider /candidate/spider.py -o -
```

## 沙盒開關

`SPIDERFORGE_SANDBOX=1` 時 `settings.py` 會停用 Neon pipeline 並啟用網域白名單
中介層。候選程式跑在這個模式下，抓不到白名單以外的網域，也寫不進資料庫。

## 測試

```bash
uv run pytest crawler/tests
```

`test_spider_quality.py` 是契約測試：每支 spider 的輸出都要有必要欄位、
時間格式正確、不含整段全文。`fixture_runner.py` 用保存下來的 response 執行
callback，不觸碰正式網站 —— 所以這組測試在 CI 裡跑得動。
