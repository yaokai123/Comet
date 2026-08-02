param(
    [string]$OutputName = "Comet-portable"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$electronDist = Join-Path $root "node_modules/electron/dist"
$dist = Join-Path $root "dist"
$main = Join-Path $root "electron"
$out = Join-Path $root "release/$OutputName"
$app = Join-Path $out "resources/app"

if (-not (Test-Path $electronDist)) {
    throw "Electron runtime not found. Run: npm install && npx install-electron --no"
}

if (-not (Test-Path $dist)) {
    throw "Frontend dist not found. Run: npm run build"
}

if (Test-Path $out) {
    Remove-Item -LiteralPath $out -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $app | Out-Null

Copy-Item -Path (Join-Path $electronDist "*") -Destination $out -Recurse -Force
Copy-Item -Path $dist -Destination (Join-Path $app "dist") -Recurse -Force
Copy-Item -Path $main -Destination (Join-Path $app "electron") -Recurse -Force
Copy-Item -Path (Join-Path $root "package.json") -Destination (Join-Path $app "package.json") -Force

$electronExe = Join-Path $out "electron.exe"
$cometExe = Join-Path $out "Comet.exe"
if (Test-Path $electronExe) {
    Rename-Item -LiteralPath $electronExe -NewName "Comet.exe"
}

Write-Host "Portable desktop app created:"
Write-Host $cometExe
