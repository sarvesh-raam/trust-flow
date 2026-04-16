"""Persistence helpers for workflow records and blocked-state snapshots.

The app currently keeps active workflow records in memory for fast UI updates,
but judges and fresh machines need state to survive process reloads. This
module provides:

- Local JSON persistence under ./data/workflow_records
- Lightweight blocked-state snapshots under ./data/workflow_snapshots
- Best-effort SQLite row updates for status / declaration / summary
- Optional Firestore sync when Firebase Admin is configured
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import firebase_client
from models import WorkflowRecord

WORKFLOW_RECORD_DIR = Path("data") / "workflow_records"
WORKFLOW_SNAPSHOT_DIR = Path("data") / "workflow_snapshots"

WORKFLOW_RECORD_DIR.mkdir(parents=True, exist_ok=True)
WORKFLOW_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _record_path(run_id: str) -> Path:
    return WORKFLOW_RECORD_DIR / f"{run_id}.json"


def _snapshot_path(run_id: str) -> Path:
    return WORKFLOW_SNAPSHOT_DIR / f"{run_id}.json"


def _json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def save_workflow_record_local(record: WorkflowRecord) -> None:
    """Persist the full workflow record to JSON."""
    payload = record.model_dump(mode="json")
    _write_json(_record_path(str(record.id)), payload)


async def save_workflow_record_remote(record: WorkflowRecord) -> None:
    """Best-effort Firestore sync for the current workflow record."""
    if firebase_client.db is None:
        return

    payload = record.model_dump(mode="json")
    declaration = payload.get("result", {}).get("declaration")

    def _write() -> None:
        firebase_client.db.collection("workflow_runs").document(str(record.id)).set(
            {
                "run_id": str(record.id),
                "status": record.status.value,
                "country": record.country.value,
                "updated_at": datetime.utcnow().isoformat(),
                "summary": payload.get("result", {}).get("summary"),
                "error": payload.get("result", {}).get("error"),
                "declaration_json": (
                    json.dumps(declaration, ensure_ascii=False) if declaration else None
                ),
                "workflow_record": payload,
            },
            merge=True,
        )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write)


async def persist_workflow_record(record: WorkflowRecord) -> None:
    save_workflow_record_local(record)
    await save_workflow_record_remote(record)


def load_workflow_record_local(run_id: str) -> WorkflowRecord | None:
    """Load a workflow record from local JSON."""
    path = _record_path(run_id)
    if path.exists():
        return WorkflowRecord.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    return None


async def load_workflow_record(run_id: str) -> WorkflowRecord | None:
    """Load from local JSON first, then optionally from Firestore."""
    local = load_workflow_record_local(run_id)
    if local is not None:
        return local

    if firebase_client.db is None:
        return None

    def _read() -> dict[str, Any] | None:
        snap = firebase_client.db.collection("workflow_runs").document(run_id).get()
        if not snap.exists:
            return None
        return snap.to_dict()

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _read)
    if not data:
        return None

    payload = data.get("workflow_record")
    if payload:
        return WorkflowRecord.model_validate(payload)

    result: dict[str, Any] = {}
    declaration_json = data.get("declaration_json")
    if declaration_json:
        try:
            result["declaration"] = json.loads(declaration_json)
        except json.JSONDecodeError:
            pass
    if data.get("summary"):
        result["summary"] = data["summary"]
    if data.get("error"):
        result["error"] = data["error"]

    return WorkflowRecord.model_validate(
        {
            "id": run_id,
            "document_id": run_id,
            "country": data.get("country", "us"),
            "status": data.get("status", "queued"),
            "result": result,
            "steps": [],
            "created_at": data.get("created_at") or datetime.utcnow().isoformat(),
            "updated_at": data.get("updated_at") or datetime.utcnow().isoformat(),
        }
    )


def list_workflow_records_local() -> list[WorkflowRecord]:
    """Return all locally persisted workflow records, newest first."""
    records: list[WorkflowRecord] = []
    for path in sorted(
        WORKFLOW_RECORD_DIR.glob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.append(WorkflowRecord.model_validate(payload))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return records


def save_blocked_snapshot(run_id: str, values: dict[str, Any]) -> None:
    """Persist the graph state values used for HITL fallback resume."""
    _write_json(_snapshot_path(run_id), values)


def load_blocked_snapshot(run_id: str) -> dict[str, Any] | None:
    path = _snapshot_path(run_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
