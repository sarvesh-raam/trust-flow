"""Lightweight in-memory vector store for HS code semantic retrieval.

Backed by the bundled data/hs_codes_sample.json. Embeddings are computed once
on first call (lazy, thread-safe) using sentence-transformers and cached for
the lifetime of the process.

Falls back to keyword-overlap scoring when sentence-transformers cannot be
loaded (e.g. model download unavailable in air-gapped environments).
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import TypedDict

log = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent.parent / "data" / "hs_codes_sample.json"
_INIT_LOCK = threading.Lock()

# Module-level state — populated once by _ensure_loaded()
_entries: list[dict[str, str]] = []
_embeddings = None   # np.ndarray shape (N, D), L2-normalised
_model = None        # SentenceTransformer instance
_vector_ready = False


class HSEntry(TypedDict):
    code: str
    description: str
    score: float  # cosine similarity in [0, 1]; keyword mode uses overlap ratio


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _load_json() -> list[dict[str, str]]:
    try:
        with _DATA_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        log.info("vector_store.json_loaded entries=%d", len(data))
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("vector_store.json_load_failed error=%s path=%s", exc, _DATA_PATH)
        return []


def _ensure_loaded() -> bool:
    """Lazy-init the embedding model and pre-compute corpus embeddings.

    Returns True when the vector index is ready, False when only keyword
    fallback is available.
    """
    global _entries, _embeddings, _model, _vector_ready

    if _vector_ready:
        return True
    if _embeddings is not None:
        return False  # init was attempted but failed; stay in fallback

    with _INIT_LOCK:
        # Double-checked locking
        if _vector_ready:
            return True
        if _embeddings is not None:
            return False

        _entries = _load_json()
        if not _entries:
            return False

        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer

            log.info("vector_store.model_loading model=all-MiniLM-L6-v2")
            _model = SentenceTransformer("all-MiniLM-L6-v2")
            descriptions = [e["description"] for e in _entries]
            raw = _model.encode(descriptions, normalize_embeddings=True)
            # Keep a plain numpy array (not a torch tensor) for portability
            _embeddings = np.asarray(raw, dtype="float32")
            _vector_ready = True
            log.info(
                "vector_store.ready entries=%d dim=%d",
                len(_entries),
                _embeddings.shape[1],
            )
            return True

        except Exception as exc:
            log.warning(
                "vector_store.st_unavailable error=%s — keyword fallback active", exc
            )
            # Sentinel: non-None but not ready — prevents re-init attempts
            _embeddings = object()
            return False


# ---------------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def _keyword_search(query: str, top_k: int) -> list[HSEntry]:
    if not _entries:
        return []
    qtoks = _tokenize(query)
    if not qtoks:
        return []
    scored: list[tuple[float, dict[str, str]]] = []
    for entry in _entries:
        overlap = len(qtoks & _tokenize(entry.get("description", "")))
        if overlap:
            scored.append((overlap / len(qtoks), entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        HSEntry(code=e["code"], description=e["description"], score=s)
        for s, e in scored[:top_k]
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_hs(query: str, top_k: int = 8) -> list[HSEntry]:
    """Return up to *top_k* HS code candidates for *query*, ordered by score.

    Uses semantic cosine similarity when the embedding model is available,
    falls back to keyword overlap otherwise.

    This function is synchronous and safe to call from an executor thread.
    """
    if not _ensure_loaded():
        log.debug("vector_store.using_keyword_fallback query=%s", query[:60])
        return _keyword_search(query, top_k)

    import numpy as np

    q_emb = np.asarray(
        _model.encode([query], normalize_embeddings=True), dtype="float32"
    )
    scores = (_embeddings @ q_emb.T).flatten()
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [
        HSEntry(
            code=_entries[i]["code"],
            description=_entries[i]["description"],
            score=float(scores[i]),
        )
        for i in top_indices
    ]
