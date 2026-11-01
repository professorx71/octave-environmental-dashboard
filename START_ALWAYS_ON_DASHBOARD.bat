@echo off
cd /d "%~dp0"
echo Starting PBF Temperature Dashboard...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_always_on_dashboard.ps1"
