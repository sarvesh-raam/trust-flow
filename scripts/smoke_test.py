#!/usr/bin/env python3
import httpx
import time
import sys
import shutil
import json
from pathlib import Path

import jwt
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://localhost:8000/api/v1"
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "hackstrom-secret-change-in-prod")  # must match the backend

def get_auth_headers():
    token = jwt.encode({
        "sub": "smoke_test_user",
        "email": "test@example.com",
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=30),
        "iat": datetime.utcnow()
    }, SECRET_KEY, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}

TEST_DOCS = Path(__file__).parent.parent / "test_docs" / "weight_conflict_set"
UPLOADS_DIR = Path(__file__).parent.parent / "backend" / "uploads"

def main():
    print("--- 1. Uploading Documents ---")
    invoice_path = TEST_DOCS / "invoice.pdf"
    bl_path = TEST_DOCS / "bl.pdf"
    
    if not invoice_path.exists() or not bl_path.exists():
        print("Test documents missing! Run scripts/generate_test_docs.py first.")
        sys.exit(1)
        
    with open(invoice_path, "rb") as f1, open(bl_path, "rb") as f2:
        files = {
            "invoice_pdf": ("invoice.pdf", f1, "application/pdf"),
            "bl_pdf": ("bl.pdf", f2, "application/pdf")
        }
        data = {"country": "us"}
        resp = httpx.post(
            f"{BASE_URL}/upload/", 
            files=files, 
            data=data,
            headers=get_auth_headers()
        )
        resp.raise_for_status()
        doc_id = resp.json()["id"]
        
    print(f"Uploaded. Document ID: {doc_id}")

    print("\n--- 2. Triggering Workflow ---")
    resp = httpx.post(f"{BASE_URL}/workflow/", json={"document_id": doc_id, "country": "us"}, headers=get_auth_headers())
    resp.raise_for_status()
    run_id = resp.json()["id"]
    print(f"Workflow Run ID: {run_id}")

    print("\n--- 3. Polling for BLOCK (max 60s) ---")
    status = None
    issues = []
    
    for _ in range(30):
        resp = httpx.get(f"{BASE_URL}/workflow/status/{run_id}", headers=get_auth_headers())
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]
        if status in ("blocked", "failed"):
            issues = data.get("result", {}).get("issues", [])
            break
        time.sleep(2)
        
    print(f"Status: {status}")
    
    print("\n--- 4. Asserting BLOCK and 'gross_weight_kg' issue ---")
    assert status == "blocked", f"Expected 'blocked' but got {status}"
    
    has_target_issue = False
    for i in issues:
        field = i.get("field", "")
        if "gross_weight_kg" in field:
            has_target_issue = True
            break
            
    assert has_target_issue, "Expected 'gross_weight_kg' conflict in issues list"
    print("Assertion passed: Pipeline correctly halted on weight discrepancy.")

    print("\n--- 5. Resuming Workflow via HITL Override ---")
    resp = httpx.post(f"{BASE_URL}/workflow/resume/{run_id}", json={"gross_weight_kg": 860.0}, headers=get_auth_headers())
    resp.raise_for_status()
    print("Resume packet sent.")

    print("\n--- 6. Polling for PASS / COMPLETED ---")
    for _ in range(30):
        resp = httpx.get(f"{BASE_URL}/workflow/status/{run_id}", headers=get_auth_headers())
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]
        if status == "completed":
            break
        if status in ("failed", "blocked"):
            print(f"Failure or unexpected block! Details: {data.get('result', {})}")
            sys.exit(1)
        time.sleep(2)
        
    print(f"Status: {status}")

    print("\n--- 7. Fetching Final Declaration ---")
    resp = httpx.get(f"{BASE_URL}/workflow/declaration/{run_id}", headers=get_auth_headers())
    resp.raise_for_status()
    decl = resp.json()
    print("Declaration fetched.")
    
    line_items = decl.get("invoice", {}).get("line_items", [])
    for item in line_items:
        assert "hs_code" in item, "Missing hs_code field in line item!"
    print("Assertion passed: Declaration features line item hs_codes.")

    print("\n--- 8. Audit Trail output ---")
    resp = httpx.get(f"{BASE_URL}/workflow/{run_id}", headers=get_auth_headers())
    resp.raise_for_status()
    steps = resp.json().get("steps", [])
    print(json.dumps(steps, indent=2))
    
    print("\n✅ Smoke test complete!")

if __name__ == "__main__":
    main()
