param(
    [string]$ApiKey,
    [string]$Username,
    [string]$Model = "embedding-3",
    [string]$Name = "智谱 embedding-3",
    [string]$BaseUrl = "https://open.bigmodel.cn/api/paas/v4"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"
$scriptPath = Join-Path $root "api\scripts\configure_zhipu_embedding.py"

if (-not $ApiKey -and (Test-Path $envPath)) {
    $line = Get-Content $envPath | Where-Object { $_ -match "^\s*ZHIPU_API_KEY\s*=" } | Select-Object -Last 1
    if ($line) {
        $ApiKey = ($line -replace "^\s*ZHIPU_API_KEY\s*=", "").Trim().Trim('"').Trim("'")
    }
}

if (-not $ApiKey) {
    throw "Missing ZHIPU_API_KEY. Add it to .env or run: .\scripts\configure-zhipu-embedding.ps1 -ApiKey <your-key>"
}

if (-not (Test-Path $scriptPath)) {
    throw "Missing script: $scriptPath"
}

docker cp $scriptPath comet-api:/tmp/configure_zhipu_embedding.py

$argsList = @(
    "exec", "comet-api",
    "python", "/tmp/configure_zhipu_embedding.py",
    "--api-key", $ApiKey,
    "--model", $Model,
    "--name", $Name,
    "--base-url", $BaseUrl
)

if ($Username) {
    $argsList += @("--username", $Username)
}

docker @argsList
