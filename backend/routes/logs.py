"""Live log streaming — SSE ring buffer of structlog events.

Each user only receives events tagged with their own user_id (bound for the
duration of a pipeline run in routes/workflow.py, or passed explicitly on
request-time log calls).  Untagged server events are never streamed.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from auth import verify_token

router = APIRouter()


@dataclass(eq=False)
class _Subscriber:
    user_id: str
    queue: asyncio.Queue[dict | None]
    loop: asyncio.AbstractEventLoop


_LOG_BUFFER: deque[dict] = deque(maxlen=500)
_SUBSCRIBERS: list[_Subscriber] = []


def _offer(queue: asyncio.Queue[dict | None], event: dict) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass


def capture_log_event(event: dict) -> None:
    """Store a structlog event and fan it out to its owner's SSE subscribers.

    Called from any thread (pipeline nodes log from executor threads), so
    delivery goes through the subscriber's event loop.
    """
    _LOG_BUFFER.append(event)
    owner = event.get("user_id")
    if not owner:
        return
    for sub in list(_SUBSCRIBERS):
        if sub.user_id == owner:
            try:
                sub.loop.call_soon_threadsafe(_offer, sub.queue, event)
            except RuntimeError:  # subscriber's loop already closed
                pass


@router.get("/stream")
async def stream_logs(token: str = Query(...)) -> StreamingResponse:
    """SSE stream of the caller's own backend log events.
    Token must be passed as ?token= because browser EventSource cannot set headers.
    """
    payload = verify_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload["sub"]

    sub = _Subscriber(user_id, asyncio.Queue(maxsize=200), asyncio.get_running_loop())
    _SUBSCRIBERS.append(sub)
    queue = sub.queue

    async def _generate() -> AsyncGenerator[str, None]:
        # Replay this user's recent events to the new client
        for entry in list(_LOG_BUFFER):
            if entry.get("user_id") == user_id:
                yield f"data: {json.dumps(entry)}\n\n"
        try:
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=25)
                    if entry is None:
                        break
                    yield f"data: {json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            try:
                _SUBSCRIBERS.remove(sub)
            except ValueError:
                pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
