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

try {
    Stop-ScheduledTask -TaskName "PBF Temperature Dashboard"
    Write-Host "PBF dashboard scheduled task stopped." -ForegroundColor Yellow
}
catch {
    Write-Host ("Unable to stop scheduled task: " + $_.Exception.Message) -ForegroundColor Red
}

Read-Host "Press Enter to close"
