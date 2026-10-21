"""Development server with auto-restart on code changes.

Usage (from backend/):  python dev.py

Use this instead of `uvicorn --reload`. On Windows uvicorn stops the old
worker by broadcasting a console Ctrl+C and then waits for it indefinitely;
once PyTorch (via Docling) is loaded that event is often lost, so reloads
hang and the old code keeps serving.  watchfiles stops the server process
directly (and gives up waiting after a timeout), so restarts always happen.

Kept separate from main.py so this watcher process stays lightweight: only
the server child imports the app and its ML dependencies.
"""
from __future__ import annotations

from pathlib import Path

import uvicorn
from watchfiles import PythonFilter, run_process

BACKEND_DIR = Path(__file__).resolve().parent


def _serve() -> None:
    uvicorn.run("main:app", host="0.0.0.0", port=8000, timeout_graceful_shutdown=3)


def _report(changes: set) -> None:
    names = sorted({Path(path).name for _, path in changes})
    print(f"[dev] code changed ({', '.join(names)}) - restarting backend", flush=True)


if __name__ == "__main__":
    run_process(
        BACKEND_DIR,
        target=_serve,
        watch_filter=PythonFilter(ignore_paths=[BACKEND_DIR / "tests"]),
        callback=_report,
        sigint_timeout=5,
    )
