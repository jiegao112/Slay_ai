@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Slay AI launcher - start backend + frontend floating ball
rem
rem  Usage:
rem    start_all.bat         start both
rem    start_all.bat stop    stop the backend window
rem ============================================================

if /i "%~1"=="stop" goto :stop

echo ============================================================
echo   Slay AI - starting backend and frontend
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtualenv not found: .venv\Scripts\python.exe
    echo         Run "uv sync" first.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] pythonw.exe not found.
    pause
    exit /b 1
)

echo [1/3] Starting backend  http://127.0.0.1:8000 ...
start "SlayAI-Backend" /D "%~dp0" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo [2/3] Waiting for backend to become ready ...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 30;$i++){ try{ $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2; if($r.status -eq 'ok'){ $ok=$true; break } }catch{}; Start-Sleep -Milliseconds 500 }; if($ok){ Write-Host ('    backend ready: ' + $r.status) } else { Write-Host '    WARNING: backend not detected within 30s, check the SlayAI-Backend window' }"

echo [3/3] Starting frontend floating ball ...
start "" /D "%~dp0" ".venv\Scripts\pythonw.exe" "frontend\overlay.py"

echo.
echo ============================================================
echo   Done.
echo     Backend  : window titled "SlayAI-Backend" (close it or Ctrl+C to stop)
echo     Frontend : desktop floating ball (right-click the ball to exit)
echo     Stop all : run  start_all.bat stop
echo ============================================================
echo.
pause
exit /b 0

:stop
echo Stopping backend ...
taskkill /FI "WINDOWTITLE eq SlayAI-Backend*" /T /F >nul 2>nul
echo.
echo Frontend floating ball: right-click the ball and choose exit.
echo.
pause
exit /b 0
