"""nodes/hs_rag_node.py — Semantic RAG HS-code classification node.

Stage 1 — Retrieve
    search_hs_openai() embeds the line-item description using
    text-embedding-3-large and queries the ChromaDB HS index, returning
    up to 8 semantically ranked candidates.

Stage 2 — Rerank + Generate
    gpt-5.4 (via get_active_reason_model()) selects the single best code
    from the retrieved candidates, writes a 2-3 sentence rationale, assigns
    a confidence score, and flags low-confidence items for human review.

Replaces the former hs_retrieve + compliance_reason two-node pipeline.
State side-effects (per line item):
    item.hs_candidates  — full candidate list with similarity scores
    item.hs_code        — winning HTS code chosen by the LLM
    state.__dict__["_hs_selections"]  — list[HSSelection] consumed by graph.py
"""
from __future__ import annotations

import structlog

from llm_client import get_active_reason_model, get_instructor_client
from llm_instrumented import tracked_instructor_create
from models import HSCandidate, WorkflowState
from nodes.compliance_reason import HSSelection

# Try to import the heavy vector store (OpenAI/Chroma)
try:
    from vector_store import search_hs_openai
except ImportError:
    search_hs_openai = None

# Local fallback store
from nodes.local_vector_store import search_hs as search_hs_local

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are an expert customs classification specialist.
Given a product description and semantically retrieved HTS code candidates,
select the single most accurate HTS code.
Respond only in the required JSON schema."""


async def hs_rag_node(state: WorkflowState) -> WorkflowState:
    """Retrieve-Rerank-Generate HS classification for every invoice line item.

    Mutates state.invoice.line_items in-place (hs_code + hs_candidates).
    Stashes HSSelection objects in state.__dict__["_hs_selections"] so the
    graph.py wrapper can promote flag_for_review items to WARN compliance issues.
    """
    if not state.invoice or not state.invoice.line_items:
        return state

    client = get_instructor_client()
    model  = get_active_reason_model()   # gpt-5.4

    for i, item in enumerate(state.invoice.line_items):

        # ── Stage 1: Semantic Retrieval ───────────────────────────────────────
        candidates = []
        try:
            if search_hs_openai:
                candidates = await search_hs_openai(item.description, top_k=8)
        except Exception as exc:
            log.debug("hs_rag.openai_search_failed", index=i, error=str(exc))

        if not candidates:
            # Fallback to local 
            log.info("hs_rag.using_local_fallback", index=i)
            try:
                candidates = search_hs_local(item.description, top_k=8)
            except Exception as exc:
                log.error("hs_rag.local_search_failed", index=i, error=str(exc))

        if not candidates:
            log.warning("hs_rag.no_candidates", description=item.description[:60])
            continue

        # Populate item.hs_candidates from retrieval results.
        # Confidence is the raw similarity score; rationale is filled after LLM.
        item.hs_candidates = [
            HSCandidate(
                code=c["code"],
                description=c["description"],
                confidence=round(c["score"], 4),
                rationale=None,
            )
            for c in candidates
        ]

        # ── Stage 2: LLM Reranking + Rationale (gpt-5.4) ─────────────────────
        candidate_text = "\n".join(
            f"{j + 1}. [{c['code']}] {c['description']} (similarity: {c['score']:.3f})"
            for j, c in enumerate(candidates)
        )

        try:
            selection: HSSelection = tracked_instructor_create(
                client,
                model=model,
                call_type="rag_rerank",
                response_model=HSSelection,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Product description: {item.description}\n"
                            f"Quantity: {item.quantity}, Unit price: {item.unit_price}\n\n"
                            f"Candidates:\n{candidate_text}\n\n"
                            f"line_item_index: {i}"
                        ),
                    },
                ],
                max_retries=2,
            )
        except Exception as exc:
            log.error("hs_rag.llm_failed", index=i, error=str(exc))
            # Fallback: take the highest-similarity candidate, skip rationale
            best = candidates[0]
            item.hs_code = best["code"]
            continue

        # ── Apply selection ────────────────────────────────────────────────────
        item.hs_code = selection.selected_code

        # Enrich the winning candidate in-place with LLM confidence + rationale
        for c in item.hs_candidates:
            if c.code == selection.selected_code:
                c.confidence = selection.confidence
                c.rationale  = selection.rationale
                break

        if selection.flag_for_review:
            log.warning(
                "hs_rag.flagged_for_review",
                index=i,
                code=selection.selected_code,
                confidence=selection.confidence,
            )

        # Stash for graph.py wrapper — mirrors compliance_reason.py convention
        state.__dict__.setdefault("_hs_selections", []).append(selection)

    return state
