"""Who is asking, according to the BFF rather than according to the browser.

ui_context.role and ui_context.client_id are HINTS. Anyone can send
{"role": "AD_PARTNER_ADMIN"}. Once a token is present, the `me` endpoint is the
authority: it returns the user's grants, and each grant names a client, a role
and that role's permissions. Genie takes its role, client and permissions from
there and only from there.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..bff import BffClient
from ..bff.endpoints import endpoint
from ..store import KeyValue
from .tokens import decode

ME_TTL_S = 300                        # one `me` read per token per 5 minutes, across every task


@dataclass(frozen=True, slots=True)
class Grant:
    client_id: str
    client_name: str | None
    role: str
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    name: str | None
    primary_role: str | None
    home_client_id: str | None
    grants: tuple[Grant, ...]

    def grant_for(self, client_id: str | None) -> Grant | None:
        """The grant for a client, or the home client's when none is named."""
        wanted = client_id or self.home_client_id
        for g in self.grants:
            if g.client_id == wanted:
                return g
        if self.grants:
            return self.grants[0] if not client_id else None
        # Measured on QA (2026-10-22): an AD_PARTNER_ADMIN's /me carries
        # "grants": [] and names its agency only in clientId + primaryRole. That is
        # the user's own agency, so it stands as their grant. Permissions stay
        # empty: Genie's checks are advisory, and the BFF still refuses anything
        # this user may not read.
        if self.home_client_id and wanted == self.home_client_id:
            return Grant(self.home_client_id, None, self.primary_role or "", frozenset())
        return None

    def to_dict(self) -> dict:
        return {"user_id": self.user_id, "name": self.name, "primary_role": self.primary_role,
                "home_client_id": self.home_client_id,
                "grants": [[g.client_id, g.client_name, g.role, sorted(g.permissions)]
                           for g in self.grants]}

    @classmethod
    def from_dict(cls, d: dict) -> "Identity":
        return cls(user_id=d["user_id"], name=d["name"], primary_role=d["primary_role"],
                   home_client_id=d["home_client_id"],
                   grants=tuple(Grant(c, n, r, frozenset(p)) for c, n, r, p in d["grants"]))


async def whoami(bff: BffClient, token: str) -> Identity:
    data = (await bff.get(endpoint("me"), token=token)).data or {}
    grants = tuple(
        Grant(client_id=str(g.get("clientId")), client_name=g.get("clientName"),
              role=str(g.get("role") or ""), permissions=frozenset(g.get("permissions") or ()))
        for g in data.get("grants") or [] if g.get("clientId"))
    return Identity(user_id=str(data.get("userId") or ""), name=data.get("name"),
                    primary_role=data.get("primaryRole"),
                    home_client_id=data.get("clientId"), grants=grants)


async def whoami_cached(kv: KeyValue, bff: BffClient, token: str, prefix: str = "genie:") -> Identity:
    """`me` for this token, read once per token per few minutes and shared by every task.

    Keyed by a hash of the token - the token itself is never stored - and never kept
    past the token's own expiry, so a cached identity can only be the identity the BFF
    gave for exactly this token. A BffError propagates: no identity is ever guessed."""
    key = f"{prefix}me:{hashlib.sha256(token.encode()).hexdigest()}"
    raw = await kv.get(key)
    if raw is not None:
        try:
            return Identity.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            pass
    identity = await whoami(bff, token)
    info = decode(token)
    ttl = ME_TTL_S if info is None or info.expires_in is None else min(ME_TTL_S, info.expires_in)
    if ttl > 0 and identity.user_id:
        await kv.set(key, json.dumps(identity.to_dict(), ensure_ascii=False), ttl)
    return identity
