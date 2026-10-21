"""nodes/hs_retrieve.py — USITC HTS REST API lookup with local fallback.

Live path:
    GET https://hts.usitc.gov/reststop/search?keyword=<description>
    → [{"htsno": "8413.70.20.04", "indent": "2", "description": "Submersible pumps"}, ...]
    Rows come in tariff-schedule order; a row's full meaning is its chain of
    parent headings (by indent), so leaves are ranked on that full path.

Fallback (demo / offline mode):
    The bundled /data/hs_codes_sample.json is searched by keyword overlap,
    both when the API is unreachable and to top up a thin API result.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import httpx
import structlog

from models import HSCandidate, WorkflowState

log = structlog.get_logger(__name__)

USITC_BASE = "https://hts.usitc.gov/reststop"

# Path is relative to this file: backend/nodes/hs_retrieve.py → backend/data/
_SAMPLE_PATH = Path(__file__).parent.parent / "data" / "hs_codes_sample.json"


# ---------------------------------------------------------------------------
# Fallback helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_sample_data() -> list[dict[str, str]]:
    """Load the bundled sample data once per process and cache it."""
    try:
        with _SAMPLE_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        log.info("hs_retrieve.sample_loaded", entries=len(data))
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("hs_retrieve.sample_load_failed", error=str(exc), path=str(_SAMPLE_PATH))
        return []


def _tokenize(text: str) -> set[str]:
    """Meaningful words only: lowercase, 3+ letters, plural 's' dropped.

    Numbers and model codes ("CP-500", "500 cc") are ignored — matching on
    them pairs unrelated goods (a "500" pump with a 500 cc shotgun).
    """
    words = re.findall(r"[a-z]{3,}", re.sub(r"<[^>]+>", " ", text.lower()))
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words}


def _fallback_search(description: str, top_k: int) -> list[HSCandidate]:
    """Keyword-overlap search against the local sample data.

    Each sample entry is scored by how many tokens from *description* appear
    in its own description.  Entries with zero overlap are excluded.
    """
    query_tokens = _tokenize(description)
    if not query_tokens:
        return []

    scored: list[tuple[int, dict[str, str]]] = []
    for entry in _load_sample_data():
        overlap = len(query_tokens & _tokenize(entry.get("description", "")))
        if overlap:
            scored.append((overlap, entry))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        HSCandidate(
            code=entry.get("code", ""),
            description=entry.get("description", ""),
            confidence=0.0,   # scored by compliance_reason node
            rationale=None,
        )
        for _, entry in scored[:top_k]
    ]


# ---------------------------------------------------------------------------
# USITC API helper
# ---------------------------------------------------------------------------


def _rank_usitc_rows(rows: list[dict], description: str, top_k: int) -> list[HSCandidate]:
    """Rank 10-digit HTS lines by word overlap of their full heading path.

    A leaf like "Submersible pumps" only makes sense under its parents
    ("Pumps for liquids … > Other centrifugal pumps"), so each leaf is scored
    on the joined chain of descriptions above it (tracked by `indent`).  A leaf
    must match at least half of the query's words to count.
    """
    query = _tokenize(description)
    if not query:
        return []
    needed = max(1, (len(query) + 1) // 2)

    path: list[tuple[int, str]] = []   # (indent, description) of open headings
    heading = ""                       # current 4-digit heading
    scored: list[tuple[int, int, str, str]] = []
    for order, row in enumerate(rows):
        code = (row.get("htsno") or "").replace(".", "")
        if code and code[:4] != heading:
            heading, path = code[:4], []   # new heading: drop the old chain
        indent = int(row.get("indent") or 0)
        text = re.sub(r"<[^>]+>", "", row.get("description") or "").strip()
        while path and path[-1][0] >= indent:
            path.pop()
        path.append((indent, text))

        if len(code) != 10:
            continue
        full = " > ".join(t for _, t in path if t)
        score = len(query & _tokenize(full))
        if score >= needed:
            scored.append((-score, order, code, full))

    scored.sort()
    return [
        HSCandidate(code=code, description=full[-300:], confidence=0.0, rationale=None)
        for _, _, code, full in scored[:top_k]
    ]


async def search_hs_codes(description: str, top_k: int = 5) -> list[HSCandidate]:
    """Return up to *top_k* HS code candidates for *description*.

    Queries the live USITC HTS search first, then tops up with the bundled
    sample data when the API is unreachable or returns too few matches.
    Candidates are only suggestions — compliance_reason picks the final code.
    """
    candidates: list[HSCandidate] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{USITC_BASE}/search", params={"keyword": description})
            resp.raise_for_status()
            rows = resp.json()
        candidates = _rank_usitc_rows(rows if isinstance(rows, list) else [], description, top_k)
        log.info("hs_retrieve.api_ok", description=description[:60], rows=len(rows), hits=len(candidates))
    except Exception as exc:
        log.warning("hs_retrieve.api_failed", description=description[:60], error=str(exc))

    if len(candidates) < top_k:
        seen = {c.code for c in candidates}
        extra = [c for c in _fallback_search(description, top_k) if c.code not in seen]
        candidates += extra[: top_k - len(candidates)]
        if extra:
            log.info("hs_retrieve.fallback_used", description=description[:60], hits=len(extra))
    return candidates


# ---------------------------------------------------------------------------
# Public node function  (async — called directly by the graph wrapper)
# ---------------------------------------------------------------------------


async def hs_retrieve_node(state: WorkflowState) -> WorkflowState:
    """Populate hs_candidates for every invoice line item.

    Fires one USITC API request (or fallback search) per line item.
    Requests are issued sequentially to avoid hammering the public API.
    Results are written directly onto each LineItem object; the node returns
    the same state instance for the graph wrapper to diff against.
    """
    if not state.invoice or not state.invoice.line_items:
        log.info("hs_retrieve.skipped", reason="no_invoice_or_line_items")
        return state

    for item in state.invoice.line_items:
        candidates = await search_hs_codes(item.description, top_k=5)
        item.hs_candidates = candidates
        log.debug(
            "hs_retrieve.item_done",
            description=item.description[:60],
            candidates=len(candidates),
        )

    return state
