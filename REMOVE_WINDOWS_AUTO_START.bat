@echo off
pushd "%~dp0" >nul 2>&1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" autostart.py --remove
) else (
  py autostart.py --remove
)
pause
