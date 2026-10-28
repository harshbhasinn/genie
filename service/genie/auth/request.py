"""Who is asking, for any Genie endpoint: the user's token and what the BFF says it belongs to.

Shared by chat and the calculator, so both trust exactly the same things: the dashboard's
Authorization header (the dev token file only when no header came), and the identity `me`
returns for that token - never a claim read from the token, never anything the browser sends.
"""
from __future__ import annotations

import logging

from fastapi import Request

from ..bff import BffError
from .identity import Identity, whoami_cached
from .tokens import bearer

log = logging.getLogger(__name__)


async def token_for(request: Request) -> str | None:
    """The user's own token: the dashboard's header in production, the dev file otherwise."""
    forwarded = bearer(request.headers.get("authorization"))
    if forwarded:
        return forwarded
    source = request.app.state.tokens
    fresh = getattr(source, "fresh", None)
    return await fresh() if fresh else source.token()


async def identity_for(request: Request, token: str | None) -> Identity | None:
    """Who the BFF says this token belongs to - read through the `me` cache. None when
    there is no BFF, no token, or the BFF would not say."""
    bff = getattr(request.app.state, "bff", None)
    if bff is None or not token:
        return None
    store = request.app.state.sessions
    try:
        identity = await whoami_cached(store.kv, bff, token, store.prefix)
    except BffError as err:
        log.info("whoami failed: %s", err.kind)
        return None
    return identity if identity.user_id else None
