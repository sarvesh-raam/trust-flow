"""Hackstrom Track 3 — LangGraph document-processing pipeline.

Topology:

    ingest ─► preprocess ─► ocr_extract ─┬─(conf < 0.7)─► vision_adjudication ─┐
                                          └─(conf ≥ 0.7)──────────────────────►─┤
                                                                                 ▼
                                                                          field_extract
                                                                                 │
                                                                           reconcile
                                                                                 │
                                                                          hs_retrieve
                                                                                 │
                                                                     compliance_reason
                                                                                 │
                                                                deterministic_validate
                                                                                 │
                                                                     country_validate
                                                                ┌────────────────┤
                                                           (BLOCK)          (no BLOCK)
                                                                ▼                │
                                                       interrupt_node            │
                                                                └───────────────►┤
                                                                                 ▼
                                                                   declaration_generate
                                                                                 │
                                                                        audit_trace ─► END

HITL note: interrupt_node raises NodeInterrupt, which pauses the run at the
last checkpoint.  routes/workflow.py applies the operator's corrections with
update_state(as_node="compliance_reason") so both validate nodes re-run on the
corrected data before the run continues.  main.py swaps in a SQLite
checkpointer so paused runs survive a restart.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml
from sqlmodel import Field as SQLField, Session, SQLModel, delete

from db import BACKEND_DIR, engine
from metrics import OCR_CONFIDENCE
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

try:
    from langgraph.errors import NodeInterrupt
except ImportError:  # very old langgraph builds
    class NodeInterrupt(Exception):  # type: ignore[misc]
        """Sentinel exception that pauses a LangGraph run for human review."""

from models import (
    AuditEvent,
    BillOfLading,
    ComplianceIssue,
    ComplianceResult,
    HSCandidate,
    InvoiceDocument,
    WorkflowState,
)
from nodes.ocr_extract import ocr_extract_node as _ocr_extract_impl
from nodes.field_extract import field_extract_node as _field_extract_impl
from nodes.hs_retrieve import hs_retrieve_node as _hs_retrieve_impl
from nodes.compliance_reason import compliance_reason_node as _compliance_reason_impl

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# SQLModel — audit persistence
# ---------------------------------------------------------------------------

class AuditEventRow(SQLModel, table=True):
    """Persistent audit record written by the audit_trace node."""

    __tablename__ = "audit_events"

    id: int | None = SQLField(default=None, primary_key=True)
    run_id: str = SQLField(index=True)
    timestamp: datetime = SQLField(default_factory=datetime.utcnow)
    node_name: str
    input_summary: str = ""
    output_summary: str = ""
    latency_ms: float = 0.0


def persist_audit_trail(run_id: str, events: list[AuditEvent]) -> int:
    """Replace the stored audit rows for run_id with events.

    Idempotent: called when a run pauses for HITL and again by audit_trace when
    it finishes, without duplicating rows.
    """
    rows = [
        AuditEventRow(
            run_id=run_id,
            timestamp=event.timestamp,
            node_name=event.node_name,
            input_summary=event.input_summary,
            output_summary=event.output_summary,
            latency_ms=event.latency_ms,
        )
        for event in events
    ]
    with Session(engine) as session:
        session.exec(delete(AuditEventRow).where(AuditEventRow.run_id == run_id))
        session.add_all(rows)
        session.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# GraphState — extends WorkflowState with runtime-only fields
# ---------------------------------------------------------------------------

class GraphState(WorkflowState):
    """Full pipeline state.

    WorkflowState carries the persisted output fields (OCR data, trade docs,
    compliance, audit trail).  The fields below are ephemeral runtime context
    that the caller provides and that nodes consume but which are not part of
    the final declaration.
    """

    # Provided by the caller before the graph starts
    document_id: str = ""
    country: str = "us"

    # Set by declaration_generate
    declaration: dict[str, Any] | None = None
    summary: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_ms() -> float:
    return time.monotonic() * 1000


def _elapsed(start_ms: float) -> float:
    return _now_ms() - start_ms


def _emit(
    state: GraphState,
    node_name: str,
    input_summary: str,
    output_summary: str,
    latency_ms: float,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Attach an AuditEvent to the state updates returned by a node."""
    event = AuditEvent(
        node_name=node_name,
        input_summary=input_summary,
        output_summary=output_summary,
        latency_ms=latency_ms,
    )
    updates["audit_trail"] = state.audit_trail + [event]
    return updates


def _recompute_status(
    issues: list[ComplianceIssue],
) -> Literal["PASS", "WARN", "BLOCK"]:
    if any(i.severity == "block" for i in issues):
        return "BLOCK"
    if any(i.severity == "warn" for i in issues):
        return "WARN"
    return "PASS"


def _merge_issues(
    current: ComplianceResult | None,
    new_issues: list[ComplianceIssue],
) -> ComplianceResult:
    """Return a new ComplianceResult with new_issues appended and status recomputed."""
    existing = current.issues if current else []
    all_issues = existing + new_issues
    return ComplianceResult(
        status=_recompute_status(all_issues),
        issues=all_issues,
    )


def _rules_dir() -> Path:
    """COUNTRY_RULES_DIR env var, else <repo>/country_rules (local dev layout)."""
    env_dir = os.getenv("COUNTRY_RULES_DIR")
    return Path(env_dir) if env_dir else BACKEND_DIR.parent / "country_rules"


def _load_rules(country: str) -> dict[str, Any]:
    rules_path = _rules_dir() / f"{country.lower()}.yaml"
    if rules_path.exists():
        return yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    log.warning("country_rules.missing", country=country, path=str(rules_path))
    return {}


# ---------------------------------------------------------------------------
# Node 1 — ingest
# ---------------------------------------------------------------------------

async def ingest(state: GraphState) -> dict[str, Any]:
    """Log document receipt.

    The caller sets run_id to the workflow id, so the declaration, audit rows
    and checkpoint thread can all be traced back to the same workflow.
    """
    t0 = _now_ms()
    run_id = state.run_id
    log.info(
        "ingest.start",
        run_id=str(run_id),
        document_id=state.document_id,
        invoice_path=state.invoice_pdf_path,
        bl_path=state.bl_pdf_path,
        country=state.country,
    )
    return _emit(
        state,
        "ingest",
        input_summary=f"doc={state.document_id} country={state.country}",
        output_summary=f"run_id={run_id} received",
        latency_ms=_elapsed(t0),
        updates={},
    )


# ---------------------------------------------------------------------------
# Node 2 — preprocess
# ---------------------------------------------------------------------------

async def preprocess(state: GraphState) -> dict[str, Any]:
    """Pre-flight validation: confirm both file paths exist and are readable.

    Does not set needs_vision_fallback — that is determined by actual OCR
    confidence in ocr_extract_node.  A quick filename-extension check is
    logged as a hint only.
    """
    t0 = _now_ms()
    image_extensions = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}

    inv_path = state.invoice_pdf_path or ""
    bl_path  = state.bl_pdf_path  or ""

    inv_image = Path(inv_path).suffix.lower() in image_extensions if inv_path else False
    bl_image  = Path(bl_path).suffix.lower()  in image_extensions if bl_path  else False

    missing = [
        label for label, p in [("invoice", inv_path), ("bl", bl_path)]
        if not p or not Path(p).exists()
    ]
    if missing:
        log.warning("preprocess.files_missing", missing=missing)
    else:
        log.info(
            "preprocess.done",
            inv_path=inv_path,
            bl_path=bl_path,
            inv_image_hint=inv_image,
            bl_image_hint=bl_image,
        )

    return _emit(
        state,
        "preprocess",
        input_summary=f"invoice={inv_path or 'N/A'} bl={bl_path or 'N/A'}",
        output_summary=f"inv_image_hint={inv_image} bl_image_hint={bl_image} missing={missing}",
        latency_ms=_elapsed(t0),
        updates={},
    )


# ---------------------------------------------------------------------------
# Node 3 — ocr_extract
# ---------------------------------------------------------------------------

async def ocr_extract(state: GraphState) -> dict[str, Any]:
    """Run Docling OCR on both uploaded documents.

    Delegates to nodes.ocr_extract.ocr_extract_node (sync, CPU-bound) via
    run_in_executor so the event loop is not blocked.
    """
    t0 = _now_ms()

    loop = asyncio.get_running_loop()
    updated: WorkflowState = await loop.run_in_executor(
        None, _ocr_extract_impl, state
    )

    OCR_CONFIDENCE.observe(updated.ocr_confidence)
    inv_chars = len(updated.invoice_ocr_text or "")
    bl_chars  = len(updated.bl_ocr_text  or "")
    inv_bbox  = len(updated.invoice_bboxes or [])
    bl_bbox   = len(updated.bl_bboxes   or [])

    return _emit(
        state,
        "ocr_extract",
        input_summary=(
            f"invoice={state.invoice_pdf_path or 'N/A'} "
            f"bl={state.bl_pdf_path or 'N/A'}"
        ),
        output_summary=(
            f"inv_chars={inv_chars} bl_chars={bl_chars} "
            f"inv_bboxes={inv_bbox} bl_bboxes={bl_bbox} "
            f"confidence={updated.ocr_confidence:.3f} "
            f"fallback={updated.needs_vision_fallback}"
        ),
        latency_ms=_elapsed(t0),
        updates={
            "invoice_ocr_text":    updated.invoice_ocr_text,
            "bl_ocr_text":         updated.bl_ocr_text,
            "invoice_tables":      updated.invoice_tables,
            "bl_tables":           updated.bl_tables,
            "invoice_bboxes":      updated.invoice_bboxes,
            "bl_bboxes":           updated.bl_bboxes,
            "ocr_confidence":      updated.ocr_confidence,
            "needs_vision_fallback": updated.needs_vision_fallback,
        },
    )


# ---------------------------------------------------------------------------
# Node 4 (conditional) — vision_adjudication
# ---------------------------------------------------------------------------

async def vision_adjudication(state: GraphState) -> dict[str, Any]:
    """Handle a low-confidence scan.

    No vision model is wired up yet, so this node does NOT alter the OCR text
    or confidence.  It raises a WARN so the operator knows to check the
    extracted fields against the source document.
    TODO: send page images to a multimodal model and re-extract text + bboxes.
    """
    t0 = _now_ms()
    log.warning(
        "vision_adjudication.triggered",
        original_confidence=state.ocr_confidence,
    )

    issue = ComplianceIssue(
        field="ocr_confidence",
        message=(
            f"Low OCR confidence ({state.ocr_confidence:.2f} < 0.70) and no vision "
            f"fallback is configured — verify extracted fields against the documents"
        ),
        severity="warn",
    )
    return _emit(
        state,
        "vision_adjudication",
        input_summary=f"confidence={state.ocr_confidence:.3f}",
        output_summary="vision model not configured; flagged for manual review",
        latency_ms=_elapsed(t0),
        updates={
            "compliance_result":     _merge_issues(state.compliance_result, [issue]),
            "needs_vision_fallback": False,
        },
    )


# ---------------------------------------------------------------------------
# Node 5 — field_extract
# ---------------------------------------------------------------------------

async def field_extract(state: GraphState) -> dict[str, Any]:
    """Extract structured InvoiceDocument and BillOfLading from raw OCR text.

    Delegates to nodes/field_extract.py which uses Instructor + Groq or a
    local vLLM server.  Run in a thread executor so the blocking I/O call
    does not stall the event loop.
    """
    t0 = _now_ms()
    inv_chars = len(state.invoice_ocr_text or "")
    bl_chars  = len(state.bl_ocr_text  or "")

    loop    = asyncio.get_event_loop()
    updated: GraphState = await loop.run_in_executor(None, _field_extract_impl, state)

    invoice = updated.invoice
    bl      = updated.bill_of_lading

    return _emit(
        state,
        "field_extract",
        input_summary=f"inv_chars={inv_chars} bl_chars={bl_chars}",
        output_summary=(
            f"invoice={invoice.invoice_number if invoice else 'none'} "
            f"bl={bl.bl_number if bl else 'none'}"
        ),
        latency_ms=_elapsed(t0),
        updates={"invoice": invoice, "bill_of_lading": bl},
    )


# ---------------------------------------------------------------------------
# Node 6 — reconcile
# ---------------------------------------------------------------------------

WEIGHT_TOLERANCE_PCT = 5.0


def weight_issues(
    inv: InvoiceDocument | None, bl: BillOfLading | None
) -> list[ComplianceIssue]:
    """BLOCK when invoice and B/L gross weights diverge by more than 5 %.

    Shared by reconcile and the HITL resume route, which re-runs it on the
    operator's corrected values.
    """
    if not (inv and bl and inv.gross_weight_kg > 0 and bl.gross_weight_kg > 0):
        return []
    diff_pct = abs(inv.gross_weight_kg - bl.gross_weight_kg) / bl.gross_weight_kg * 100
    if diff_pct <= WEIGHT_TOLERANCE_PCT:
        return []
    log.warning("reconcile.weight_mismatch", diff_pct=diff_pct)
    return [
        ComplianceIssue(
            field="gross_weight_kg",
            message=(
                f"Invoice weight ({inv.gross_weight_kg} kg) differs from "
                f"B/L weight ({bl.gross_weight_kg} kg) by {diff_pct:.1f}% "
                f"(threshold {WEIGHT_TOLERANCE_PCT:g}%)"
            ),
            severity="block",
        )
    ]


async def reconcile(state: GraphState) -> dict[str, Any]:
    """Cross-check invoice vs B/L.  Adds a BLOCK when gross weights diverge > 5 %."""
    t0 = _now_ms()
    inv = state.invoice
    bl = state.bill_of_lading

    new_issues = weight_issues(inv, bl)
    compliance = _merge_issues(state.compliance_result, new_issues)
    summary = "weight_diff_ok" if not new_issues else "weight_mismatch"
    return _emit(
        state,
        "reconcile",
        input_summary=(
            f"inv_weight={inv.gross_weight_kg if inv else 'N/A'} "
            f"bl_weight={bl.gross_weight_kg if bl else 'N/A'}"
        ),
        output_summary=summary,
        latency_ms=_elapsed(t0),
        updates={"compliance_result": compliance},
    )


# ---------------------------------------------------------------------------
# Node 7 — hs_retrieve
# ---------------------------------------------------------------------------

async def hs_retrieve(state: GraphState) -> dict[str, Any]:
    """Query USITC HTS API for HS code candidates for each invoice line item.

    Delegates to nodes/hs_retrieve.py which tries the live USITC REST API and
    silently falls back to the bundled sample data when offline.
    """
    t0 = _now_ms()

    if not state.invoice:
        return _emit(
            state,
            "hs_retrieve",
            input_summary="invoice=None",
            output_summary="skipped",
            latency_ms=_elapsed(t0),
            updates={},
        )

    n_items = len(state.invoice.line_items)
    await _hs_retrieve_impl(state)   # mutates state.invoice.line_items in-place

    total_candidates = sum(len(item.hs_candidates) for item in state.invoice.line_items)
    log.info("hs_retrieve.done", items=n_items, total_candidates=total_candidates)
    return _emit(
        state,
        "hs_retrieve",
        input_summary=f"line_items={n_items}",
        output_summary=f"items={n_items} total_candidates={total_candidates}",
        latency_ms=_elapsed(t0),
        updates={"invoice": state.invoice},
    )


# ---------------------------------------------------------------------------
# Node 8 — compliance_reason
# ---------------------------------------------------------------------------

async def compliance_reason(state: GraphState) -> dict[str, Any]:
    """LLM picks the best HS code for each line item and writes a rationale.

    Delegates to nodes/compliance_reason.py (sync Instructor call) via
    run_in_executor.  After the call, any line items flagged for review
    (confidence < 0.5 or code not in candidates) are added as WARN compliance
    issues so the downstream validate node can surface them.
    """
    t0 = _now_ms()

    if not state.invoice:
        return _emit(
            state,
            "compliance_reason",
            input_summary="invoice=None",
            output_summary="skipped",
            latency_ms=_elapsed(t0),
            updates={},
        )

    n_items = len(state.invoice.line_items)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _compliance_reason_impl, state)

    # ── Surface flag_for_review items as WARN compliance issues ──────────────
    selections = state.__dict__.pop("_hs_selections", [])
    new_issues: list[ComplianceIssue] = []
    for sel in selections:
        if sel.flag_for_review:
            idx = sel.line_item_index
            desc = (
                state.invoice.line_items[idx].description[:60]
                if idx < n_items
                else f"index {idx}"
            )
            new_issues.append(
                ComplianceIssue(
                    field=f"line_items.{idx}.hs_code",
                    message=(
                        f"HS code '{sel.selected_code}' flagged for review: "
                        f"{sel.rationale[:120]}"
                    ),
                    severity="warn",
                )
            )
            log.warning(
                "compliance_reason.flagged",
                index=idx,
                description=desc,
                code=sel.selected_code,
                confidence=sel.confidence,
            )

    chosen = [item.hs_code for item in state.invoice.line_items if item.hs_code]
    compliance = _merge_issues(state.compliance_result, new_issues)
    log.info("compliance_reason.done", codes_assigned=chosen, flags=len(new_issues))
    return _emit(
        state,
        "compliance_reason",
        input_summary=f"items={n_items}",
        output_summary=f"codes_assigned={chosen} flags={len(new_issues)}",
        latency_ms=_elapsed(t0),
        updates={"invoice": state.invoice, "compliance_result": compliance},
    )


# ---------------------------------------------------------------------------
# Node 9 — deterministic_validate
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


async def deterministic_validate(state: GraphState) -> dict[str, Any]:
    """Pure-Python checks: required fields, date format, positive weight.

    Issues with severity='block' trigger the interrupt_node branch.
    """
    t0 = _now_ms()
    new_issues: list[ComplianceIssue] = []

    inv = state.invoice
    bl = state.bill_of_lading

    # ── Invoice checks ────────────────────────────────────────────────────────
    if inv is None:
        new_issues.append(
            ComplianceIssue(
                field="invoice",
                message="No invoice document was extracted",
                severity="block",
            )
        )
    else:
        if not inv.invoice_number.strip():
            new_issues.append(
                ComplianceIssue(
                    field="invoice.invoice_number",
                    message="Invoice number is empty",
                    severity="block",
                )
            )
        if not _ISO_DATE_RE.match(inv.date):
            new_issues.append(
                ComplianceIssue(
                    field="invoice.date",
                    message=f"Date '{inv.date}' is not ISO-8601 (YYYY-MM-DD)",
                    severity="block",
                )
            )
        if not inv.seller.strip():
            new_issues.append(
                ComplianceIssue(
                    field="invoice.seller",
                    message="Seller name is empty",
                    severity="block",
                )
            )
        if not inv.buyer.strip():
            new_issues.append(
                ComplianceIssue(
                    field="invoice.buyer",
                    message="Buyer name is empty",
                    severity="block",
                )
            )
        if inv.gross_weight_kg <= 0:
            new_issues.append(
                ComplianceIssue(
                    field="invoice.gross_weight_kg",
                    message="Gross weight must be greater than zero",
                    severity="block",
                )
            )
        if inv.total_amount <= 0:
            new_issues.append(
                ComplianceIssue(
                    field="invoice.total_amount",
                    message="Total amount must be greater than zero",
                    severity="warn",
                )
            )

    # ── B/L checks ───────────────────────────────────────────────────────────
    if bl is None:
        new_issues.append(
            ComplianceIssue(
                field="bill_of_lading",
                message="No bill of lading was extracted",
                severity="block",
            )
        )
    else:
        if not bl.bl_number.strip():
            new_issues.append(
                ComplianceIssue(
                    field="bill_of_lading.bl_number",
                    message="B/L number is empty",
                    severity="block",
                )
            )
        if not bl.vessel.strip():
            new_issues.append(
                ComplianceIssue(
                    field="bill_of_lading.vessel",
                    message="Vessel name is empty",
                    severity="warn",
                )
            )
        if bl.gross_weight_kg <= 0:
            new_issues.append(
                ComplianceIssue(
                    field="bill_of_lading.gross_weight_kg",
                    message="B/L gross weight must be greater than zero",
                    severity="block",
                )
            )

    compliance = _merge_issues(state.compliance_result, new_issues)
    block_count = sum(1 for i in new_issues if i.severity == "block")
    warn_count = sum(1 for i in new_issues if i.severity == "warn")
    log.info(
        "deterministic_validate.done",
        status=compliance.status,
        new_blocks=block_count,
        new_warns=warn_count,
    )
    return _emit(
        state,
        "deterministic_validate",
        input_summary=f"inv={'ok' if inv else 'None'} bl={'ok' if bl else 'None'}",
        output_summary=f"status={compliance.status} blocks={block_count} warns={warn_count}",
        latency_ms=_elapsed(t0),
        updates={"compliance_result": compliance},
    )


# ---------------------------------------------------------------------------
# Node 11 — interrupt_node (HITL pause)
# ---------------------------------------------------------------------------

async def interrupt_node(state: GraphState) -> dict[str, Any]:
    """Pause execution for human-in-the-loop review of BLOCK-level findings.

    Raises NodeInterrupt; the run stays paused at its last checkpoint until
    routes/workflow.py applies corrections and resumes it.
    """
    blocks = [
        i
        for i in (state.compliance_result.issues if state.compliance_result else [])
        if i.severity == "block"
    ]
    if not blocks:
        # All block issues resolved — continue to declaration_generate.
        log.info("interrupt_node.no_blocks", run_id=str(state.run_id))
        return {}
    msg = (
        f"HITL interrupt: {len(blocks)} BLOCK-level issue(s) require human review. "
        f"run_id={state.run_id}. "
        f"Issues: {[i.field + ': ' + i.message for i in blocks]}"
    )
    log.warning("interrupt_node.raised", run_id=str(state.run_id), block_count=len(blocks))
    raise NodeInterrupt(msg)


# ---------------------------------------------------------------------------
# Node 10 — country_validate
# ---------------------------------------------------------------------------

def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list)) and not value) or (
        isinstance(value, str) and not value.strip()
    )


def _field_value(state: GraphState, dotted: str) -> Any:
    """Resolve 'invoice.currency' / 'bill_of_lading.vessel' against the state."""
    obj: Any = state
    for part in dotted.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def _hs_digits(code: str | None) -> str:
    return re.sub(r"\D", "", code or "")


async def country_validate(state: GraphState) -> dict[str, Any]:
    """Apply the customs rules in country_rules/<country>.yaml.

    Checks (all optional, driven by the YAML):
      required_fields         — missing invoice / B/L fields
      field_formats           — regex per dotted field path
      line_items              — HS code present / minimum digits
      value_thresholds        — e.g. US formal-entry threshold
      restricted_hs_prefixes  — goods needing a permit / licence
    Fields already flagged by deterministic_validate are not flagged twice.
    required_screenings are informational and go into the declaration only.
    """
    t0 = _now_ms()
    rules = _load_rules(state.country)
    new_issues: list[ComplianceIssue] = []
    inv = state.invoice
    tag = f"[{state.country.upper()}]"

    if not rules:
        return _emit(
            state,
            "country_validate",
            input_summary=f"country={state.country}",
            output_summary=f"no rules file in {_rules_dir()}, skipped",
            latency_ms=_elapsed(t0),
            updates={},
        )

    flagged = {i.field for i in (state.compliance_result.issues if state.compliance_result else [])}

    def add(field: str, message: str, severity: str) -> None:
        if field in flagged:
            return
        flagged.add(field)
        new_issues.append(
            ComplianceIssue(field=field, message=f"{tag} {message}", severity=severity)  # type: ignore[arg-type]
        )

    # ── Required fields ──────────────────────────────────────────────────────
    req = rules.get("required_fields") or {}
    req_severity = req.get("severity", "warn")
    for doc_key in ("invoice", "bill_of_lading"):
        if getattr(state, doc_key) is None:
            continue  # deterministic_validate already blocked the missing document
        for name in req.get(doc_key) or []:
            if _is_empty(_field_value(state, f"{doc_key}.{name}")):
                add(f"{doc_key}.{name}", f"{doc_key}.{name} is required", req_severity)

    # ── Field formats ────────────────────────────────────────────────────────
    for path, spec in (rules.get("field_formats") or {}).items():
        value = _field_value(state, path)
        pattern = spec.get("pattern", "")
        if pattern and not _is_empty(value) and not re.match(pattern, str(value)):
            add(
                path,
                f"'{value}' does not match {spec.get('description') or pattern}",
                spec.get("severity", "warn"),
            )

    line_items = inv.line_items if inv else []

    # ── Line-item HS code requirements ───────────────────────────────────────
    li_rules = rules.get("line_items") or {}
    min_digits = int(li_rules.get("hs_code_min_digits") or 0)
    for idx, li in enumerate(line_items):
        field = f"line_items.{idx}.hs_code"
        digits = _hs_digits(li.hs_code)
        if li_rules.get("require_hs_code") and not digits:
            add(field, f"No HS code assigned to '{li.description[:50]}'", "warn")
        elif min_digits and digits and len(digits) < min_digits:
            add(field, f"HS code {li.hs_code} has fewer than {min_digits} digits", "warn")

    # ── Value thresholds ─────────────────────────────────────────────────────
    for rule in rules.get("value_thresholds") or []:
        value = _field_value(state, rule.get("field", ""))
        currency = rule.get("currency")
        if not isinstance(value, (int, float)) or (currency and inv and inv.currency != currency):
            continue
        if value > float(rule.get("above", float("inf"))):
            add(rule["field"], rule.get("message", "value threshold exceeded"), rule.get("severity", "warn"))

    # ── Restricted goods ─────────────────────────────────────────────────────
    for idx, li in enumerate(line_items):
        digits = _hs_digits(li.hs_code)
        for rule in rules.get("restricted_hs_prefixes") or []:
            if digits and digits.startswith(str(rule.get("prefix", "_"))):
                add(
                    f"line_items.{idx}.hs_code",
                    f"{li.hs_code}: {rule.get('message', 'restricted goods')}",
                    rule.get("severity", "warn"),
                )
                break

    compliance = _merge_issues(state.compliance_result, new_issues)
    log.info(
        "country_validate.done",
        country=state.country,
        status=compliance.status,
        new_issues=len(new_issues),
    )
    return _emit(
        state,
        "country_validate",
        input_summary=f"country={state.country} inv={'ok' if inv else 'None'}",
        output_summary=f"status={compliance.status} new_issues={len(new_issues)}",
        latency_ms=_elapsed(t0),
        updates={"compliance_result": compliance},
    )


# ---------------------------------------------------------------------------
# Node 12 — declaration_generate
# ---------------------------------------------------------------------------

async def declaration_generate(state: GraphState) -> dict[str, Any]:
    """Build the final customs declaration JSON and a human-readable summary."""
    t0 = _now_ms()
    inv = state.invoice
    bl = state.bill_of_lading
    cr = state.compliance_result

    declaration: dict[str, Any] = {
        "run_id": str(state.run_id),
        "generated_at": datetime.utcnow().isoformat(),
        "jurisdiction": state.country.upper(),
        "invoice": inv.model_dump() if inv else None,
        "bill_of_lading": bl.model_dump() if bl else None,
        "compliance": cr.model_dump() if cr else None,
        "required_screenings": _load_rules(state.country).get("required_screenings") or [],
        "hs_codes": (
            [
                {"description": li.description, "hs_code": li.hs_code}
                for li in (inv.line_items if inv else [])
            ]
        ),
    }

    status = cr.status if cr else "UNKNOWN"
    issue_count = len(cr.issues) if cr else 0
    hs_assigned = sum(1 for li in (inv.line_items if inv else []) if li.hs_code)

    summary = (
        f"Declaration {state.run_id} | {state.country.upper()} | "
        f"Status: {status} | Issues: {issue_count} | "
        f"HS codes assigned: {hs_assigned}/{len(inv.line_items) if inv else 0} | "
        f"Vessel: {bl.vessel if bl else 'N/A'} | "
        f"Total: {inv.currency if inv else ''} {inv.total_amount if inv else 0:,.2f}"
    )

    log.info(
        "declaration_generate.done",
        status=status,
        hs_assigned=hs_assigned,
    )
    return _emit(
        state,
        "declaration_generate",
        input_summary=f"status={status} issues={issue_count}",
        output_summary=summary,
        latency_ms=_elapsed(t0),
        updates={"declaration": declaration, "summary": summary},
    )


# ---------------------------------------------------------------------------
# Node 13 — audit_trace
# ---------------------------------------------------------------------------

async def audit_trace(state: GraphState) -> dict[str, Any]:
    """Persist every AuditEvent collected during the run to SQLite."""
    t0 = _now_ms()
    run_id_str = str(state.run_id)

    persisted = persist_audit_trail(run_id_str, state.audit_trail)

    log.info("audit_trace.done", run_id=run_id_str, events_persisted=persisted)
    return _emit(
        state,
        "audit_trace",
        input_summary=f"events={len(state.audit_trail)}",
        output_summary=f"persisted={persisted} rows",
        latency_ms=_elapsed(t0),
        updates={},
    )


# ---------------------------------------------------------------------------
# Routing functions (conditional edges)
# ---------------------------------------------------------------------------

def _route_ocr(state: GraphState) -> str:
    """After ocr_extract: needs_vision_fallback → vision_adjudication, else → field_extract."""
    if state.needs_vision_fallback:
        log.info("routing.vision_fallback", confidence=state.ocr_confidence)
        return "vision_adjudication"
    return "field_extract"


def _route_validation(state: GraphState) -> str:
    """After country_validate: any BLOCK → interrupt_node, else → declaration_generate."""
    cr = state.compliance_result
    if cr and any(i.severity == "block" for i in cr.issues):
        log.warning("routing.block_found", issues=[i.field for i in cr.issues if i.severity == "block"])
        return "interrupt_node"
    return "declaration_generate"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def compile_graph(**compile_kwargs: Any) -> Any:
    """Build and compile the document-processing StateGraph.

    Args:
        **compile_kwargs: forwarded directly to builder.compile().
            Defaults to an in-memory checkpointer; main.py replaces it with
            AsyncSqliteSaver at startup for durable HITL resume.

    Returns:
        A compiled LangGraph runnable (supports .invoke / .ainvoke).

    Example:
        graph = compile_graph()
        try:
            result = await graph.ainvoke(
                GraphState(document_id="abc", invoice_pdf_path="/tmp/inv.pdf", bl_pdf_path="/tmp/bl.pdf", country="us")
            )
        except NodeInterrupt as e:
            # present to operator, then re-invoke with corrected state
    """
    builder: StateGraph = StateGraph(GraphState)

    # ── Register nodes ───────────────────────────────────────────────────────
    builder.add_node("ingest",                  ingest)
    builder.add_node("preprocess",              preprocess)
    builder.add_node("ocr_extract",             ocr_extract)
    builder.add_node("vision_adjudication",     vision_adjudication)
    builder.add_node("field_extract",           field_extract)
    builder.add_node("reconcile",               reconcile)
    builder.add_node("hs_retrieve",             hs_retrieve)
    builder.add_node("compliance_reason",       compliance_reason)
    builder.add_node("deterministic_validate",  deterministic_validate)
    builder.add_node("interrupt_node",          interrupt_node)
    builder.add_node("country_validate",        country_validate)
    builder.add_node("declaration_generate",    declaration_generate)
    builder.add_node("audit_trace",             audit_trace)

    # ── Entry point ──────────────────────────────────────────────────────────
    builder.set_entry_point("ingest")

    # ── Linear backbone ──────────────────────────────────────────────────────
    builder.add_edge("ingest",            "preprocess")
    builder.add_edge("preprocess",        "ocr_extract")
    # ocr_extract → conditional (see below)
    builder.add_edge("vision_adjudication", "field_extract")
    builder.add_edge("field_extract",     "reconcile")
    builder.add_edge("reconcile",         "hs_retrieve")
    builder.add_edge("hs_retrieve",       "compliance_reason")
    builder.add_edge("compliance_reason", "deterministic_validate")
    builder.add_edge("deterministic_validate", "country_validate")
    # country_validate → conditional (see below)
    builder.add_edge("interrupt_node",    "declaration_generate")
    builder.add_edge("declaration_generate", "audit_trace")
    builder.add_edge("audit_trace",       END)

    # ── Conditional edges ─────────────────────────────────────────────────────
    builder.add_conditional_edges(
        "ocr_extract",
        _route_ocr,
        {"vision_adjudication": "vision_adjudication", "field_extract": "field_extract"},
    )
    builder.add_conditional_edges(
        "country_validate",
        _route_validation,
        {"interrupt_node": "interrupt_node", "declaration_generate": "declaration_generate"},
    )

    if "checkpointer" not in compile_kwargs:
        compile_kwargs["checkpointer"] = MemorySaver()
    return builder.compile(**compile_kwargs)


# ---------------------------------------------------------------------------
# Module-level instance — imported by routes/workflow.py
# ---------------------------------------------------------------------------
document_graph = compile_graph()
