"""Workflow routes — trigger, monitor, and bbox-annotate LangGraph document pipelines."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session as DBSession, select

from db import UploadRunRow, WorkflowRunRow, engine
from dependencies import get_current_user
from graph import (
    GraphState,
    NodeInterrupt,
    _merge_issues,
    document_graph,
    persist_audit_trail,
    weight_issues,
)
from models import (
    AuditEvent,
    BillOfLading,
    ComplianceResult,
    CountryCode,
    InvoiceDocument,
    ResumeRequest,
    WorkflowCreateRequest,
    WorkflowRecord,
    WorkflowResponse,
    WorkflowStatus,
    WorkflowStep,
)

log = structlog.get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Bbox models
# ---------------------------------------------------------------------------

class BBoxEntry(BaseModel):
    """A single field-level bounding box in the PDF viewer response."""

    field_name: str = Field(description="Invoice field name, e.g. 'invoice_number'")
    value: str = Field(description="Extracted field value as a string")
    bbox: list[float] = Field(
        description="[x1, y1, x2, y2] in PDF points, origin at top-left of page"
    )
    page: int = Field(default=1, description="1-based page number")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Match confidence derived from text overlap"
    )
    source: str = Field(default="invoice", description="'invoice' or 'bl'")


class StatusResponse(WorkflowResponse):
    """WorkflowResponse extended with structured bbox data for the PDF viewer."""

    bboxes: list[BBoxEntry] = Field(
        default_factory=list,
        description="Field-level bounding boxes derived from OCR provenance data",
    )
    invoice_pdf_url: str | None = Field(default=None, description="URL to the invoice PDF")
    bl_pdf_url: str | None = Field(default=None, description="URL to the bill of lading PDF")

# ---------------------------------------------------------------------------
# Bbox helper
# ---------------------------------------------------------------------------

#: Invoice field names surfaced in the status response (in display order).
_INVOICE_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "date",
    "seller",
    "buyer",
    "total_amount",
    "gross_weight_kg",
)


def map_fields_to_bboxes(
    invoice: InvoiceDocument | None,
    bboxes: list[dict[str, Any]] | None,
) -> list[BBoxEntry]:
    """Map structured invoice fields back to their OCR bounding boxes.

    Algorithm:
      For each field value, scan the OCR bbox list and find the entry whose
      `text` best overlaps the field value (case-insensitive substring match).
      The overlap score is the character length of the matched substring;
      longer matches beat shorter ones.  Entries with zero overlap are skipped.

    Args:
        invoice:  Structured invoice extracted by field_extract_node.
        bboxes:   Raw OCR bbox list produced by ocr_extract_node.
                  Each entry: {text, bbox: [l,t,r,b], page, source}.

    Returns:
        List of BBoxEntry objects, one per matched field (unmatched fields
        are silently omitted).
    """
    if not invoice or not bboxes:
        return []

    # Support both Pydantic model and plain dict invoice representations.
    def _fget(obj: Any, field: str, default: Any = "") -> str:
        val = obj.get(field, default) if isinstance(obj, dict) else getattr(obj, field, default)
        return "" if val is None else str(val)

    # Build a flat map of field_name → string value for the fields we expose.
    field_values: dict[str, str] = {
        "invoice_number":  _fget(invoice, "invoice_number"),
        "date":            _fget(invoice, "date"),
        "seller":          _fget(invoice, "seller"),
        "buyer":           _fget(invoice, "buyer"),
        "total_amount":    _fget(invoice, "total_amount"),
        "gross_weight_kg": _fget(invoice, "gross_weight_kg"),
    }

    result: list[BBoxEntry] = []

    for field_name in _INVOICE_FIELDS:
        raw_value = field_values.get(field_name, "")
        if not raw_value:
            continue

        val_lower = raw_value.lower().strip()
        best_entry: dict[str, Any] | None = None
        best_score: int = 0

        for ocr_entry in bboxes:
            ocr_text = (ocr_entry.get("text") or "").lower().strip()
            if not ocr_text:
                continue

            # Prefer the match direction that yields the longer overlap.
            if val_lower in ocr_text:
                score = len(val_lower)
            elif ocr_text in val_lower:
                score = len(ocr_text)
            else:
                continue

            if score > best_score:
                best_score = score
                best_entry = ocr_entry

        if best_entry is None:
            log.debug("map_fields_to_bboxes.no_match", field=field_name, value=raw_value[:40])
            continue

        confidence = min(1.0, best_score / max(len(val_lower), 1))
        result.append(
            BBoxEntry(
                field_name=field_name,
                value=raw_value,
                bbox=best_entry.get("bbox", [0.0, 0.0, 0.0, 0.0]),
                page=best_entry.get("page", 1),
                confidence=round(confidence, 3),
                source=best_entry.get("source", "invoice"),
            )
        )
        log.debug(
            "map_fields_to_bboxes.matched",
            field=field_name,
            score=best_score,
            confidence=round(confidence, 3),
        )

    return result


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _to_record(row: WorkflowRunRow) -> WorkflowRecord:
    return WorkflowRecord(
        id=uuid.UUID(row.id),
        document_id=uuid.UUID(row.document_id),
        country=CountryCode(row.country),
        status=WorkflowStatus(row.status),
        steps=[WorkflowStep(**s) for s in json.loads(row.steps_json or "[]")],
        result=json.loads(row.result_json or "{}"),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_owned(workflow_id: str, user_id: str) -> WorkflowRunRow:
    with DBSession(engine) as session:
        row = session.get(WorkflowRunRow, workflow_id)
    # 404 (not 403) so other users' run ids are not confirmed to exist.
    if not row or row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return row


def _save(
    workflow_id: str,
    status_: WorkflowStatus,
    result: dict[str, Any] | None = None,
    steps: list[WorkflowStep] | None = None,
) -> WorkflowRunRow | None:
    """Update a workflow row and mirror its status onto the upload row."""
    with DBSession(engine) as session:
        row = session.get(WorkflowRunRow, workflow_id)
        if not row:
            return None
        row.status = status_.value
        if result is not None:
            row.result_json = json.dumps(result, default=str)
        if steps is not None:
            row.steps_json = json.dumps([s.model_dump(mode="json") for s in steps])
        row.updated_at = datetime.utcnow()

        upload = session.get(UploadRunRow, row.document_id)
        if upload:
            upload.status = "hitl_paused" if status_ == WorkflowStatus.BLOCKED else status_.value
            if result is not None:
                upload.summary = result.get("summary") or upload.summary
                upload.error = result.get("error")
                if result.get("declaration"):
                    upload.declaration_json = json.dumps(result["declaration"], default=str)
            upload.updated_at = row.updated_at
            session.add(upload)

        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def mark_interrupted_runs() -> int:
    """At startup: fail runs whose background task died with the old process.

    BLOCKED runs are left alone — their graph state lives in the SQLite
    checkpointer, so they can still be resumed.
    """
    active = [WorkflowStatus.QUEUED.value, WorkflowStatus.RUNNING.value]
    with DBSession(engine) as session:
        ids = [
            r.id for r in session.exec(
                select(WorkflowRunRow).where(WorkflowRunRow.status.in_(active))  # type: ignore[attr-defined]
            ).all()
        ]
    for wid in ids:
        _save(wid, WorkflowStatus.FAILED, result={
            "error": "Server restarted while this run was in progress — please start a new run.",
            "bboxes": [],
        })
    if ids:
        log.warning("workflow.marked_interrupted", count=len(ids))
    return len(ids)


# ---------------------------------------------------------------------------
# Graph-state helpers (values may be models, or dicts after deserialisation)
# ---------------------------------------------------------------------------

def _state_get(state: Any, key: str, default: Any = None) -> Any:
    """Get a value from either a dict state or Pydantic BaseModel state."""
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def _as_model(model: type[BaseModel], value: Any) -> Any:
    if value is None or isinstance(value, model):
        return value
    return model.model_validate(value if isinstance(value, dict) else value.model_dump())


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def _steps_from(audit_trail: list[Any]) -> list[WorkflowStep]:
    return [
        WorkflowStep(name=_state_get(e, "node_name", "?"), status=WorkflowStatus.COMPLETED)
        for e in audit_trail
    ]


def _bboxes_from(values: Any) -> list[dict[str, Any]]:
    invoice = _as_model(InvoiceDocument, _state_get(values, "invoice"))
    raw = (_state_get(values, "invoice_bboxes") or []) + (_state_get(values, "bl_bboxes") or [])
    return [b.model_dump() for b in map_fields_to_bboxes(invoice, raw)]


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------

async def _handle_blocked(workflow_id: str, msg: str, config: dict) -> None:
    """Mark the workflow BLOCKED and expose what the HITL panel needs."""
    snapshot = await document_graph.aget_state(config)
    values = getattr(snapshot, "values", None) or {}
    cr = _as_model(ComplianceResult, values.get("compliance_result"))
    audit_trail = [_as_model(AuditEvent, e) for e in values.get("audit_trail") or []]
    issues = [i.model_dump() for i in cr.issues] if cr else []

    # The run may never reach audit_trace, so persist what we have now.
    persist_audit_trail(workflow_id, audit_trail)
    _save(
        workflow_id,
        WorkflowStatus.BLOCKED,
        result={
            "hitl_required":     True,
            "message":           msg,
            "compliance_status": "BLOCK",
            "issues":            issues,
            "blocking_issues":   [i for i in issues if i["severity"] == "block"],
            "invoice":           _dump(values.get("invoice")),
            "bill_of_lading":    _dump(values.get("bill_of_lading")),
            "bboxes":            _bboxes_from(values),
        },
        steps=_steps_from(audit_trail),
    )
    log.warning("workflow.hitl_interrupt", workflow_id=workflow_id, detail=msg)


_LAST_EXCEPTION_RE = re.compile(r"<last_exception>\s*(.*?)\s*</last_exception>", re.DOTALL)


def _readable_error(exc: Exception) -> str:
    """Short, user-facing error text.

    Instructor's retry error wraps every attempt in XML; only the final
    exception is useful to show in the UI.
    """
    text = str(exc)
    match = _LAST_EXCEPTION_RE.search(text)
    return (match.group(1) if match else text).strip()[:1000]


async def _drive_graph(workflow_id: str, graph_input: GraphState | None) -> None:
    """Run (graph_input) or resume (None) the graph and record the outcome."""
    config = {"configurable": {"thread_id": workflow_id}}
    try:
        result = await document_graph.ainvoke(graph_input, config)

        # LangGraph 1.1.6+ does NOT re-raise NodeInterrupt — ainvoke returns a
        # dict containing '__interrupt__' when a node pauses execution.
        if isinstance(result, dict) and "__interrupt__" in result:
            interrupts = result["__interrupt__"]
            msg = str(interrupts[0].value) if interrupts else "HITL interrupt"
            await _handle_blocked(workflow_id, msg, config)
            return

        cr = _as_model(ComplianceResult, _state_get(result, "compliance_result"))
        audit_trail = _state_get(result, "audit_trail") or []
        bboxes = _bboxes_from(result)

        _save(
            workflow_id,
            WorkflowStatus.COMPLETED,
            result={
                "declaration":       _state_get(result, "declaration"),
                "summary":           _state_get(result, "summary", ""),
                "compliance_status": cr.status if cr else None,
                "compliance_issues": len(cr.issues) if cr else 0,
                "audit_events":      len(audit_trail),
                "bboxes":            bboxes,
            },
            steps=_steps_from(audit_trail),
        )
        log.info("workflow.completed", workflow_id=workflow_id, bboxes=len(bboxes))

    except NodeInterrupt as exc:
        # Fallback: older LangGraph behaviour where NodeInterrupt propagates.
        await _handle_blocked(workflow_id, str(exc), config)

    except Exception as exc:
        _save(workflow_id, WorkflowStatus.FAILED, result={"error": _readable_error(exc), "bboxes": []})
        log.exception("workflow.failed", workflow_id=workflow_id, error=str(exc))


async def _run_graph(
    workflow_id: str,
    document_id: str,
    country: str,
    invoice_pdf_path: str,
    bl_pdf_path: str,
) -> None:
    """Background task: run the LangGraph pipeline and update workflow state."""
    _save(workflow_id, WorkflowStatus.RUNNING)
    initial_state = GraphState(
        run_id=uuid.UUID(workflow_id),
        document_id=document_id,
        country=country,
        invoice_pdf_path=invoice_pdf_path,
        bl_pdf_path=bl_pdf_path,
    )
    await _drive_graph(workflow_id, initial_state)


async def _resume_graph(workflow_id: str) -> None:
    await _drive_graph(workflow_id, None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/",
    response_model=WorkflowResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a processing workflow for a document pair",
)
async def create_workflow(
    body: WorkflowCreateRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
) -> WorkflowResponse:
    run_id_str = str(body.document_id)
    with DBSession(engine) as db_session:
        upload_row = db_session.get(UploadRunRow, run_id_str)
    if not upload_row or upload_row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")

    workflow_id = str(uuid.uuid4())
    with DBSession(engine) as db_session:
        row = WorkflowRunRow(
            id=workflow_id,
            document_id=run_id_str,
            user_id=user_id,
            country=body.country.value,
            status=WorkflowStatus.QUEUED.value,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)

    background_tasks.add_task(
        _run_graph,
        workflow_id=workflow_id,
        document_id=run_id_str,
        country=body.country.value,
        invoice_pdf_path=upload_row.invoice_path,
        bl_pdf_path=upload_row.bl_path,
    )

    log.info("workflow.queued", workflow_id=workflow_id, document_id=run_id_str)
    return WorkflowResponse(**_to_record(row).model_dump())


# NOTE: /status/{run_id} MUST be declared before /{workflow_id} so FastAPI
# does not swallow the literal word "status" as a workflow_id path parameter.
@router.get(
    "/status/{run_id}",
    response_model=StatusResponse,
    summary="Get workflow status with field-level bbox annotations",
)
async def get_run_status(run_id: str, user_id: str = Depends(get_current_user)) -> StatusResponse:
    """Return the workflow record plus a `bboxes` list for the PDF viewer overlay.

    `run_id` is the same UUID returned by POST /workflow/ (i.e. the workflow_id).
    While BLOCKED, `result` also carries `issues`, `invoice` and
    `bill_of_lading` for the human-review panel.
    """
    wf = _to_record(_get_owned(run_id, user_id))
    bboxes = [BBoxEntry(**b) for b in wf.result.get("bboxes", [])]
    file_base = f"/api/v1/upload/{wf.document_id}/file"

    return StatusResponse(
        **wf.model_dump(),
        bboxes=bboxes,
        invoice_pdf_url=f"{file_base}/invoice",
        bl_pdf_url=f"{file_base}/bl",
    )


@router.get(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Get workflow status and results",
)
async def get_workflow(workflow_id: str, user_id: str = Depends(get_current_user)) -> WorkflowResponse:
    return WorkflowResponse(**_to_record(_get_owned(workflow_id, user_id)).model_dump())


@router.get(
    "/",
    response_model=list[WorkflowResponse],
    summary="List the current user's workflows (newest first)",
)
async def list_workflows(user_id: str = Depends(get_current_user)) -> list[WorkflowResponse]:
    with DBSession(engine) as session:
        rows = session.exec(
            select(WorkflowRunRow)
            .where(WorkflowRunRow.user_id == user_id)
            .order_by(WorkflowRunRow.created_at.desc())  # type: ignore[attr-defined]
        ).all()
    return [WorkflowResponse(**_to_record(r).model_dump()) for r in rows]


def _carried_over_issues(cr: ComplianceResult | None) -> list:
    """Issues raised before validation that a resume does not re-derive.

    OCR-quality and HS-review warnings come from nodes that are not re-run;
    everything else (weight reconciliation, deterministic and country checks)
    is recomputed on the corrected data.  Country-rule messages start with a
    "[US]"-style tag, which is how they are told apart from HS-review warnings.
    """
    if not cr:
        return []
    return [
        i for i in cr.issues
        if i.field == "ocr_confidence"
        or (i.field.startswith("line_items.") and not i.message.startswith("["))
    ]


@router.post(
    "/resume/{run_id}",
    response_model=WorkflowResponse,
    summary="Resume a blocked HITL workflow with corrected values",
)
async def resume_workflow(
    run_id: str,
    body: ResumeRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
) -> WorkflowResponse:
    row = _get_owned(run_id, user_id)
    if row.status != WorkflowStatus.BLOCKED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Workflow is not blocked")

    config = {"configurable": {"thread_id": run_id}}
    snapshot = await document_graph.aget_state(config)
    values = getattr(snapshot, "values", None)
    if not values:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Paused graph state is no longer available — please start a new run",
        )

    invoice = _as_model(InvoiceDocument, values.get("invoice"))
    bl = _as_model(BillOfLading, values.get("bill_of_lading"))
    if body.gross_weight_kg is not None and bl is not None:
        bl = bl.model_copy(update={"gross_weight_kg": body.gross_weight_kg})
    if body.invoice_gross_weight_kg is not None and invoice is not None:
        invoice = invoice.model_copy(update={"gross_weight_kg": body.invoice_gross_weight_kg})

    # Rebuild compliance for everything downstream of extraction: the weight
    # check runs here, and writing the update "as" compliance_reason makes
    # deterministic_validate and country_validate run again on the corrected
    # data before the graph decides whether to block a second time.
    cr = _as_model(ComplianceResult, values.get("compliance_result"))
    compliance = _merge_issues(None, _carried_over_issues(cr) + weight_issues(invoice, bl))

    await document_graph.aupdate_state(
        config,
        {"invoice": invoice, "bill_of_lading": bl, "compliance_result": compliance},
        as_node="compliance_reason",
    )

    row = _save(run_id, WorkflowStatus.RUNNING) or row
    background_tasks.add_task(_resume_graph, workflow_id=run_id)
    log.info(
        "workflow.resumed",
        workflow_id=run_id,
        bl_weight=body.gross_weight_kg,
        invoice_weight=body.invoice_gross_weight_kg,
    )
    return WorkflowResponse(**_to_record(row).model_dump())


@router.get(
    "/declaration/{run_id}",
    summary="Get final extracted declaration",
)
async def get_declaration(run_id: str, user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    wf = _to_record(_get_owned(run_id, user_id))
    if wf.status != WorkflowStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workflow is not complete yet")

    decl = wf.result.get("declaration")
    if not decl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Declaration not generated")

    return decl
