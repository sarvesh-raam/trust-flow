@echo off
setlocal
title Hackstrom Track 3 - Demo Launcher

echo.
echo  =========================================================
echo   HACKSTROM TRACK 3 -- DEMO LAUNCHER
echo  =========================================================
echo.

REM ── Resolve repo root ─────────────────────────────────────────────────────────
set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "VENV=%BACKEND%\.venv\Scripts\python.exe"

REM ── Sanity checks ─────────────────────────────────────────────────────────────
if not exist "%VENV%" (
    echo  [ERR] Backend venv not found at %VENV%
    echo       Run:  cd backend ^&^& python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause & exit /b 1
)
if not exist "%FRONTEND%\node_modules" (
    echo  [ERR] Frontend node_modules missing
    echo       Run:  cd frontend ^&^& npm install
    pause & exit /b 1
)

REM ── Kill any stale process on 8000 ────────────────────────────────────────────
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr /R "0\.0\.0\.0:8000 "') do (
    taskkill /F /PID %%p >nul 2>&1
)

echo  [1/3] Starting FastAPI backend  (http://localhost:8000) ...
start "Hackstrom Backend" cmd /k "cd /d %BACKEND% && %VENV% -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo  [2/3] Starting Vite dev server  (http://localhost:5173) ...
start "Hackstrom Frontend" cmd /k "cd /d %FRONTEND% && npm run dev"

echo  [3/3] Waiting 5 s then opening browser...
timeout /t 5 /nobreak >nul

start "" "http://localhost:5173/?demo=true"

echo.
echo  =========================================================
echo   Backend  : http://localhost:8000
echo   Frontend : http://localhost:5173
echo   Demo URL : http://localhost:5173/?demo=true
echo.
echo   Ctrl+Shift+D  -- demo script overlay
echo   Close the two terminal windows to stop.
echo  =========================================================
echo.
pause
