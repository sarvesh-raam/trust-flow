"""Shared SQLModel engine, tables and storage paths for Hackstrom Track 3.

All tables (including graph.AuditEventRow) share this engine; main.py calls
init_db() at startup once every table module has been imported.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Field as SQLField, SQLModel, create_engine

BACKEND_DIR = Path(__file__).resolve().parent


def _backend_path(value: str) -> Path:
    """Resolve a possibly-relative path against the backend dir, not the CWD."""
    p = Path(value)
    return p if p.is_absolute() else (BACKEND_DIR / p).resolve()


DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./hackstrom.db")

# Uploaded PDFs/images. Written by routes/upload.py, read by the OCR node.
UPLOAD_DIR: Path = _backend_path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# LangGraph checkpoints — lets BLOCKED (HITL) runs survive a restart.
CHECKPOINT_DB: Path = _backend_path(os.getenv("CHECKPOINT_DB", "checkpoints.db"))

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


class UploadRunRow(SQLModel, table=True):
    """One row per uploaded file-pair; written by upload.py, read by workflow.py."""

    __tablename__ = "upload_runs"

    run_id: str = SQLField(primary_key=True)
    user_id: str = SQLField(default="", index=True)
    invoice_path: str = SQLField(default="")
    bl_path: str = SQLField(default="")
    country: str = SQLField(default="us")
    status: str = SQLField(
        default="uploaded",
        # uploaded | running | hitl_paused | completed | failed
    )
    declaration_json: str | None = SQLField(default=None)
    summary: str | None = SQLField(default=None)
    error: str | None = SQLField(default=None)
    created_at: datetime = SQLField(default_factory=datetime.utcnow)
    updated_at: datetime = SQLField(default_factory=datetime.utcnow)


class WorkflowRunRow(SQLModel, table=True):
    """Persisted workflow record (replaces the old in-memory dict)."""

    __tablename__ = "workflow_runs"

    id: str = SQLField(primary_key=True)
    document_id: str = SQLField(index=True)
    user_id: str = SQLField(default="", index=True)
    country: str = SQLField(default="us")
    status: str = SQLField(default="queued")
    result_json: str = SQLField(default="{}")
    steps_json: str = SQLField(default="[]")
    # Declaration chat history; kept apart from result_json, which is
    # rewritten every time the pipeline re-runs.
    chat_json: str = SQLField(default="[]")
    created_at: datetime = SQLField(default_factory=datetime.utcnow)
    updated_at: datetime = SQLField(default_factory=datetime.utcnow)


def _ensure_column(table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table (create_all never alters tables)."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    if column not in {c["name"] for c in insp.get_columns(table)}:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_column("upload_runs", "user_id", "VARCHAR NOT NULL DEFAULT ''")
    _ensure_column("workflow_runs", "chat_json", "VARCHAR NOT NULL DEFAULT '[]'")
