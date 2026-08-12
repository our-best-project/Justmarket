<#
.SYNOPSIS
    EventSignal API 上 GCP 的一次性環境建置。跑過一次就不用再跑。

.DESCRIPTION
    做四件事：建專案 -> 綁計費 -> 啟用 API -> 建 Artifact Registry。
    每一步都可以重複執行（already exists 會被吞掉），跑到一半失敗可以直接重跑。

.EXAMPLE
    # 先看看有哪些計費帳戶可用
    gcloud billing accounts list

    .\setup-gcp.ps1 -ProjectId eventsignal-nash-2026 -BillingAccount 01ABCD-234567-89EFGH

.NOTES
    前置：gcloud CLI 必須已安裝。裝法見同目錄 README.md。

    ⚠️ 本檔必須存成 UTF-8 **with BOM**。
       Windows PowerShell 5.1 對沒有 BOM 的 .ps1 會用 Big5 解讀，
       中文註解變亂碼後可能夾帶 \ 或 ' 而讓語法整個爆掉。
#>
[CmdletBinding()]
param(
    # 全 GCP 唯一，6-30 字，只能小寫英數與連字號。取一個不會撞的，例如加年份。
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')]
    [string]$ProjectId,

    # gcloud billing accounts list 查得到的 ACCOUNT_ID
    [Parameter(Mandatory = $true)]
    [string]$BillingAccount,

    # asia-southeast1 = 新加坡，跟 .env 裡的 Neon（ap-southeast-1）同城。
    # 為什麼不選台灣的 asia-east1：見 README〈區域為什麼選新加坡〉。
    [string]$Region = 'asia-southeast1',

    # Artifact Registry 的 repo 名稱，要跟 cloudbuild.yaml 的 _REPO 一致
    [string]$RepoName = 'eventsignal'
)

# ⚠️ 這裡刻意「不」設 Stop。
# gcloud 是原生執行檔，很多正常訊息（含 describe 查無資料）都走 stderr；
# PowerShell 5.1 在 Stop 模式下會把那些 stderr 當成終止錯誤，
# 即使加了 2>$null 也擋不掉。改成手動檢查 $LASTEXITCODE。
$ErrorActionPreference = 'Continue'

function Write-Step { param([string]$m) Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Skip { param([string]$m) Write-Host "    (已存在，略過) $m" -ForegroundColor DarkGray }
function Write-Ok   { param([string]$m) Write-Host "    $m" -ForegroundColor Green }

# 探測用：只想知道「東西在不在」，不在是正常情況，不要吵。
# stderr 併進 stdout 再整包丟掉，這樣不會產生 ErrorRecord。
function Test-GcloudResource {
    param([string[]]$GcloudArgs)
    $null = & gcloud @GcloudArgs 2>&1
    return ($LASTEXITCODE -eq 0)
}

# 必須成功的動作：失敗就停，並說清楚停在哪。
function Invoke-GcloudStrict {
    param([string[]]$GcloudArgs, [string]$What)
    & gcloud @GcloudArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Host "✗ $What 失敗（gcloud 離開碼 $LASTEXITCODE）" -ForegroundColor Red
        Write-Host '  修正後可直接重跑本腳本，已完成的步驟會自動略過。' -ForegroundColor Red
        exit 1
    }
}

# ── 0. 確認 gcloud 在 PATH 上 ────────────────────────────────
Write-Step '檢查 gcloud CLI'
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Write-Host @'
找不到 gcloud。請先安裝：

  1. 下載安裝檔
     https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe
  2. 執行它，一路 Next（勾選 "Bundled Python" 保持預設即可）
  3. 【重要】關掉這個 PowerShell 視窗，重開一個新的
     — 安裝程式改的是系統 PATH，現有視窗讀不到

裝好後重新執行本腳本。
'@ -ForegroundColor Yellow
    exit 1
}
(& gcloud version 2>&1 | Select-Object -First 1) | ForEach-Object { Write-Host "    $_" }

# ── 1. 登入 ─────────────────────────────────────────────
Write-Step '確認登入狀態'
$activeAccount = (& gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>&1 | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($activeAccount) -or $LASTEXITCODE -ne 0) {
    Write-Host '    尚未登入，開啟瀏覽器…'
    Invoke-GcloudStrict @('auth', 'login') '登入'
    $activeAccount = (& gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>&1 | Select-Object -First 1)
}
Write-Host "    已登入：$activeAccount"

# ── 2. 建專案 ────────────────────────────────────────────
Write-Step "建立專案 $ProjectId"
if (Test-GcloudResource @('projects', 'describe', $ProjectId, '--format=value(projectId)')) {
    Write-Skip $ProjectId
} else {
    Invoke-GcloudStrict @('projects', 'create', $ProjectId, '--name=EventSignal') '建立專案'
}

Invoke-GcloudStrict @('config', 'set', 'project', $ProjectId) '設定預設專案'
Invoke-GcloudStrict @('config', 'set', 'run/region', $Region) '設定預設區域'

$projectNumber = (& gcloud projects describe $ProjectId --format='value(projectNumber)' 2>&1 | Select-Object -First 1)
Write-Host "    專案編號：$projectNumber"

# ── 3. 綁計費 ────────────────────────────────────────────
# 沒有這一步，後面 services enable 會直接被拒絕。
Write-Step '連結計費帳戶'
$linked = (& gcloud billing projects describe $ProjectId --format='value(billingEnabled)' 2>&1 | Select-Object -First 1)
if ($linked -eq 'True') {
    Write-Skip '計費已啟用'
} else {
    Invoke-GcloudStrict @('billing', 'projects', 'link', $ProjectId, "--billing-account=$BillingAccount") '連結計費帳戶'
}

# ── 4. 啟用 API ─────────────────────────────────────────
# 這一步會跑一到兩分鐘，是正常的。重複執行不會有副作用。
Write-Step '啟用必要的 API（約需 1-2 分鐘）'
Invoke-GcloudStrict @(
    'services', 'enable',
    'run.googleapis.com',
    'cloudbuild.googleapis.com',
    'artifactregistry.googleapis.com',
    "--project=$ProjectId"
) '啟用 API'
Write-Ok '完成'

# ── 5. Artifact Registry ───────────────────────────────
# 映像檔的家。Cloud Build 推上來、Cloud Run 從這裡拉。
Write-Step "建立 Artifact Registry repo：$RepoName ($Region)"
if (Test-GcloudResource @('artifacts', 'repositories', 'describe', $RepoName, "--location=$Region", "--project=$ProjectId", '--format=value(name)')) {
    Write-Skip $RepoName
} else {
    Invoke-GcloudStrict @(
        'artifacts', 'repositories', 'create', $RepoName,
        '--repository-format=docker',
        "--location=$Region",
        "--project=$ProjectId",
        '--description=EventSignal-container-images'
    ) '建立 Artifact Registry'
    Write-Ok '完成'
}

# ── 6. Cloud Build 服務帳戶權限 ──────────────────────────
# 新專案的 Cloud Build 預設身分不一定帶齊權限，先補上比事後查 403 快。
# 兩種可能的預設身分都補；不存在的那個會失敗，屬正常，靜默忽略。
Write-Step 'Cloud Build 服務帳戶授權'
foreach ($sa in @("$projectNumber@cloudbuild.gserviceaccount.com",
                  "$projectNumber-compute@developer.gserviceaccount.com")) {
    foreach ($role in @('roles/artifactregistry.writer', 'roles/logging.logWriter')) {
        $null = & gcloud projects add-iam-policy-binding $ProjectId `
            --member="serviceAccount:$sa" --role=$role `
            --condition=None --quiet 2>&1
    }
}
Write-Ok '完成（部分服務帳戶不存在屬正常）'

Write-Host ''
Write-Host '─────────────────────────────────────────────' -ForegroundColor Green
Write-Host ' 環境建置完成' -ForegroundColor Green
Write-Host "   專案     : $ProjectId"
Write-Host "   區域     : $Region"
Write-Host "   映像檔庫 : $Region-docker.pkg.dev/$ProjectId/$RepoName"
Write-Host ''
Write-Host ' 下一步：' -ForegroundColor Green
Write-Host "   .\deploy.ps1 -ProjectId $ProjectId"
Write-Host '─────────────────────────────────────────────' -ForegroundColor Green
