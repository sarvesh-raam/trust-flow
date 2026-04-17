@echo off
:: Run backend + frontend simultaneously in separate windows.
:: Usage: double-click OR run from CMD in the project root.

set ROOT=%~dp0

echo [hackstrom] Starting Backend ^(http://localhost:8000^)...
start "HACKSTROM BACKEND" cmd /k "cd /d %ROOT%backend && (if not exist .venv python -m venv .venv) && .venv\Scripts\activate && pip install -q -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo [hackstrom] Starting Frontend ^(http://localhost:5173^)...
start "HACKSTROM FRONTEND" cmd /k "cd /d %ROOT%frontend && (if not exist node_modules npm install) && npm run dev"

echo.
echo   Backend  --^> http://localhost:8000
echo   Frontend --^> http://localhost:5173
echo   API Docs --^> http://localhost:8000/docs
echo   GRAPH tab in frontend shows live pipeline + logs
echo.
echo   Close the backend/frontend windows to stop the services.
pause
