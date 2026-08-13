param(
    [string]$DuieContainer = "comet-duie2-500",
    [string]$DedupContainer = "comet-dblp-acm-1000",
    [string]$LogPath = "D:\Comet-main\api\eval\results\authorized-eval-queue.log"
)

$ErrorActionPreference = "Stop"

function Write-QueueLog([string]$Message) {
    $timestamp = (Get-Date).ToString("o")
    Add-Content -LiteralPath $LogPath -Value "$timestamp $Message" -Encoding utf8
}

Write-QueueLog "waiting for $DuieContainer before DBLP-ACM"
$seenRunning = $false
while ($true) {
    $status = docker inspect $DuieContainer --format "{{.State.Status}}"
    if ($status -eq "running") {
        $seenRunning = $true
    }
    if ($status -eq "exited") {
        # The container may still be the failed pre-queue attempt. Wait until the
        # HotpotQA queue has actually restarted it once.
        if (-not $seenRunning) {
            Start-Sleep -Seconds 60
            continue
        }
        $exitCode = docker inspect $DuieContainer --format "{{.State.ExitCode}}"
        if ($exitCode -eq "0") {
            Write-QueueLog "$DuieContainer completed; starting $DedupContainer"
            docker start $DedupContainer | Out-Null
        }
        else {
            Write-QueueLog "$DuieContainer failed with exit $exitCode; DBLP-ACM not started"
        }
        break
    }
    Start-Sleep -Seconds 60
}
