$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Base

$LogDir = Join-Path $Base "logs"
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}
$LogFile = Join-Path $LogDir ("dashboard-" + (Get-Date -Format "yyyy-MM-dd") + ".log")
$Python = Join-Path $Base ".venv\Scripts\python.exe"

if (!(Test-Path $Python)) {
    Add-Content $LogFile "$(Get-Date -Format s) ERROR: .venv Python not found"
    exit 2
}
if (!(Test-Path (Join-Path $Base ".env"))) {
    Add-Content $LogFile "$(Get-Date -Format s) ERROR: .env not found"
    exit 3
}

Add-Content $LogFile ""
Add-Content $LogFile "============================================================"
Add-Content $LogFile "$(Get-Date -Format s) Starting PBF Dashboard v6.0.0"
Add-Content $LogFile "============================================================"

& $Python -m uvicorn server:app --host 0.0.0.0 --port 8000 *>> $LogFile
exit $LASTEXITCODE
