if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Run this PowerShell script as Administrator." -ForegroundColor Red
    Read-Host "Press Enter"
    exit 1
}
Stop-ScheduledTask -TaskName "PBF Temperature Dashboard" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName "PBF Temperature Dashboard"
Write-Host "PBF dashboard restarted." -ForegroundColor Green
Read-Host "Press Enter"
