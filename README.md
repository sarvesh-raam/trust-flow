# Trust Flow — Autonomous Customs Compliance Orchestrator

<p align="center">
  <img src="https://img.shields.io/badge/Version-1.0.0-blue?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB" alt="React">
  <img src="https://img.shields.io/badge/LangGraph-FFAB00?style=for-the-badge&logo=openai" alt="LangGraph">
  <img src="https://img.shields.io/badge/Firebase-FFCA28?style=for-the-badge&logo=firebase&logoColor=black" alt="Firebase">
</p>

---

## 📖 Problem Statement
Manual customs document verification causes significant supply chain delays and results in costly fines for importers and exporters. Discrepancies between Commercial Invoices and Bills of Lading are common yet difficult to detect with traditional template-based systems. No existing solution handles complex layout analysis and multi-country jurisdictional rules while providing integrated human escalation in a single automated pipeline.

## Solution
Trust Flow runs a stateful LangGraph pipeline that ingests an invoice + bill of lading pair, extracts structured fields, reconciles the two documents, classifies HS codes, applies country rules and generates a customs declaration. Docling OCR anchors every extracted value back to its bounding box in the source PDF. When a blocking issue is found, the run pauses (`NodeInterrupt`) and an operator corrects the flagged fields in the review panel; the run then re-validates and continues. Every node is recorded in an audit trail, and Prometheus metrics plus a live log stream make the pipeline observable.

---

## Architecture

| Layer | Implementation | Location |
|-------|---------------|----------|
| **Model** | Pydantic models + SQLModel tables (SQLite) | `backend/models.py`, `backend/db.py` |
| **View** | React TypeScript SPA | `frontend/src/` |
| **Controller** | FastAPI route handlers | `backend/routes/` |
| **Pipeline** | LangGraph `StateGraph` with SQLite checkpointer | `backend/graph.py`, `backend/nodes/` |

### Persistence
- **SQLite** (`DATABASE_URL`) stores uploads, workflow runs (per user) and the audit trail.
- **LangGraph checkpoints** (`CHECKPOINT_DB`) keep paused (BLOCKED) runs resumable across restarts.
- Pipelines run as in-process background tasks; runs that were mid-flight when the server stopped are marked failed on startup.

---

## Agent Pipeline

### Topology
```
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
```

### Pipeline Nodes
| Node | Description |
|------|-------------|
| **ingest** | Receives the document pair; the run id is the workflow id. |
| **preprocess** | Confirms both files exist and are readable. |
| **ocr_extract** | Docling layout analysis, text, tables and bounding boxes; quality score from Docling's confidence report. |
| **vision_adjudication** | Low-confidence scans: flags a warning for manual verification (no vision model wired yet). |
| **field_extract** | LLM structured extraction (Instructor + Groq, or a local vLLM server) into `InvoiceDocument` / `BillOfLading`. |
| **reconcile** | Cross-document checks — BLOCK when invoice and B/L gross weights differ by more than 5 %. |
| **hs_retrieve** | HS code candidates per line item from the USITC HTS API, with a bundled offline fallback. |
| **compliance_reason** | LLM picks the best HS code per line item; low-confidence picks are flagged for review. |
| **deterministic_validate** | Required fields, ISO dates, positive weights and amounts. |
| **country_validate** | Country rules from `country_rules/<country>.yaml` (required fields, formats, value thresholds, restricted goods). |
| **interrupt_node** | Pauses the run with `NodeInterrupt` while BLOCK issues remain. |
| **declaration_generate** | Builds the declaration JSON and a human-readable summary. |
| **audit_trace** | Persists the per-node audit trail. |

### HITL Flow
1. **Detection**: reconcile / deterministic_validate / country_validate raise a BLOCK issue (e.g. weight mismatch > 5 %, non-ISO date).
2. **Suspension**: `interrupt_node` raises `NodeInterrupt`; the state is saved by the checkpointer and the run is marked `blocked`.
3. **Review**: the review panel shows every blocking issue with one pre-filled input per field to correct.
4. **Resume**: `POST /api/v1/workflow/resume/{run_id}` with `{"corrections": {"invoice.date": "2024-03-15", ...}}` applies the values; validation re-runs and the run either completes or blocks again.

---

## REST API

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| **POST** | `/api/v1/auth/google` | Exchange a Firebase ID token (or `local-guest`, if enabled) for a JWT | None |
| **POST** | `/api/v1/auth/refresh` | New access token from a refresh token | None |
| **POST** | `/api/v1/upload/` | Upload an invoice + B/L pair (PDF, PNG, JPEG or TIFF) | Bearer JWT |
| **GET** | `/api/v1/upload/{id}/file/{invoice\|bl}` | Download your own uploaded file | Bearer JWT |
| **POST** | `/api/v1/workflow/` | Start the pipeline for an upload | Bearer JWT |
| **GET** | `/api/v1/workflow/` | List your workflow runs | Bearer JWT |
| **GET** | `/api/v1/workflow/status/{run_id}` | Status, issues and field-level bbox annotations | Bearer JWT |
| **POST** | `/api/v1/workflow/resume/{run_id}` | Submit HITL field corrections | Bearer JWT |
| **GET** | `/api/v1/workflow/declaration/{run_id}` | Final declaration JSON | Bearer JWT |
| **GET** | `/api/v1/logs/stream?token=…` | Live log stream (Server-Sent Events) | JWT in query |
| **GET** | `/metrics` | Prometheus scrape endpoint | None |
| **GET** | `/health` | Liveness check | None |

Every workflow and upload is owned by the user who created it; other users get 404.

---

## Tech Stack

| Component | Technology | Why Chosen |
|-----------|------------|------------|
| **Agent Pipeline** | LangGraph 1.x + SQLite checkpointer | Stateful, interruptible workflows that survive restarts. |
| **OCR Engine** | Docling 2.x | Layout analysis with bbox provenance and a built-in quality score. |
| **LLM Client** | Instructor + Groq (`openai/gpt-oss-120b`) or local vLLM | Structured Pydantic extraction from unstructured text. |
| **Backend** | FastAPI + SQLModel | Async API with auto-generated OpenAPI docs. |
| **Auth** | Firebase Google sign-in (verified server-side) + PyJWT | Google SSO with stateless sessions. |
| **Frontend** | React + react-pdf + React Flow | Live bbox overlays, review panel and pipeline graph. |
| **Observability** | Prometheus + Grafana, structlog | Pipeline KPIs and structured logs. |

---

## Observability

| Service | Role | Port |
|---------|------|------|
| **Prometheus** | Scrapes `/metrics` (request metrics plus `trustflow_pipeline_runs_total`, `trustflow_compliance_status_total`, `trustflow_hitl_*`, `trustflow_ocr_confidence`) | 9090 |
| **Grafana** | Dashboards over Prometheus | 3001 |
| **GRAPH tab** | Live pipeline graph and log stream in the web app | 3000 |

---

## Security

| Control | Implementation | Location |
|---------|---------------|----------|
| **Authentication** | Firebase ID tokens verified (signature, issuer, audience, expiry) before a JWT is issued | `backend/auth.py` |
| **Authorisation** | Uploads, files and workflows are scoped to their owner | `backend/routes/` |
| **Uploads** | Type detected from file bytes, size-limited, served only through the authenticated download route | `backend/routes/upload.py` |
| **PII** | Emails are hashed before going into JWTs and logs | `backend/auth.py` |
| **Guest login** | Off unless `ALLOW_GUEST_LOGIN=true` (all guests share one account — demo only) | `backend/routes/auth_routes.py` |

---

## How to Run

### Local (Windows / macOS / Linux)
1. `cp backend/.env.example backend/.env` and set at least `GROQ_API_KEY` and `JWT_SECRET_KEY`.
2. Start both servers: double-click `start_all.bat` (Windows), or run `./start_all.ps1` / `./start_all.sh`.
   To run only the backend: `cd backend` then `python dev.py` (restarts on code changes).
   Use `dev.py` rather than `uvicorn --reload`, which hangs on Windows once Docling/PyTorch is loaded.
3. Open http://localhost:3000 and sign in with Google.

### Docker Compose
```bash
cp backend/.env.example backend/.env   # set GROQ_API_KEY
export JWT_SECRET_KEY=... GRAFANA_ADMIN_PASSWORD=...
docker compose up -d --build
```

| Service | URL |
|---------|-----|
| **Dashboard** | http://localhost:3000 |
| **API Docs** | http://localhost:8000/docs |
| **Prometheus** | http://localhost:9090 |
| **Grafana** | http://localhost:3001 (admin / `GRAFANA_ADMIN_PASSWORD`) |

### Kubernetes (Minikube)
```bash
minikube start --memory=8192 --cpus=4
minikube addons enable ingress
eval $(minikube docker-env)
kubectl apply -k k8s/
```
Set real values in `k8s/01-secrets.yaml` first. The backend runs as a single replica (SQLite).

### Tests
```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest tests
cd frontend && npm run lint && npm run build
```
GitHub Actions runs the same checks on every push to `main` and on pull requests (`.github/workflows/ci.yml`).

---

## Roadmap
- Semantic (RAG) HS-code retrieval and an operator chat for editing declarations — prototyped on an earlier branch, to be re-integrated with ownership checks.
- A worker queue (Celery/Redis) for running pipelines outside the API process.
- A vision model for low-confidence scans.

---

## 👥 Contributors
Autonomous AI Solution for Trade Compliance:

- [github.com/sarvesh-raam](https://github.com/sarvesh-raam)
- [github.com/abinavmugundhan](https://github.com/abinavmugundhan)
- [github.com/Shiva085000](https://github.com/Shiva085000)
