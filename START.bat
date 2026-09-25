@echo off
setlocal EnableExtensions
title Nunes Recruitment Console V11.11.6

REM ================================================================
REM NAS / UNC SAFE FAST START
REM ================================================================
REM CMD cannot use \\server\share as a current directory.
REM If launched from a NAS/UNC path, copy the small application source
REM to a local cache and run there. Python/node_modules/.next/data caches
REM are NOT copied from the NAS.
REM ================================================================

set "SOURCE_DIR=%~dp0"
if "%SOURCE_DIR:~-1%"=="\" set "SOURCE_DIR=%SOURCE_DIR:~0,-1%"

REM UNC path begins with two backslashes.
if "%SOURCE_DIR:~0,2%"=="\\" goto :RUN_FROM_LOCAL_CACHE

cd /d "%SOURCE_DIR%"
goto :LOCAL_START


:RUN_FROM_LOCAL_CACHE
set "CACHE_ROOT=%LOCALAPPDATA%\NunesRecruitmentConsole\AppCache\V11_11_6"
set "CACHE_MARKER=%CACHE_ROOT%\NUNES_BUILD_VERSION.txt"
set "NEED_COPY=1"

if exist "%CACHE_MARKER%" (
    set "CACHE_VERSION="
    set /p CACHE_VERSION=<"%CACHE_MARKER%"
    if /I "%CACHE_VERSION%"=="V11.11.6" set "NEED_COPY=0"
)

if "%NEED_COPY%"=="1" (
    echo.
    echo ============================================================
    echo  NUNES V11.11.6 - NAS LOCAL CACHE
    echo ============================================================
    echo The project is on a network share.
    echo Copying the small app files locally once for faster/reliable startup...
    echo.

    if not exist "%CACHE_ROOT%" mkdir "%CACHE_ROOT%" >nul 2>&1

    where robocopy >nul 2>&1
    if not errorlevel 1 (
        robocopy "%SOURCE_DIR%" "%CACHE_ROOT%" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP ^
          /XD ".venv" "__pycache__" "node_modules" ".next" ^
          /XF "automation.db" "api.log" "ui.log" "api.pid" "ui.pid" "NUNES_BUILD_VERSION.txt"
        if errorlevel 8 goto :CACHE_FAIL
    ) else (
        xcopy "%SOURCE_DIR%\*" "%CACHE_ROOT%\" /E /I /Y /Q >nul
        if errorlevel 1 goto :CACHE_FAIL
    )

    >"%CACHE_MARKER%" echo V11.11.6
)

REM Run the local copy. This avoids C:\Windows\.venv and avoids slow NAS builds.
call "%CACHE_ROOT%\START.bat"
exit /b %errorlevel%


:LOCAL_START
set "PYEXE="

REM Reuse already-prepared shared Python first.
set "NUNES_RUNTIME=%LOCALAPPDATA%\NunesRecruitmentConsole\FastRuntime\runtime.json"
if exist "%NUNES_RUNTIME%" (
    where powershell >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "try{$r=Get-Content -Raw $env:NUNES_RUNTIME ^| ConvertFrom-Json; if(Test-Path $r.python_exe){$r.python_exe}}catch{}"`) do (
            if not defined PYEXE set "PYEXE=%%P"
        )
    )
)

REM Compatible Python Launcher versions.
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

REM PATH fallback.
if not defined PYEXE (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do (
            if not defined PYEXE set "PYEXE=%%P"
        )
    )
)

REM Common per-user installs.
if not defined PYEXE (
    for %%V in (313 312 311 310) do (
        if not defined PYEXE if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
            set "PYEXE=%LocalAppData%\Programs\Python\Python%%V\python.exe"
        )
    )
)

REM One-time bootstrap when Python is absent.
if not defined PYEXE (
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo [SETUP] Python is missing. Installing Python 3.12 once...
        winget install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
        if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
            set "PYEXE=%LocalAppData%\Programs\Python\Python312\python.exe"
        )
    )
)

if not defined PYEXE (
    echo.
    echo Compatible Python was not found.
    echo Install Python 3.10, 3.11, 3.12 or 3.13 and run START.bat again.
    pause
    exit /b 1
)

REM Already running = open immediately.
"%PYEXE%" -c "import json,urllib.request,sys; d=json.load(urllib.request.urlopen('http://127.0.0.1:5286/version',timeout=.7)); sys.exit(0 if d.get('version')=='V11.11.6' else 1)" >nul 2>&1
if not errorlevel 1 (
    start "" "http://127.0.0.1:5285/#overview"
    exit /b 0
)

REM Clear only this application's fixed ports.
for %%X in (5285 5286) do (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr ":%%X "') do (
        taskkill /PID %%P /T /F >nul 2>&1
    )
)

REM Fast compatible-cache check. V11.10.8 runtime is reusable.
"%PYEXE%" fast_setup.py --quick-check >nul 2>&1
if not errorlevel 1 goto :FAST_LAUNCH

echo.
echo ============================================================
echo  NUNES V11.11.6 - ONE-TIME FAST SETUP
echo ============================================================
echo Reusing compatible Python, Node, dashboard build and cache when available.
echo Nothing is reinstalled unless it is actually missing.
echo.

"%PYEXE%" fast_setup.py --prepare --system-python "%PYEXE%"
if errorlevel 1 goto :FAIL

REM Register local cached path in Windows Task Scheduler.
"%PYEXE%" autostart.py --install >nul 2>&1

:FAST_LAUNCH
start "" /b "%PYEXE%" launcher.py
exit /b 0


:CACHE_FAIL
echo.
echo Could not prepare the local cache from:
echo   %SOURCE_DIR%
echo.
echo Check NAS read permission and local AppData write permission.
pause
exit /b 1


:FAIL
echo.
echo Setup failed. Run DIAGNOSE_V11_11_6.bat for the exact reason.
pause
exit /b 1
