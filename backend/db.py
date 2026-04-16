"""Shared SQLModel engine and base tables for Hackstrom Track 3.

graph.py imports `engine` from here, then calls SQLModel.metadata.create_all(engine)
*after* defining AuditEventRow, so all tables in the metadata get created together.
"""
from __future__ import annotations

import os
from datetime import datetime

from sqlmodel import Field as SQLField, SQLModel, create_engine

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/hackstrom.db")

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


class UploadRunRow(SQLModel, table=True):
    """One row per uploaded file-pair; written by upload.py, read by workflow.py."""

    __tablename__ = "upload_runs"

    run_id: str = SQLField(primary_key=True)
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
