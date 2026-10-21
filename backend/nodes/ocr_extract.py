"""nodes/ocr_extract.py — Real Docling OCR extraction node.

Converts each uploaded PDF/image through Docling's DocumentConverter and
populates WorkflowState with:
  - Markdown text per document
  - Tables (as list-of-dicts via DataFrame)
  - Bounding boxes per text element (fed to the frontend bbox overlay)
  - A mean OCR confidence score across both documents
  - A needs_vision_fallback flag when confidence < 0.7
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import structlog

from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

from models import WorkflowState

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level converter — initialises ML models once per process lifetime.
# Re-creating DocumentConverter on every call would reload EasyOCR/TableFormer
# weights each time, adding ~10–30 s of startup latency.
# ---------------------------------------------------------------------------

def _build_converter() -> DocumentConverter:
    """Create a DocumentConverter with OCR and table-structure extraction enabled."""
    try:
        # Docling ≥ 2.x API: pass PdfPipelineOptions through format_options.
        from docling.document_converter import PdfFormatOption  # noqa: PLC0415

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True

        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
    except (ImportError, TypeError):
        # Docling 1.x fallback: PdfFormatOption not yet available.
        log.warning("docling.format_options_unavailable", fallback="default_converter")
        return DocumentConverter()


_CONVERTER: DocumentConverter = _build_converter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tables(document: Any) -> list[list[dict[str, Any]]]:
    """Return all tables from a Docling document as list[list[dict]]."""
    tables: list[list[dict[str, Any]]] = []
    for table in document.tables:
        try:
            df = table.export_to_dataframe()
            tables.append(df.to_dict(orient="records"))
        except Exception as exc:
            log.warning("ocr_extract.table_export_failed", error=str(exc))
    return tables


def _extract_bboxes(document: Any, source: str) -> list[dict[str, Any]]:
    """Return one bbox dict per text element that has provenance information.

    Docling reports PDF boxes with a bottom-left origin; the frontend overlay
    expects top-left, so each box is converted using its page height.
    """
    bboxes: list[dict[str, Any]] = []
    for element, _level in document.iterate_items():
        if not (hasattr(element, "prov") and element.prov):
            continue
        prov = element.prov[0]
        try:
            box = prov.bbox.to_top_left_origin(page_height=document.pages[prov.page_no].size.height)
            bboxes.append(
                {
                    "text": element.text if hasattr(element, "text") else "",
                    "bbox": [box.l, box.t, box.r, box.b],
                    "page": prov.page_no,
                    "source": source,
                }
            )
        except (AttributeError, KeyError) as exc:
            # Provenance shape varies by Docling version; skip malformed entries.
            log.debug("ocr_extract.bbox_skip", error=str(exc))
    return bboxes


def _doc_confidence(result: Any, text: str) -> float:
    """Docling's own conversion quality score, in [0.0, 1.0].

    Uses ConversionResult.confidence.mean_score (layout + parse + OCR quality);
    falls back to parse_score, then to "has text or not" on Docling builds
    without a confidence report.  Sub-scores are NaN when they do not apply
    (e.g. ocr_score for a native text-layer PDF).
    """
    report = getattr(result, "confidence", None)
    for name in ("mean_score", "parse_score"):
        score = getattr(report, name, None)
        if isinstance(score, (int, float)) and math.isfinite(score):
            return min(1.0, max(0.0, float(score)))
    return 1.0 if text.strip() else 0.0


# ---------------------------------------------------------------------------
# Public node function
# ---------------------------------------------------------------------------

def ocr_extract_node(state: WorkflowState) -> WorkflowState:
    """Extract text, tables, and bounding boxes from both uploaded documents.

    This is a *synchronous* function intentionally — Docling's converter is
    CPU-bound and uses its own internal thread management.  The LangGraph async
    wrapper in graph.py calls it via asyncio.get_event_loop().run_in_executor()
    so it does not block the event loop.

    Side-effects on state (all new fields; nothing existing is cleared):
        invoice_ocr_text, invoice_tables, invoice_bboxes,
        bl_ocr_text,      bl_tables,      bl_bboxes,
        ocr_confidence, needs_vision_fallback
    """
    confidences: list[float] = []

    for doc_type, pdf_path in [
        ("invoice", state.invoice_pdf_path),
        ("bl",      state.bl_pdf_path),
    ]:
        if not pdf_path:
            log.warning("ocr_extract.path_missing", doc_type=doc_type)
            confidences.append(0.0)
            continue

        resolved = Path(pdf_path)
        if not resolved.exists():
            log.error(
                "ocr_extract.file_not_found",
                doc_type=doc_type,
                path=str(resolved),
            )
            confidences.append(0.0)
            continue

        log.info(
            "ocr_extract.converting",
            doc_type=doc_type,
            path=str(resolved),
            size_bytes=resolved.stat().st_size,
        )

        try:
            result = _CONVERTER.convert(str(resolved))
        except Exception as exc:
            log.error(
                "ocr_extract.convert_failed",
                doc_type=doc_type,
                path=str(resolved),
                error=str(exc),
            )
            # Fail the run with the real cause rather than treating the
            # document as blank (which would surface as a misleading
            # "No invoice document was extracted" BLOCK).
            label = "invoice" if doc_type == "invoice" else "bill of lading"
            raise RuntimeError(f"OCR could not read the {label}: {exc}") from exc

        document = result.document

        # ── Markdown text ──────────────────────────────────────────────────
        try:
            text: str = document.export_to_markdown()
        except Exception as exc:
            log.warning("ocr_extract.markdown_failed", doc_type=doc_type, error=str(exc))
            text = ""

        # ── Tables ────────────────────────────────────────────────────────
        tables = _extract_tables(document)

        # ── Bounding boxes ────────────────────────────────────────────────
        bboxes = _extract_bboxes(document, source=doc_type)

        # ── Per-document confidence ───────────────────────────────────────
        doc_conf = _doc_confidence(result, text)
        confidences.append(doc_conf)

        log.info(
            "ocr_extract.doc_done",
            doc_type=doc_type,
            chars=len(text),
            tables=len(tables),
            bboxes=len(bboxes),
            confidence=round(doc_conf, 3),
        )

        # ── Write into state ──────────────────────────────────────────────
        if doc_type == "invoice":
            state.invoice_ocr_text = text
            state.invoice_tables   = tables
            state.invoice_bboxes   = bboxes
        else:
            state.bl_ocr_text  = text
            state.bl_tables    = tables
            state.bl_bboxes    = bboxes

    # ── Aggregate confidence ──────────────────────────────────────────────────
    state.ocr_confidence = (
        sum(confidences) / len(confidences) if confidences else 0.0
    )
    state.needs_vision_fallback = state.ocr_confidence < 0.7

    log.info(
        "ocr_extract.done",
        mean_confidence=round(state.ocr_confidence, 3),
        needs_vision_fallback=state.needs_vision_fallback,
    )
    return state
