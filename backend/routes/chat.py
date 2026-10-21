"""Declaration chat — ask about a run's documents and propose field edits.

The assistant only *proposes* changes. Nothing is written until the owner
applies a proposal, and applying goes through the same path as the HITL
review panel: the editable-field allow-list and validation in
routes.workflow._apply_corrections, then a re-run of validation onwards
(apply_corrections_and_rerun). Compliance results are never editable.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import Session

from db import WorkflowRunRow, engine
from dependencies import get_current_user
from models import BillOfLading, ComplianceResult, InvoiceDocument, WorkflowStatus
from nodes.field_extract import _active_model, get_client, is_rate_limited, structured_completion
from routes.workflow import (
    _EDITABLE_FIELDS,
    _apply_corrections,
    _as_model,
    _get_owned,
    apply_corrections_and_rerun,
    graph_values,
    rate_limit_message,
)

log = structlog.get_logger(__name__)
router = APIRouter()

MAX_HISTORY = 50     # messages kept per run
CONTEXT_TURNS = 10   # earlier messages sent to the model with each question
_NUMERIC_FIELDS = {"gross_weight_kg", "total_amount"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ProposedChange(BaseModel):
    path: str = Field(description="Field path from the editable list, e.g. 'bill_of_lading.gross_weight_kg'")
    value: str | float = Field(description="New value for the field")
    reason: str = Field(default="", description="One short sentence on why")


class ChatPlan(BaseModel):
    """What the model returns for one user message."""

    reply: str = Field(description="Answer to the user, in plain language")
    proposed_changes: list[ProposedChange] = Field(
        default_factory=list,
        description="Field edits, only when the user explicitly asks to change a value",
    )


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    proposals: list[ProposedChange] = Field(default_factory=list)
    proposal_status: Literal["none", "pending", "applied", "dismissed"] = "none"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatHistory(BaseModel):
    messages: list[ChatMessage]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ProposalAction(BaseModel):
    action: Literal["apply", "dismiss"]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _load_history(row: WorkflowRunRow) -> list[ChatMessage]:
    return [ChatMessage.model_validate(m) for m in json.loads(row.chat_json or "[]")]


def _save_history(run_id: str, messages: list[ChatMessage]) -> None:
    with Session(engine) as session:
        row = session.get(WorkflowRunRow, run_id)
        if row is None:
            return
        row.chat_json = json.dumps([m.model_dump(mode="json") for m in messages[-MAX_HISTORY:]])
        session.add(row)
        session.commit()


# ---------------------------------------------------------------------------
# Context + model call
# ---------------------------------------------------------------------------

def _documents(values: dict[str, Any]) -> tuple[InvoiceDocument | None, BillOfLading | None]:
    return (
        _as_model(InvoiceDocument, values.get("invoice")),
        _as_model(BillOfLading, values.get("bill_of_lading")),
    )


def _editable_paths(invoice: InvoiceDocument | None) -> list[str]:
    paths = [f"{doc}.{field}" for doc, (_, fields) in _EDITABLE_FIELDS.items() for field in sorted(fields)]
    n_items = len(invoice.line_items) if invoice else 0
    return paths + [f"line_items.{i}.hs_code" for i in range(n_items)]


def _context(row: WorkflowRunRow, values: dict[str, Any]) -> dict[str, Any]:
    invoice, bl = _documents(values)
    cr = _as_model(ComplianceResult, values.get("compliance_result"))
    inv = invoice.model_dump(mode="json") if invoice else None
    if inv:
        # Keep the prompt small: only the top HS candidates per line item.
        for li in inv.get("line_items", []):
            li["hs_candidates"] = [
                {"code": c["code"], "description": c["description"][:120]}
                for c in li.get("hs_candidates", [])[:3]
            ]
    return {
        "run_status": row.status,
        "country": row.country,
        "invoice": inv,
        "bill_of_lading": bl.model_dump(mode="json") if bl else None,
        "compliance": cr.model_dump(mode="json") if cr else None,
        "summary": values.get("summary") or None,
    }


SYSTEM_PROMPT = """\
You are a customs-compliance assistant inside Trust Flow. You help the user \
understand ONE processed shipment (a commercial invoice + bill of lading) and \
fix extraction mistakes.

Rules:
- Answer from the run data below; say so when something is not in it.
- Explain compliance issues in plain language: what they mean and how to fix them.
- Only fill `proposed_changes` when the user explicitly asks to change, set or \
correct a value. Use ONLY these field paths: {editable}
- Never claim a change was made: the user reviews and applies proposals \
themselves, and validation re-runs afterwards.
- Compliance status, issues and screenings cannot be edited directly; they are \
recomputed from the documents.
- Keep replies short (a few sentences or a short list).

Run data (JSON):
{context}
"""


def plan_chat_reply(context: dict[str, Any], editable: list[str], history: list[ChatMessage],
                    message: str) -> ChatPlan:
    """One structured LLM call (blocking — run it in a thread)."""
    messages: list[dict[str, str]] = [{
        "role": "system",
        "content": SYSTEM_PROMPT.format(editable=", ".join(editable), context=json.dumps(context, default=str)),
    }]
    messages += [{"role": m.role, "content": m.content} for m in history[-CONTEXT_TURNS:]]
    messages.append({"role": "user", "content": message})
    return structured_completion(
        get_client(), _active_model(), response_model=ChatPlan, messages=messages, max_retries=2,
    )


def _as_corrections(changes: list[ProposedChange]) -> dict[str, Any]:
    """Proposal list → corrections dict, with numeric fields coerced to numbers."""
    corrections: dict[str, Any] = {}
    for change in changes:
        value: Any = change.value
        if change.path.rsplit(".", 1)[-1] in _NUMERIC_FIELDS and isinstance(value, str):
            try:
                value = float(value.replace(",", "").strip())
            except ValueError:
                pass  # left as text; validation rejects it with a clear message
        corrections[change.path] = value
    return corrections


def _valid_changes(changes: list[ProposedChange], invoice: InvoiceDocument | None,
                   bl: BillOfLading | None) -> tuple[list[ProposedChange], list[str]]:
    """Keep the proposals that pass the same validation as the review panel."""
    kept: list[ProposedChange] = []
    rejected: list[str] = []
    for change in changes:
        try:
            _apply_corrections(invoice, bl, _as_corrections([change]))
            kept.append(change)
        except HTTPException as exc:
            rejected.append(str(exc.detail))
    return kept, rejected


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/{run_id}", response_model=ChatHistory, summary="Chat history for a run")
async def get_chat(run_id: str, user_id: str = Depends(get_current_user)) -> ChatHistory:
    return ChatHistory(messages=_load_history(_get_owned(run_id, user_id)))


@router.post("/{run_id}", response_model=ChatHistory, summary="Ask about a run or request a field change")
async def send_chat(run_id: str, body: ChatRequest, user_id: str = Depends(get_current_user)) -> ChatHistory:
    row = _get_owned(run_id, user_id)
    values = await graph_values(run_id)
    if not values or not (values.get("invoice") or values.get("bill_of_lading")):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chat is available once the documents have been extracted",
        )
    history = _load_history(row)
    invoice, bl = _documents(values)

    try:
        plan = await run_in_threadpool(
            plan_chat_reply, _context(row, values), _editable_paths(invoice), history, body.message,
        )
    except Exception as exc:
        log.warning("chat.llm_failed", workflow_id=run_id, user_id=user_id, error=str(exc)[:300])
        if is_rate_limited(exc):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=rate_limit_message(str(exc)))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The assistant is unavailable right now — please try again")

    proposals, rejected = _valid_changes(plan.proposed_changes, invoice, bl)
    reply = plan.reply
    if rejected:
        reply += "\n\n(Some suggested edits were left out because they aren't allowed: " + "; ".join(rejected) + ")"

    history += [
        ChatMessage(role="user", content=body.message),
        ChatMessage(role="assistant", content=reply, proposals=proposals,
                    proposal_status="pending" if proposals else "none"),
    ]
    _save_history(run_id, history)
    log.info("chat.message", workflow_id=run_id, user_id=user_id, proposals=len(proposals))
    return ChatHistory(messages=history[-MAX_HISTORY:])


@router.post(
    "/{run_id}/proposals/{index}",
    response_model=ChatHistory,
    summary="Apply or dismiss the edits proposed in one assistant message",
)
async def act_on_proposal(
    run_id: str,
    index: int,
    body: ProposalAction,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
) -> ChatHistory:
    row = _get_owned(run_id, user_id)
    history = _load_history(row)
    if not 0 <= index < len(history) or history[index].proposal_status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No pending proposal at that position")
    message = history[index]

    if body.action == "apply":
        # Values come from the stored proposal, never from the client.
        await apply_corrections_and_rerun(
            row, _as_corrections(message.proposals), background_tasks,
            allowed=(WorkflowStatus.BLOCKED, WorkflowStatus.COMPLETED),
        )
        message.proposal_status = "applied"
    else:
        message.proposal_status = "dismissed"

    _save_history(run_id, history)
    log.info("chat.proposal", workflow_id=run_id, user_id=user_id, action=body.action,
             fields=[p.path for p in message.proposals])
    return ChatHistory(messages=history)
