# 就事論市 Justmarket

每天幾百篇台股新聞，大多在講同一件事。Justmarket 把它們收斂成**可驗證的事件**：
合併重複報導、生成摘要與分類、算出「這件事多重要（★1–5）」與「市場信不信（0–100）」，
然後只回傳證據與分數 —— **不給買賣建議**。

## 怎麼跑

需要 [uv](https://docs.astral.sh/uv/)（Python 3.12）與 Node 20+。

```bash
cp .env.example .env    # 填 DATABASE_URL、LLM_API_KEY、FINMIND_TOKEN
```

### 後端：一個入口

```bash
uv sync --all-extras && uv run python -m backend --help
```

`--help` 就是完整的操作清單，不需要去別的檔案找指令：

| 子命令 | 做什麼 |
|---|---|
| `api` | 啟動只讀 API（http://localhost:8000/docs） |
| `daily` | 跑完整一輪每日管線 |
| `pipeline --stages …` | 只跑指定管線段 |
| `ingest` / `embed` / `finmind` / `market-index` | 單段執行 |
| `health` | 管線健康記分板 |
| `serve-flows` | 掛上 Prefect 三條每日排程並常駐 |

只要開 API 給前端用的話，裝輕量那組就好：

```bash
uv sync --extra api && uv run python -m backend api
```

### 前端

```bash
cd frontend && npm ci && npm run dev
```

`vite dev` 會把 `/api/*` 代理到 `http://127.0.0.1:8000`，所以前後端同源、免 CORS。
線上版走 GitHub Pages，建置時用 `VITE_API_BASE` 指到公開 API。

### 測試與 lint

```bash
uv run pytest
```

```bash
uv run ruff check .
```

```bash
cd frontend && npm run check
```

另外兩套測試環境獨立，各自跑：`uv run pytest spider_forge/tests`、`uv run pytest crawler/tests`。

## 系統長什麼樣

```
爬蟲 ──→ ingestion ──→ embedding ──→ clustering ──→ llm ──→ scoring
crawler/   驗證＋標    BGE-M3        去重成事件      摘要    ★重要性
（Scrapy） ticker      1024 維                       分類    市場驗證分數
                                                              │
                              frontend/  ←──  api（只讀 FastAPI）  ←┘
                          （GitHub Pages）      Cloud Run
```

段與段之間不直接傳資料，一律用 `articles.status` 當交接棒：
`pending → vectorized → clustered → summarized → scored`。
每段只吃上一格狀態、做完推進下一格 —— 所以當機可重跑、單段可重跑，不需要任何暫存檔。

排程由 Prefect 跑三條 flow（皆 Asia/Taipei）：新聞每小時（06–23 時整點）、
大盤指數 15:05、FinMind 籌碼 18:10。細節見 [`docs/operations.md`](docs/operations.md)。

## 目錄

| 路徑 | 是什麼 |
|---|---|
| `backend/` | 後端主套件。子模組職責見 [`__init__.py`](backend/__init__.py) |
| `spider_forge/` | AI 爬蟲生成系統。獨立執行，**不在任何管線 flow 內** |
| `crawler/` | 正式 Scrapy 專案。被 `ingestion` 以子程序呼叫，不被 import |
| `frontend/` | 前端（TypeScript + Vite + three.js），唯一版本 |
| `db/` | PostgreSQL DDL 與 migrations |
| `data/` | 共用語料與 ticker stoplist |
| `scripts/` | 一次性維運腳本（資料修復、DB 觀測） |
| `tests/` | 後端整合測試 |
| `GCP/` | Cloud Run 部署腳本與說明 |
| `docs/` | [架構](docs/architecture.md)、[API 契約](docs/api.md)、[維運](docs/operations.md)、[關鍵決策](docs/decisions.md) |

依賴只有一份來源：根目錄 `pyproject.toml`，鎖檔 `uv.lock`（另附 PEP 751 的 `pylock.toml`，是匯出產物，可刪除重建）。

## 邊界

- **只讀 API**：所有分數在處理層算好存庫，API 不運算、不寫入。
- **不給投資建議**：輸出是證據與分數，不是買賣訊號。
- **取不到資料就說取不到**：前端不以模擬值替代真實資料。
