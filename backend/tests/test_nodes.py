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


# ── HS code ranking (USITC search rows) ──────────────────────────────────────

from nodes.hs_retrieve import _rank_usitc_rows  # noqa: E402

_USITC_ROWS = [
    {"htsno": "8413", "indent": "0", "description": "Pumps for liquids; liquid elevators:"},
    {"htsno": "8413.70", "indent": "1", "description": "Other centrifugal pumps:"},
    {"htsno": "8413.70.20.04", "indent": "2", "description": "Submersible pumps"},
    {"htsno": "9303", "indent": "0", "description": "Other firearms (sporting shotguns)"},
    {"htsno": "9303.20.00.30", "indent": "1", "description": "Exceeding <il>500 cc</il> > Pump action"},
    {"htsno": "4202", "indent": "0", "description": "Trunks, suitcases"},
    {"htsno": "4202.21.60.00", "indent": "1", "description": "Handbags with outer surface of leather"},
]


def test_hs_ranking_uses_heading_path_and_ignores_model_numbers():
    codes = [c.code for c in _rank_usitc_rows(_USITC_ROWS, "Industrial Centrifugal Pump CP-500", 5)]
    assert codes[0] == "8413702004"              # matched "centrifugal" + "pump" via parents
    assert "9303200030" not in codes             # "pump action" alone is below the threshold


def test_hs_ranking_resets_context_at_each_heading():
    [only] = _rank_usitc_rows(_USITC_ROWS, "leather handbags", 5)
    assert only.code == "4202216000"
    assert "firearm" not in only.description.lower()


# ── JWT secret loading ───────────────────────────────────────────────────────

import pytest  # noqa: E402

import auth  # noqa: E402


@pytest.mark.parametrize("value", ["change-me", "hackstrom-secret-key-2026", "short-but-real"])
def test_placeholder_or_short_jwt_secret_is_refused(monkeypatch, value):
    monkeypatch.setenv("JWT_SECRET_KEY", value)
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        auth._load_secret()


def test_missing_jwt_secret_gets_a_random_key(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    first, second = auth._load_secret(), auth._load_secret()
    assert len(first) >= 32 and first != second


def test_strong_jwt_secret_is_used_as_is(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "k" * 40)
    assert auth._load_secret() == "k" * 40


# ── LLM rate-limit fallback ──────────────────────────────────────────────────

from nodes import field_extract  # noqa: E402
from routes.workflow import rate_limit_message  # noqa: E402

_RATE_LIMIT_ERROR = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` "
    "... Please try again in 4m18.336s.', 'code': 'rate_limit_exceeded'}}"
)


class _FakeClient:
    """Records the model of each call; fails for models in `limited`."""

    def __init__(self, limited):
        self.limited, self.models = limited, []
        self.chat = self.completions = self

    def create(self, model, **kwargs):
        self.models.append(model)
        if model in self.limited:
            raise RuntimeError(_RATE_LIMIT_ERROR)
        return f"answer from {model}"


def test_rate_limited_model_falls_back_to_smaller_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.delenv("GROQ_FALLBACK_MODEL", raising=False)
    client = _FakeClient(limited={"openai/gpt-oss-120b"})
    assert field_extract.structured_completion(client, "openai/gpt-oss-120b") == "answer from openai/gpt-oss-20b"
    assert client.models == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_other_errors_and_disabled_fallback_are_not_retried(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")

    class Broken(_FakeClient):
        def create(self, model, **kwargs):
            self.models.append(model)
            raise ValueError("schema validation failed")

    client = Broken(limited=set())
    with pytest.raises(ValueError):
        field_extract.structured_completion(client, "openai/gpt-oss-120b")
    assert client.models == ["openai/gpt-oss-120b"]

    monkeypatch.setenv("GROQ_FALLBACK_MODEL", "")
    client = _FakeClient(limited={"openai/gpt-oss-120b"})
    with pytest.raises(RuntimeError):
        field_extract.structured_completion(client, "openai/gpt-oss-120b")


def test_rate_limit_error_is_explained_in_plain_language():
    msg = _readable_error(RuntimeError(_RATE_LIMIT_ERROR))
    assert "usage limit" in msg and "4m18s" in msg and "GROQ_MODEL" in msg
    assert rate_limit_message("no hint") .endswith("in backend/.env.")
