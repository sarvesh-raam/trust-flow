"""llm_instrumented.py — Thin wrapper around Instructor that records Prometheus metrics.

Usage (drop-in replacement for client.chat.completions.create):

    from llm_instrumented import tracked_instructor_create

    result = tracked_instructor_create(
        client,
        model="gpt-4o",
        call_type="extraction",
        response_model=InvoiceDocument,
        messages=[...],
        max_retries=3,
    )

Metrics recorded on every call:
    llm_call_duration_seconds{model, call_type}   — Histogram (wall-clock seconds)
    llm_tokens_total{model, token_type}            — Counter (prompt + completion tokens)
    llm_call_errors_total{model, error_type}       — Counter (on exception only)
"""
from __future__ import annotations

import time
from typing import Any, Type, TypeVar

import structlog

from metrics import LLM_CALL_DURATION, LLM_CALL_ERRORS, LLM_TOKEN_USAGE

log = structlog.get_logger(__name__)

T = TypeVar("T")


def tracked_instructor_create(
    client: Any,
    *,
    model: str,
    call_type: str,
    response_model: Type[T],
    messages: list[dict],
    max_retries: int = 3,
    **kwargs: Any,
) -> T:
    """Call client.chat.completions.create and record Prometheus metrics.

    Parameters
    ----------
    client      : Instructor-patched OpenAI/Groq client
    model       : model identifier string (used as Prometheus label)
    call_type   : logical name for the call site, e.g. "extraction" or "rag_rerank"
    response_model : Pydantic model class passed to Instructor
    messages    : chat messages list
    max_retries : forwarded to Instructor
    **kwargs    : any additional kwargs forwarded to Instructor
    """
    t0 = time.perf_counter()
    try:
        result: T = client.chat.completions.create(
            model=model,
            response_model=response_model,
            messages=messages,
            max_retries=max_retries,
            **kwargs,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        error_type = type(exc).__name__
        LLM_CALL_DURATION.labels(model=model, call_type=call_type).observe(elapsed)
        LLM_CALL_ERRORS.labels(model=model, error_type=error_type).inc()
        log.warning(
            "llm_instrumented.error",
            model=model,
            call_type=call_type,
            error_type=error_type,
            elapsed_s=round(elapsed, 3),
        )
        raise

    elapsed = time.perf_counter() - t0
    LLM_CALL_DURATION.labels(model=model, call_type=call_type).observe(elapsed)

    # Extract token usage from the raw response if available.
    # Instructor stores the underlying httpx response on ._raw_response.
    try:
        usage = result._raw_response.usage  # type: ignore[attr-defined]
        if usage:
            LLM_TOKEN_USAGE.labels(model=model, token_type="prompt").inc(
                usage.prompt_tokens or 0
            )
            LLM_TOKEN_USAGE.labels(model=model, token_type="completion").inc(
                usage.completion_tokens or 0
            )
    except AttributeError:
        # Groq or other backends may not expose _raw_response — skip silently.
        pass

    log.debug(
        "llm_instrumented.ok",
        model=model,
        call_type=call_type,
        elapsed_s=round(elapsed, 3),
    )
    return result
