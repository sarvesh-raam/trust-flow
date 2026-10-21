"""Unit tests for node helpers: OCR confidence, bbox orientation, error text."""
from __future__ import annotations

import math
from types import SimpleNamespace

from nodes.ocr_extract import _doc_confidence, _extract_bboxes
from routes.workflow import _readable_error


# ── OCR confidence ───────────────────────────────────────────────────────────

def test_confidence_uses_docling_mean_score():
    result = SimpleNamespace(confidence=SimpleNamespace(mean_score=0.83, parse_score=1.0))
    assert _doc_confidence(result, "some text") == 0.83


def test_confidence_skips_nan_scores():
    result = SimpleNamespace(confidence=SimpleNamespace(mean_score=math.nan, parse_score=0.9))
    assert _doc_confidence(result, "some text") == 0.9


def test_confidence_without_report_depends_on_text():
    assert _doc_confidence(SimpleNamespace(), "text") == 1.0
    assert _doc_confidence(SimpleNamespace(), "   ") == 0.0


# ── Bounding boxes ───────────────────────────────────────────────────────────

class _BottomLeftBox:
    """Minimal stand-in for docling's BoundingBox with a bottom-left origin."""

    def __init__(self, l, t, r, b):
        self.l, self.t, self.r, self.b = l, t, r, b

    def to_top_left_origin(self, page_height):
        return _BottomLeftBox(self.l, page_height - self.t, self.r, page_height - self.b)


def test_bboxes_are_converted_to_top_left_origin():
    element = SimpleNamespace(
        text="COMMERCIAL INVOICE",
        prov=[SimpleNamespace(bbox=_BottomLeftBox(40, 766, 200, 757), page_no=1)],
    )
    document = SimpleNamespace(
        iterate_items=lambda: [(element, 0)],
        pages={1: SimpleNamespace(size=SimpleNamespace(height=842.0))},
    )
    [entry] = _extract_bboxes(document, source="invoice")
    x1, y1, x2, y2 = entry["bbox"]
    assert (y1, y2) == (76.0, 85.0)   # near the top of the page, positive height
    assert y2 > y1


# ── Error text ───────────────────────────────────────────────────────────────

def test_readable_error_extracts_instructor_last_exception():
    raw = (
        "<failed_attempts>\n<generation number=\"1\">...</generation>\n</failed_attempts>\n\n"
        "<last_exception>\n    Error code: 404 - model_not_found\n</last_exception>"
    )
    assert _readable_error(RuntimeError(raw)) == "Error code: 404 - model_not_found"


def test_readable_error_passes_plain_messages_through():
    assert _readable_error(ValueError("OCR could not read the invoice")) == "OCR could not read the invoice"
