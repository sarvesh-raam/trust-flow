"""Test setup: isolated DB / uploads / checkpoints and mocked OCR + LLM nodes."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Must happen before any app module is imported (db.py reads these at import).
_TMP = Path(tempfile.mkdtemp(prefix="trustflow-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["CHECKPOINT_DB"] = str(_TMP / "checkpoints.db")
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ.pop("GROQ_API_KEY", None)

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402

import graph  # noqa: E402
from auth import create_access_token  # noqa: E402
from models import BillOfLading, InvoiceDocument, LineItem  # noqa: E402

PDF_BYTES = b"%PDF-1.4\n%fake test document\n"


def auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id, f'{user_id}@example.com')}"}


class FakeDocs:
    """Controls what the mocked extraction returns for each test."""

    invoice_weight = 820.0
    bl_weight = 820.0
    total_amount = 1200.0
    hs_code: str | None = "8471300000"


@pytest.fixture
def fake_docs(monkeypatch):
    docs = FakeDocs()

    def fake_ocr(state):
        state.invoice_ocr_text = "INVOICE INV-001 Acme Corp"
        state.bl_ocr_text = "BILL OF LADING BL-77"
        state.invoice_bboxes = [{"text": "INV-001", "bbox": [10, 10, 60, 20], "page": 1, "source": "invoice"}]
        state.bl_bboxes = []
        state.ocr_confidence = 0.95
        state.needs_vision_fallback = False
        return state

    def fake_extract(state):
        state.invoice = InvoiceDocument(
            invoice_number="INV-001", date="2026-01-05", seller="Acme Corp", buyer="Globex LLC",
            currency="USD", total_amount=docs.total_amount, gross_weight_kg=docs.invoice_weight,
            line_items=[LineItem(description="laptop computers", quantity=10, unit_price=120)],
        )
        state.bill_of_lading = BillOfLading(
            bl_number="BL-77", vessel="MV Test", port_of_loading="Shanghai", port_of_discharge="Los Angeles",
            gross_weight_kg=docs.bl_weight, consignee="Globex LLC", shipper="Acme Corp",
        )
        return state

    async def fake_hs(state):
        return state

    def fake_reason(state):
        for li in state.invoice.line_items:
            li.hs_code = docs.hs_code
        return state

    monkeypatch.setattr(graph, "_ocr_extract_impl", fake_ocr)
    monkeypatch.setattr(graph, "_field_extract_impl", fake_extract)
    monkeypatch.setattr(graph, "_hs_retrieve_impl", fake_hs)
    monkeypatch.setattr(graph, "_compliance_reason_impl", fake_reason)
    return docs


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:  # runs lifespan → SQLite checkpointer
        yield c
