"""Who is asking, according to the BFF rather than according to the browser.

ui_context.role and ui_context.client_id are HINTS. Anyone can send
{"role": "AD_PARTNER_ADMIN"}. Once a token is present, the `me` endpoint is the
authority: it returns the user's grants, and each grant names a client, a role
and that role's permissions. Genie takes its role, client and permissions from
there and only from there.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..bff import BffClient
from ..bff.endpoints import endpoint


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


async def whoami(bff: BffClient, token: str) -> Identity:
    data = (await bff.get(endpoint("me"), token=token)).data or {}
    grants = tuple(
        Grant(client_id=str(g.get("clientId")), client_name=g.get("clientName"),
              role=str(g.get("role") or ""), permissions=frozenset(g.get("permissions") or ()))
        for g in data.get("grants") or [] if g.get("clientId"))
    return Identity(user_id=str(data.get("userId") or ""), name=data.get("name"),
                    primary_role=data.get("primaryRole"),
                    home_client_id=data.get("clientId"), grants=grants)
