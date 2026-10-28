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

from ..auth.identity import Identity, whoami_cached
from ..auth.tokens import bearer
from ..bff import BffError
from .responder import Responder
from .session import LOCAL_OWNER, Session, SessionStore, UIContext

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


async def _identity(request: Request, store: SessionStore, token: str | None) -> Identity | None:
    """Who the BFF says this token belongs to - read through the `me` cache. None when
    there is no BFF, no token, or the BFF would not say."""
    bff = getattr(request.app.state, "bff", None)
    if bff is None or not token:
        return None
    try:
        identity = await whoami_cached(store.kv, bff, token, store.prefix)
    except BffError as err:
        log.info("whoami failed: %s", err.kind)
        return None
    return identity if identity.user_id else None


def _owner(request: Request, identity: Identity | None) -> str | None:
    """Whose sessions this request may load: the verified user, never a claim. Only a
    LOCAL run with no BFF has no users to tell apart (one shared owner); anywhere else,
    nobody verified means nobody's sessions - a fresh, unsaved one. So a deployed Genie
    without a BFF (admin mode, a misconfiguration) can never pool its callers' sessions."""
    if identity is not None:
        return f"user:{identity.user_id}"
    settings = getattr(request.app.state, "settings", None)
    local = settings is not None and settings.genie_env == "local"
    if local and getattr(request.app.state, "bff", None) is None:
        return LOCAL_OWNER
    return None


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

    token = await _token_for(request)
    identity = await _identity(request, store, token)
    session = await store.load(_owner(request, identity), req.session_id)
    if req.ui_context:
        session.context = UIContext.parse(req.ui_context.model_dump())
    _apply_identity(request, session, identity)

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
            # Saved however the turn ended, so a follow-up asked before an error or a
            # dropped connection is still answerable next turn.
            await store.save(session)
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
