# GCP 部署進度紀錄

**目標：** 把 EventSignal 的 FastAPI 唯讀 API 放上 Google Cloud Run。
**狀態：✅ 已完成**（2026-08-12）

> 範圍說明：這次**只部署 API**。Prefect 排程、爬蟲、embedding、LLM、評分
> 全部維持在本機 Docker 跑，前端也還沒上雲。
>
> ⚠️ **這份是歷史紀錄，不是現況導覽。** 2026-08-12 稍晚 repo 做了目錄重構：
> `workspace/` 那層拿掉、`backend/app/` 變成根目錄的 `backend/`、
> `backend/Dockerfile` 移到根目錄、四份 requirements 併成 `pyproject.toml` + `uv.lock`、
> 前端改名 `frontend/`。下面第 4 節之後提到的路徑指的是**當時**的結構，刻意保留原文不改。
> 現在要部署，照 [`README.md`](README.md) 走就好，指令與腳本都還是同一套。

---

## 🎉 上線資訊

| 項目 | 值 |
|---|---|
| 服務名稱 | `eventsignal-api` |
| 版本 | `eventsignal-api-00001-62d`（100% 流量） |
| 主要網址 | https://eventsignal-api-894746398367.asia-southeast1.run.app |
| 舊格式網址 | https://eventsignal-api-of7exryuwq-as.a.run.app |
| API 文件 | `<網址>/docs` |
| 健康檢查 | `<網址>/health` → `{"status":"ok","db":"ok"}` |
| 事件端點 | `<網址>/api/v1/events` |

> 兩個網址都有效、指向同一個服務。Cloud Run 現在會同時給
> 「專案編號格式」和「舊的雜湊格式」兩種網址。建議對外統一用第一個。

---

## 為什麼要做這件事：上雲前後對照

### 一句話總結

**把「後端 API」從「需要有人啟動的東西」變成「一直都在的東西」。**

### 具體差異

| 面向 | 上雲前（只有本機） | 上雲後（Cloud Run） |
|---|---|---|
| **要用 API 得先做什麼** | 開 Docker Desktop → `docker compose up` → 等容器起來 | 什麼都不用做，打網址就有 |
| **誰能存取** | 只有 `localhost:8000`，**只有自己那台電腦** | 任何人、任何裝置、任何時間，只要有網路 |
| **組員要串 API** | 得自己裝 Python 環境、跑 uvicorn、還要拿到 `.env` 裡的 Neon 帳密 | 把網址寫進前端就好，不需要任何後端環境 |
| **展示 / 口試** | 得帶自己的電腦，現場開 Docker，祈禱不出錯 | 手機開瀏覽器就能秀 |
| **HTTPS 憑證** | 沒有，`http://localhost` | Google 自動配發並續期 |
| **環境一致性** | 「我這邊明明可以跑」的經典問題 | 線上跑的就是那個映像檔，行為一致 |
| **伺服器維護** | 你的電腦就是伺服器，關機就沒了 | 沒有機器要管，Google 負責 |
| **成本** | 電費 | 閒置時 $0（`--min-instances=0`），有流量才計費 |

### 對這個團隊專案最實際的三個好處

**1. Jenny 不用再等你開機**

README 的分工表裡，`frontend_Jenny/` 的任務 T08、T18、T19、T22 全都卡在「串 API」。
以前她要開發前端，得先讓後端跑起來——不是裝一整套 Python 環境，就是等你把電腦開著。
現在給她一個網址就結束了。**這是六人分工的專案裡，解掉的最大一個相依。**

**2. 口試 / 報告不會出意外**

不用當場開 Docker、不用擔心筆電沒電、不用怕 `docker compose` 卡住。
用手機開 `/docs` 就能展示完整 API 契約並即時執行。

**3. 後端這一層從此「不用管」**

程式碼沒改就不用碰它。改了才跑一次 `deploy.ps1`。

### 但這些事情**沒有**改變

誠實講，這次只搬了整個系統的一層：

| 項目 | 現況 |
|---|---|
| **資料更新** | Prefect 管線（爬蟲 → embedding → LLM → 評分）**仍在本機 Docker**。線上 API 只會回 Neon 裡「上次跑管線」的資料 |
| **前端** | `frontend_Jenny/` **仍在本機**，沒有畫面可看，線上只有 JSON 和 `/docs` |
| **改程式碼** | 存檔不會自動更新，要跑 `deploy.ps1` 重新部署才生效 |
| **資料庫** | 本來就在 Neon 雲端，這次沒動 |

所以「打開網址就看到今天五件大事」**還沒達成**——那需要前端也上線。

### 付出的代價

| 代價 | 說明 |
|---|---|
| 多一個部署步驟 | 改完程式碼要記得跑 `deploy.ps1`，本機改完存檔就生效的即時感沒了 |
| `DATABASE_URL` 明文 | 存在 Cloud Run 服務設定裡，任何專案 Viewer 都看得到（見〈關於明文環境變數〉） |
| 多一套東西要顧 | GCP 帳號、計費帳戶、預算提醒 |
| 冷啟動 | 久沒人打的第一個請求要等幾秒（展示前用 `demo-mode.ps1 -On` 可消除） |
| 依賴網路 | 斷網就連不到（本機版至少 localhost 還能跑） |

### 下一步的性價比排序

1. **部署前端** — 工程量小（多半是靜態檔案），但直接讓「給網址就能看畫面」成真
2. **`market_index_daily` + `finmind_after_market` 上 Cloud Run Job** — 只需 `psycopg`/`requests`，
   沒有 GPU，成本近乎零，能讓大盤與籌碼資料每天自動更新
3. **`news_daily` 全鏈上雲** — 卡在 `embedding_Timyo` 需要 torch + 2GB 模型權重，難度高一階

---

## 進度總覽

| # | 步驟 | 狀態 |
|---|---|---|
| 0 | 準備部署設定檔 | ✅ |
| 1 | 安裝 gcloud CLI | ✅ |
| 2 | 登入 Google 帳號 | ✅ |
| 2.5 | 建立計費帳戶 | ✅ |
| 3 | 建立 GCP 專案 | ✅ |
| 3.5 | 本機測試 docker build | ✅ |
| 4 | 綁計費 + 啟用 API + Artifact Registry | ✅ |
| 5 | 建置並部署到 Cloud Run | ✅ |
| 6 | 驗證 API 能用 | ✅ |

---

## 步驟 0：準備部署設定檔

在本機建立 5 個檔案（當時尚未上傳任何東西）：

| 檔案 | 用途 |
|---|---|
| `.gcloudignore` | 控制上傳範圍，特別是擋掉 `.env` |
| `GCP/cloudbuild.yaml` | 指定建置 `api` stage |
| `GCP/setup-gcp.ps1` | 一次性環境建置 |
| `GCP/deploy.ps1` | 可重複執行的部署腳本 |
| `GCP/README.md` | 技術說明 |

（上表已更新為重構後的路徑，因為這五個檔案現在還在用；下方的敘述文字維持原樣。）

事前確認的三件事：

- API 路徑（`main.py` → `db/session.py` → `core/config.require`）**只需要 `DATABASE_URL`**，其餘 token 都是 worker 在用
- `core/config.load_env()` 用 `os.environ.setdefault`，**真環境變數優先**，所以 `.env` 完全不必進映像檔
- Neon 在 `ap-southeast-1`（新加坡），故 Cloud Run 也選新加坡

---

## 步驟 1：安裝 gcloud CLI

下載執行 `GoogleCloudSDKInstaller.exe`，全部預設，**裝完重開 PowerShell**（PATH 才會生效）。

結果：`Google Cloud SDK 580.0.0`

---

## 步驟 2：登入 Google 帳號

```powershell
gcloud auth login              # → jsv1001jsv@gmail.com
gcloud billing accounts list   # → Listed 0 items.（發現沒有計費帳戶）
```

---

## 步驟 2.5：建立計費帳戶

**踩坑：** 在 GCP 的 `/freetrial/signup/` 表單新增信用卡時跳出 `OR-RWE-03`。

`OR-RWE-03` 是 Google Payments（不是 GCP）的通用錯誤，意思是「卡片驗證未通過」。

**解法：** 不要在 GCP 的嵌入式表單加卡。改成——

1. 先到 <https://pay.google.com/payments/home> 把卡加進 Google Payments ✅
2. 再回 <https://console.cloud.google.com/freetrial> 走試用註冊
3. 付款方式下拉選單會出現已存好的卡片，直接選 → 一次過

**結果：** 計費帳戶 `01E030-613B23-643B9C`，含 $300 / 90 天試用金。

---

## 步驟 3：建立 GCP 專案

```powershell
gcloud projects create eventsignal-nash-2026 --name="EventSignal"
gcloud config set project eventsignal-nash-2026
```

**踩坑：** `gcloud config set project` **不會驗證專案是否存在**，只是寫本機設定值。
所以看到 `Updated property` 不代表專案建成功了。要確認得用：

```powershell
gcloud projects list
```

**結果：** 專案 `eventsignal-nash-2026`（編號 894746398367）確實存在。

> 順帶發現帳號下有一堆 Google 自動建的專案（`gen-lang-client-*` 是 AI Studio 申請
> Gemini 金鑰時建的、`dazzling-byway-*` 是首次進 Console 時建的、`project-<uuid>` 是
> Firebase/AI Studio 建的）。都是空的不收費。
> **注意：`gen-lang-client-*` 綁著正在用的 Gemini 金鑰，不要刪。**

---

## 步驟 3.5：本機測試 docker build

上雲前先在本機驗一次，建置失敗在本機除錯比翻 Cloud Build log 快得多。

```powershell
cd C:\final_project_version\Stock-information-platform\workspace
docker build --target api -f backend/Dockerfile -t eventsignal-api:test .
```

**建置結果：✅ 55.6 秒**

- build context 傳輸量 **7.10 MB**（與 `.gcloudignore` 預估的 7.2 MB 相符）
- `--target api` 正確停在 api stage，沒有誤建 worker

```powershell
docker run --rm -p 8000:8000 --env-file .env eventsignal-api:test
# → 失敗：port is already allocated（docker-compose 的 api 服務已佔用 8000）
docker run --rm -p 8001:8000 --env-file .env eventsignal-api:test   # 改用 8001
```

**啟動結果：✅** `http://localhost:8001/health` 回 ok
（此端點會實際連 Neon，等於同時驗證容器與資料庫）

---

## 步驟 4：綁計費 + 啟用 API + Artifact Registry

```powershell
.\setup-gcp.ps1 -ProjectId eventsignal-nash-2026 -BillingAccount 01E030-613B23-643B9C
```

### 踩坑 1：PowerShell 讀 .ps1 的編碼

腳本原本存成 UTF-8 **無 BOM**，Windows PowerShell 5.1 會改用 **Big5** 解讀。
中文註解變亂碼後夾帶 `\` 或 `'`，語法整個爆掉，而且**錯誤行號會指到無關的地方**。

→ **修正：兩支 `.ps1` 都改存 UTF-8 with BOM。**

### 踩坑 2：`$ErrorActionPreference = 'Stop'` 撞上 gcloud 的 stderr

`gcloud artifacts repositories describe` 查無資料時會寫 stderr（**這是預期行為**，
repo 本來就還沒建）。Stop 模式下 PowerShell 5.1 會把原生指令的 stderr 當成終止錯誤
——**加 `2>$null` 也擋不掉**。腳本因此停在建立 Artifact Registry 之前。

同樣的地雷在 `deploy.ps1` 更嚴重：`gcloud builds submit` 會把整份建置日誌寫到 stderr。

→ **修正：改成 `$ErrorActionPreference = 'Continue'`，用 `$LASTEXITCODE` 手動判斷成敗；
探測型呼叫改用 `2>&1` 整包吞掉。**

### 完成後狀態

| 項目 | 值 |
|---|---|
| 計費 | ✅ 已連結 `01E030-613B23-643B9C` |
| 已啟用 API | `run` / `cloudbuild` / `artifactregistry` |
| Artifact Registry | `asia-southeast1-docker.pkg.dev/eventsignal-nash-2026/eventsignal` |

---

## 步驟 5–6：部署與驗證

```powershell
.\deploy.ps1 -ProjectId eventsignal-nash-2026
```

流程：讀 `.env` 取 `DATABASE_URL` → Cloud Build 建映像檔並推上 Artifact Registry
→ 部署 Cloud Run → 自動打 `/health`

**結果：✅ HTTP 200 `{"status":"ok","db":"ok"}`**

`db:"ok"` 代表 Cloud Run 連得到新加坡的 Neon，**不需要處理 IP 白名單**。

---

## 「上傳了什麼」的完整解答：三次過濾、四個階段

> 這題最容易搞混，因為「上傳」有兩個意思：**寄過去的整箱材料** ≠ **做出來的成品**。
> 「整個 `backend/` 都上傳了嗎？」→ **到階段 ② 為止是對的；到階段 ③ 就只剩 `app/`。**

```
① 你的電腦          ② Cloud Storage        ③ Artifact Registry     ④ Cloud Run
  workspace 全部  ──▶   建置材料暫存     ──▶      容器映像檔      ──▶   實際執行中
   30.4 MB              7.2 MB                  6.9 MB               5 個模組
   332 檔               213 檔
            ↑                    ↑                        ↑
      .gcloudignore        Dockerfile COPY          main.py import
```

### 階段 ①→②：`.gcloudignore` 過濾（30.4 MB → 7.2 MB）

| 帶過去 | 留下（不上傳） |
|---|---|
| `backend/`（幾乎全部） | **`.env` ← 機密，最重要的一項** |
| `scripts/`、`GCP/` | `frontend_Jenny/` 11 MB |
| `docker-compose.yml` | `data/` 13 MB（僅留 `ticker_stoplist.json`） |
| `data/ticker_stoplist.json` | `DATABASE/`、`runtime/`、`experiments_everyone/` |
| `.env.example`、`.dockerignore` | `backend/tests/`、所有 `*.md` |

### 階段 ②→③：`Dockerfile` 的 COPY 挑選（7.2 MB → 6.9 MB）

**這一關是最容易誤解的地方。** `backend/` 整包已經到了 Cloud Storage，
但 `backend/Dockerfile` 的 api stage 只有兩行 COPY：

```dockerfile
COPY backend/requirements-api.txt /tmp/requirements-api.txt
COPY --chown=eventsignal:eventsignal backend/app ./app
```

| 進映像檔 | 停在 Cloud Storage（人在 GCP，但不會執行） |
|---|---|
| `backend/app/` — 6,920 KB | `backend/crawler_runtime/` — 116 KB |
| `backend/requirements-api.txt` | `backend/uv.lock` — 214 KB |
| | `backend/pyproject.toml` |
| | `backend/requirements.txt`、`requirements-worker.txt` |
| | `backend/Dockerfile` 本身 |
| | `scripts/`、`GCP/`、`docker-compose.yml` |

### 階段 ③→④：`main.py` 的 import 鏈

映像檔裡有**整個** `app/`，但實際跑起來只碰得到 5 個模組。

| 實際執行 | 有帶去但永遠不會執行（約 1 MB） |
|---|---|
| `app/main.py` | `app/spider_forge_system/` — 584 KB |
| `app/api/` | `app/llm_MMJSUN/` — 284 KB |
| `app/core/` | `app/clustering_Timyo/` — 92 KB |
| `app/db/` | `app/scoring_Bright/`、`finmind_Bright/` |
| `app/schemas/`、`app/services/` | `app/embedding_Timyo/`、`crawler_Arku/`、`orchestration/` |
| `app/static/` — 5,800 KB（圖片） | `app/market_index/` |

> 這些「死程式碼」無害：它們的相依套件（torch、scrapy、prefect）
> 都沒裝進 api 映像檔，就算被誤 import 也會直接 ImportError，不會偷偷跑起來。
> 要清掉得改 Dockerfile 的 COPY 只複製需要的子目錄，但那會讓 Dockerfile
> 與團隊其他人的用法脫節，現階段不值得。

### 另外注入的（不在映像檔裡）

| 項目 | 位置 |
|---|---|
| `DATABASE_URL` | Cloud Run 服務設定，**明文環境變數** |

### ⚠️ 資料本身完全不在 GCP 上

事件、新聞、籌碼資料全部存在 **Neon（新加坡）**。
Cloud Run 每收到一個請求就去 Neon 撈一次，容器本身是用完即丟的，
寫進容器的檔案下次重啟就消失。

---

## 最終上傳到 GCP 的東西

### ✅ 有上傳

| 項目 | 內容 | 存在哪 |
|---|---|---|
| 程式碼壓縮檔 | 7.2 MB，`.gcloudignore` 過濾後的 workspace | Cloud Storage（建置暫存） |
| 容器映像檔 | `python:3.12-slim` + fastapi/uvicorn/pydantic/psycopg/tzdata + **整個 `backend/app/`（7.1 MB）** | Artifact Registry |
| `DATABASE_URL` | Neon 連線字串（**明文**環境變數） | Cloud Run 服務設定 |

> 映像檔含 `backend/app/` **全部內容**，包括 `spider_forge_system/`、`llm_MMJSUN/`、
> `embedding_Timyo/` 等約 1 MB 永遠不會被執行的程式碼（`main.py` 的 import 鏈只碰
> `api`/`core`/`db`/`schemas`/`services`）。它們的相依套件沒裝，誤 import 會直接
> ImportError，無害。`static/` 的 5.8 MB 圖片是必要的。

### ❌ 沒上傳

| 項目 | 原因 |
|---|---|
| `.env` 檔本身 | `.gcloudignore` 排除，已用 pathspec 實測確認 |
| `FINMIND_TOKEN`、`DEEPSEEK_API`、`KIMI_API`、`LLM_API_KEY`、`PREFECT_DB_PASSWORD` | API 用不到 |
| `frontend_Jenny/`（11 MB） | 前端未部署 |
| `data/`（13 MB，僅留 `ticker_stoplist.json`）、`DATABASE/`、`experiments_everyone/`、`runtime/`、`backend/tests/`、`backend/crawler_runtime/` | API 用不到 |
| Prefect / 爬蟲 / embedding / LLM / 評分 | 仍在本機 Docker |
| 資料本身 | 繼續存在 Neon（新加坡） |

---

## 日常操作

**更新線上版本**（改完程式碼後）：

```powershell
cd C:\final_project_version\Stock-information-platform\workspace\GCP
.\deploy.ps1 -ProjectId eventsignal-nash-2026
```

**看 log：**

```powershell
gcloud run services logs read eventsignal-api --region=asia-southeast1 --limit=50
```

**看目前設定：**

```powershell
gcloud run services describe eventsignal-api --region=asia-southeast1
```

---

## 步驟 7：設定預算提醒 ✅

```powershell
gcloud services enable billingbudgets.googleapis.com --project=eventsignal-nash-2026

gcloud billing budgets create --billing-account=01E030-613B23-643B9C --display-name="EventSignal 每月預算" --budget-amount=300 --threshold-rule="percent=0.5" --threshold-rule="percent=0.9" --threshold-rule="percent=1.0,basis=forecasted-spend" --filter-projects="projects/eventsignal-nash-2026"
```

**結果：** `Created [5ab293fc-9609-4b17-9600-d91ceab98022]`

| 設定 | 值 |
|---|---|
| 金額 | TWD 300 / 月 |
| 範圍 | 僅 `eventsignal-nash-2026` 專案 |
| 門檻 | 實際花費 50%、90%；**預測**花費 100% |
| 收件者 | 計費帳戶管理員（`jsv1001jsv@gmail.com`） |

### ⚠️ 預算提醒只會「通知」，不會「停止」花費

超過門檻 Google 只寄信，服務照跑、費用照算。這是最常見的誤解。
真要自動斷閘得接 Pub/Sub + Cloud Function 解除計費綁定，以目前用量沒必要。

實際的防護是這幾層，預算提醒只是最後一道保險：

1. 試用期間額度用完，Google 會自動暫停而非偷偷扣款
2. `--min-instances=0`：沒人打就沒有實例在跑，不計費
3. `--max-instances=2`：被灌流量最多也只起 2 個實例

### 踩坑：PowerShell 的兩個字元陷阱

**反引號前面必須有空格。** 寫成 `...forecasted-spend\`` （緊貼）時，
反引號不是換行接續而是轉義字元，會把下一行黏成同一個參數，
指令靜默失敗、完全沒有輸出。

**逗號要用引號包起來。** PowerShell 的 `,` 是陣列運算子，
`--threshold-rule=percent=1.0,basis=forecasted-spend` 會被拆成兩個元素，
gcloud 收到的是空格分隔的 `percent=1.0 basis=forecasted-spend` 而報錯。
→ 正解：`--threshold-rule="percent=1.0,basis=forecasted-spend"`

---

## 待辦
- [ ] 改用唯讀的 Neon role（API 本就唯讀，可大幅降低明文連線字串外流的衝擊）
- [ ] 部署前端 `frontend_Jenny`，並把正式網域加進 `main.py` 的 CORS `allow_origins`
      （目前只允許 localhost，前端上線後不改會被 CORS 擋）
- [ ] 接 `psycopg_pool`（現在每個請求開一條新連線，故 `--concurrency=10 --max-instances=2` 壓著）
- [ ] 若要每日自動更新資料，需另外部署 Prefect worker（吃 GPU，成本與複雜度高一階）
