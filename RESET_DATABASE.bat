@echo off
pushd "%~dp0" >nul 2>&1
echo This deletes only the local applicant history database.
echo It does NOT delete Indeed data or send any emails.
set /p C=Type YES to continue:
if /I not "%C%"=="YES" exit /b
if exist "data\automation.db" del /q "data\automation.db"
echo Done.
pause
