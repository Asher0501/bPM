@echo off
cd /d "%~dp0backend"

taskkill /f /im python.exe >nul 2>&1
timeout /t 1 /nobreak >nul

set PYTHON=C:\Users\Asher\WorkSpace\01_Software\AnaConda\python
start "bePm Backend" cmd /k "cd /d %cd% && %PYTHON% -X utf8 main.py"
:wait
timeout /t 1 /nobreak >nul
%PYTHON% -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:48090/api/health')" >nul 2>&1
if errorlevel 1 goto wait

start "" http://127.0.0.1:48090

echo.
echo bePm ready: http://127.0.0.1:48090
echo Press any key to stop.
pause >nul
taskkill /f /im python.exe >nul 2>&1
