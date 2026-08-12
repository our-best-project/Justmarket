param(
    [ValidateSet(
        "config", "pull", "build", "build-api", "build-runner",
        "gpu-test", "up", "down", "ps", "logs"
    )]
    [string]$Action = "up"
)

$ErrorActionPreference = "Stop"
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$dockerCandidates = @(
    (Get-Command docker -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }

if (-not $dockerCandidates) {
    throw "找不到 Docker CLI。請啟動 Docker Desktop，或重新開啟 PowerShell。"
}

$docker = $dockerCandidates[0]
$dockerDir = Split-Path -Parent $docker
if (($env:PATH -split ";") -notcontains $dockerDir) {
    # Docker credential helper 與 CLI 在同一目錄；只影響本 script 程序。
    $env:PATH = "$dockerDir;$env:PATH"
}
$composeArgs = @("compose", "--project-directory", $workspaceRoot, "-f", (Join-Path $workspaceRoot "docker-compose.yml"))

Push-Location $workspaceRoot
try {
    switch ($Action) {
        "config" { & $docker @composeArgs config }
        "pull" { & $docker @composeArgs pull prefect-db prefect-redis prefect-server prefect-services }
        "build" { & $docker @composeArgs build api prefect-runner }
        "build-api" { & $docker @composeArgs build api }
        "build-runner" { & $docker @composeArgs build prefect-runner }
        "gpu-test" {
            & $docker @composeArgs run --rm --no-deps prefect-runner python -c `
                "import torch; print({'torch': torch.__version__, 'cuda': torch.cuda.is_available(), 'device': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None})"
        }
        "up" { & $docker @composeArgs up -d }
        "down" { & $docker @composeArgs down }
        "ps" { & $docker @composeArgs ps }
        "logs" { & $docker @composeArgs logs -f --tail 200 }
    }
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $Action 失敗（exit $LASTEXITCODE）"
    }
}
finally {
    Pop-Location
}
