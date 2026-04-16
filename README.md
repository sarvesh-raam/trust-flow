# Trust Flow: Autonomous Customs Compliance Agent

**Hackstrom '26 | Track 3 | Autonomous Compliance Agent**

An AI-powered agentic workflow that automates the verification and compliance of customs documentation, significantly reducing manual effort and human error in international trade.

## 📄 Problem Statement
The manual verification of customs documents—specifically **Commercial Invoices** and **Bills of Lading**—is a bottleneck in global logistics. Small discrepancies in gross weight, HS codes, or vessel names often go unnoticed until a border delay occurs, leading to high demurrage costs and regulatory fines.

## 💡 Solution: Agentic Reconciliation
Trust Flow uses a **multi-node LangGraph pipeline** to:
1.  **Ingest & Extract**: Uses `Docling` and `Instructor` to extract structured Pydantic models from "unstructured" PDFs.
2.  **Semantic Retrieval**: Queries the **USITC HTS API** and local semantic caches to find precise HS codes.
3.  **Autonomous Compliance**: Compares weights, dates, and names between inconsistent documents using an LLM (Llama 3.3).
4.  **HITL (Human-in-the-Loop)**: Issues an **INTERRUPT** if a critical discrepancy is found, allowing a human operator to correct data before final declaration generation.
5.  **Audit Trail**: Maintains a complete record of agent decisions and reasoning for regulatory transparency.

## 🛠 Tech Stack
- **Frontend**: React (Vite), CSS3, Firebase Auth, Radix UI.
- **Backend**: FastAPI (Python 3.11), SQLModel (SQLite).
- **AI/ML**: LangGraph, Groq (Llama 3.3 70B), Docling (Doc Layout), Instructor.
- **Infrastructure**: Docker Compose, Redis (Task Queue), Celery (Workers).
- **Observability**: Grafana, Loki (Error Logging).

## 🚀 How to Run Locally

### 1. Prerequisites
- Docker Desktop installed (with WSL 2 integration enabled for Windows users).
- A **Groq API Key** from [console.groq.com](https://console.groq.com/).
- A **Firebase Web App** project (for authentication).

### 2. Environment Setup
Create a `.env` file in the root directory (or use the one provided):
```bash
# Groq Key for AI
GROQ_API_KEY=your_groq_key_here

# Firebase Keys for Frontend login
VITE_FIREBASE_API_KEY=your_key
...
```

### 3. Launch with Docker
```powershell
# Build and start all services
docker-compose up -d --build
```

### 4. Access the Application
- **Frontend**: [http://localhost:3000](http://localhost:3000)
- **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Flower (Tasks)**: [http://localhost:5555](http://localhost:5555)
- **Grafana (Logs)**: [http://localhost:3001](http://localhost:3001)

## 📊 Benchmarks & Maintainability
- **Singleton Architecture**: Database connections managed via a module-level singleton in `workflow_db.py`.
- **Horizontal Scaling**: Celery workers can be scaled independently using `docker-compose up --scale worker1=3`.
- **Modern Observability**: Real-time error streaming to Loki for rapid production debugging.
