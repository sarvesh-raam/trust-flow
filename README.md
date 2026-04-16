# Trust Flow: Autonomous Customs Compliance Orchestrator

**Hackstrom '26 | Track 3 | Autonomous Compliance Agent**

Trust Flow is a high-performance agentic AI framework designed to reconcile and validate international trade documentation with enterprise-grade precision.

## Problem Statement
The global logistics industry faces significant operational risks due to manual document verification. Discrepancies between Commercial Invoices and Bills of Lading (e.g., weight mismatches, vessel name errors, or date inconsistencies) often lead to customs delays, heavy fines, and supply chain disruptions. Existing solutions lack the "agentic" ability to handle complex layout analysis and human-in-the-loop (HITL) error resolution.

## The Solution
Trust Flow implements an autonomous Multi-Agent System (MAS) that:
- Performs deterministic cross-document validation using LLMs.
- Utilizes **LangGraph** to manage stateful, interruptible workflows.
- Employs **Docling** for sub-pixel OCR and layout-aware extraction.
- Integrates a **Human-in-the-Loop** mechanism to pause processing when a high-severity mismatch is detected, allowing for manual correction.

## System Screenshots
![Document Ingest](docs/assets/document_ingest.jpeg)
![Workflow Orchestration](docs/assets/workflow_orchestration.jpeg)

## Technology Stack
- **AI Core**: LangGraph, Docling, Groq (Llama-3.3-70B).
- **Backend**: FastAPI, SQLModel (ORM), Uvicorn.
- **Frontend**: React, TypeScript, TailwindCSS / Vanilla CSS.
- **Persistence & Tasking**: SQLite, Redis, Celery.
- **Observability**: Grafana, Loki (Error Logging).

## Architectural Compliance (Maintainability)
- **Model-View-Controller (MVC)**: Clear separation of concerns between SQLModel schemas (`models/`), FastAPI orchestrators (`routes/`), and React components (`view/`).
- **Singleton Pattern**: The database engine is managed via a strict Singleton Manager in `workflow_db.py` to optimize resource allocation and prevent connection leaks.
- **Clean Project Structure**: Consolidated directories for `/scripts`, `/docs`, and `/resources` to maintain a clutter-free root.
- **Error Logging**: Centralized structured logging using `structlog` and real-time observability via the Loki/Grafana stack.

## How to Run

### 1. Environment Configuration
Create a `.env` file in the root directory with your `GROQ_API_KEY` and Firebase credentials.

### 2. Deployment via Docker
```bash
docker-compose up -d --build
```

### 3. Access
- **Dashboard**: `http://localhost:3000`
- **API Documentation**: `http://localhost:8000/docs`
- **Observability Hub**: `http://localhost:3001` (Grafana)

---
*Maintained by the Trust Flow Engineering Team for Hackstrom '26.*
