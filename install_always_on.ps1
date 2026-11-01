$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    Write-Host ""
    Write-Host "ERROR: Run INSTALL_ALWAYS_ON_MINIPC.bat as Administrator." -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter"
    exit 10
}

$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Base

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " PBF Rise Baking Burnaby - Always-On Mini-PC Setup v6.0.0" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (!(Test-Path ".env")) {
    Write-Host ""
    Write-Host "ERROR: .env is missing." -ForegroundColor Red
    Write-Host "Copy the working private .env file from your current dashboard into this folder."
    Read-Host "Press Enter"
    exit 11
}

$PythonExe = $null
try {
    $PythonExe = (Get-Command python.exe -ErrorAction Stop).Source
} catch {}

if (-not $PythonExe) {
    try {
        $PythonExe = (Get-Command py.exe -ErrorAction Stop).Source
    } catch {}
}

if (-not $PythonExe) {
    Write-Host ""
    Write-Host "ERROR: Python was not found." -ForegroundColor Red
    Write-Host "Install Python 3.11 or newer and select Add Python to PATH."
    Read-Host "Press Enter"
    exit 12
}

Write-Host ""
Write-Host "[1/7] Creating dedicated virtual environment..." -ForegroundColor Green
if (!(Test-Path ".venv\Scripts\python.exe")) {
    if ((Split-Path $PythonExe -Leaf).ToLower() -eq "py.exe") {
        & $PythonExe -3 -m venv ".venv"
    } else {
        & $PythonExe -m venv ".venv"
    }
}

$VenvPython = Join-Path $Base ".venv\Scripts\python.exe"

Write-Host "[2/7] Installing required Python packages..." -ForegroundColor Green
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

Write-Host "[3/7] Opening TCP 8000 on Private Windows networks..." -ForegroundColor Green
Get-NetFirewallRule -DisplayName "PBF Temperature Dashboard TCP 8000" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue

New-NetFirewallRule `
    -DisplayName "PBF Temperature Dashboard TCP 8000" `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort 8000 `
    -Action Allow `
    -Profile Private | Out-Null

Write-Host "[4/7] Disabling AC sleep/hibernate for 24/7 operation..." -ForegroundColor Green
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null

Write-Host "[5/7] Creating dashboard startup task..." -ForegroundColor Green
$DashboardTask = "PBF Temperature Dashboard"
$Runner = Join-Path $Base "run_dashboard_service.ps1"

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""

$Trigger = New-ScheduledTaskTrigger -AtStartup

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$Principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Unregister-ScheduledTask -TaskName $DashboardTask -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $DashboardTask `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "PBF Rise Baking Burnaby LI-COR environmental monitoring dashboard" | Out-Null

Write-Host "[6/7] Creating 5-minute watchdog..." -ForegroundColor Green
$WatchTask = "PBF Dashboard Watchdog"
$WatchScript = Join-Path $Base "watchdog.ps1"

$WatchAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$WatchScript`""

$WatchTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

$WatchSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew

$WatchPrincipal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Unregister-ScheduledTask -TaskName $WatchTask -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $WatchTask `
    -Action $WatchAction `
    -Trigger $WatchTrigger `
    -Settings $WatchSettings `
    -Principal $WatchPrincipal `
    -Description "Restarts the PBF dashboard if the local web server stops responding" | Out-Null

Write-Host "[7/7] Starting dashboard..." -ForegroundColor Green
Start-ScheduledTask -TaskName $DashboardTask
Start-Sleep -Seconds 6

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " SETUP COMPLETE" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Mini-PC local URL: http://localhost:8000"
Write-Host ""
Write-Host "LAN URLs:" -ForegroundColor Cyan

Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*"
    } |
    ForEach-Object {
        Write-Host ("  http://" + $_.IPAddress + ":8000")
    }

Write-Host ""
Write-Host "IMPORTANT:" -ForegroundColor Yellow
Write-Host "1. In BIOS/UEFI set Restore on AC Power Loss / After Power Failure = POWER ON."
Write-Host "2. Ask IT to reserve this mini-PC's LAN IP address."
Write-Host "3. Ethernet and a small UPS are recommended."
Write-Host ""
Write-Host "The dashboard now starts at Windows boot even when nobody logs in."
Write-Host ""
Read-Host "Press Enter"
