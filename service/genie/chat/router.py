"""POST /v1/genie/chat — SSE.

Event protocol, stable from here:

    event: start   {"sessionId", "turnId", "mode"}
    event: block   {"type", "content", "meta"}     one per block, incl. tokens
    event: done    {"turnId", "latencyMs", "blocks"}
    event: error   {"code", "message", "retryable"}

Field names are camelCase on the wire, in and out (genie/wire.py). The old snake_case
request spellings are refused (422), never silently ignored - a dropped sessionId would
otherwise quietly start a new conversation every turn.

`done` always fires, including after `error`, so a client can close cleanly on
one event rather than guessing from a dropped connection.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from ..auth.caller import identity_for, owner_for, token_for
from ..auth.identity import Identity
from ..wire import sse as _sse
from .responder import Responder
from .session import Session, SessionStore, UIContext

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/genie", tags=["chat"])


def _apply_identity(request: Request, session: Session, identity: Identity | None) -> None:
    """Replace the browser's HINTS (role, client) with what the BFF says about this token.
    Without an identity the session has none, and live answers say so."""
    session.identity = identity
    if identity is None:
        return
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
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")
    screen: str | None = None
    role: str | None = None
    client_id: str | None = None
    visible_metrics: list[str] = Field(default_factory=list)
    selected_entity: str | None = None
    brand_id: str | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    ui_context: UIContextIn | None = None


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    responder: Responder = request.app.state.responder
    store: SessionStore = request.app.state.sessions

    token = await token_for(request)
    identity = await identity_for(request, store.kv, store.prefix, token)
    session = await store.load(owner_for(request, identity), req.session_id)
    if req.ui_context:
        session.context = UIContext.parse(req.ui_context.model_dump())
    _apply_identity(request, session, identity)

    turn_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()

    async def stream() -> AsyncIterator[str]:
        count = 0
        saved = False

        async def save() -> None:
            # However the turn ended, so a follow-up asked before an error or a dropped
            # connection is still answerable next turn. Once.
            nonlocal saved
            if not saved:
                saved = True
                await store.save(session)

        try:
            yield _sse("start", {"session_id": session.id, "turn_id": turn_id,
                                 "mode": responder.mode})
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
            except Exception:  # noqa: BLE001 — the stream must not die silently
                # The details go to the log, never to the browser.
                log.exception("turn %s failed", turn_id)
                yield _sse("error", {"code": "INTERNAL_ERROR",
                                     "message": "Genie couldn't finish this answer. Try again.",
                                     "retryable": True})
            await save()
            elapsed = int((time.perf_counter() - started) * 1000)
            log.info("turn=%s blocks=%d ms=%d screen=%s",
                     turn_id, count, elapsed, session.context.screen)
            yield _sse("done", {"turn_id": turn_id, "latency_ms": elapsed, "blocks": count})
        finally:
            await save()

    return StreamingResponse(
        stream(), media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},  # nginx would otherwise buffer the stream
    )
