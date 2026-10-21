"""API-level tests: auth, ownership, uploads and the HITL block → resume flow."""
from __future__ import annotations

import jwt
from sqlmodel import Session, select

from conftest import PDF_BYTES, auth_headers


def _upload(client, user="alice", invoice=PDF_BYTES, bl=PDF_BYTES):
    return client.post(
        "/api/v1/upload/",
        files={
            "invoice_pdf": ("invoice.pdf", invoice, "application/pdf"),
            "bl_pdf": ("bl.pdf", bl, "application/pdf"),
        },
        data={"country": "us"},
        headers=auth_headers(user),
    )


def _start_run(client, user="alice"):
    doc = _upload(client, user)
    assert doc.status_code == 202, doc.text
    wf = client.post(
        "/api/v1/workflow/",
        json={"document_id": doc.json()["id"], "country": "us"},
        headers=auth_headers(user),
    )
    assert wf.status_code == 202, wf.text
    # TestClient runs background tasks before returning, so the run is done.
    return wf.json()["id"], doc.json()["id"]


def _status(client, run_id, user="alice"):
    r = client.get(f"/api/v1/workflow/status/{run_id}", headers=auth_headers(user))
    assert r.status_code == 200, r.text
    return r.json()


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_forged_firebase_token_is_rejected(client):
    forged = jwt.encode({"sub": "attacker", "email": "x@y.z"}, "anything", algorithm="HS256")
    r = client.post("/api/v1/auth/google", json={"firebase_token": forged})
    assert r.status_code == 401
    assert "anything" not in r.text


def test_refresh_takes_json_body(client):
    from auth import create_refresh_token

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": create_refresh_token("alice")})
    assert r.status_code == 200 and r.json()["access_token"]


def test_api_requires_token(client):
    assert client.get("/api/v1/workflow/").status_code in (401, 403)


# ── Uploads ──────────────────────────────────────────────────────────────────

def test_uploads_are_not_public_static_files(client):
    doc_id = _upload(client).json()["id"]
    assert client.get(f"/uploads/{doc_id}_invoice.pdf").status_code == 404


def test_owner_can_download_file_others_cannot(client):
    doc_id = _upload(client, "alice").json()["id"]
    url = f"/api/v1/upload/{doc_id}/file/invoice"
    ok = client.get(url, headers=auth_headers("alice"))
    assert ok.status_code == 200 and ok.content == PDF_BYTES
    assert client.get(url, headers=auth_headers("mallory")).status_code == 404
    assert client.get(url).status_code in (401, 403)


def test_upload_rejects_non_document_bytes(client):
    r = _upload(client, invoice=b"MZ\x90\x00 not a pdf")
    assert r.status_code == 415


def test_upload_keeps_real_image_extension(client):
    from db import UploadRunRow, engine

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    doc_id = _upload(client, invoice=png).json()["id"]
    with Session(engine) as s:
        row = s.get(UploadRunRow, doc_id)
    assert row.invoice_path.endswith("_invoice.png") and row.bl_path.endswith("_bl.pdf")


# ── Pipeline + HITL ──────────────────────────────────────────────────────────

def test_clean_run_can_pass(client, fake_docs):
    run_id, _ = _start_run(client)
    st = _status(client, run_id)
    assert st["status"] == "completed"
    decl = st["result"]["declaration"]
    assert st["result"]["compliance_status"] == "PASS", decl["compliance"]
    assert decl["run_id"] == run_id
    assert decl["required_screenings"]  # informational, not issues


def test_country_rules_flag_formal_entry_and_short_hs_code(client, fake_docs):
    fake_docs.total_amount = 9000
    fake_docs.hs_code = "847130"
    run_id, _ = _start_run(client)
    issues = _status(client, run_id)["result"]["declaration"]["compliance"]["issues"]
    messages = " | ".join(i["message"] for i in issues)
    assert "formal entry" in messages and "fewer than 10 digits" in messages


def test_weight_conflict_blocks_and_exposes_review_data(client, fake_docs):
    fake_docs.bl_weight = 1000
    run_id, _ = _start_run(client)
    st = _status(client, run_id)
    assert st["status"] == "blocked"
    result = st["result"]
    assert result["blocking_issues"][0]["field"] == "gross_weight_kg"
    assert result["invoice"]["gross_weight_kg"] == 820
    assert result["bill_of_lading"]["gross_weight_kg"] == 1000
    assert st["steps"], "audit steps should be visible while blocked"
    assert st["invoice_pdf_url"].startswith("/api/v1/upload/")


def test_resume_with_bad_value_blocks_again_then_good_value_completes(client, fake_docs):
    from db import engine
    from graph import AuditEventRow

    fake_docs.bl_weight = 1000
    run_id, _ = _start_run(client)
    hdrs = auth_headers("alice")

    r = client.post(f"/api/v1/workflow/resume/{run_id}", json={"gross_weight_kg": 5}, headers=hdrs)
    assert r.status_code == 200, r.text
    assert _status(client, run_id)["status"] == "blocked", "5 kg vs 820 kg must not pass"

    r = client.post(f"/api/v1/workflow/resume/{run_id}", json={"gross_weight_kg": 825}, headers=hdrs)
    assert r.status_code == 200, r.text
    st = _status(client, run_id)
    assert st["status"] == "completed", st["result"]
    decl = st["result"]["declaration"]
    assert decl["bill_of_lading"]["gross_weight_kg"] == 825
    assert not [i for i in decl["compliance"]["issues"] if i["severity"] == "block"]

    with Session(engine) as s:
        rows = s.exec(select(AuditEventRow).where(AuditEventRow.run_id == run_id)).all()
    names = [r.node_name for r in rows]
    assert "audit_trace" not in names or names.count("ingest") == 1  # no duplicate rows
    assert "declaration_generate" in names


def test_resume_rejects_non_positive_weight(client, fake_docs):
    fake_docs.bl_weight = 1000
    run_id, _ = _start_run(client)
    r = client.post(f"/api/v1/workflow/resume/{run_id}", json={"gross_weight_kg": 0}, headers=auth_headers("alice"))
    assert r.status_code == 422


def test_other_users_cannot_see_or_resume_runs(client, fake_docs):
    fake_docs.bl_weight = 1000
    run_id, doc_id = _start_run(client, "alice")
    mallory = auth_headers("mallory")
    assert client.get(f"/api/v1/workflow/status/{run_id}", headers=mallory).status_code == 404
    assert client.post(f"/api/v1/workflow/resume/{run_id}", json={"gross_weight_kg": 820}, headers=mallory).status_code == 404
    assert client.post("/api/v1/workflow/", json={"document_id": doc_id, "country": "us"}, headers=mallory).status_code == 404
    assert run_id not in [w["id"] for w in client.get("/api/v1/workflow/", headers=mallory).json()]


def test_blocked_run_survives_restart(fake_docs):
    """A paused run can be resumed after the app (and checkpointer) restarts."""
    from fastapi.testclient import TestClient

    import main

    fake_docs.bl_weight = 1000
    with TestClient(main.app) as c1:
        run_id, _ = _start_run(c1)
        assert _status(c1, run_id)["status"] == "blocked"

    with TestClient(main.app) as c2:  # fresh lifespan = fresh SQLite connection
        assert _status(c2, run_id)["status"] == "blocked"
        r = c2.post(f"/api/v1/workflow/resume/{run_id}", json={"gross_weight_kg": 820}, headers=auth_headers("alice"))
        assert r.status_code == 200, r.text
        assert _status(c2, run_id)["status"] == "completed"
