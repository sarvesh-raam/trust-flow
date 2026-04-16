"""Single source of truth for all LLM clients in Hackstrom Track 3.

Priority for chat / reasoning calls:
  1. ORGANIZER_API_KEY set → OpenAI-compatible endpoint at ORGANIZER_API_BASE
  2. GROQ_API_KEY set      → Groq cloud (llama-3.3-70b-versatile)
  3. Neither               → RuntimeError at first call (fail fast)

Embeddings require ORGANIZER_API_KEY; there is no local fallback.

Usage:
    from llm_client import (
        get_instructor_client,   # Instructor-patched client
        get_raw_openai_client,   # Raw OpenAI client (embeddings)
        get_active_chat_model,   # Model string for extraction / short tasks
        get_active_reason_model, # Model string for reasoning / rerank tasks
        get_active_embed_model,  # Embedding model string
    )
"""
from __future__ import annotations

import os
from functools import lru_cache

import instructor
from openai import OpenAI

# ---------------------------------------------------------------------------
# Environment — read once at import time (after load_dotenv() in main.py)
# ---------------------------------------------------------------------------

ORGANIZER_API_KEY    = os.getenv("ORGANIZER_API_KEY")
ORGANIZER_API_BASE   = os.getenv("ORGANIZER_API_BASE", "https://api.openai.com/v1")
ORGANIZER_CHAT_MODEL   = os.getenv("ORGANIZER_CHAT_MODEL",   "gpt-4o")
ORGANIZER_REASON_MODEL = os.getenv("ORGANIZER_REASON_MODEL", "gpt-5.4")
ORGANIZER_EMBED_MODEL  = os.getenv("ORGANIZER_EMBED_MODEL",  "text-embedding-3-large")

# Fallback: Groq for local dev without organizer key
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
_GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"

# ---------------------------------------------------------------------------
# Clients (cached — one instance per process)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_instructor_client() -> instructor.Instructor:
    """Return an Instructor-patched client.

    Prefers the organizer OpenAI-compatible endpoint; falls back to Groq
    when ORGANIZER_API_KEY is absent (local dev mode).

    Raises RuntimeError when neither key is configured.
    """
    if ORGANIZER_API_KEY:
        raw = OpenAI(api_key=ORGANIZER_API_KEY, base_url=ORGANIZER_API_BASE)
        return instructor.from_openai(raw, mode=instructor.Mode.JSON)

    if GROQ_API_KEY:
        from groq import Groq  # noqa: PLC0415

        return instructor.from_groq(Groq(api_key=GROQ_API_KEY), mode=instructor.Mode.JSON)

    raise RuntimeError(
        "No LLM API key configured. "
        "Set ORGANIZER_API_KEY (preferred) or GROQ_API_KEY (local dev)."
    )


@lru_cache(maxsize=1)
def get_raw_openai_client() -> OpenAI:
    """Return a raw OpenAI client for embeddings and non-Instructor calls.

    Requires ORGANIZER_API_KEY — there is no Groq fallback for embeddings.

    Raises RuntimeError when ORGANIZER_API_KEY is not set.
    """
    if ORGANIZER_API_KEY:
        return OpenAI(api_key=ORGANIZER_API_KEY, base_url=ORGANIZER_API_BASE)

    raise RuntimeError(
        "ORGANIZER_API_KEY is required for embeddings and raw OpenAI calls."
    )


# ---------------------------------------------------------------------------
# Model selectors
# ---------------------------------------------------------------------------


def get_active_chat_model() -> str:
    """Model for extraction and short-context tasks (field_extract, compliance)."""
    return ORGANIZER_CHAT_MODEL if ORGANIZER_API_KEY else _GROQ_FALLBACK_MODEL


def get_active_reason_model() -> str:
    """Model for multi-step reasoning tasks (hs_rag rerank, rationale generation)."""
    return ORGANIZER_REASON_MODEL if ORGANIZER_API_KEY else _GROQ_FALLBACK_MODEL


def get_active_embed_model() -> str:
    """Embedding model (always returns the organizer model; caller must ensure key is set)."""
    return ORGANIZER_EMBED_MODEL
