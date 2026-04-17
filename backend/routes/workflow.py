"""Workflow routes — trigger, monitor, edit, and chat over processed documents."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from metrics import (
    PIPELINE_RUNS_TOTAL,
    PIPELINE_DURATION_SECONDS,
    HITL_INTERRUPTS_TOTAL,
    COMPLIANCE_STATUS_TOTAL,
)
from pydantic import BaseModel, Field

from graph import (
    GraphState,
    NodeInterrupt,
    country_validate,
    declaration_generate,
    deterministic_validate,
    document_graph,
    reconcile,
)
from models import (
    BillOfLading,
    CountryCode,
    InvoiceDocument,
    ResumeRequest,
    WorkflowCreateRequest,
    WorkflowRecord,
    WorkflowResponse,
    WorkflowStatus,
    WorkflowStep,
)
from workflow_store import (
    load_blocked_snapshot,
    load_workflow_record,
    list_workflow_records_local,
    persist_workflow_record,
    save_blocked_snapshot,
)

log = structlog.get_logger(__name__)
router = APIRouter()

# In-memory store — replace with SQLModel/DB in production
_workflows: dict[str, WorkflowRecord] = {}


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


class WorkflowChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class WorkflowChatPatch(BaseModel):
    path: str = Field(description="Dot path into invoice/bill_of_lading fields")
    value: Any


class WorkflowChatPlan(BaseModel):
    reply: str
    should_update: bool = False
    patches: list[WorkflowChatPatch] = Field(default_factory=list)


class WorkflowChatResponse(BaseModel):
    reply: str
    updated: bool = False
    changes: list[str] = Field(default_factory=list)
    declaration: dict[str, Any] | None = None
    summary: str | None = None
    chat_history: list[dict[str, Any]] = Field(default_factory=list)


def _country_value(value: CountryCode | str) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _build_declaration_payload(
    run_id: str,
    country: str,
    invoice: InvoiceDocument | dict[str, Any] | None,
    bill_of_lading: BillOfLading | dict[str, Any] | None,
    compliance: Any,
) -> dict[str, Any]:
    invoice_payload = (
        invoice.model_dump() if hasattr(invoice, "model_dump") else invoice
    )
    bl_payload = (
        bill_of_lading.model_dump()
        if hasattr(bill_of_lading, "model_dump")
        else bill_of_lading
    )
    compliance_payload = (
        compliance.model_dump() if hasattr(compliance, "model_dump") else compliance
    )

    line_items = []
    if isinstance(invoice_payload, dict):
        line_items = invoice_payload.get("line_items") or []

    return {
        "run_id": run_id,
        "generated_at": datetime.utcnow().isoformat(),
        "jurisdiction": country.upper(),
        "invoice": invoice_payload,
        "bill_of_lading": bl_payload,
        "compliance": compliance_payload,
        "hs_codes": [
            {
                "description": item.get("description", ""),
                "hs_code": item.get("hs_code"),
            }
            for item in line_items
        ],
    }


def _build_summary_from_declaration(declaration: dict[str, Any] | None) -> str:
    if not declaration:
        return ""

    invoice = declaration.get("invoice") or {}
    bl = declaration.get("bill_of_lading") or {}
    compliance = declaration.get("compliance") or {}
    hs_codes = declaration.get("hs_codes") or []

    total_items = len(invoice.get("line_items") or [])
    assigned_hs = sum(1 for entry in hs_codes if entry.get("hs_code"))
    issues = len(compliance.get("issues") or [])

    return (
        f"Declaration {declaration.get('run_id')} | "
        f"{declaration.get('jurisdiction', 'N/A')} | "
        f"Status: {compliance.get('status', 'UNKNOWN')} | "
        f"Issues: {issues} | "
        f"HS codes assigned: {assigned_hs}/{total_items} | "
        f"Vessel: {bl.get('vessel', 'N/A')} | "
        f"Total: {invoice.get('currency', '')} {invoice.get('total_amount', 0):,.2f}"
    )


def _chat_history_from_record(wf: WorkflowRecord) -> list[dict[str, Any]]:
    history = wf.result.get("chat_history")
    return history if isinstance(history, list) else []


def _append_chat_history(
    wf: WorkflowRecord,
    user_message: str,
    assistant_reply: str,
    updated: bool,
    changes: list[str],
) -> list[dict[str, Any]]:
    history = _chat_history_from_record(wf)
    history.extend(
        [
            {"role": "user", "content": user_message},
            {
                "role": "assistant",
                "content": assistant_reply,
                "updated": updated,
                "changes": changes,
                "timestamp": datetime.utcnow().isoformat(),
            },
        ]
    )
    return history[-20:]

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


def _coerce_path_value(path: str, value: Any) -> Any:
    if value is None:
        return None

    lower_path = path.lower()
    if "weight" in lower_path or "amount" in lower_path or "quantity" in lower_path:
        try:
            return float(str(value).replace(",", "").strip())
        except ValueError:
            return value

    return value


def _set_nested_value(root: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in path.split(".") if part]
    if parts and parts[0] == "declaration":
        parts = parts[1:]

    current: Any = root
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1

        if isinstance(current, list):
            list_index = int(part)
            if is_last:
                current[list_index] = value
            else:
                current = current[list_index]
            continue

        if is_last:
            current[part] = value
            return

        next_part = parts[index + 1]
        if part not in current or current[part] is None:
            current[part] = [] if next_part.isdigit() else {}
        current = current[part]


async def _rebuild_result_from_documents(
    wf: WorkflowRecord,
    invoice_payload: dict[str, Any] | InvoiceDocument | None,
    bl_payload: dict[str, Any] | BillOfLading | None,
) -> tuple[dict[str, Any], WorkflowStatus]:
    invoice = (
        invoice_payload
        if isinstance(invoice_payload, InvoiceDocument) or invoice_payload is None
        else InvoiceDocument.model_validate(invoice_payload)
    )
    bill_of_lading = (
        bl_payload
        if isinstance(bl_payload, BillOfLading) or bl_payload is None
        else BillOfLading.model_validate(bl_payload)
    )

    state = GraphState(
        run_id=wf.id,
        document_id=str(wf.document_id),
        country=_country_value(wf.country),
        invoice=invoice,
        bill_of_lading=bill_of_lading,
    )

    updates = await reconcile(state)
    state = state.model_copy(update=updates)

    updates = await deterministic_validate(state)
    state = state.model_copy(update=updates)

    compliance = state.compliance_result
    has_block = bool(
        compliance and any(issue.severity == "block" for issue in compliance.issues)
    )

    if not has_block:
        updates = await country_validate(state)
        state = state.model_copy(update=updates)

    updates = await declaration_generate(state)
    state = state.model_copy(update=updates)

    declaration = state.declaration or _build_declaration_payload(
        str(wf.id),
        _country_value(wf.country),
        state.invoice,
        state.bill_of_lading,
        state.compliance_result,
    )
    summary = state.summary or _build_summary_from_declaration(declaration)
    compliance_payload = declaration.get("compliance") or {}
    issues = compliance_payload.get("issues") or []

    result = {
        "declaration": declaration,
        "summary": summary,
        "compliance_status": compliance_payload.get("status"),
        "compliance_issues": len(issues),
        "audit_events": len(state.audit_trail),
        "bboxes": wf.result.get("bboxes", []),
        "chat_history": _chat_history_from_record(wf),
    }

    workflow_status = (
        WorkflowStatus.BLOCKED
        if compliance_payload.get("status") == "BLOCK"
        else WorkflowStatus.COMPLETED
    )
    return result, workflow_status


def _fallback_chat_plan(
    declaration: dict[str, Any],
    message: str,
) -> WorkflowChatPlan:
    lower = message.lower().strip()

    editable_fields = {
        "invoice number": "invoice.invoice_number",
        "invoice date": "invoice.date",
        "seller": "invoice.seller",
        "buyer": "invoice.buyer",
        "invoice gross weight": "invoice.gross_weight_kg",
        "gross weight invoice": "invoice.gross_weight_kg",
        "total amount": "invoice.total_amount",
        "currency": "invoice.currency",
        "bl number": "bill_of_lading.bl_number",
        "bill of lading number": "bill_of_lading.bl_number",
        "vessel": "bill_of_lading.vessel",
        "port of loading": "bill_of_lading.port_of_loading",
        "port of discharge": "bill_of_lading.port_of_discharge",
        "consignee": "bill_of_lading.consignee",
        "shipper": "bill_of_lading.shipper",
        "bl gross weight": "bill_of_lading.gross_weight_kg",
        "bill gross weight": "bill_of_lading.gross_weight_kg",
        "bill of lading gross weight": "bill_of_lading.gross_weight_kg",
    }

    if any(keyword in lower for keyword in ("set ", "change ", "update ", "modify ")):
        for alias, path in editable_fields.items():
            marker = f"{alias} to "
            if marker in lower:
                raw_value = message[lower.index(marker) + len(marker):].strip(" .")
                return WorkflowChatPlan(
                    reply=f"Updated {alias} to {raw_value}.",
                    should_update=True,
                    patches=[
                        WorkflowChatPatch(
                            path=path,
                            value=_coerce_path_value(path, raw_value),
                        )
                    ],
                )

    compliance = declaration.get("compliance") or {}
    invoice = declaration.get("invoice") or {}
    bl = declaration.get("bill_of_lading") or {}
    issues = compliance.get("issues") or []

    if "summary" in lower or "what do we have" in lower or "status" in lower:
        summary = _build_summary_from_declaration(declaration)
        return WorkflowChatPlan(reply=summary or "No declaration summary is available yet.")

    if "weight" in lower:
        return WorkflowChatPlan(
            reply=(
                f"Invoice gross weight is {invoice.get('gross_weight_kg', 'N/A')} kg and "
                f"bill of lading gross weight is {bl.get('gross_weight_kg', 'N/A')} kg."
            )
        )

    if "issue" in lower or "problem" in lower or "compliance" in lower:
        if not issues:
            return WorkflowChatPlan(reply="There are no active compliance issues on this declaration.")
        return WorkflowChatPlan(
            reply="Active compliance issues:\n"
            + "\n".join(
                f"- [{issue.get('severity', 'warn').upper()}] {issue.get('field')}: {issue.get('message')}"
                for issue in issues
            )
        )

    return WorkflowChatPlan(
        reply=(
            "I can answer questions about the extracted invoice and bill of lading, "
            "or update fields when you say something like 'change bill of lading gross weight to 860'."
        )
    )


async def _plan_chat_response(
    declaration: dict[str, Any],
    message: str,
) -> WorkflowChatPlan:
    try:
        from llm_client import get_active_chat_model, get_instructor_client
        from llm_instrumented import tracked_instructor_create
    except Exception:
        return _fallback_chat_plan(declaration, message)

    try:
        client = get_instructor_client()
        from llm_client import ORGANIZER_API_KEY, ORGANIZER_CHAT_MODEL
        model = ORGANIZER_CHAT_MODEL if ORGANIZER_API_KEY else "llama3-8b-8192"
    except Exception:
        return _fallback_chat_plan(declaration, message)

    prompt = (
        "You are the in-app customs review assistant. "
        "You can answer questions about the current declaration and apply targeted edits "
        "when the user explicitly requests a change.\n\n"
        "Rules:\n"
        "- Only set should_update=true if the user clearly wants to change extracted data.\n"
        "- Keep edits minimal and use paths rooted at invoice.* or bill_of_lading.*.\n"
        "- Do not invent fields outside the current declaration shape.\n"
        "- reply should be concise and mention what changed when you emit patches.\n\n"
        f"Current declaration JSON:\n{json.dumps(declaration, ensure_ascii=False)}\n\n"
        f"User message:\n{message}"
    )

    try:
        return tracked_instructor_create(
            client,
            model=model,
            call_type="bill_chat",
            response_model=WorkflowChatPlan,
            messages=[{"role": "user", "content": prompt}],
            max_retries=2,
        )
    except Exception:
        return _fallback_chat_plan(declaration, message)


# ---------------------------------------------------------------------------
# Background pipeline runner
# ---------------------------------------------------------------------------

def _state_get(state: Any, key: str, default: Any = None) -> Any:
    """Get a value from either a dict state or Pydantic BaseModel state."""
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


async def _handle_blocked(
    wf: WorkflowRecord,
    workflow_id: str,
    msg: str,
    config: dict,
) -> None:
    """Shared logic for setting wf to BLOCKED after a NodeInterrupt."""
    wf.status = WorkflowStatus.BLOCKED
    from graph import document_graph

    saved_state = document_graph.get_state(config)
    issues: list[dict] = []
    audit_steps: list[WorkflowStep] = []
    invoice_payload: dict[str, Any] | None = None
    bl_payload: dict[str, Any] | None = None
    compliance_payload: dict[str, Any] | None = None
    mapped_bboxes: list[dict[str, Any]] = []

    if saved_state and getattr(saved_state, "values", None):
        values = saved_state.values
        save_blocked_snapshot(workflow_id, values)

        cr = values.get("compliance_result")
        if cr:
            issues = [i.model_dump() for i in cr.issues]
            compliance_payload = cr.model_dump()

        invoice = values.get("invoice")
        bill_of_lading = values.get("bill_of_lading")
        invoice_payload = (
            invoice.model_dump() if hasattr(invoice, "model_dump") else invoice
        )
        bl_payload = (
            bill_of_lading.model_dump()
            if hasattr(bill_of_lading, "model_dump")
            else bill_of_lading
        )

        mapped_bboxes = [
            bbox.model_dump()
            for bbox in map_fields_to_bboxes(
                invoice,
                (values.get("invoice_bboxes") or []) + (values.get("bl_bboxes") or []),
            )
        ]

        audit_trail = saved_state.values.get("audit_trail") or []
        audit_steps = [
            WorkflowStep(
                name=e.node_name,
                status=WorkflowStatus.COMPLETED,
                output={
                    "output_summary": e.output_summary,
                    "reasoning_note": e.reasoning_note,
                },
            )
            for e in audit_trail
        ]
    n_blocks = len([i for i in issues if i.get("severity") == "block"])
    audit_steps.append(
        WorkflowStep(
            name="interrupt_node",
            status=WorkflowStatus.BLOCKED,
            output={
                "reasoning_note": f"⛔ HITL pause: {n_blocks} blocking issue(s) require human review. Pipeline suspended at this node — awaiting operator correction."
            },
        )
    )

    declaration = _build_declaration_payload(
        run_id=workflow_id,
        country=_country_value(wf.country),
        invoice=invoice_payload,
        bill_of_lading=bl_payload,
        compliance=compliance_payload or {"status": "BLOCK", "issues": issues},
    )
    summary = _build_summary_from_declaration(declaration)

    wf.steps = audit_steps
    wf.result = {
        "declaration": declaration,
        "invoice": invoice_payload,
        "bill_of_lading": bl_payload,
        "summary": summary,
        "compliance_status": "BLOCK",
        "compliance_issues": len(issues),
        "hitl_required": True,
        "message": msg,
        "bboxes": mapped_bboxes,
        "issues": issues,
        "reasoning_note": f"BLOCKED for human review. Fix the conflicting fields, then resume or edit through chat.",
        "chat_history": _chat_history_from_record(wf),
    }
    wf.updated_at = datetime.utcnow()
    await persist_workflow_record(wf)
    log.warning("workflow.hitl_interrupt", workflow_id=workflow_id, detail=msg)


async def _run_graph(
    workflow_id: str,
    document_id: str,
    country: str,
    invoice_pdf_path: str,
    bl_pdf_path: str,
) -> None:
    """Background task: run the LangGraph pipeline and update workflow state."""

    # Guard: backend may have restarted, wiping the in-memory dict.
    wf = _workflows.get(workflow_id)
    if not wf:
        log.error("workflow.run_graph.missing", workflow_id=workflow_id)
        return

    # Set RUNNING immediately so the UI reflects progress.
    wf.status = WorkflowStatus.RUNNING
    wf.updated_at = datetime.utcnow()
    await persist_workflow_record(wf)
    log.info("workflow.run_graph.start", workflow_id=workflow_id,
             invoice=invoice_pdf_path, bl=bl_pdf_path)

    initial_state = GraphState(
        document_id=document_id,
        country=country,
        invoice_pdf_path=invoice_pdf_path,
        bl_pdf_path=bl_pdf_path,
    )

    config = {"configurable": {"thread_id": workflow_id}}
    _pipeline_start = time.monotonic()
    t_start = time.monotonic()

    try:
        result = await document_graph.ainvoke(initial_state, config)

        # LangGraph 1.1.6+ does NOT re-raise NodeInterrupt — ainvoke returns a
        # dict containing '__interrupt__' when a node pauses execution.
        if isinstance(result, dict) and "__interrupt__" in result:
            interrupts = result["__interrupt__"]
            msg = str(interrupts[0].value) if interrupts else "HITL interrupt"
            await _handle_blocked(wf, workflow_id, msg, config)
            PIPELINE_RUNS_TOTAL.labels(status="blocked", country=country).inc()
            HITL_INTERRUPTS_TOTAL.labels(reason="compliance_block").inc()
            return

        # Normal completion — result may be a dict or a GraphState Pydantic model.
        cr = _state_get(result, "compliance_result")
        invoice = _state_get(result, "invoice")
        invoice_bboxes = _state_get(result, "invoice_bboxes") or []
        bl_bboxes = _state_get(result, "bl_bboxes") or []
        audit_trail = _state_get(result, "audit_trail") or []

        mapped_bboxes = map_fields_to_bboxes(invoice, invoice_bboxes + bl_bboxes)

        _elapsed = time.monotonic() - _pipeline_start
        _cr = _state_get(result, "compliance_result")
        _comp = _cr.status if _cr else "UNKNOWN"
        PIPELINE_RUNS_TOTAL.labels(status="completed", country=country).inc()
        PIPELINE_DURATION_SECONDS.labels(country=country).observe(_elapsed)
        COMPLIANCE_STATUS_TOTAL.labels(status=_comp).inc()
        wf.status = WorkflowStatus.COMPLETED
        wf.result = {
            "declaration":       _state_get(result, "declaration"),
            "summary":           _state_get(result, "summary", ""),
            "compliance_status": cr.status if cr else None,
            "compliance_issues": len(cr.issues) if cr else 0,
            "audit_events":      len(audit_trail),
            "bboxes":            [b.model_dump() for b in mapped_bboxes],
        }
        wf.steps = [
            WorkflowStep(
                name=e.node_name,
                status=WorkflowStatus.COMPLETED,
                output={
                    "output_summary": e.output_summary,
                    "reasoning_note": e.reasoning_note,
                },
            )
            for e in audit_trail
        ]
        wf.updated_at = datetime.utcnow()
        elapsed = time.monotonic() - t_start
        await persist_workflow_record(wf)
        log.info("workflow.completed", workflow_id=workflow_id, bboxes=len(mapped_bboxes),
                 duration_s=round(elapsed, 2))

    except NodeInterrupt as exc:
        # Fallback: older LangGraph behaviour where NodeInterrupt propagates.
        PIPELINE_RUNS_TOTAL.labels(status="blocked", country=country).inc()
        HITL_INTERRUPTS_TOTAL.labels(reason="compliance_block").inc()
        await _handle_blocked(wf, workflow_id, str(exc), config)

    except Exception as exc:
        PIPELINE_RUNS_TOTAL.labels(status="failed", country=country).inc()
        wf.status = WorkflowStatus.FAILED
        wf.result = {"error": str(exc), "bboxes": []}
        wf.updated_at = datetime.utcnow()
        await persist_workflow_record(wf)
        log.error("workflow.failed", workflow_id=workflow_id, error=str(exc))

    if wf.status not in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.BLOCKED}:
        wf.updated_at = datetime.utcnow()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a processing workflow for a document pair",
)
async def create_workflow(
    body: WorkflowCreateRequest,
    background_tasks: BackgroundTasks,
) -> WorkflowResponse:
    # Use the original document/run ID to ensure persistence mapping works.
    workflow_id = str(body.document_id)

    wf = WorkflowRecord(
        id=uuid.UUID(workflow_id),
        document_id=body.document_id,
        country=body.country,
        status=WorkflowStatus.QUEUED,
    )
    _workflows[workflow_id] = wf
    await persist_workflow_record(wf)

    # Resolve actual file paths from the DB row written by the upload route.
    run_id_str = str(body.document_id)
    from repositories.run_repository import get_run
    upload_row = await get_run(run_id_str)

    if upload_row:
        invoice_path = upload_row.get("invoice_path")
        bl_path      = upload_row.get("bl_path")
    else:
        # Fallback for uploads made before this DB-backed path was introduced.
        invoice_path = f"uploads/{run_id_str}_invoice.pdf"
        bl_path      = f"uploads/{run_id_str}_bl.pdf"
        log.warning(
            "workflow.upload_row_missing",
            run_id=run_id_str,
            fallback_invoice=invoice_path,
            fallback_bl=bl_path,
        )

    background_tasks.add_task(
        _run_graph,
        workflow_id=workflow_id,
        document_id=run_id_str,
        country=body.country.value,
        invoice_pdf_path=invoice_path,
        bl_pdf_path=bl_path,
    )

    log.info("workflow.queued", workflow_id=workflow_id, document_id=run_id_str)
    return WorkflowResponse(**wf.model_dump())


# NOTE: /status/{run_id} MUST be declared before /{workflow_id} so FastAPI
# does not swallow the literal word "status" as a workflow_id path parameter.
@router.get(
    "/status/{run_id}",
    response_model=StatusResponse,
    summary="Get workflow status with field-level bbox annotations",
)
async def get_run_status(run_id: str) -> StatusResponse:
    """Return the workflow record plus a `bboxes` list for the PDF viewer overlay.

    `run_id` is the same UUID returned by POST /workflow/ (i.e. the workflow_id).
    The `bboxes` field is populated once the pipeline has completed OCR and
    extraction; it is an empty list while the run is still in progress.
    """
    wf = _workflows.get(run_id)
    if not wf:
        wf = await load_workflow_record(run_id)
        if wf is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
        _workflows[run_id] = wf

    result = wf.result.copy() if wf.result else {}
    steps = wf.steps or []

    if wf.status == WorkflowStatus.BLOCKED:
        snapshot = load_blocked_snapshot(run_id)
        if snapshot:
            if snapshot.get("invoice") and not result.get("invoice"):
                result["invoice"] = snapshot["invoice"]
            if snapshot.get("bill_of_lading") and not result.get("bill_of_lading"):
                result["bill_of_lading"] = snapshot["bill_of_lading"]

    raw_bboxes: list[dict[str, Any]] = result.get("bboxes", [])  # type: ignore[assignment]
    bboxes = [BBoxEntry(**b) for b in raw_bboxes]

    base_data = wf.model_dump(exclude={"result", "steps"})

    return StatusResponse(
        **base_data,
        result=result,
        steps=steps,
        bboxes=bboxes,
        invoice_pdf_url=f"/uploads/{wf.document_id}_invoice.pdf",
        bl_pdf_url=f"/uploads/{wf.document_id}_bl.pdf"
    )


@router.get(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Get workflow status and results",
)
async def get_workflow(workflow_id: str) -> WorkflowResponse:
    wf = _workflows.get(workflow_id)
    if not wf:
        wf = await load_workflow_record(workflow_id)
        if wf is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        _workflows[workflow_id] = wf
    return WorkflowResponse(**wf.model_dump())


@router.get(
    "/",
    response_model=list[WorkflowResponse],
    summary="List all workflows",
)
async def list_workflows() -> list[WorkflowResponse]:
    for wf in list_workflow_records_local():
        _workflows[str(wf.id)] = wf

    if not _workflows:
        from repositories.run_repository import _repo as run_repo
        rows = await run_repo.list_all()
        for row in rows:
            wf = WorkflowRecord(
                id=uuid.UUID(row.get("run_id")),
                document_id=row.get("run_id"),
                country=CountryCode(row.get("country", "US")),
                status=WorkflowStatus(row.get("status", "queued").lower()),
                updated_at=row.get("updated_at", datetime.utcnow().isoformat()),
            )
            _workflows[row.get("run_id")] = wf

    records = sorted(
        _workflows.values(),
        key=lambda item: item.updated_at,
        reverse=True,
    )
    return [WorkflowResponse(**wf.model_dump()) for wf in records]


@router.post(
    "/resume/{run_id}",
    response_model=WorkflowResponse,
    summary="Resume a blocked HITL workflow with corrected values",
)
async def resume_workflow(
    run_id: str,
    body: ResumeRequest,
    background_tasks: BackgroundTasks,
) -> WorkflowResponse:
    wf = _workflows.get(run_id)
    if not wf:
        wf = await load_workflow_record(run_id)
        if wf is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        _workflows[run_id] = wf
    if wf.status != WorkflowStatus.BLOCKED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Workflow is not blocked")

    snapshot = load_blocked_snapshot(run_id) or {}
    invoice_payload = (
        snapshot.get("invoice")
        or wf.result.get("invoice")
        or (wf.result.get("declaration") or {}).get("invoice")
    )
    bl_payload = (
        snapshot.get("bill_of_lading")
        or wf.result.get("bill_of_lading")
        or (wf.result.get("declaration") or {}).get("bill_of_lading")
    )

    if not invoice_payload or not bl_payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Blocked document snapshot is missing; cannot resume this run.",
        )

    if body.gross_weight_kg is not None:
        if isinstance(bl_payload, dict):
            bl_payload["gross_weight_kg"] = body.gross_weight_kg
        if isinstance(invoice_payload, dict) and not invoice_payload.get("gross_weight_kg"):
            invoice_payload["gross_weight_kg"] = body.gross_weight_kg

    wf.status = WorkflowStatus.RUNNING
    wf.updated_at = datetime.utcnow()
    result, final_status = await _rebuild_result_from_documents(
        wf,
        invoice_payload=invoice_payload,
        bl_payload=bl_payload,
    )
    wf.status = final_status
    wf.result = result
    wf.steps = [
        *wf.steps,
        WorkflowStep(
            name="manual_resume",
            status=WorkflowStatus.COMPLETED if final_status == WorkflowStatus.COMPLETED else WorkflowStatus.BLOCKED,
            output={
                "reasoning_note": (
                    f"Manual correction applied. Gross weight set to {body.gross_weight_kg} kg."
                    if body.gross_weight_kg is not None
                    else "Manual correction applied."
                )
            },
        ),
    ]
    await persist_workflow_record(wf)
    log.info("workflow.resumed", workflow_id=run_id)
    return WorkflowResponse(**wf.model_dump())


@router.post(
    "/chat/{run_id}",
    response_model=WorkflowChatResponse,
    summary="Chat about a processed document pair and optionally edit extracted data",
)
async def chat_with_workflow(
    run_id: str,
    body: WorkflowChatRequest,
) -> WorkflowChatResponse:
    wf = _workflows.get(run_id)
    if not wf:
        wf = await load_workflow_record(run_id)
        if wf is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        _workflows[run_id] = wf

    declaration = wf.result.get("declaration")
    if not declaration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This workflow does not have a declaration available yet.",
        )

    plan = await _plan_chat_response(declaration, body.message)
    updated = False
    changes: list[str] = []
    working_declaration = json.loads(json.dumps(declaration))

    if plan.should_update and plan.patches:
        for patch in plan.patches:
            coerced = _coerce_path_value(patch.path, patch.value)
            _set_nested_value(working_declaration, patch.path, coerced)
            changes.append(f"{patch.path} -> {coerced}")

        rebuilt_result, final_status = await _rebuild_result_from_documents(
            wf,
            invoice_payload=working_declaration.get("invoice"),
            bl_payload=working_declaration.get("bill_of_lading"),
        )
        wf.status = final_status
        wf.updated_at = datetime.utcnow()
        updated = True
        working_declaration = rebuilt_result.get("declaration") or working_declaration

        wf.result = {
            **wf.result,
            **rebuilt_result,
        }

    history = _append_chat_history(
        wf,
        user_message=body.message,
        assistant_reply=plan.reply,
        updated=updated,
        changes=changes,
    )
    wf.result["chat_history"] = history
    wf.result["declaration"] = working_declaration
    wf.result["summary"] = wf.result.get("summary") or _build_summary_from_declaration(working_declaration)
    wf.updated_at = datetime.utcnow()
    await persist_workflow_record(wf)

    return WorkflowChatResponse(
        reply=plan.reply,
        updated=updated,
        changes=changes,
        declaration=wf.result.get("declaration"),
        summary=wf.result.get("summary"),
        chat_history=history,
    )


@router.get(
    "/declaration/{run_id}",
    summary="Get final extracted declaration",
)
async def get_declaration(run_id: str) -> dict[str, Any]:
    wf = _workflows.get(run_id)
    if not wf:
        wf = await load_workflow_record(run_id)
    if wf and wf.result.get("declaration"):
        return wf.result["declaration"]

    from repositories.run_repository import get_run

    run = await get_run(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    
    declaration_json = run.get("declaration_json")
    if not declaration_json:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Declaration not generated yet")
    
    # Firestore has string representation (unencrypted)
    return json.loads(declaration_json) if isinstance(declaration_json, str) else declaration_json
