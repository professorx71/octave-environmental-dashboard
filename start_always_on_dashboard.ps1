$ErrorActionPreference = "Stop"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    exit
}

$TaskName = "PBF Temperature Dashboard"

try {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 4
    Write-Host "PBF dashboard scheduled task started." -ForegroundColor Green

    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing -TimeoutSec 8
        Write-Host ("Dashboard responding - HTTP " + $r.StatusCode) -ForegroundColor Green
    }
    catch {
        Write-Host ("Task started, but the dashboard is not responding yet: " + $_.Exception.Message) -ForegroundColor Yellow
        Write-Host "Wait 10 seconds and open http://localhost:8000" -ForegroundColor Yellow
    }
}
catch {
    Write-Host ("Unable to start scheduled task: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    Write-Host "If the task does not exist, run INSTALL_ALWAYS_ON_MINIPC.bat as Administrator first." -ForegroundColor Yellow
}

Read-Host "Press Enter to close"
