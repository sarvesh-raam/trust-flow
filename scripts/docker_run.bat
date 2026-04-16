@echo off
echo.
echo  Building and starting Hackstrom Track 3 (Docker)...
echo.

cd /d "%~dp0"

docker-compose down

docker-compose build --no-cache

docker-compose up -d

echo.
echo  Waiting for services to start...
timeout /t 6 /nobreak >nul

start "" "http://localhost:3000"

echo.
echo  =========================================================
echo   App  : http://localhost:3000
echo   API  : http://localhost:8000/docs
echo   Logs : docker-compose logs -f
echo  =========================================================
echo.
pause
