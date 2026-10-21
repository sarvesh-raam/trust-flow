"""Hackstrom Track 3 — FastAPI entrypoint."""
from __future__ import annotations

import os

# Must run before anything imports PyTorch (pulled in by Docling): its Intel
# Fortran runtime installs a console Ctrl+C handler that swallows the event
# `uvicorn --reload` uses on Windows to stop the old worker, so reloads hang.
os.environ.setdefault("FOR_DISABLE_CONSOLE_CTRL_HANDLER", "1")

# Load .env before any other application imports so that os.getenv() calls in
# sub-modules (graph.py, nodes/field_extract.py, db.py …) see the values.
from dotenv import load_dotenv
load_dotenv()

import sys
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from db import CHECKPOINT_DB, init_db
from dependencies import get_current_user
from graph import document_graph
from routes.auth_routes import router as auth_router
from routes.chat import router as chat_router
from routes.logs import capture_log_event, router as logs_router
from routes.upload import router as upload_router
from routes.workflow import mark_interrupted_runs, router as workflow_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Windows consoles default to a legacy code page; without this, log lines
# containing characters like "→" or "≠" raise UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _sse_capture(_logger, _method, event_dict):
    """Structlog processor: fan out every log event to /api/v1/logs/stream subscribers."""
    capture_log_event({
        "ts": event_dict.get("timestamp", ""),
        "level": event_dict.get("level", "info"),
        "event": str(event_dict.get("event", "")),
        **{k: str(v) for k, v in event_dict.items()
           if k not in ("timestamp", "level", "event", "_record", "_logger")},
    })
    return event_dict


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _sse_capture,
        structlog.dev.ConsoleRenderer(),
    ]
)
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

# Every table module (db, graph) is imported above, so create_all sees them all.
init_db()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Swap in a SQLite checkpointer so BLOCKED (HITL) runs survive restarts."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: PLC0415

    mark_interrupted_runs()
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        document_graph.checkpointer = saver
        log.info("checkpointer.ready", path=str(CHECKPOINT_DB))
        yield


app = FastAPI(
    title="Hackstrom Track 3 API",
    description="Intelligent document processing with LangGraph + multi-country rules",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

_DEFAULT_ORIGINS = ",".join(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in (5173, 5174, 5175, 3000)
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", _DEFAULT_ORIGINS).split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus scrape endpoint (request metrics + metrics.py pipeline counters).
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
# Uploaded files are served only via the authenticated
# GET /api/v1/upload/{id}/file/{invoice|bl} route — never as public static files.
app.include_router(auth_router)
app.include_router(upload_router, prefix="/api/v1/upload", tags=["upload"], dependencies=[Depends(get_current_user)])
app.include_router(workflow_router, prefix="/api/v1/workflow", tags=["workflow"], dependencies=[Depends(get_current_user)])
app.include_router(chat_router, prefix="/api/v1/workflow/chat", tags=["chat"], dependencies=[Depends(get_current_user)])
# Auth is checked inside the route: EventSource can only send the token as ?token=.
app.include_router(logs_router, prefix="/api/v1/logs", tags=["logs"])


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Built frontend (single-container deployments, e.g. Hugging Face Spaces)
# ---------------------------------------------------------------------------
# Mounted last so every API route above takes precedence over the static files.
_STATIC_DIR = os.getenv("STATIC_DIR", "").strip()
if _STATIC_DIR:
    if os.path.isdir(_STATIC_DIR):
        app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="frontend")
        log.info("frontend.served", path=_STATIC_DIR)
    else:
        log.warning("frontend.static_dir_missing", path=_STATIC_DIR)


# ---------------------------------------------------------------------------
# Plain entrypoint (no auto-restart — use `python dev.py` while developing)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, timeout_graceful_shutdown=3)
