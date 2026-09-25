@echo off
setlocal EnableExtensions
pushd "%~dp0" >nul 2>&1

set "REPO_URL=https://github.com/Nunes-instruments/indeed_auomation.git"
set "BRANCH=main"

echo ============================================================
echo  NUNES RECRUITMENT - GITHUB CONNECT
echo ============================================================
echo Repository:
echo   %REPO_URL%
echo.

where git >nul 2>&1
if errorlevel 1 (
  echo Git is not installed. Install Git for Windows first.
  pause
  exit /b 1
)

if not exist ".git" git init
git config user.name >nul 2>&1 || git config user.name "Nunes Recruitment"
git config user.email >nul 2>&1 || git config user.email "noreply@nunes.local"

git checkout -B %BRANCH% >nul 2>&1

git remote get-url origin >nul 2>&1
if not errorlevel 1 git remote remove origin
git remote add origin "%REPO_URL%"

echo Checking the remote repository...
git ls-remote --heads origin %BRANCH% > "%TEMP%\nunes-remote-head.txt" 2>nul
for %%A in ("%TEMP%\nunes-remote-head.txt") do set "REMOTE_SIZE=%%~zA"

if not "%REMOTE_SIZE%"=="0" (
  echo Existing main branch found. Fetching it safely...
  git fetch origin %BRANCH%
  if errorlevel 1 goto :AUTH_FAIL

  REM Do not overwrite local candidate data/secrets; those are ignored by git.
  git reset --mixed origin/%BRANCH% >nul 2>&1
)

git add -A
git diff --cached --quiet
if not errorlevel 1 (
  echo No new code changes to commit.
) else (
  git commit -m "NUNES Recruitment V11.11.5 shadcn UI baseline"
  if errorlevel 1 goto :FAIL
)

echo.
echo Pushing code to Nunes-instruments/indeed_auomation...
git push -u origin %BRANCH%
if errorlevel 1 goto :AUTH_FAIL

echo.
echo GitHub connected successfully.
echo This console checks GitHub automatically every 60 seconds.
echo Candidate data, settings, passwords, API keys, resumes, logs,
echo node_modules and production builds are excluded from GitHub.
pause
popd >nul 2>&1
exit /b 0

:AUTH_FAIL
echo.
echo GitHub authentication/repository access is required.
echo Sign in with Git Credential Manager or grant this repository access,
echo then run GITHUB_SETUP_ONCE.bat again.
pause
popd >nul 2>&1
exit /b 1

:FAIL
echo.
echo GitHub setup failed.
pause
popd >nul 2>&1
exit /b 1
