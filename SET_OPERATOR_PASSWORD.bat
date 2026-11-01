@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" set_operator_password.py
) else (
  python set_operator_password.py
)
pause
