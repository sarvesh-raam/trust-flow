"""Hackstrom Track 3 — FastAPI entrypoint."""
from __future__ import annotations

# Load .env before any other application imports so that os.getenv() calls in
# sub-modules (graph.py, nodes/field_extract.py, db.py …) see the values.
from dotenv import load_dotenv
load_dotenv()

import os
import logging

import structlog
import uvicorn
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from routes.upload import router as upload_router
from routes.workflow import router as workflow_router
from routes.auth_routes import router as auth_router
from routes.logs import router as logs_router, capture_log_event
from dependencies import get_current_user

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _sse_capture(_logger, _method, event_dict):
    """Structlog processor: fan out every log event to SSE subscribers."""
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

loki_url = os.getenv("LOKI_URL")
if loki_url:
    try:
        import logging_loki
        loki_handler = logging_loki.LokiHandler(
            url=loki_url,
            tags={"service": "hackstrom-backend", "event": "hackstrom26"},
            version="1",
        )
        logging.getLogger().addHandler(loki_handler)
    except Exception as e:
        # We define log below, but can't use it yet if import failed. 
        # But we'll use a direct print for this one edge case.
        print(f"Loki handler failed: {e}")

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Hackstrom Track 3 API",
    description="Intelligent document processing with LangGraph + multi-country rules",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        # Prometheus / Grafana — allow scraping /metrics from monitoring stack
        "http://localhost:9090",
        "http://localhost:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static files + startup DB init
# ---------------------------------------------------------------------------
uploads_dir = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(uploads_dir, exist_ok=True)
os.makedirs("data", exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")



# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth_router)
app.include_router(upload_router, prefix="/api/v1/upload", tags=["upload"], dependencies=[Depends(get_current_user)])
app.include_router(workflow_router, prefix="/api/v1/workflow", tags=["workflow"], dependencies=[Depends(get_current_user)])
app.include_router(logs_router, prefix="/api/v1/logs", tags=["logs"])


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
