#!/usr/bin/env bash
# Hackstrom Track 3 — Demo Launcher (macOS / Linux / WSL)
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

# Detect Python in venv (Windows path vs Unix path)
if [[ -f "$BACKEND/.venv/Scripts/python.exe" ]]; then
  PYTHON="$BACKEND/.venv/Scripts/python.exe"
elif [[ -f "$BACKEND/.venv/bin/python" ]]; then
  PYTHON="$BACKEND/.venv/bin/python"
else
  echo "[ERR] Backend venv not found. Run:"
  echo "      cd backend && python -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

if [[ ! -d "$FRONTEND/node_modules" ]]; then
  echo "[ERR] Frontend node_modules missing. Run: cd frontend && npm install"
  exit 1
fi

# Kill anything on port 8000
if command -v lsof &>/dev/null; then
  lsof -ti:8000 | xargs kill -9 2>/dev/null || true
fi

echo ""
echo "  ======================================================="
echo "  HACKSTROM TRACK 3 -- DEMO LAUNCHER"
echo "  ======================================================="
echo ""

# Start backend
echo "  [1/3] Backend  → http://localhost:8000"
(cd "$BACKEND" && "$PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload) &
BACKEND_PID=$!

# Start frontend dev server
echo "  [2/3] Frontend → http://localhost:5173"
(cd "$FRONTEND" && npm run dev) &
FRONTEND_PID=$!

# Wait for servers to start
sleep 5

# Open browser
echo "  [3/3] Opening browser..."
DEMO_URL="http://localhost:5173/?demo=true"
if command -v open &>/dev/null; then
  open "$DEMO_URL"
elif command -v xdg-open &>/dev/null; then
  xdg-open "$DEMO_URL"
elif command -v start &>/dev/null; then
  start "$DEMO_URL"
fi

echo ""
echo "  ======================================================="
echo "  Backend  : http://localhost:8000"
echo "  Frontend : http://localhost:5173"
echo "  Demo URL : $DEMO_URL"
echo ""
echo "  Ctrl+Shift+D  -- demo script overlay"
echo "  Ctrl+C        -- stop both servers"
echo "  ======================================================="
echo ""

# Wait for either process to exit, then clean up
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo '  Stopped.'" EXIT
wait
