# EventSignal API 部署到 Cloud Run

只部署 `backend/Dockerfile` 的 **api** stage（FastAPI 只讀 API）。
Prefect runner、爬蟲、embedding 都還在本機 Docker 跑，這次不動。

---

## 這個資料夾有什麼

| 檔案 | 用途 | 你會用到嗎 |
|---|---|---|
| `setup-gcp.ps1` | 一次性環境建置（建專案、綁計費、啟用 API、建映像檔庫） | 已跑過，不用再跑 |
| `deploy.ps1` | **改完程式碼後跑這支更新線上版本** | 常用 |
| `cloudbuild.yaml` | 告訴 Cloud Build 只建 `api` stage | 不用手動碰 |
| `README.md` | 本檔，技術說明與排錯 | 卡住時查 |
| `gcp_進度紀錄.md` | 部署過程的完整流水帳與踩過的坑 | 回顧／交接時看 |

### ⚠️ 為什麼 `.gcloudignore` 不在這個資料夾

`workspace/.gcloudignore` **必須留在 workspace 根目錄**，不能搬進來。

`gcloud builds submit` 只會讀 build context 根目錄的那一份。搬走等於沒有過濾規則，
後果是 **`.env`（含 Neon 帳密）會被打包上傳到 Cloud Build 的暫存 bucket**，
而且 build context 會從 7.2 MB 膨脹回 30 MB。

同理，這個資料夾**不能改名**，否則 `deploy.ps1` 裡的
`$BuildConfig = 'GCP/cloudbuild.yaml'` 會找不到檔案。真要改名，那一行也要一起改。

---

## 這次的設計決定

| 項目 | 決定 | 理由 |
|---|---|---|
| 託管服務 | Cloud Run | API 無狀態、requirements-api.txt 只有 5 個套件，冷啟動快；沒流量時不計費 |
| 區域 | `asia-southeast1`（新加坡） | 與你的 Neon 同城。理由見下一節 |
| 資料庫 | 維持 Neon（不搬） | 目前沒有搬遷需求，Cloud Run 直接連外部 Postgres 沒問題 |
| 機密處理 | **明文環境變數** | 依你的要求，步驟最少。風險與後續改法見下方〈關於明文環境變數〉 |
| 容器 port | 8000 | Dockerfile 寫死 `--port 8000`；用 Cloud Run 的 `--port=8000` 對齊，不用改 Dockerfile |

---

## 區域為什麼選新加坡，不選台灣

我讀了你 `.env` 裡的 `DATABASE_URL`，Neon 執行個體在 **`ap-southeast-1`（新加坡）**。

直覺會選 `asia-east1`（彰化），因為使用者在台灣。但這裡有個關鍵細節：
`db/session.py` 的 `get_conn()` 是**每個請求開一條全新的 psycopg 連線**。
新連線 = TCP 三次握手 + TLS 交握，來回好幾趟。

| Cloud Run 放哪 | 使用者 → API | API → Neon（每次請求都要重新連） |
|---|---|---|
| `asia-east1`（台灣） | 快，約 5ms | **慢，跨海約 150ms＋** |
| `asia-southeast1`（新加坡） | 約 40ms | 快，同城約 1ms |

使用者延遲只付一次，資料庫連線延遲每個請求都要付。所以放新加坡整體比較快。

**要改回台灣的話**，兩個腳本和 `cloudbuild.yaml` 的 region 都要一起改：

```powershell
.\setup-gcp.ps1 -ProjectId <你的專案> -BillingAccount <你的帳戶> -Region asia-east1
.\deploy.ps1    -ProjectId <你的專案> -Region asia-east1
```

> 真正的解法是把 `psycopg_pool` 接上（`session.py` 註解自己也提到了）。
> 有了連線池就不必每次重連，那時區域選哪裡就沒差了，可以搬回台灣。

---

# 💻 換一台電腦要怎麼做（學校電腦、組員電腦）

> **重點觀念：GCP 上的東西已經建好了，換電腦不需要重建。**
> 專案、計費、Artifact Registry、Cloud Run 服務都在雲端，跟你用哪台電腦無關。
> 新電腦只需要「能操作它們的工具」而已。
>
> ⚠️ **絕對不要在新電腦上跑 `setup-gcp.ps1`。** 沒必要（資源都在），
> 而且它會強制要求 `-BillingAccount` 參數，徒增困擾。

先確認你要做哪件事，三種情境需要的東西差很多：

| 情境 | 需要什麼 | 時間 |
|---|---|---|
| **A. 只是要展示 / 給人看** | 只要瀏覽器 | 0 分鐘 |
| **B. 要重新部署（改了程式碼）** | gcloud + 程式碼 + `.env` | 約 15 分鐘 |
| **C. 學校電腦不給裝軟體** | 只要瀏覽器（用 Cloud Shell） | 約 5 分鐘 |

---

## 情境 A：只是要展示

**什麼都不用裝。** 服務跑在 Google 的機器上，跟你的電腦無關。

開瀏覽器輸入：

```
https://eventsignal-api-894746398367.asia-southeast1.run.app/docs
```

就這樣。沒有 Python、沒有 Docker、沒有虛擬環境。

> 唯一要注意：太久沒人打會有幾秒冷啟動。在意的話，
> 展示前在**自己的電腦**上先跑 `.\demo-mode.ps1 -On`（見〈現場展示 SOP〉）。

---

## 情境 B：要在新電腦上重新部署

### B-1. 安裝 gcloud CLI

1. 下載執行 <https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe>
2. 一路 Next，保持預設
3. **關掉 PowerShell 重開一個新視窗**（PATH 才會生效）

```powershell
gcloud version
```

> 學校電腦沒有安裝權限的話，直接跳到**情境 C**。

### B-2. 登入並指定專案

```powershell
gcloud auth login
gcloud config set project eventsignal-nash-2026
gcloud config set run/region asia-southeast1
```

用**同一個 Google 帳號**（`jsv1001jsv@gmail.com`）登入。
驗證看得到既有服務：

```powershell
gcloud run services list --region=asia-southeast1
```

看到 `eventsignal-api` 就代表接上了。

### B-3. 取得程式碼

git clone 或直接複製整個 `workspace` 資料夾。

**必須包含這兩個檔案，少一個就不能部署：**

| 檔案 | 為什麼容易漏 |
|---|---|
| `workspace/.gcloudignore` | 是隱藏檔（開頭是 `.`），檔案總管預設看不到，複製資料夾時常常漏掉 |
| `workspace/.env` | **`.gitignore` 第 6 行擋著它，git clone 一定拿不到** |

### B-4. ⚠️ 補上 `.env`（最容易卡住的一步）

`.env` **不在版控裡**，新電腦上一定沒有。三種取得方式：

1. 從舊電腦用隨身碟／私訊複製過去（最快）
2. 複製 `.env.example` 成 `.env`，再填入真值
3. 跟組員要

`deploy.ps1` 只需要其中一個變數：

```
DATABASE_URL=postgresql://<user>:<password>@<host>/<db>?sslmode=require
```

> 只是要部署 API 的話，其他 15 個變數（`FINMIND_TOKEN`、`LLM_API_KEY`、
> `DEEPSEEK_API`、`KIMI_API` 等）**可以留空**——它們是資料管線在用的，
> API 路徑完全不碰。

沒有 `.env` 時 `deploy.ps1` 會直接停下並告訴你，不會靜默失敗。

### B-5. 部署

```powershell
cd <你的路徑>\workspace\GCP
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass   # 若跳出指令碼被停用才需要

.\deploy.ps1 -ProjectId eventsignal-nash-2026 -WhatIfOnly    # 先空跑確認路徑正確
.\deploy.ps1 -ProjectId eventsignal-nash-2026                # 確認無誤再真的跑
```

`-WhatIfOnly` 只印出將執行的指令、不會真的部署。
**印出的 `workspace :` 路徑要正好指到 `workspace` 資料夾**，多一層少一層都是錯的。

### B-6. 不需要做的事

| 項目 | 為什麼不用 |
|---|---|
| `setup-gcp.ps1` | GCP 資源已存在，重跑無意義 |
| 建計費帳戶 | 已經有了 |
| Docker Desktop | **只有想在本機測試建置才需要**。Cloud Build 在雲端跑，不用本機 Docker |
| 裝 Python / 建虛擬環境 | 部署完全不需要 |

---

## 情境 C：學校電腦不給裝軟體 → 用 Cloud Shell

Google 提供瀏覽器內的 Linux 終端機，**gcloud 已經預裝好了**，
不用安裝任何東西，也不需要管理員權限。

### C-1. 開啟

<https://console.cloud.google.com/?cloudshell=true&project=eventsignal-nash-2026>

或在 Console 右上角點 `>_` 圖示。第一次會花約 30 秒配置環境。

### C-2. 確認身分與專案

```bash
gcloud config set project eventsignal-nash-2026
gcloud run services list --region=asia-southeast1
```

Cloud Shell 會自動用你登入 Console 的帳號，**不需要再 `gcloud auth login`**。

### C-3. 常用查詢（不需要程式碼就能做）

```bash
# 拿服務網址
gcloud run services describe eventsignal-api --region=asia-southeast1 --format='value(status.url)'

# 看 log
gcloud run services logs read eventsignal-api --region=asia-southeast1 --limit=50

# 展示模式開／關
gcloud run services update eventsignal-api --region=asia-southeast1 --min-instances=1
gcloud run services update eventsignal-api --region=asia-southeast1 --min-instances=0
```

### C-4. 要在 Cloud Shell 重新部署

可以，但 `.ps1` 腳本在 Linux 上跑不了，得手動下指令：

```bash
git clone <你的 repo>
cd <repo>/workspace

# .env 不在版控裡，手動建一個最小版本
echo 'DATABASE_URL=postgresql://<user>:<pw>@<host>/<db>?sslmode=require' > .env

gcloud builds submit --config=GCP/cloudbuild.yaml \
  --substitutions=_REGION=asia-southeast1,_REPO=eventsignal,_IMAGE=api

printf "DATABASE_URL: '%s'\n" "$(grep '^DATABASE_URL=' .env | cut -d= -f2-)" > /tmp/env.yaml
gcloud run deploy eventsignal-api \
  --image=asia-southeast1-docker.pkg.dev/eventsignal-nash-2026/eventsignal/api:latest \
  --region=asia-southeast1 --port=8000 --env-vars-file=/tmp/env.yaml \
  --allow-unauthenticated --memory=512Mi --min-instances=0 --max-instances=2 --concurrency=10
rm /tmp/env.yaml
```

> ⚠️ Cloud Shell 的家目錄會保留，但**閒置一段時間後環境會被回收**。
> 別把 `.env` 長期留在上面，用完就 `rm .env`。

---

## 換電腦排錯

| 現象 | 原因 |
|---|---|
| `gcloud : 無法辨識` | 裝完沒重開 PowerShell |
| `因為這個系統上已停用指令碼執行` | 跑 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| `.env 不存在` | `.gitignore` 擋著，git clone 拿不到，要手動補（見 B-4） |
| build context 突然變成 30 MB | `.gcloudignore` 沒複製到（隱藏檔，容易漏） |
| `PERMISSION_DENIED` | 登入的 Google 帳號不對，`gcloud auth list` 確認 |
| `deploy.ps1` 說找不到 `cloudbuild.yaml` | 不是在 `GCP\` 資料夾裡執行，或資料夾被改名 |
| 腳本開頭噴一堆語法錯誤 | `.ps1` 的 UTF-8 BOM 掉了（複製工具造成），見〈常見錯誤〉 |

---

## 執行順序（首次從零建置，僅供參考）

> 下面是**當初從零開始**的完整流程，已經跑過一次了。
> 換電腦請看上面的〈換一台電腦要怎麼做〉，不要重跑這一段。

### 步驟 0：安裝 gcloud CLI（一次性）

1. 下載並執行 <https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe>
2. 一路 Next，保持預設（含 Bundled Python）
3. **關掉 PowerShell 重開一個新視窗** — 安裝程式改的是系統 PATH，舊視窗讀不到

驗證：

```powershell
gcloud version
```

### 步驟 1：查你的計費帳戶 ID

Cloud Run 需要專案綁定有效計費帳戶（即使用量在免費額度內也要綁）。
還沒有的話先到 <https://console.cloud.google.com/billing> 建一個。

```powershell
gcloud auth login
gcloud billing accounts list
```

記下 `ACCOUNT_ID` 欄位，格式像 `01ABCD-234567-89EFGH`。

### 步驟 2：建置 GCP 環境（一次性）

```powershell
cd C:\final_project_version\Stock-information-platform\workspace\GCP

.\setup-gcp.ps1 -ProjectId eventsignal-prod-2026 -BillingAccount 01ABCD-234567-89EFGH
```

> `-ProjectId` 是**全 GCP 唯一**的，撞名會失敗，換一個就好。

這支會建專案、綁計費、啟用 Run / Cloud Build / Artifact Registry 三個 API、建映像檔庫。
可重複執行，中途失敗直接重跑。

### 步驟 3：部署

```powershell
.\deploy.ps1 -ProjectId eventsignal-prod-2026
```

首次約 3–5 分鐘（大多花在 Cloud Build 裝 Python 套件）。之後每次更新程式碼就重跑這一支。

跑完會印出服務網址，並自動打一次 `/health` 驗證。

---

## 部署前務必確認：Neon 的連線來源限制

**這是最可能讓你卡住的一點。**

Cloud Run 的對外 IP 是浮動的，每次擴縮都可能不同。如果你的 Neon 專案設了 IP Allow list，
Cloud Run 會連不上，`/health` 會回 **503**（服務本身是活的，但資料庫不通）。

到 Neon Console → 你的 Project → Settings → IP Allow 檢查：

- **沒開白名單**（預設）→ 不用做任何事，可以直接部署
- **有開白名單** → 二選一：
  - 暫時關掉白名單（最快，但等於對全網開放，只適合 demo 階段）
  - 加 VPC connector + Cloud NAT 給 Cloud Run 固定出口 IP，再把該 IP 加進白名單（正解，但多好幾步）

---

## 關於明文環境變數

你選了明文環境變數（`--env-vars-file`），我照做了。但要你清楚知道現況：

`DATABASE_URL` 會存在 Cloud Run 的服務設定裡，**任何具備專案 Viewer 權限的人**都看得到完整的
Neon 帳號密碼 —— 在 Console 的服務詳情頁，或 `gcloud run services describe` 的輸出。
你的 README 上有 6 位組員，如果之後都加進這個專案，等於每個人都拿得到正式庫的完整存取權。

**兩個低成本的緩解方式**，隨時可以加上：

1. **給 API 一個唯讀的 Neon role。**
   `main.py` 的 CORS 只開 `allow_methods=["GET"]`，整個 API 層本來就是唯讀設計。
   在 Neon 開一個只有 `SELECT` 權限的 role 給 Cloud Run 用，就算字串外流也寫不壞資料。
   這個改動只需要換 `.env` 裡的連線字串，不用動程式。

2. **之後改用 Secret Manager。**
   遷移成本很低 —— `deploy.ps1` 裡把 `--env-vars-file=$envYaml` 換成
   `--set-secrets=DATABASE_URL=eventsignal-db-url:latest`，其他都不用動。
   要改的時候跟我說，我幫你補上建 secret 的指令。

腳本本身有做的防護：
- `.gcloudignore` 明確排除 `.env`，避免它被打包上傳到 Cloud Build 的 staging bucket
- 終端機只印遮蔽過的連線字串（`://****:****@`），不留在捲動紀錄
- 產生的暫存 env YAML 在 `finally` 區塊刪除，不留在磁碟

---

## 常見錯誤

| 現象 | 原因與處理 |
|---|---|
| `gcloud : 無法辨識…` | 安裝後沒重開 PowerShell。關掉重開。 |
| `PERMISSION_DENIED: billing` | 專案沒綁計費。重跑 `setup-gcp.ps1`。 |
| Build 成功但 Cloud Run 起不來 | 檢查是否誤建到 worker stage。`cloudbuild.yaml` 的 `--target=api` 不能拿掉。 |
| `/health` 回 503 | 服務活著但 Neon 連不上。先看 Neon IP 白名單，再看連線字串是否含 `?sslmode=require`。 |
| `/health` 逾時無回應 | port 沒對上。Dockerfile 是 8000，`deploy.ps1` 的 `--port=8000` 不能拿掉。 |
| 前端呼叫被 CORS 擋 | `main.py` 的 `allow_origins` 目前只有 localhost。前端上線後要把正式網域加進去。 |

看即時 log：

```powershell
gcloud run services logs read eventsignal-api --region=asia-east1 --limit=50
```

---

## 有意設定的參數，改之前請先看這裡

`deploy.ps1` 裡這幾個值不是隨手填的：

- **`--concurrency=10` 與 `--max-instances=2`**
  `db/session.py` 的 `get_conn()` 是**每個請求開一條新連線**（程式註解也寫了「MVP 規模夠用；量大再換 psycopg_pool」）。
  Cloud Run 預設 concurrency 是 80、max-instances 是 100 —— 那組合最多會朝 Neon 開 8000 條連線。
  目前設定把上限壓在 20 條。
  好消息是你的連線字串走的是 Neon **pooler** 端點（`ep-...-pooler...`），
  前面有 PgBouncer 擋著，所以就算超量也不會直接打爆後端；但每請求重連的延遲成本還是照付。
  **要調高流量之前，先把 `psycopg_pool` 接上。**

- **`--min-instances=0`**
  沒流量時不留實例，等於不計費，但第一個請求會有幾秒冷啟動。
  Demo 前想避免這個延遲，臨時改成 1 即可（會開始持續計費）。

- **`--memory=512Mi`**
  API 映像檔只有 fastapi / uvicorn / pydantic / psycopg，512Mi 綽綽有餘。
  注意 `app/static/images` 有 5.8MB 會進映像檔，那是磁碟不是記憶體，不影響。
