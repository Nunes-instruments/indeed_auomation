@echo off
setlocal EnableExtensions
pushd "%~dp0" >nul 2>&1
where git >nul 2>&1 || (echo Git is not installed.& pause & exit /b 1)
if not exist ".git" (call GITHUB_SETUP_ONCE.bat & exit /b %errorlevel%)

for /f "tokens=1-4 delims=/ " %%a in ('date /t') do set D=%%a-%%b-%%c-%%d
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set T=%%a-%%b

git add -A
git diff --cached --quiet
if not errorlevel 1 (
  echo No code changes to push.
  pause
  exit /b 0
)

git commit -m "NUNES Recruitment update %DATE% %TIME%"
if errorlevel 1 (pause & exit /b 1)
git push origin main
if errorlevel 1 (pause & exit /b 1)

echo.
echo Update pushed. Other connected PCs will pull/rebuild/restart automatically.
echo After they finish updating, a browser refresh is enough.
pause
popd >nul 2>&1
