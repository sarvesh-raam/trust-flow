# Hackstrom Track 3 — Autonomous Customs Compliance Agent

AI-powered customs document processing: upload a commercial invoice + bill of lading, and a 12-node LangGraph pipeline extracts fields, assigns HS codes, validates compliance rules, handles human-in-the-loop corrections, and generates a final declaration.

---

## Quick Start (Docker — recommended)

> Requires: [Docker Desktop](https://www.docker.com/products/docker-desktop/) running, a Groq API key.

**1. Set your Groq API key in the root `.env` file:**

```
GROQ_API_KEY=gsk_...
```

**2. Build and start everything:**

```bash
docker-compose build
docker-compose up -d
```

**3. Open the app:**

| Service  | URL                          |
|----------|------------------------------|
| Frontend | http://localhost:3000        |
| API docs | http://localhost:8000/docs   |
| Health   | http://localhost:8000/health |

**One-command restart:**

```bash
docker-compose down && docker-compose up -d
```

**View logs:**

```bash
docker-compose logs -f
docker-compose logs backend   # backend only
docker-compose logs frontend  # frontend only
```

**Stop everything:**

```bash
docker-compose down
```

> Windows users: double-click `docker_run.bat` to build, start, and open the browser automatically.

---

## Run End-to-End Demo Test

After the stack is up, run the full pipeline test (upload → BLOCK → HITL resume → PASS):

```bash
# from repo root
backend/.venv/Scripts/python.exe scripts/full_demo_test.py   # Windows
python scripts/full_demo_test.py                              # Mac/Linux
```

Expected output: all 7 steps PASS, compliance = PASS, 12 audit nodes completed.

---

## Local Dev (without Docker)

### Backend

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt

# Set env vars
cp .env.example .env   # fill in GROQ_API_KEY

uvicorn main:app --reload --port 8000
# API: http://localhost:8000
# Docs: http://localhost:8000/docs
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# UI: http://localhost:5173
```

> In local dev the Vite proxy forwards `/api` to `localhost:8000` automatically — no extra config needed.

---

## Firebase Authentication Setup (manual)

Sign-in is powered by Firebase Google Auth. Fill in `frontend/.env` with your project's values:

```
VITE_FIREBASE_API_KEY=...
VITE_FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=your-project-id
VITE_FIREBASE_STORAGE_BUCKET=your-project.appspot.com
VITE_FIREBASE_MESSAGING_SENDER_ID=...
VITE_FIREBASE_APP_ID=...
```

Then in [Firebase Console](https://console.firebase.google.com):
1. Authentication → Sign-in method → enable **Google**
2. Authentication → Settings → Authorized domains → add `localhost` and `localhost:3000`

After updating `.env`, rebuild the frontend image:

```bash
docker-compose build frontend && docker-compose up -d frontend
```

> Demo mode bypasses auth — append `?demo=true` to the URL to skip login.

---

## Architecture

```
hackstrom-track3/
├── backend/
│   ├── main.py               # FastAPI app, CORS, router registration
│   ├── graph.py              # 12-node LangGraph pipeline
│   ├── models.py             # Pydantic v2 schemas
│   ├── auth.py               # JWT create/verify helpers
│   ├── dependencies.py       # FastAPI auth dependency (get_current_user)
│   └── routes/
│       ├── upload.py         # POST /api/v1/upload/
│       ├── workflow.py       # POST /api/v1/workflow/ + HITL resume
│       └── auth_routes.py    # POST /api/v1/auth/google (Firebase → JWT)
├── frontend/
│   └── src/
│       ├── lib/
│       │   ├── api.ts         # Axios client + Bearer interceptor + 401 auto-retry
│       │   ├── firebase.ts    # Firebase init, signInWithGoogle, signOut
│       │   └── pdfStore.ts    # sessionStorage PDF persistence
│       ├── contexts/
│       │   └── AuthContext.tsx # onAuthStateChanged → backend JWT exchange
│       └── pages/
│           ├── LoginPage.tsx
│           ├── UploadPage.tsx
│           ├── WorkflowPage.tsx
│           └── DeclarationPage.tsx
├── scripts/
│   └── full_demo_test.py     # End-to-end pipeline test
├── country_rules/
│   ├── us.yaml               # US compliance rules
│   └── uae.yaml              # UAE compliance rules
├── docker-compose.yml
└── docker_run.bat            # Windows one-click launcher
```

---

## Pipeline (12 nodes)

```
ingest → preprocess → ocr_extract → vision_adjudication → field_extract
  → reconcile → hs_retrieve → compliance_reason → deterministic_validate
  → country_validate → [interrupt_node on BLOCK] → declaration_generate → audit_trace
```

| Node | What it does |
|------|-------------|
| `ingest` | Store PDFs, create DB record |
| `preprocess` | Docling parse → raw text + page images |
| `ocr_extract` | RapidOCR over scanned pages |
| `vision_adjudication` | LLM picks best text source per page |
| `field_extract` | Instructor + Groq → InvoiceDocument + BillOfLading models |
| `reconcile` | Cross-doc field reconciliation |
| `hs_retrieve` | USITC HTS API → HS code per line item |
| `compliance_reason` | LLM compliance reasoning |
| `deterministic_validate` | Rule engine (weight, B/L presence, vessel) |
| `country_validate` | YAML country rules (US / UAE) |
| `interrupt_node` | LangGraph HITL — pauses on BLOCK severity issues |
| `declaration_generate` | Final declaration with HS codes + compliance status |
| `audit_trace` | 12-step audit trail committed to DB |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_API_KEY` | Yes | Groq LLM API key |
| `GROQ_MODEL` | No | Default: `llama-3.3-70b-versatile` |
| `DATABASE_URL` | No | Default: `sqlite:///./hackstrom.db` |
| `JWT_SECRET_KEY` | No | Default: insecure dev key (set for prod) |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI + Uvicorn |
| AI orchestration | LangGraph 1.1.6 |
| LLM | Groq (llama-3.3-70b-versatile) via Instructor |
| Document parsing | Docling, PyMuPDF, RapidOCR |
| Auth | Firebase Google Auth + PyJWT |
| Frontend | React 18 + TypeScript + Vite |
| Styling | Tailwind CSS |
| State | TanStack Query v5 |
| PDF viewer | react-pdf |
| Containerization | Docker + nginx |
