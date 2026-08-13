<#
.SYNOPSIS
    把 FastAPI（根目錄 Dockerfile 的 api stage）建置並部署到 Cloud Run。

.DESCRIPTION
    每次要更新線上版本就跑這支。流程：
      讀 .env 取 DATABASE_URL -> Cloud Build 建 api 映像檔 -> 部署 Cloud Run -> 打 /health 驗證

.EXAMPLE
    .\deploy.ps1 -ProjectId eventsignal-nash-2026

.EXAMPLE
    # 只想確認會做什麼，不真的動手
    .\deploy.ps1 -ProjectId eventsignal-nash-2026 -WhatIfOnly

.NOTES
    ⚠️ 本檔必須存成 UTF-8 **with BOM**（理由同 setup-gcp.ps1）。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region      = 'asia-southeast1',
    [string]$RepoName    = 'eventsignal',
    [string]$ServiceName = 'eventsignal-api',

    # 印出將執行的指令就結束，不實際部署
    [switch]$WhatIfOnly
)

# ⚠️ 不設 Stop：gcloud builds submit 會把整份建置日誌寫到 stderr，
# 在 Stop 模式下 PowerShell 5.1 會誤判成終止錯誤。改用 $LASTEXITCODE 判斷。
$ErrorActionPreference = 'Continue'

function Write-Step { param([string]$m) Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan }

function Stop-WithError {
    param([string]$Message)
    Write-Host ''
    Write-Host "✗ $Message" -ForegroundColor Red
    exit 1
}

# repo 根目錄：本腳本在 <repo>/GCP/ 底下（只差一層）
$WorkspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$EnvFile       = Join-Path $WorkspaceRoot '.env'
$BuildConfig   = 'GCP/cloudbuild.yaml'          # 相對於 build context，一律用正斜線
$ImageUri      = "$Region-docker.pkg.dev/$ProjectId/$RepoName/api:latest"

Write-Host "repo 根目錄: $WorkspaceRoot"
Write-Host "服務      : $ServiceName ($Region)"

# ── 1. 從 .env 取出 DATABASE_URL ─────────────────────────
# 不把值印出來，也不寫進任何會進版控的檔案。
Write-Step '讀取 .env'
if (-not (Test-Path $EnvFile)) {
    Stop-WithError ".env 不存在：$EnvFile`n  請先從 .env.example 複製一份並填入 Neon 連線字串。"
}

$envMap = @{}
foreach ($line in (Get-Content $EnvFile -Encoding UTF8)) {
    $t = $line.Trim()
    if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $i = $t.IndexOf('=')
    $envMap[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
}

$databaseUrl = $envMap['DATABASE_URL']
if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    Stop-WithError 'DATABASE_URL 在 .env 裡是空的。app/db/session.py 靠它連 Neon，沒有值服務起不來。'
}
# 只顯示遮蔽版，避免機密留在終端機捲動紀錄裡
Write-Host "    DATABASE_URL = $($databaseUrl -replace '://[^@]+@', '://****:****@')"

if ($databaseUrl -notmatch 'sslmode=') {
    Write-Host '    ⚠ 連線字串沒有 sslmode 參數。Neon 需要 ?sslmode=require，否則會連線失敗。' -ForegroundColor Yellow
}

if ($WhatIfOnly) {
    Write-Host ''
    Write-Host '將執行：' -ForegroundColor Yellow
    Write-Host "  gcloud builds submit --config=$BuildConfig --project=$ProjectId"
    Write-Host "  gcloud run deploy $ServiceName --image=$ImageUri --port=8000 ..."
    exit 0
}

# ── 2. Cloud Build ─────────────────────────────────────
# 從 repo 根目錄送出：Dockerfile 內的 COPY 是從根算起的路徑，
# 且 .gcloudignore 也放在根目錄。
Write-Step 'Cloud Build 建置 api 映像檔（首次約 3-5 分鐘）'
Push-Location $WorkspaceRoot
try {
    & gcloud builds submit `
        --config=$BuildConfig `
        --substitutions="_REGION=$Region,_REPO=$RepoName,_IMAGE=api" `
        --project=$ProjectId
    $buildExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($buildExit -ne 0) {
    Stop-WithError "Cloud Build 失敗（離開碼 $buildExit）。上面的 log 連結有完整輸出。"
}

# ── 3. 部署 Cloud Run ──────────────────────────────────
# 環境變數走檔案而不是 --set-env-vars：連線字串裡的 , 和 = 會把
# --set-env-vars 的分隔規則打亂，用 YAML 檔沒有這個問題。
Write-Step 'Cloud Run 部署'
$envYaml = Join-Path $env:TEMP "eventsignal-env-$(Get-Random).yaml"
try {
    # YAML 單引號字串：內部的 ' 要寫成 ''
    $envLines = @("DATABASE_URL: '$($databaseUrl.Replace("'", "''"))'")

    # 正式站台的 origin 已寫在 backend/main.py 的 DEFAULT_ORIGINS，這裡不必設。
    # 只有要多開別的來源（預覽部署、自訂網域）才需要在 .env 放 CORS_EXTRA_ORIGINS。
    $corsExtra = $envMap['CORS_EXTRA_ORIGINS']
    if (-not [string]::IsNullOrWhiteSpace($corsExtra)) {
        $envLines += "CORS_EXTRA_ORIGINS: '$($corsExtra.Replace("'", "''"))'"
        Write-Host "    CORS_EXTRA_ORIGINS = $corsExtra"
    }

    Set-Content -Path $envYaml -Value $envLines -Encoding UTF8

    & gcloud run deploy $ServiceName `
        --image=$ImageUri `
        --project=$ProjectId `
        --region=$Region `
        --platform=managed `
        --port=8000 `
        --env-vars-file=$envYaml `
        --allow-unauthenticated `
        --memory=512Mi `
        --cpu=1 `
        --min-instances=0 `
        --max-instances=2 `
        --concurrency=10 `
        --timeout=60s `
        --quiet
    $deployExit = $LASTEXITCODE
} finally {
    # 機密不留在磁碟上
    if (Test-Path $envYaml) { Remove-Item $envYaml -Force }
}
if ($deployExit -ne 0) {
    Stop-WithError "Cloud Run 部署失敗（離開碼 $deployExit）"
}

# ── 4. 煙霧測試 ─────────────────────────────────────────
# /health 不是靜態 ok — main.py 裡它會實際去連 Neon，連不上回 503。
# 拿它當驗證等於同時驗了「服務起來了」和「資料庫通了」。
Write-Step '驗證 /health'
$url = (& gcloud run services describe $ServiceName `
    --project=$ProjectId --region=$Region --format='value(status.url)' 2>&1 | Select-Object -First 1)

Write-Host "    服務網址：$url"
Start-Sleep -Seconds 3
try {
    $resp = Invoke-WebRequest -Uri "$url/health" -TimeoutSec 30 -UseBasicParsing
    Write-Host "    HTTP $($resp.StatusCode)  $($resp.Content)" -ForegroundColor Green
} catch {
    $code = 0
    if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
        $code = [int]$_.Exception.Response.StatusCode
    }
    if ($code -eq 503) {
        Write-Host '    HTTP 503 — 服務活著，但連不上 Neon。' -ForegroundColor Yellow
        Write-Host '    多半是 Neon 端擋了 Cloud Run 的來源 IP，或連線字串不對。' -ForegroundColor Yellow
        Write-Host "    看 log： gcloud run services logs read $ServiceName --region=$Region --limit=50" -ForegroundColor Yellow
    } else {
        Write-Host "    驗證失敗：$($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ''
Write-Host '─────────────────────────────────────────────' -ForegroundColor Green
Write-Host ' 部署完成' -ForegroundColor Green
Write-Host "   API 文件 : $url/docs"
Write-Host "   健康檢查 : $url/health"
Write-Host "   事件端點 : $url/api/v1/events"
Write-Host '─────────────────────────────────────────────' -ForegroundColor Green
