@echo off
cd /d "%~dp0"
title PBF Rise Baking Burnaby Dashboard v6.0.0

if not exist ".env" (
 echo.
 echo ERROR: .env not found.
 echo Copy your existing private .env into this v6.0.0 folder.
 echo.
 pause
 exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
 echo.
 echo ============================================================
 echo   PBF Rise Baking Burnaby Dashboard v6.0.0
 echo ============================================================
 echo.
 echo The dedicated Python environment has not been installed yet.
 echo.
 echo RIGHT-CLICK:
 echo     INSTALL_ALWAYS_ON_MINIPC.bat
 echo and choose:
 echo     Run as administrator
 echo.
 echo That installer creates .venv and installs FastAPI, Uvicorn,
 echo LI-COR dependencies, Windows trust-store support, and the
 echo 24/7 scheduled tasks.
 echo.
 pause
 exit /b 2
)

set "PYTHON=%~dp0.venv\Scripts\python.exe"

"%PYTHON%" -c "import uvicorn, fastapi, httpx, dotenv" >nul 2>&1
if errorlevel 1 (
 echo.
 echo Required packages are missing from the dedicated environment.
 echo Installing requirements now...
 echo.
 "%PYTHON%" -m pip install -r requirements.txt
 if errorlevel 1 (
   echo.
   echo ERROR: Package installation failed.
   echo Run INSTALL_ALWAYS_ON_MINIPC.bat as Administrator.
   pause
   exit /b 3
 )
)

echo.
echo ============================================================
echo   PBF Rise Baking Burnaby Dashboard v6.0.0
echo   Environmental Monitoring - Manual Server Start
echo ============================================================
echo.
echo This computer:
echo   http://localhost:8000
echo.
echo LAN addresses:
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'} | ForEach-Object {Write-Host ('  http://' + $_.IPAddress + ':8000')}"
echo.
echo IMPORTANT:
echo For normal 24/7 operation you do NOT need to keep this window open.
echo The Always-On installer runs the dashboard through Windows Task Scheduler.
echo.
echo Press CTRL+C to stop this manual server.
echo ============================================================
echo.

"%PYTHON%" -m uvicorn server:app --host 0.0.0.0 --port 8000
echo.
echo Dashboard server stopped.
pause
