@echo off
setlocal
pushd "%~dp0" >nul 2>&1
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE where python >nul 2>&1 && set "PYEXE=python"

if defined PYEXE (
    %PYEXE% stop_server.py
)

exit /b 0
