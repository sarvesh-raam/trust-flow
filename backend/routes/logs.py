"""Live log streaming — SSE ring buffer of structlog events."""
from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from auth import verify_token

router = APIRouter()

_LOG_BUFFER: deque[dict] = deque(maxlen=500)
_SUBSCRIBERS: list[asyncio.Queue[dict | None]] = []


def capture_log_event(event: dict) -> None:
    """Fan out a structlog event to the ring buffer and all SSE subscribers."""
    _LOG_BUFFER.append(event)
    for q in list(_SUBSCRIBERS):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


@router.get("/stream")
async def stream_logs(token: str = Query(...)) -> StreamingResponse:
    """SSE stream of backend log events.
    Token must be passed as ?token= because browser EventSource cannot set headers.
    """
    payload = verify_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token")

    queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=200)
    _SUBSCRIBERS.append(queue)

    async def _generate() -> AsyncGenerator[str, None]:
        # Replay existing buffer to new client
        for entry in list(_LOG_BUFFER):
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
                _SUBSCRIBERS.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
