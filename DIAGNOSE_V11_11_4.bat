@echo off
setlocal EnableExtensions
pushd "%~dp0" >nul 2>&1
title Nunes Recruitment Console V11.11.4 Diagnostics

echo ============================================================
echo  NUNES V11.11.4 - MULTI-ROLE HR PIPELINE DIAGNOSTICS
echo ============================================================
echo.

echo [Original folder]
echo %~dp0
echo.

echo [Local execution cache]
echo %LOCALAPPDATA%\NunesRecruitmentConsole\AppCache\V11_11_4
if exist "%LOCALAPPDATA%\NunesRecruitmentConsole\AppCache\V11_11_4\NUNES_BUILD_VERSION.txt" (
    type "%LOCALAPPDATA%\NunesRecruitmentConsole\AppCache\V11_11_4\NUNES_BUILD_VERSION.txt"
) else (
    echo Cache not created yet.
)
echo.

echo [UI/API]
netstat -ano | findstr ":5285"
netstat -ano | findstr ":5286"
echo.

echo [Version]
powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 3 http://127.0.0.1:5286/version | ConvertTo-Json } catch { Write-Host $_.Exception.Message }"
echo.

echo [Runtime self-test]
powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 5 http://127.0.0.1:5286/api/self-test | ConvertTo-Json -Depth 8 } catch { Write-Host $_.Exception.Message }"
echo.

echo [Live Detection]
powershell -NoProfile -Command "try { $d=Invoke-RestMethod -TimeoutSec 5 'http://127.0.0.1:5286/api/dashboard?lite=1'; $d.live_detection | ConvertTo-Json -Depth 8 } catch { Write-Host $_.Exception.Message }"
echo.

echo [Recruitment Pipeline]
powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 5 http://127.0.0.1:5286/api/recruitment/status | ConvertTo-Json -Depth 8 } catch { Write-Host $_.Exception.Message }"
echo.

echo [Latest backend log]
if exist "data\api.log" powershell -NoProfile -Command "Get-Content 'data\api.log' -Tail 220"
echo.

popd >nul 2>&1
pause

echo.
echo [OpenAI ranking key status]
powershell -NoProfile -Command "try { Invoke-RestMethod -TimeoutSec 3 http://127.0.0.1:5286/api/ranking/openai/status ^| ConvertTo-Json -Depth 5 } catch { Write-Host $_.Exception.Message }"

echo.
echo [GitHub]
where git 2^>nul
git remote -v 2^>nul
if exist "%LOCALAPPDATA%\NunesRecruitmentConsole\github_update_status.json" type "%LOCALAPPDATA%\NunesRecruitmentConsole\github_update_status.json"
