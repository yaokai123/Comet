param(
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

function Invoke-Compose {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ComposeArgs
    )

    docker compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-OptionalCompose {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$ComposeArgs
    )

    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        docker compose @ComposeArgs 2>$null
        $global:LASTEXITCODE = 0
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
}

function Test-DockerImage {
    param([string]$Image)

    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        docker image inspect $Image *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
}

Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "==> Docker Compose config check"
Invoke-Compose -ComposeArgs @("config") | Out-Null

Write-Host "==> Preparing local Python base image"
if (-not (Test-DockerImage "comet-python-base:3.12-slim")) {
    if (-not (Test-DockerImage "python:3.12-slim")) {
        throw "Missing local image python:3.12-slim. Pull it once with: docker pull python:3.12-slim"
    }
    docker tag python:3.12-slim comet-python-base:3.12-slim
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to tag local python base image"
    }
}

Write-Host "==> Removing stale redis container, if present"
Invoke-OptionalCompose -ComposeArgs @("stop", "redis")
Invoke-OptionalCompose -ComposeArgs @("rm", "-f", "redis")

if ($NoBuild) {
    Write-Host "==> Starting services without rebuild"
    Invoke-Compose -ComposeArgs @("up", "-d")
} else {
    Write-Host "==> Building and starting services"
    Invoke-Compose -ComposeArgs @("up", "-d", "--build")
}

Write-Host "==> Current service status"
Invoke-Compose -ComposeArgs @("ps")

Write-Host "==> Quick endpoint checks"
try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:5173" -TimeoutSec 10 | Out-Null
    Write-Host "web ok: http://localhost:5173"
} catch {
    Write-Host "web check failed; inspect web/api logs below if needed"
}

try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8000/docs" -TimeoutSec 10 | Out-Null
    Write-Host "api docs ok: http://localhost:8000/docs"
} catch {
    Write-Host "api docs check failed; recent API logs:"
    docker compose logs api --tail=120
}

Write-Host "==> Done"
