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

from ..auth.identity import whoami
from ..auth.tokens import bearer, decode
from ..bff import BffError
from .responder import Responder
from .session import Session, SessionStore, UIContext

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/genie", tags=["chat"])


async def _token_for(request: Request) -> str | None:
    """The user's own token: the dashboard's header in production, the dev file otherwise."""
    forwarded = bearer(request.headers.get("authorization"))
    if forwarded:
        return forwarded
    source = request.app.state.tokens
    fresh = getattr(source, "fresh", None)
    return await fresh() if fresh else source.token()


async def _establish_identity(request: Request, session: Session, token: str | None) -> None:
    """Replace the browser's HINTS (role, client) with what the BFF says about this token.

    Read once per session per user; a different user's token re-reads it. On any
    failure the session simply has no identity, and live answers say so.
    """
    bff = getattr(request.app.state, "bff", None)
    if bff is None or not token:
        return
    info = decode(token)
    subject = info.subject if info else None
    if session.identity is not None and session.identity_subject == subject:
        identity = session.identity
    else:
        try:
            identity = await whoami(bff, token)
        except BffError as err:
            log.info("whoami failed: %s", err.kind)
            session.identity, session.identity_subject = None, None
            return
        session.identity, session.identity_subject = identity, subject
    grant = identity.grant_for(session.context.client_id)
    if grant is None:
        # The hinted client is not one this user holds. Ignore the hint; use their own.
        log.warning("ui_context client is not in the user's grants; using the home client")
        session.context.client_id = None
        grant = identity.grant_for(None)
    if grant is not None:
        session.context.client_id = grant.client_id
        session.context.role = grant.role or session.context.role
        # Genie's permission checks are advisory (the BFF is the gate). Only use the
        # grant's permissions once their vocabulary matches the catalog's, or an
        # unfamiliar naming scheme would deny everything.
        known = request.app.state.ontology.permission_names
        session.permissions = grant.permissions if grant.permissions & known else frozenset()


class UIContextIn(BaseModel):
    screen: str | None = None
    role: str | None = None
    client_id: str | None = None
    visible_metrics: list[str] = Field(default_factory=list)
    selected_entity: str | None = None
    brand_id: str | None = None


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
    token = await _token_for(request)
    await _establish_identity(request, session, token)

    turn_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()

    async def stream() -> AsyncIterator[str]:
        yield _sse("start", {"session_id": session.id, "turn_id": turn_id,
                             "mode": responder.mode})
        count = 0
        try:
            async for block in responder.respond(session, req.message, token=token):
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
