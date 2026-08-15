param([switch]$Keep)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$compose = Join-Path $root "docker-compose.sse-ha.yml"
$python = Join-Path $root "api\.venv\Scripts\python.exe"
$state = Join-Path $root "tmp\sse-ha-state.json"
$project = "comet-sse-ha"

try {
    Write-Host "[SSE-HA 1/5] Starting Docker multi-instance stack..."
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

    Write-Host "[SSE-HA 2/5] Testing refresh, network switch and load balancing..."
    & $python (Join-Path $root "api\eval\sse_ha_probe.py") refresh --state $state
    if ($LASTEXITCODE -ne 0) { throw "refresh/network/load-balancing probe failed" }

    Push-Location (Join-Path $root "web")
    try {
        Write-Host "[SSE-HA 3/5] Running the real-browser resume/idempotency test (up to 60 seconds)..."
        npm.cmd run test:sse-ha
        if ($LASTEXITCODE -ne 0) { throw "real browser ChatService SSE recovery test failed" }
    } finally {
        Pop-Location
    }

    Write-Host "[SSE-HA 4/5] Stopping api1 and testing instance-crash failover..."
    docker compose -p $project -f $compose stop api1
    & $python (Join-Path $root "api\eval\sse_ha_probe.py") failover --state $state
    if ($LASTEXITCODE -ne 0) { throw "instance-crash failover probe failed" }
    Write-Host "[SSE-HA 5/5] All Docker multi-instance recovery tests passed."
} finally {
    if (-not $Keep) {
        Write-Host "[SSE-HA cleanup] Removing comet-sse-ha containers, volumes and network..."
        docker compose -p $project -f $compose down --volumes --remove-orphans
    }
}
