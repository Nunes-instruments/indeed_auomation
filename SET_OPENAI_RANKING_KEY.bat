@echo off
setlocal EnableExtensions
pushd "%~dp0" >nul 2>&1
set "KEYFILE=%LOCALAPPDATA%\NunesRecruitmentConsole\secrets\openai_ranking_key.dpapi"
set "KEYDIR=%LOCALAPPDATA%\NunesRecruitmentConsole\secrets"
if not exist "%KEYDIR%" mkdir "%KEYDIR%" >nul 2>&1

echo ============================================================
echo  OPENAI RANKING KEY - SECURE WINDOWS STORAGE
echo ============================================================
echo This key is used ONLY for ranking refinement.
echo It is encrypted with your Windows account DPAPI and never put in GitHub.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Read-Host 'Paste the NEW OpenAI API key' -AsSecureString; $e=ConvertFrom-SecureString $s; Set-Content -LiteralPath $env:KEYFILE -Value $e -Encoding ASCII"
if errorlevel 1 (
  echo Could not save the key.
  pause
  exit /b 1
)
echo.
echo Ranking key saved securely. Restart is not normally required.
pause
popd >nul 2>&1
