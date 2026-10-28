"""Who is calling - shared by every Genie endpoint (chat, the campaign planner).

The token is the user's own; the identity is what the BFF's `me` says about that token,
never what the token or the browser claims; the owner is the key everything per-user is
stored under. One path for all of it, so chat and the planner cannot disagree about who
a request belongs to.
"""
from __future__ import annotations

import logging

from fastapi import Request

from ..bff import BffError
from ..store import KeyValue
from .identity import Identity, whoami_cached
from .tokens import bearer

log = logging.getLogger(__name__)

LOCAL_OWNER = "local"


async def token_for(request: Request) -> str | None:
    """The user's own token: the dashboard's header in production, the dev file otherwise."""
    forwarded = bearer(request.headers.get("authorization"))
    if forwarded:
        return forwarded
    source = request.app.state.tokens
    fresh = getattr(source, "fresh", None)
    return await fresh() if fresh else source.token()


async def identity_for(request: Request, kv: KeyValue, prefix: str,
                       token: str | None) -> Identity | None:
    """Who the BFF says this token belongs to - read through the `me` cache. None when
    there is no BFF, no token, or the BFF would not say."""
    bff = getattr(request.app.state, "bff", None)
    if bff is None or not token:
        return None
    try:
        identity = await whoami_cached(kv, bff, token, prefix)
    except BffError as err:
        log.info("whoami failed: %s", err.kind)
        return None
    return identity if identity.user_id else None


def is_local_without_bff(request: Request) -> bool:
    """A developer's machine with no BFF: there are no users to tell apart."""
    settings = getattr(request.app.state, "settings", None)
    local = settings is not None and settings.genie_env == "local"
    return local and getattr(request.app.state, "bff", None) is None


def owner_for(request: Request, identity: Identity | None) -> str | None:
    """Whose data this request may touch: the verified user, never a claim. Only a LOCAL
    run with no BFF has no users to tell apart (one shared owner); anywhere else, nobody
    verified means nobody's data. So a deployed Genie without a BFF (admin mode, a
    misconfiguration) can never pool its callers."""
    if identity is not None:
        return f"user:{identity.user_id}"
    if is_local_without_bff(request):
        return LOCAL_OWNER
    return None
