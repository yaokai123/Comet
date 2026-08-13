param(
    [string]$HotpotContainer = "comet-hotpotqa-500",
    [string]$DuieContainer = "comet-duie2-500",
    [string]$LogPath = "D:\Comet-main\api\eval\results\authorized-eval-queue.log"
)

$ErrorActionPreference = "Stop"

function Write-QueueLog([string]$Message) {
    $timestamp = (Get-Date).ToString("o")
    Add-Content -LiteralPath $LogPath -Value "$timestamp $Message" -Encoding utf8
}

Write-QueueLog "waiting for $HotpotContainer"
while ($true) {
    $status = docker inspect $HotpotContainer --format "{{.State.Status}}"
    if ($status -eq "exited") {
        $exitCode = docker inspect $HotpotContainer --format "{{.State.ExitCode}}"
        if ($exitCode -eq "0") {
            Write-QueueLog "$HotpotContainer completed; starting $DuieContainer"
            docker start $DuieContainer | Out-Null
        }
        else {
            Write-QueueLog "$HotpotContainer failed with exit $exitCode; DuIE not started"
        }
        break
    }
    Start-Sleep -Seconds 60
}
