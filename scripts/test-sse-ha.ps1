param([switch]$Keep)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$compose = Join-Path $root "docker-compose.sse-ha.yml"
$python = Join-Path $root "api\.venv\Scripts\python.exe"
$state = Join-Path $root "tmp\sse-ha-state.json"
$project = "comet-sse-ha"

try {
    docker compose -p $project -f $compose up -d --build postgres redis migrate api1 api2 lb
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:18080/probe/health" -TimeoutSec 2
            if ($health.ok) { $ready = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "SSE HA probe did not become ready" }

    & $python (Join-Path $root "api\eval\sse_ha_probe.py") refresh --state $state
    if ($LASTEXITCODE -ne 0) { throw "refresh/network/load-balancing probe failed" }

    docker compose -p $project -f $compose stop api1
    & $python (Join-Path $root "api\eval\sse_ha_probe.py") failover --state $state
    if ($LASTEXITCODE -ne 0) { throw "instance-crash failover probe failed" }
} finally {
    if (-not $Keep) {
        docker compose -p $project -f $compose down --volumes --remove-orphans
    }
}
