$TaskName = "PBF Temperature Dashboard"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Base "logs"
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}
$LogFile = Join-Path $LogDir "watchdog.log"

try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing -TimeoutSec 10
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
        Add-Content $LogFile "$(Get-Date -Format s) OK"
        exit 0
    }
    throw "Unexpected HTTP status $($r.StatusCode)"
}
catch {
    Add-Content $LogFile "$(Get-Date -Format s) FAIL: $($_.Exception.Message)"
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $TaskName
    Add-Content $LogFile "$(Get-Date -Format s) Dashboard restart requested"
}
