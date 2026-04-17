# Run backend + frontend simultaneously in two new PowerShell windows.
# Usage (from project root):  .\start_all.ps1

$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition

# ── Backend ────────────────────────────────────────────────────────────────────
$backendScript = @"
cd '$Root\backend'
if (-not (Test-Path '.venv')) {
    python -m venv .venv
}
.\.venv\Scripts\Activate.ps1
pip install -q -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendScript `
    -WorkingDirectory "$Root\backend"

# ── Frontend ───────────────────────────────────────────────────────────────────
$frontendScript = @"
cd '$Root\frontend'
if (-not (Test-Path 'node_modules')) {
    npm install
}
npm run dev
"@

Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendScript `
    -WorkingDirectory "$Root\frontend"

Write-Host ""
Write-Host "  Backend  --> http://localhost:8000"
Write-Host "  Frontend --> http://localhost:5173"
Write-Host "  API Docs --> http://localhost:8000/docs"
Write-Host "  Open frontend, log in, click the GRAPH tab for live pipeline + logs."
Write-Host ""
Write-Host "  Close the backend/frontend windows to stop."
