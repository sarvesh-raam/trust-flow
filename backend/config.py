"""Centralised environment-variable configuration for Hackstrom Track 3.

All modules should import from here rather than calling os.getenv() directly,
so that defaults and validation live in one place.

Usage:
    from config import settings
    key = settings.GROQ_API_KEY
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # ── LLM ──────────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = field(
        default_factory=lambda: os.getenv("GROQ_API_KEY", "")
    )
    GROQ_MODEL: str = field(
        default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    )
    ORGANIZER_GROQ_API_KEY: str = field(
        default_factory=lambda: os.getenv("ORGANIZER_GROQ_API_KEY", os.getenv("GROQ_API_KEY", ""))
    )
    ORGANIZER_GROQ_MODEL: str = field(
        default_factory=lambda: os.getenv("ORGANIZER_GROQ_MODEL", "llama-3.3-70b-versatile")
    )
    RAG_EMBED_MODEL: str = field(
        default_factory=lambda: os.getenv("RAG_EMBED_MODEL", "all-MiniLM-L6-v2")
    )
    VLLM_BASE_URL: str = field(
        default_factory=lambda: os.getenv("VLLM_BASE_URL", "http://localhost:8001/v1")
    )
    VLLM_MODEL: str = field(
        default_factory=lambda: os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    )

    # ── Database (SQLite — kept while Firestore runs in parallel) ────────────
    DATABASE_URL: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/hackstrom.db")
    )

    # ── Cache / queue ─────────────────────────────────────────────────────────
    REDIS_URL: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    )

    # ── Firebase ─────────────────────────────────────────────────────────────
    # Base64-encoded service-account JSON (set in .env or container env).
    FIREBASE_SERVICE_ACCOUNT_JSON: str = field(
        default_factory=lambda: os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    )
    FIREBASE_STORAGE_BUCKET: str = field(
        default_factory=lambda: os.getenv("FIREBASE_STORAGE_BUCKET", "")
    )

    # ── Auth ──────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = field(
        default_factory=lambda: os.getenv("JWT_SECRET_KEY", "hackstrom-secret-change-in-prod")
    )

    # ── Observability ─────────────────────────────────────────────────────────
    LOKI_URL: str = field(
        default_factory=lambda: os.getenv("LOKI_URL", "")
    )

    # ── File storage ─────────────────────────────────────────────────────────
    UPLOAD_DIR: str = field(
        default_factory=lambda: os.getenv("UPLOAD_DIR", "uploads")
    )


settings = Settings()
