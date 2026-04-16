"""Hackstrom Track 3 — FastAPI entrypoint."""
from __future__ import annotations

# Load .env before any other application imports so that os.getenv() calls in
# sub-modules (graph.py, nodes/field_extract.py, db.py …) see the values.
from dotenv import load_dotenv
load_dotenv()

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.upload import router as upload_router
from routes.workflow import router as workflow_router
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)

import os
import logging

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
        print(f"Loki handler failed to init: {e} — continuing without Loki")

logger = logging.getLogger("hackstrom")
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
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
from fastapi import Depends
from dependencies import get_current_user
from routes.auth_routes import router as auth_router

uploads_dir = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(uploads_dir, exist_ok=True)
os.makedirs("data", exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from workflow_db import db as workflow_db_instance
from sqlmodel import SQLModel
try:
    # Explicitly ensure all tables are created on startup
    SQLModel.metadata.create_all(workflow_db_instance._engine)
    print("[DB] Tables successfully verified/created at startup")
except Exception as e:
    print(f"[DB] Fatal error during startup table creation: {e}")

app.include_router(auth_router)
app.include_router(upload_router, prefix="/api/v1/upload", tags=["upload"], dependencies=[Depends(get_current_user)])
app.include_router(workflow_router, prefix="/api/v1/workflow", tags=["workflow"], dependencies=[Depends(get_current_user)])


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
