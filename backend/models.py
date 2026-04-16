from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStatus(str, Enum):
    QUEUED = "queued"
    UPLOADED = "uploaded" # Native state for newly uploaded docs
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class CountryCode(str, Enum):
    US = "us"
    UAE = "uae"


# ---------------------------------------------------------------------------
# Document schemas
# ---------------------------------------------------------------------------

class DocumentUploadRequest(BaseModel):
    filename: str
    country: CountryCode = CountryCode.US
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    filename: str
    country: CountryCode
    status: DocumentStatus = DocumentStatus.PENDING
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DocumentResponse(DocumentRecord):
    # PII-free response model: DocumentRecord audited for raw email leaks.
    workflow_id: UUID | None = None


# ---------------------------------------------------------------------------
# Extraction — field-level building block
# ---------------------------------------------------------------------------

class ExtractedField(BaseModel):
    """A single field extracted from a document by the LLM, with provenance."""

    value: Any = Field(
        description="The extracted value; type depends on the field (str, float, date, etc.)"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model confidence in this extraction, in the range [0, 1]",
    )
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="Bounding box (x0, y0, x1, y1) in normalised page coordinates, if available",
    )
    source_doc: str = Field(
        default="",
        description="Filename or document identifier from which this field was extracted",
    )


# ---------------------------------------------------------------------------
# HS-code classification
# ---------------------------------------------------------------------------

class HSCandidate(BaseModel):
    """One candidate HS tariff code produced by the classifier."""

    code: str = Field(
        description="6- or 8-digit Harmonised System code, e.g. '8471.30'"
    )
    description: str = Field(
        description="Official WCO description for this HS heading"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Classifier confidence score for this candidate",
    )
    rationale: str | None = Field(
        default=None,
        description="Optional free-text explanation of why this code was chosen",
    )


# ---------------------------------------------------------------------------
# Line items (shared by invoice and bill of lading)
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    """A single line on a commercial invoice or bill of lading."""

    description: str = Field(
        description="Goods description as it appears on the document"
    )
    quantity: float = Field(
        default=1.0,
        ge=0.0,
        description="Quantity of this line item",
    )
    unit_price: float = Field(
        default=0.0,
        ge=0.0,
        description="Unit price in the document currency",
    )
    hs_code: str | None = Field(
        default=None,
        description="Best-match HS code, populated after classification",
    )
    hs_candidates: list[HSCandidate] = Field(
        default_factory=list,
        description="Ranked list of HS code candidates from the classifier",
    )


# ---------------------------------------------------------------------------
# Trade documents
# ---------------------------------------------------------------------------

class InvoiceDocument(BaseModel):
    """Structured representation of a commercial invoice."""

    invoice_number: str = Field(
        description="Unique invoice reference number as printed on the document"
    )
    date: str = Field(
        description="Invoice date in ISO-8601 format (YYYY-MM-DD)"
    )
    seller: str = Field(
        description="Legal name of the selling entity"
    )
    buyer: str = Field(
        description="Legal name of the buying / consignee entity"
    )
    line_items: list[LineItem] = Field(
        default_factory=list,
        description="Ordered list of goods line items on the invoice",
    )
    total_amount: float = Field(
        default=0.0,
        ge=0.0,
        description="Total invoice amount (sum of line items, before tax / freight)",
    )
    currency: str = Field(
        default="USD",
        description="ISO-4217 currency code, e.g. 'USD', 'AED', 'EUR'",
    )
    gross_weight_kg: float = Field(
        default=0.0,
        ge=0.0,
        description="Total gross weight of all goods in kilograms",
    )


class BillOfLading(BaseModel):
    """Structured representation of an ocean or air bill of lading."""

    bl_number: str = Field(
        description="Unique bill of lading reference number"
    )
    vessel: str = Field(
        description="Name of the carrying vessel or flight number"
    )
    port_of_loading: str = Field(
        description="UN/LOCODE or plain-text name of the port of loading"
    )
    port_of_discharge: str = Field(
        description="UN/LOCODE or plain-text name of the port of discharge"
    )
    gross_weight_kg: float = Field(
        default=0.0,
        ge=0.0,
        description="Total gross weight declared on the B/L in kilograms",
    )
    consignee: str = Field(
        description="Legal name of the consignee (receiving party)"
    )
    shipper: str = Field(
        description="Legal name of the shipper (sending party)"
    )
    line_items: list[LineItem] = Field(
        default_factory=list,
        description="Cargo description lines from the B/L",
    )


# ---------------------------------------------------------------------------
# Legacy extraction result (kept for upload route compatibility)
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel):
    document_id: UUID
    fields: list[ExtractedField] = Field(default_factory=list)
    raw_text: str = ""
    page_count: int = 0
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

class ComplianceIssue(BaseModel):
    """A single compliance finding raised during the validate node."""

    field: str = Field(
        description="Dot-path to the document field that triggered this issue, e.g. 'line_items.0.hs_code'"
    )
    message: str = Field(
        description="Human-readable description of the compliance finding"
    )
    severity: Literal["warn", "block"] = Field(
        description="'warn' flags for review; 'block' halts clearance"
    )


class ComplianceResult(BaseModel):
    """Aggregated compliance verdict for a document pair."""

    status: Literal["PASS", "WARN", "BLOCK"] = Field(
        description=(
            "PASS — no issues found; "
            "WARN — advisory issues present, processing can continue; "
            "BLOCK — critical issues that must be resolved before clearance"
        )
    )
    issues: list[ComplianceIssue] = Field(
        default_factory=list,
        description="All compliance issues found, ordered by severity (block first)",
    )


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class AuditEvent(BaseModel):
    """Immutable record of a single LangGraph node execution."""

    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC time at which the node completed",
    )
    node_name: str = Field(
        description="Name of the LangGraph node that produced this event"
    )
    input_summary: str = Field(
        default="",
        description="Short human-readable summary of the node's input state",
    )
    output_summary: str = Field(
        default="",
        description="Short human-readable summary of the node's output state",
    )
    latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock execution time of the node in milliseconds",
    )
    reasoning_note: str | None = Field(
        default=None,
        description="One-sentence human-readable description of the node's decision",
    )


# ---------------------------------------------------------------------------
# LangGraph workflow state  (replaces the former WorkflowState used by routes)
# ---------------------------------------------------------------------------

class WorkflowRecord(BaseModel):
    """Persisted pipeline run record — used by the /workflow API route."""

    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    country: CountryCode
    status: WorkflowStatus = WorkflowStatus.QUEUED
    steps: list[WorkflowStep] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowStep(BaseModel):
    name: str
    status: WorkflowStatus = WorkflowStatus.QUEUED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)


class WorkflowCreateRequest(BaseModel):
    document_id: UUID
    country: CountryCode = CountryCode.US


class ResumeRequest(BaseModel):
    gross_weight_kg: float | None = None


class WorkflowResponse(WorkflowRecord):
    # PII-free response model: WorkflowRecord audited for raw email leaks.
    pass


class WorkflowState(BaseModel):
    """Pydantic model that travels through the LangGraph nodes as mutable state."""

    run_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for this pipeline run",
    )

    # ── Input paths ───────────────────────────────────────────────────────────
    invoice_pdf_path: str | None = Field(
        default=None,
        description="Absolute path to the uploaded commercial invoice PDF/image",
    )
    bl_pdf_path: str | None = Field(
        default=None,
        description="Absolute path to the uploaded bill-of-lading PDF/image",
    )

    # ── OCR outputs (set by ocr_extract / vision_adjudication) ───────────────
    invoice_ocr_text: str | None = Field(
        default=None,
        description="Markdown-formatted text extracted from the invoice document",
    )
    bl_ocr_text: str | None = Field(
        default=None,
        description="Markdown-formatted text extracted from the bill-of-lading document",
    )
    invoice_tables: list[Any] | None = Field(
        default=None,
        description="Tables extracted from the invoice as list[list[dict]] (one entry per table)",
    )
    bl_tables: list[Any] | None = Field(
        default=None,
        description="Tables extracted from the B/L as list[list[dict]]",
    )
    invoice_bboxes: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Bounding boxes for each text element in the invoice. "
            "Each dict: {text, bbox: [l,t,r,b], page, source}. "
            "Fed directly to the frontend bbox overlay."
        ),
    )
    bl_bboxes: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Bounding boxes for each text element in the B/L. "
            "Each dict: {text, bbox: [l,t,r,b], page, source}."
        ),
    )
    invoice_page_image: str | None = Field(
        default=None,
        description="Base64-encoded JPEG of the first page of the invoice for Vision tasks.",
    )
    bl_page_image: str | None = Field(
        default=None,
        description="Base64-encoded JPEG of the first page of the B/L for Vision tasks.",
    )

    # ── OCR quality ───────────────────────────────────────────────────────────
    ocr_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Mean confidence across both documents: "
            "ratio of bbox elements to word count, clipped to [0, 1]"
        ),
    )
    needs_vision_fallback: bool = Field(
        default=False,
        description="True when ocr_confidence < 0.7; routes to vision_adjudication node",
    )

    # ── Extracted trade documents ─────────────────────────────────────────────
    invoice: InvoiceDocument | None = Field(
        default=None,
        description="Structured invoice extracted by the ingest/extract nodes",
    )
    bill_of_lading: BillOfLading | None = Field(
        default=None,
        description="Structured bill of lading extracted by the ingest/extract nodes",
    )
    compliance_result: ComplianceResult | None = Field(
        default=None,
        description="Compliance verdict produced by the validate node",
    )
    audit_trail: list[AuditEvent] = Field(
        default_factory=list,
        description="Ordered log of every node execution in this run",
    )


# ---------------------------------------------------------------------------
# Validation schemas (country-rule enforcement)
# ---------------------------------------------------------------------------

class ValidationRule(BaseModel):
    field: str
    rule_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    error_message: str = ""


class ValidationResult(BaseModel):
    document_id: UUID
    country: CountryCode
    passed: bool
    violations: list[ValidationRule] = Field(default_factory=list)
    validated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# API response wrappers
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
