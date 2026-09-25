@echo off
setlocal EnableExtensions
title Nunes Recruitment Console V11.11.4 - Install/Repair Once

set "SOURCE_DIR=%~dp0"
if "%SOURCE_DIR:~-1%"=="\" set "SOURCE_DIR=%SOURCE_DIR:~0,-1%"

if "%SOURCE_DIR:~0,2%"=="\\" (
    echo Network-share install detected.
    echo START.bat will create/use the local execution cache first.
    call "%SOURCE_DIR%\START.bat"
    exit /b %errorlevel%
)

cd /d "%SOURCE_DIR%"

set "PYEXE="

set "NUNES_RUNTIME=%LOCALAPPDATA%\NunesRecruitmentConsole\FastRuntime\runtime.json"
if exist "%NUNES_RUNTIME%" (
    where powershell >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "try{$r=Get-Content -Raw $env:NUNES_RUNTIME ^| ConvertFrom-Json; if(Test-Path $r.python_exe){$r.python_exe}}catch{}"`) do (
            if not defined PYEXE set "PYEXE=%%P"
        )
    )
)

where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3.13 3.12 3.11 3.10) do (
        if not defined PYEXE (
            for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)" 2^>nul') do (
                if not defined PYEXE set "PYEXE=%%P"
            )
        )
    )
)

if not defined PYEXE (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do (
            if not defined PYEXE set "PYEXE=%%P"
        )
    )
)

if not defined PYEXE (
    echo Compatible Python was not found.
    pause
    exit /b 1
)

"%PYEXE%" fast_setup.py --prepare --system-python "%PYEXE%"
if errorlevel 1 (
    pause
    exit /b 1
)

"%PYEXE%" autostart.py --install

echo.
echo Runtime repaired and ready.
pause
