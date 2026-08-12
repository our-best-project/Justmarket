<#
.SYNOPSIS
    展示模式開關：消除 Cloud Run 冷啟動延遲。

.DESCRIPTION
    -On   把 min-instances 設成 1，服務常駐待命，請求即時回應（會持續計費）
    -Off  設回 0，沒人用就不執行、不計費（但會有冷啟動）

    展示前 10 分鐘開，展示結束記得關。

.EXAMPLE
    .\demo-mode.ps1 -On
    .\demo-mode.ps1 -Off
    .\demo-mode.ps1 -Status

.NOTES
    ⚠️ 本檔必須存成 UTF-8 with BOM（Windows PowerShell 5.1 才不會用 Big5 誤讀）。
#>
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'On')]  [switch]$On,
    [Parameter(ParameterSetName = 'Off')] [switch]$Off,
    [Parameter(ParameterSetName = 'Status')] [switch]$Status,

    [string]$ProjectId   = 'eventsignal-nash-2026',
    [string]$Region      = 'asia-southeast1',
    [string]$ServiceName = 'eventsignal-api'
)

# gcloud 會把正常訊息寫到 stderr，Stop 模式下 PS 5.1 會誤判成錯誤
$ErrorActionPreference = 'Continue'

$common = @("--region=$Region", "--project=$ProjectId")

function Get-ServiceUrl {
    (& gcloud run services describe $ServiceName @common --format='value(status.url)' 2>&1 |
        Select-Object -First 1)
}

function Get-MinInstances {
    $v = (& gcloud run services describe $ServiceName @common `
        --format='value(spec.template.metadata.annotations."autoscaling.knative.dev/minScale")' 2>&1 |
        Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($v)) { return '0' }
    return $v.Trim()
}

# ── 只查狀態 ──────────────────────────────────────────
if ($PSCmdlet.ParameterSetName -eq 'Status') {
    $min = Get-MinInstances
    $url = Get-ServiceUrl
    Write-Host ''
    Write-Host "服務      : $ServiceName ($Region)"
    Write-Host "網址      : $url"
    if ($min -eq '0') {
        Write-Host "展示模式  : 關閉（min-instances=0，會有冷啟動）" -ForegroundColor DarkGray
    } else {
        Write-Host "展示模式  : 開啟（min-instances=$min，常駐計費中）" -ForegroundColor Yellow
    }
    Write-Host ''
    Write-Host '用法： .\demo-mode.ps1 -On   /   .\demo-mode.ps1 -Off'
    exit 0
}

# ── 切換 ─────────────────────────────────────────────
$target = if ($On) { 1 } else { 0 }
$label  = if ($On) { '開啟' } else { '關閉' }

Write-Host ''
Write-Host "==> $label 展示模式（min-instances=$target）" -ForegroundColor Cyan

& gcloud run services update $ServiceName @common --min-instances=$target --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host "✗ 切換失敗（gcloud 離開碼 $LASTEXITCODE）" -ForegroundColor Red
    exit 1
}

$url = Get-ServiceUrl

if ($On) {
    # 先打一次把實例叫醒，並確認 Neon 也通
    Write-Host ''
    Write-Host '==> 暖機中（同時驗證資料庫連線）' -ForegroundColor Cyan
    Start-Sleep -Seconds 5
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $r  = Invoke-WebRequest -Uri "$url/health" -TimeoutSec 30 -UseBasicParsing
        $sw.Stop()
        Write-Host "    HTTP $($r.StatusCode)  $($r.Content)" -ForegroundColor Green
        Write-Host "    回應時間 $($sw.ElapsedMilliseconds) ms"
    } catch {
        Write-Host "    ⚠ 暖機請求失敗：$($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Host ''
    Write-Host '─────────────────────────────────────────────' -ForegroundColor Green
    Write-Host ' 展示模式已開啟 — 服務常駐，無冷啟動' -ForegroundColor Green
    Write-Host ''
    Write-Host "   展示用網址： $url/docs"
    Write-Host ''
    Write-Host ' ⚠ 展示結束後請執行： .\demo-mode.ps1 -Off' -ForegroundColor Yellow
    Write-Host '   （常駐狀態會持續計費）' -ForegroundColor Yellow
    Write-Host '─────────────────────────────────────────────' -ForegroundColor Green
} else {
    Write-Host ''
    Write-Host '─────────────────────────────────────────────' -ForegroundColor Green
    Write-Host ' 展示模式已關閉 — 恢復閒置不計費' -ForegroundColor Green
    Write-Host '─────────────────────────────────────────────' -ForegroundColor Green
}
