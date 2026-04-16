# Release v1.0.0 — Hackstrom '26 Production Ready.

This is the initial production-ready release of **Trust Flow**, submitted for Hackstrom Track 3.

## 🚀 Key Features
- **Agentic Recovery**: Uses LangGraph to pause the pipeline when document discrepancies (weight/date) are found.
- **Deep Extraction**: Leverages Docling for pixel-perfect OCR and structured layout analysis.
- **Real-time Observability**: Fully integrated Loki/Grafana dashboard for monitoring AI agents.
- **Production Architecture**: Dockerized microservices using Redis and Celery for high-throughput document processing.

## 📦 What's Included
- **Frontend**: Premium Bloomberg-style terminal UI in React.
- **Backend**: High-performance FastAPI with Singleton DB scaling.
- **Infrastructure**: Complete Docker Compose environment with 2 workers and monitoring.

## 🛠 Installation
1. Clone the repo: `git clone https://github.com/Shiva085000/Trust_Flow.git`
2. Add your `GROQ_API_KEY` to the `.env` file.
3. Run `docker-compose up -d --build`.
