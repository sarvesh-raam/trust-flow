"""Firestore-backed repository for workflow runs and their audit trails.

Parallel persistence phase: SQLite (workflow_db.py) continues to run alongside
this repository.  To swap a call site, change one import:

    # Before
    from workflow_db import get_run, update_run_status

    # After
    from repositories.run_repository import get_run, update_run_status

Both module-level functions are async — add `await` at each call site.

Firestore schema
----------------
Collection: workflow_runs
  Document: {run_id}
    Fields:
      run_id           str
      invoice_path     str
      bl_path          str
      country          str
      status           str   uploaded | running | hitl_paused | completed | failed
      declaration_json str | null   (JSON-serialised declaration dict)
      summary          str | null
      error            str | null
      created_at       timestamp
      updated_at       timestamp
    Subcollection: audit_trail
      Document: {auto-id}
        node_name      str
        timestamp      timestamp
        input_summary  str
        output_summary str
        latency_ms     float
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from functools import partial
from typing import Any

import firebase_client
from models import AuditEvent

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_db():
    """Return the Firestore client or raise if Firebase is not configured."""
    if firebase_client.db is None:
        raise RuntimeError(
            "Firestore is not configured. "
            "Set FIREBASE_SERVICE_ACCOUNT_JSON in your environment."
        )
    return firebase_client.db


async def _blocking(fn, *args, **kwargs):
    """Run a synchronous Firestore SDK call in a thread-pool executor
    so it does not block the asyncio event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(fn, *args, **kwargs))


# ---------------------------------------------------------------------------
# RunRepository
# ---------------------------------------------------------------------------

class RunRepository:
    """Async Firestore repository for UploadRun documents.

    All public methods are coroutines; call them with `await`.
    """

    COLLECTION = "workflow_runs"
    AUDIT_SUBCOLLECTION = "audit_trail"

    # ── Write ──────────────────────────────────────────────────────────────

    async def create(
        self,
        run_id: str,
        invoice_path: str,
        bl_path: str,
        country: str,
        invoice_gcs_url: str | None = None,
        bl_gcs_url: str | None = None,
    ) -> None:
        """Create a new workflow-run document.

        Mirrors the INSERT that upload.py does via SQLModel.
        """
        db = _get_db()
        doc: dict[str, Any] = {
            "run_id": run_id,
            "invoice_path": invoice_path,
            "bl_path": bl_path,
            "invoice_gcs_url": invoice_gcs_url,
            "bl_gcs_url": bl_gcs_url,
            "country": country,
            "status": "uploaded",
            "declaration_json": None,
            "summary": None,
            "error": None,
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
        }
        try:
            await _blocking(
                db.collection(self.COLLECTION).document(run_id).set, doc
            )
            log.info("run_repository.created success run_id=%s", run_id)
        except Exception as e:
            log.error("run_repository.create failed run_id=%s error=%s", run_id, e)
            raise

    async def get_file_url(self, run_id: str, source: str) -> str | None:
        """Return the GCS URL for a specific file in a run, or None if not found."""
        run = await self.get(run_id)
        if not run:
            return None
        return run.get(f"{source}_gcs_url")

    async def update_status(
        self,
        run_id: str,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Update status (and optional result fields) on an existing run document.

        ``result`` may contain any subset of: declaration, summary, error.
        Mirrors update_run_status() in workflow_db.py.
        """
        db = _get_db()
        updates: dict[str, Any] = {
            "status": status,
            "updated_at": _utcnow(),
        }
        if result:
            if "declaration" in result:
                updates["declaration_json"] = json.dumps(result["declaration"])
            if "summary" in result:
                updates["summary"] = result["summary"]
            if "error" in result:
                updates["error"] = result["error"]

        await _blocking(
            db.collection(self.COLLECTION).document(run_id).update, updates
        )
        log.debug("run_repository.status_updated run_id=%s status=%s", run_id, status)

    async def save_audit_trail(
        self,
        run_id: str,
        events: list[AuditEvent],
    ) -> None:
        """Write all AuditEvents for a run into the audit_trail subcollection.

        Uses a Firestore batch commit to avoid N round-trips.
        Mirrors the SQLite INSERT-all in the audit_trace node (graph.py).

        Subcollection path: workflow_runs/{run_id}/audit_trail/{auto-id}
        """
        if not events:
            return

        db = _get_db()
        sub_ref = (
            db.collection(self.COLLECTION)
            .document(run_id)
            .collection(self.AUDIT_SUBCOLLECTION)
        )

        def _write_batch() -> None:
            batch = db.batch()
            for event in events:
                ref = sub_ref.document()  # auto-generated ID per event
                batch.set(ref, {
                    "node_name":      event.node_name,
                    "timestamp":      event.timestamp,
                    "input_summary":  event.input_summary,
                    "output_summary": event.output_summary,
                    "latency_ms":     event.latency_ms,
                })
            batch.commit()

        await _blocking(_write_batch)
        log.debug(
            "run_repository.audit_saved run_id=%s events=%d",
            run_id,
            len(events),
        )

    # ── Read ───────────────────────────────────────────────────────────────

    async def get(self, run_id: str) -> dict[str, Any] | None:
        """Fetch a single run document by run_id.

        Returns a plain dict with the same field names as UploadRunRow,
        or None when the document does not exist.
        """
        db = _get_db()
        snap = await _blocking(
            db.collection(self.COLLECTION).document(run_id).get
        )
        if not snap.exists:
            return None
        return snap.to_dict()

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all run documents ordered by created_at descending."""
        db = _get_db()

        def _fetch() -> list[dict[str, Any]]:
            return [
                snap.to_dict()
                for snap in db.collection(self.COLLECTION)
                .order_by("created_at", direction="DESCENDING")
                .stream()
            ]

        return await _blocking(_fetch)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_repo = RunRepository()


# ---------------------------------------------------------------------------
# Drop-in replacements for workflow_db.get_run / update_run_status
#
# Swap-in at each call site:
#   Before:  from workflow_db import get_run, update_run_status
#   After:   from repositories.run_repository import get_run, update_run_status
#
# Add `await` at each call site — these are async, the SQLite versions were not.
# ---------------------------------------------------------------------------

async def get_run(run_id: str) -> dict[str, Any] | None:
    """Async drop-in for workflow_db.get_run().

    Returns a dict with keys matching UploadRunRow column names
    (run_id, invoice_path, bl_path, country, status, declaration_json,
    summary, error, created_at, updated_at), or None if not found.
    """
    return await _repo.get(run_id)


async def update_run_status(
    run_id: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    """Async drop-in for workflow_db.update_run_status()."""
    await _repo.update_status(run_id, status, result)
