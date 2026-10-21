# Trust Flow — single production image: FastAPI backend + built frontend.
#
# Pulls the app from GitHub (so the build needs only this folder), builds the
# frontend, and serves it from the backend on $PORT (default 8000).
# Used by deploy/docker-compose.yml; see deploy/README.md.

ARG REPO=https://github.com/sarvesh-raam/trust-flow.git
ARG REF=main

# ── Source ────────────────────────────────────────────────────────────────────
FROM alpine/git:2.45.2 AS src
ARG REPO
ARG REF
# Changes whenever the branch moves, so a rebuild never reuses a stale clone.
ADD https://api.github.com/repos/sarvesh-raam/trust-flow/git/refs/heads/${REF} /tmp/ref.json
RUN git clone --depth 1 --branch "${REF}" "${REPO}" /src

# ── Frontend build ────────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /app
COPY --from=src /src/frontend/package*.json ./
RUN npm ci
COPY --from=src /src/frontend/ ./
# Uses frontend/.env.production: API at /api/v1 on the same origin.
RUN npm run build

# ── Backend + static frontend ─────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

# CPU-only PyTorch (for Docling) — the default CUDA wheels are several GB larger.
COPY --from=src --chown=user /src/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --user -r requirements.txt \
        --extra-index-url https://download.pytorch.org/whl/cpu

COPY --from=src --chown=user /src/backend/ ./
COPY --from=src --chown=user /src/country_rules/ /home/user/country_rules/
COPY --from=frontend --chown=user /app/dist /home/user/frontend
COPY --from=src --chown=user /src/test_docs/weight_conflict_set/invoice.pdf /tmp/warmup.pdf

# Database, uploads and paused-run checkpoints live under /home/user/data —
# mount a volume there to keep them across restarts.
ENV STATIC_DIR=/home/user/frontend \
    DATABASE_URL=sqlite:////home/user/data/hackstrom.db \
    CHECKPOINT_DB=/home/user/data/checkpoints.db \
    UPLOAD_DIR=/home/user/data/uploads \
    COUNTRY_RULES_DIR=/home/user/country_rules \
    PORT=8000

# Download Docling's layout/OCR models now, so the first upload isn't slow.
RUN mkdir -p /home/user/data \
    && python -c "from nodes.ocr_extract import _CONVERTER; _CONVERTER.convert('/tmp/warmup.pdf'); print('docling models ready')"

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --timeout-graceful-shutdown 3"]
