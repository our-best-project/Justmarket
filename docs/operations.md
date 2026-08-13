# 維運

## 一個入口

```bash
uv run python -m backend --help
```

值得跑第二次的操作都有名字，`--help` 就是完整清單。以前這些指令散落在 README、殼腳本
與各人的終端機歷史裡，結果是 VM 上的殼腳本與 repo 漂移了也沒人發現。

## 每日一輪

```bash
uv run python -m backend daily
```

七個步驟，每步一個子程序：爬蟲 → 向量化 → 分群 → 籌碼 → 大盤指數 → 篩選/LLM/評分 → 健康記分板。
單步崩潰不拖垮整輪，但失敗會被記錄並反映在 exit code。

只看要跑哪些步驟、不執行：

```bash
uv run python -m backend daily --list
```

步驟清單是程式碼（`backend/daily.py` 的 `STEPS`），由 `tests/test_daily_steps.py`
斷言完整性與順序。少一步，CI 先紅。

補跑某幾段：

```bash
uv run python -m backend pipeline --stages llm,scoring
```

## 排程（Prefect）

三條 deployment，時區皆 Asia/Taipei：

| flow | 排程 | 做什麼 |
|---|---|---|
| `news-hourly-taipei` | 每小時（06–23 時整點） | 爬蟲 → Neon → 向量化 → 聚類 → LLM → 評分 |
| `market-index-daily-taipei` | 每日 15:05 | 八大指數最近收盤日線 |
| `finmind-after-market-taipei` | 每日 18:10 | FinMind 全量籌碼 + 事件市場驗證重評 |

掛上排程並常駐：

```bash
uv run python -m backend serve-flows
```

FinMind 盤後資料偶爾延遲。批次是冪等的，所以「沒有新日期就結束」是安全的行為，
需要時多排一班補撿即可。

## 容器

```bash
docker compose up -d
```

起 `api`（8000）、`prefect-runner`（GPU）、以及 Prefect 的 server / services / db / redis。
Prefect UI 在 http://localhost:4200。

⚠️ `prefect-db` 存的是排程 metadata，不是應用資料。應用資料一律在 Neon。

手動才起的兩個（`profiles: manual`，不隨 `up` 啟動）：

```bash
docker compose --profile manual run --rm spider-forge run --url "https://example.com/news"
```

`crawler` 容器同時是 spider_forge 的沙盒：`read_only`、`cap_drop: ALL`、
`no-new-privileges`、pids/mem/cpu 都有上限。控制容器持有金鑰與 docker socket，
候選子容器兩者都拿不到。

## 映像檔與依賴

依賴只有一份來源：根目錄 `pyproject.toml` + `uv.lock`。三個映像檔各裝自己那組 extra：

| 映像檔 | extra | 為什麼分開 |
|---|---|---|
| `api` | `api` | 不含 torch／scrapy，維持小體積與快啟動 |
| `worker` | `worker` + `crawler` | 整條管線都在這裡跑 |
| `spider-forge` | `spider-forge` | 另含 docker CLI 與 chromium |

一律 `uv sync --frozen` —— 鎖檔說了算，build 不會偷偷解出不同版本。
`pylock.toml` 是 PEP 751 匯出產物，可刪除重建：

```bash
uv export --format pylock.toml --all-extras -o pylock.toml
```

## API 部署（Cloud Run）

腳本與完整說明在 [`GCP/README.md`](../GCP/README.md)（Nash 建的，維持原本的檔名與用法）：

```powershell
.\deploy.ps1 -ProjectId eventsignal-nash-2026 -WhatIfOnly
```

流程是「讀 `.env` 取 `DATABASE_URL` → Cloud Build 建 api stage → 部署 Cloud Run → 打 `/health` 驗證」。
build context 是 repo 根目錄，`.gcloudignore` 控制上傳範圍（也負責擋掉 `.env`）。

⚠️ 只部署 **API**。Prefect 排程、爬蟲、embedding、LLM、評分都還在本機／GCE 跑。

### 兩個 ignore 檔不是同一套語法

重構後首次部署連續失敗兩次，兩次都出在這裡。**改任何一個 ignore 檔之前先讀這段。**

| 檔案 | 誰在用 | 語法 | 一行 `db/` 會擋掉 |
|---|---|---|---|
| `.dockerignore` | 本機 `docker build` | Docker patternmatcher | **只有根目錄的 `db/`** |
| `.gcloudignore` | `gcloud builds submit` | **gitignore 語法** | **任何層級的 `db/`**，含 `backend/db/` |

gitignore 的規則是：pattern 裡沒有斜線時比對**任何深度**。所以要限定根目錄
就得寫 `/db/`。目前 `.gcloudignore` 的目錄規則都已加上開頭斜線，見
[`decisions.md` #11](decisions.md)。

後果是「**本機建得起來、Cloud Build 建不起來**」——因為兩邊過濾出的檔案根本不同。

實際踩過的兩次：

**1. `COPY failed: stat README.md: file does not exist`**

`.gcloudignore` 有一條 `*.md`，把 README 一起濾掉了。但 Dockerfile 第一段就是
`COPY pyproject.toml uv.lock README.md ./`——`uv sync` 建置本專案時，
hatchling 會依 `pyproject.toml` 的 `readme` 欄位去讀它，缺檔直接失敗。
→ 修法：`.gcloudignore` 補一行 `!README.md`。

**2. `ModuleNotFoundError: No module named 'backend.db'`**

`.gcloudignore` 有一條 `db/`（原意是擋根目錄那個放 DDL 的 `db/`），
gitignore 語法下連 `backend/db/` 一起殺掉，檔案在**上傳階段**就消失，
Docker 根本沒機會 COPY。容器啟動時 import 失敗 exit(1)。
→ 修法：改成 `/db/`。

第 2 個症狀在 Cloud Run 顯示為：

```
The user-provided container failed to start and listen on the port
defined provided by the PORT=8000 environment variable
```

**這行訊息會誤導。** 它是所有啟動失敗的通用訊息，跟 port 設定通常無關——
容器在 import 階段就崩了，根本沒活到去聽 port。**不要去調 port，先看 log。**

### 部署失敗時的診斷順序

```powershell
# 1. Cloud Build 失敗（映像檔沒建出來）→ 看建置 log 的最後幾行
gcloud builds list --limit=3
gcloud builds log <BUILD_ID> | Select-Object -Last 30

# 2. Cloud Run 失敗（映像檔有了但容器起不來）→ 看該 revision 的執行 log
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.revision_name=<REVISION>" `
  --limit=50 --format="value(textPayload)" --project=eventsignal-nash-2026
```

失敗的 revision **不會**接管流量，舊版持續服務中，可以安心慢慢查。

### 改 ignore 檔後的本機快速驗證

不用等三分鐘的 Cloud Build：

```powershell
docker build --target api -t justmarket-api:test .
docker run --rm justmarket-api:test ls /opt/justmarket/backend
```

⚠️ 但這只驗得到 `.dockerignore`。`.gcloudignore` 的效果只有真的 submit 才看得出來——
`gcloud builds submit` 開頭那行 `Creating temporary archive of N file(s)` 的 **N**
是最快的檢查點：檔案數突然變少就是有規則誤傷。

> 實測：`README.md` 修好後 160 → 165 檔，`/db/` 修好後 165 → 207 檔。

## 前端部署（GitHub Pages）

`.github/workflows/pages.yml` 在 push 到 main 時建置 `frontend/` 並發佈。

兩個 build 期變數：

- `BASE_PATH` —— workflow 自動帶 `/<repo>/`，Pages 站台掛在子路徑底下，資源路徑要有這一段。
- `VITE_API_BASE` —— 取自 repo variable `API_BASE`。**Pages 是靜態站，沒有 proxy，
  API 位址必須是絕對網址**。目前設為 Cloud Run 的 `/api/v1`。
  改的位置：Settings → Secrets and variables → Actions → Variables。

沒設 `API_BASE` 時前端會退回相對路徑並顯示「取不到資料」的錯誤頁 —— 這是刻意的，
不以模擬值冒充真實資料。

Pages 的 origin 已經寫在 `backend/main.py` 的 `DEFAULT_ORIGINS` 裡，
不需要另外設環境變數；`tests/test_cors.py` 守著它不被改掉。
⚠️ 但 CORS 是**後端**的設定 —— 改完要重新部署 Cloud Run 才會生效。

## 本機開發（還沒有 Cloud Run 也能跑）

兩個終端機：

```bash
uv run python -m backend api
```

```bash
cd frontend && npm run dev
```

`vite dev` 把 `/api/*` 代理到 `http://127.0.0.1:8000`，前後端同源、免 CORS。

⚠️ proxy 目標寫 `127.0.0.1` 不能寫 `localhost`：Node ≥17 會把 localhost 優先解析成 IPv6
`::1`，而 uvicorn 預設只聽 IPv4 —— 連不上時 Vite 會靜默回 index.html（HTTP 200 但
content-type 是 text/html），前端會誤判成資料格式錯，極難察覺。

## 出事時先看哪裡

| 症狀 | 先看 |
|---|---|
| API 起不來 | `DATABASE_URL` 有沒有設。連線池在啟動就開，設定錯會立刻失敗而不是等第一個請求 |
| `/health` 回 503 | 服務活著但 Neon 連不上 |
| 事件不更新、pending 一直漲 | `uv run python -m backend health`，看是哪一段沒推進 status |
| 「今日事件」在半夜查錯日 | 連線的 timezone 設定（見 [`api.md`](api.md)） |
| 前端整頁錯誤 | 那是預期行為：取不到資料就報錯，不退回假資料。先確認 API 與 CORS |
| 前端說被 CORS 擋 | 先確認 Cloud Run 跑的是不是最新映像檔。`DEFAULT_ORIGINS` 改了但沒重新部署，線上還是舊的 |
| Cloud Build 失敗在某個 `COPY` | `.gcloudignore` 濾掉了那個檔。注意它是 **gitignore 語法**，裸寫的規則會比對任何層級 |
| Cloud Run 說「failed to start and listen on PORT」 | **通常不是 port 的問題。** 去看 revision 的執行 log，多半是 import 失敗 |

## 維運腳本

`scripts/` 底下是一次性工具（資料修復、DB 觀測、灌語料），不進映像檔、不進排程。
它們要在裝好套件的環境跑：

```bash
uv run python scripts/peek_db.py
```
