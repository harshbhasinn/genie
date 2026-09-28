"""POST /v1/genie/chat — SSE.

Event protocol, stable from here:

    event: start   {"session_id", "turn_id", "mode"}
    event: block   {"type", "content", "meta"}     one per block, incl. tokens
    event: done    {"turn_id", "latency_ms", "blocks", "usage"}
    event: error   {"code", "message", "retryable"}

`done` always fires, including after `error`, so a client can close cleanly on
one event rather than guessing from a dropped connection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .responder import Responder
from .session import SessionStore, UIContext

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/genie", tags=["chat"])


class UIContextIn(BaseModel):
    screen: str | None = None
    role: str | None = None
    client_id: str | None = None
    visible_metrics: list[str] = Field(default_factory=list)
    selected_entity: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    ui_context: UIContextIn | None = None


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    responder: Responder = request.app.state.responder
    store: SessionStore = request.app.state.sessions

    session = store.get_or_create(req.session_id)
    if req.ui_context:
        session.context = UIContext.parse(req.ui_context.model_dump())

    turn_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()

    async def stream() -> AsyncIterator[str]:
        yield _sse("start", {"session_id": session.id, "turn_id": turn_id,
                             "mode": responder.mode})
        count = 0
        try:
            async for block in responder.respond(session, req.message):
                if await request.is_disconnected():
                    log.info("client gone, turn=%s abandoning", turn_id)
                    return
                count += 1
                yield _sse("block", {"type": block.type, "content": block.content,
                                     "meta": block.meta})
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 — the stream must not die silently
            log.exception("turn %s failed", turn_id)
            yield _sse("error", {"code": type(err).__name__,
                                 "message": str(err)[:300], "retryable": False})
        finally:
            elapsed = int((time.perf_counter() - started) * 1000)
            log.info("turn=%s blocks=%d ms=%d screen=%s",
                     turn_id, count, elapsed, session.context.screen)
            yield _sse("done", {"turn_id": turn_id, "latency_ms": elapsed,
                                "blocks": count})

    return StreamingResponse(
        stream(), media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},  # nginx would otherwise buffer the stream
    )
