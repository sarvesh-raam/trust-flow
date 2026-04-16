@echo off
setlocal
echo ============================================================
echo ◈ CUSTOMS DECL - HACKSTROM TRACK 3 STARTUP SCRIPT
echo ============================================================

set ROOT=%cd%
set BACKEND=%ROOT%\backend
set FRONTEND=%ROOT%\frontend
set VENV=%BACKEND%\.venv\Scripts\python.exe

echo [1] Checking backend virtual environment...
if not exist "%VENV%" (
    echo Error: Backend virtual environment not found at %VENV%
    echo Please run setup first!
    pause
    exit /b 1
)

echo [2] Starting Backend Services...
start "Hackstrom Backend" cmd /k "cd /d %BACKEND% && %VENV% -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo [3] Changing to Frontend directory...
cd /d "%FRONTEND%"

echo [4] Starting Frontend Dev Server...
start "Hackstrom Frontend" cmd /k "npm run dev"

echo.
echo Application initialized!
echo Backend is running on http://localhost:8000
echo Frontend will open shortly via Vite
echo.
pause
