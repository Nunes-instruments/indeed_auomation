@echo off
setlocal EnableExtensions
echo Stopping older Recruitment Console versions...
for %%X in (5115 5116 5125 5126 5135 5136 5145 5146 5155 5156 5165 5166 5175 5176 5185 5186 5195 5196 5205 5206 5215 5216 5225 5226 5235 5236 5245 5246 5255 5256 5265 5266 5275 5276) do (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr ":%%X "') do (
        taskkill /PID %%P /T /F >nul 2>&1
    )
)
echo Done.
timeout /t 1 /nobreak >nul
exit /b 0
