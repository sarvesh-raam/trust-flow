#!/usr/bin/env bash
# Run backend + frontend simultaneously.
# Usage: bash start_all.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Backend ────────────────────────────────────────────────────────────────────
echo "[hackstrom] Starting backend on http://localhost:8000 ..."
cd "$SCRIPT_DIR/backend"
if [ ! -d ".venv" ]; then
  echo "[hackstrom] Creating Python venv..."
  python -m venv .venv
  source .venv/bin/activate
  pip install -q -r requirements.txt
else
  source .venv/bin/activate
fi
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
echo "[hackstrom] Backend PID: $BACKEND_PID"

# ── Frontend ───────────────────────────────────────────────────────────────────
echo "[hackstrom] Starting frontend on http://localhost:5173 ..."
cd "$SCRIPT_DIR/frontend"
if [ ! -d "node_modules" ]; then
  echo "[hackstrom] Installing npm dependencies..."
  npm install
fi
npm run dev &
FRONTEND_PID=$!
echo "[hackstrom] Frontend PID: $FRONTEND_PID"

# ── Cleanup on exit ────────────────────────────────────────────────────────────
trap "echo '[hackstrom] Shutting down...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

echo ""
echo "  Backend  → http://localhost:8000"
echo "  Frontend → http://localhost:5173"
echo "  API Docs → http://localhost:8000/docs"
echo "  GRAPH tab in frontend shows live pipeline + logs"
echo ""
echo "  Press Ctrl+C to stop all services."
echo ""

wait
