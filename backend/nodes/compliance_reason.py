"""nodes/compliance_reason.py — LLM-based HS code selection and rationale.

For each invoice line item that has HS candidates (populated by hs_retrieve),
this node asks the LLM to pick the single best code, write a 2-3 sentence
rationale, assign a confidence, and flag the item for human review if no
candidate looks convincing (confidence < 0.5).

Uses the same Instructor-patched client as field_extract (cached, so the same
process-level connection is reused).
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from pydantic import BaseModel, Field

from llm_client import get_active_chat_model as _active_model
from llm_client import get_instructor_client as get_client
from models import WorkflowState

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior customs classification specialist with 20 years experience.
Your job is to select the most accurate HTS code for each line item from the \
provided candidates.
You must:
1. Select exactly ONE code per line item
2. Explain your reasoning in 2-3 sentences referencing the product description
3. Assign a confidence score 0.0-1.0
4. Flag if none of the candidates seem correct (confidence < 0.5)

Respond ONLY in the JSON schema provided. No preamble."""

# ---------------------------------------------------------------------------
# Response models (local to this node — not persisted in WorkflowState)
# ---------------------------------------------------------------------------


class HSSelection(BaseModel):
    line_item_index: int = Field(
        description="Zero-based index of the line item in the invoice.line_items list"
    )
    selected_code: str = Field(
        description="The chosen HS/HTS code, exactly as it appeared in the candidates list"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Your confidence that this is the correct code for this product",
    )
    rationale: str = Field(
        description="2-3 sentences explaining why this code was chosen for this product"
    )
    flag_for_review: bool = Field(
        description="True when no candidate is convincing (confidence < 0.5); requires human review"
    )


class HSSelectionList(BaseModel):
    selections: list[HSSelection] = Field(
        description="One selection per line item, ordered by line_item_index"
    )


# ---------------------------------------------------------------------------
# Public node function  (synchronous — wrapped by graph.py via run_in_executor)
# ---------------------------------------------------------------------------


def compliance_reason_node(state: WorkflowState) -> WorkflowState:
    """Call the LLM to pick the best HS code for every invoice line item.

    Side-effects on state:
        - Sets item.hs_code to the chosen code for each line item.
        - Updates the winning HSCandidate's confidence and rationale fields.
        - Stores the raw HSSelectionList on state for the graph wrapper to
          use when emitting compliance WARN issues for flagged items.

    The node silently skips line items whose index is out of range in the LLM
    response (defensive against off-by-one errors in the LLM output).
    """
    if not state.invoice or not state.invoice.line_items:
        log.info("compliance_reason.skipped", reason="no_invoice_or_line_items")
        return state

    # ── Build prompt context ──────────────────────────────────────────────────
    items_context: list[dict[str, Any]] = []
    for i, item in enumerate(state.invoice.line_items):
        items_context.append(
            {
                "index": i,
                "description": item.description,
                "quantity": item.quantity,
                "candidates": [
                    {"code": c.code, "description": c.description}
                    for c in item.hs_candidates
                ],
            }
        )

    if not any(ctx["candidates"] for ctx in items_context):
        log.info("compliance_reason.skipped", reason="no_candidates_on_any_item")
        return state

    log.info(
        "compliance_reason.start",
        model=_active_model(),
        line_items=len(items_context),
    )

    # ── LLM call ─────────────────────────────────────────────────────────────
    client = get_client()
    result: HSSelectionList = client.chat.completions.create(
        model=_active_model(),
        response_model=HSSelectionList,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Select the best HS code for each item:\n"
                    + json.dumps(items_context, indent=2)
                ),
            },
        ],
        max_retries=3,
    )

    log.info(
        "compliance_reason.llm_done",
        selections=len(result.selections),
    )

    # ── Apply selections back to line items ───────────────────────────────────
    n_items = len(state.invoice.line_items)
    for sel in result.selections:
        if sel.line_item_index < 0 or sel.line_item_index >= n_items:
            log.warning(
                "compliance_reason.index_out_of_range",
                index=sel.line_item_index,
                n_items=n_items,
            )
            continue

        item = state.invoice.line_items[sel.line_item_index]
        item.hs_code = sel.selected_code

        # Update the winning candidate's confidence and rationale in-place.
        matched = False
        for c in item.hs_candidates:
            if c.code == sel.selected_code:
                c.confidence = sel.confidence
                c.rationale = sel.rationale
                matched = True
                break

        if not matched:
            # LLM selected a code not in the candidate list — log and continue.
            # The hs_code is still set; the graph wrapper can add a WARN issue.
            log.warning(
                "compliance_reason.code_not_in_candidates",
                index=sel.line_item_index,
                selected=sel.selected_code,
                candidates=[c.code for c in item.hs_candidates],
            )

        log.debug(
            "compliance_reason.item_done",
            index=sel.line_item_index,
            description=item.description[:60],
            code=sel.selected_code,
            confidence=sel.confidence,
            flag=sel.flag_for_review,
        )

    # Stash the raw result so the graph wrapper can read flag_for_review.
    # We piggy-back on a private attribute rather than adding a field to
    # WorkflowState (it's ephemeral — not needed after this node).
    state.__dict__["_hs_selections"] = result.selections
    return state
