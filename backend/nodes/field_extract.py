"""nodes/field_extract.py — LLM-based structured extraction via Instructor.

Client selection (organizer key → Groq → vLLM) is delegated to llm_client.py.
This module owns only the prompt templates and the node function itself.
"""
from __future__ import annotations

import json
import re
from typing import Any

import structlog

from llm_client import get_active_chat_model as _active_model
from llm_client import get_instructor_client as get_client
from llm_instrumented import tracked_instructor_create
from models import BillOfLading, InvoiceDocument, LineItem, WorkflowState

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

INVOICE_PROMPT = """\
You are processing a customs document. Extract ALL fields with precision.
If a field is ambiguous, prefer the value that matches standard trade document formats.

Extract all fields from the commercial invoice below into the exact JSON \
schema provided. Rules:
- Dates must be in ISO-8601 format (YYYY-MM-DD). If the day is missing, use 01.
- Monetary amounts are plain floats (no currency symbols).
- If a field is absent from the document, use the schema default.
- For line_items, extract every row; do not skip or merge rows.
- hs_code and hs_candidates may be left empty — they are filled by a later node.

Invoice text (OCR):
{text}

Tables detected (list-of-dicts):
{tables}
"""

BL_PROMPT = """\
You are processing a customs document. Extract ALL fields with precision.
If a field is ambiguous, prefer the value that matches standard trade document formats.

Extract all fields from the Bill of Lading below into the exact JSON \
schema provided. Rules:
- Gross weight must be in kilograms. Convert if the document uses lbs (÷ 2.205).
- If a field is absent from the document, use the schema default.
- For line_items, extract every cargo description row.
- hs_code and hs_candidates may be left empty.

Bill of Lading text (OCR):
{text}

Tables detected:
{tables}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_tables(tables: list[Any] | None) -> str:
    """Render extracted tables as a compact JSON string for the prompt."""
    if not tables:
        return "[]"
    try:
        return json.dumps(tables, ensure_ascii=False, indent=None)
    except (TypeError, ValueError):
        return str(tables)


def _search(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""


def _search_float(patterns: list[str], text: str) -> float:
    raw = _search(patterns, text)
    if not raw:
        return 0.0
    try:
        return float(raw.replace(",", "").strip())
    except ValueError:
        return 0.0


def _extract_invoice_line_items(text: str) -> list[LineItem]:
    items: list[LineItem] = []
    pattern = re.compile(
        r"^\s*\d+\s+(.+?)\s{2,}(\d+(?:\.\d+)?)\s+(?:[A-Z]{3}\s*)?([\d,]+(?:\.\d+)?)\s+(?:[A-Z]{3}\s*)?([\d,]+(?:\.\d+)?)\s*$",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        description, quantity, unit_price, total = match.groups()
        try:
            items.append(
                LineItem(
                    description=description.strip(),
                    quantity=float(quantity.replace(",", "")),
                    unit_price=float(unit_price.replace(",", "")),
                )
            )
        except ValueError:
            continue

    return items


def _extract_bl_line_items(text: str) -> list[LineItem]:
    description = _search(
        [
            r"Description\s*:\s*([^\n]+)",
            r"Cargo Description\s*:\s*([^\n]+)",
        ],
        text,
    )
    if not description:
        return []
    return [LineItem(description=description)]


def _fallback_invoice(state: WorkflowState) -> InvoiceDocument:
    text = state.invoice_ocr_text or ""
    currency = _search([r"Currency\s*:\s*([A-Z]{3})"], text) or "USD"
    return InvoiceDocument(
        invoice_number=_search(
            [r"Invoice Number\s*:\s*([^\n]+)", r"Invoice No\.?\s*:\s*([^\n]+)"],
            text,
        ),
        date=_search([r"Date\s*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})"], text),
        seller=_search([r"Seller\s*:\s*([^\n]+)"], text),
        buyer=_search([r"Buyer\s*:\s*([^\n]+)", r"Consignee\s*:\s*([^\n]+)"], text),
        line_items=_extract_invoice_line_items(text),
        total_amount=_search_float(
            [
                r"Total Amount\s*:\s*(?:[A-Z]{3}\s*)?([\d,]+(?:\.\d+)?)",
                r"Total\s*:\s*(?:[A-Z]{3}\s*)?([\d,]+(?:\.\d+)?)",
            ],
            text,
        ),
        currency=currency,
        gross_weight_kg=_search_float(
            [r"Gross Weight\s*:\s*([\d,]+(?:\.\d+)?)\s*(?:kg|kgs|kilograms)?"],
            text,
        ),
    )


def _fallback_bill_of_lading(state: WorkflowState) -> BillOfLading:
    text = state.bl_ocr_text or ""
    return BillOfLading(
        bl_number=_search(
            [r"B/L Number\s*:\s*([^\n]+)", r"BL Number\s*:\s*([^\n]+)"],
            text,
        ),
        vessel=_search(
            [r"Vessel(?:\s*/\s*Voyage)?\s*:\s*([^\n]+)", r"Vessel\s*:\s*([^\n]+)"],
            text,
        ),
        port_of_loading=_search([r"Port of Loading\s*:\s*([^\n]+)"], text),
        port_of_discharge=_search([r"Port of Discharge\s*:\s*([^\n]+)"], text),
        gross_weight_kg=_search_float(
            [r"Gross Weight\s*:\s*([\d,]+(?:\.\d+)?)\s*(?:kg|kgs|kilograms)?"],
            text,
        ),
        consignee=_search([r"Consignee\s*:\s*([^\n]+)"], text),
        shipper=_search([r"Shipper\s*:\s*([^\n]+)"], text),
        line_items=_extract_bl_line_items(text),
    )


# ---------------------------------------------------------------------------
# Public node function  (synchronous — called via run_in_executor in graph.py)
# ---------------------------------------------------------------------------


def field_extract_node(state: WorkflowState) -> WorkflowState:
    """Call the LLM twice (invoice + B/L) and populate state.invoice / state.bill_of_lading.

    Uses Vision (multimodal) if images are available in the state, otherwise 
    falls back to text-only OCR extraction.
    """
    try:
        client = get_client()
        model = _active_model()
    except Exception as exc:
        log.warning("field_extract.llm_unavailable_fallback", error=str(exc))
        state.invoice = _fallback_invoice(state)
        state.bill_of_lading = _fallback_bill_of_lading(state)
        return state

    max_retries = 3

    # ── 1. Invoice ───────────────────────────────────────────────────────────
    inv_text   = state.invoice_ocr_text or ""
    inv_tables = _format_tables(state.invoice_tables)
    inv_prompt = INVOICE_PROMPT.format(text=inv_text, tables=inv_tables)

    inv_messages = [{"role": "user", "content": inv_prompt}]

    # Ingest Vision context if available
    if state.invoice_page_image:
        log.info("field_extract.vision_enabled", doc="invoice", bytes=len(state.invoice_page_image))
        inv_messages[0]["content"] = [
            {"type": "text", "text": inv_prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{state.invoice_page_image}",
                    "detail": "high"
                }
            }
        ]

    log.info("field_extract.invoice_start", model=model, vision=bool(state.invoice_page_image))

    try:
        invoice: InvoiceDocument = tracked_instructor_create(
            client,
            model=model,
            call_type="extraction",
            response_model=InvoiceDocument,
            messages=inv_messages,
            max_retries=max_retries,
        )
    except Exception as exc:
        log.warning("field_extract.invoice_fallback", error=str(exc))
        invoice = _fallback_invoice(state)

    log.info(
        "field_extract.invoice_done",
        invoice_number=invoice.invoice_number,
        line_items=len(invoice.line_items),
        total_amount=invoice.total_amount,
    )

    # ── 2. Bill of Lading ────────────────────────────────────────────────────
    bl_text   = state.bl_ocr_text or ""
    bl_tables = _format_tables(state.bl_tables)
    bl_prompt = BL_PROMPT.format(text=bl_text, tables=bl_tables)

    bl_messages = [{"role": "user", "content": bl_prompt}]

    if state.bl_page_image:
        log.info("field_extract.vision_enabled", doc="bl", bytes=len(state.bl_page_image))
        bl_messages[0]["content"] = [
            {"type": "text", "text": bl_prompt},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{state.bl_page_image}",
                    "detail": "high"
                }
            }
        ]

    log.info("field_extract.bl_start", model=model, vision=bool(state.bl_page_image))

    try:
        bill_of_lading: BillOfLading = tracked_instructor_create(
            client,
            model=model,
            call_type="extraction",
            response_model=BillOfLading,
            messages=bl_messages,
            max_retries=max_retries,
        )
    except Exception as exc:
        log.warning("field_extract.bl_fallback", error=str(exc))
        bill_of_lading = _fallback_bill_of_lading(state)

    log.info(
        "field_extract.bl_done",
        bl_number=bill_of_lading.bl_number,
        line_items=len(bill_of_lading.line_items),
    )

    state.invoice        = invoice
    state.bill_of_lading = bill_of_lading
    return state
