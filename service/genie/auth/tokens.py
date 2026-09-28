"""Where the BFF token comes from.

Three shapes were on the table (see asks/C2-TEST-USERS.md). We build for A:
production forwards the browser's own token, so Genie reads as whoever is asking
and can never act with more authority than the person at the keyboard.

The static file source is dev-only. QA tokens from adg-dashboard live FIVE
MINUTES — measured, not assumed — so it is for one-off verification runs, never
for keeping a service up. It re-reads on mtime change, so swapping the file
swaps the token with no restart.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TokenInfo:
    """Claims read WITHOUT verifying the signature.

    This is not authentication — the BFF verifies. It is so the service can say
    'your token expired 20 minutes ago' instead of surfacing an opaque 401.
    """
    subject: str | None = None
    user_id: str | None = None
    roles: tuple[str, ...] = ()
    issuer: str | None = None
    expires_at: int | None = None

    @property
    def expires_in(self) -> int | None:
        if self.expires_at is None:
            return None
        return int(self.expires_at - time.time())

    @property
    def expired(self) -> bool:
        return self.expires_in is not None and self.expires_in <= 0


class TokenSource(Protocol):
    name: str

    def token(self) -> str | None:
        ...

    def info(self) -> TokenInfo | None:
        ...


class NoToken(TokenSource):
    """Admin mode, or nothing configured. The BFF client sends no Authorization."""
    name = "none"

    def token(self) -> str | None:
        return None

    def info(self) -> TokenInfo | None:
        return None


class StaticFileToken(TokenSource):
    """Reads a JWT from a file, re-reading when the file changes."""
    name = "static-file"

    def __init__(self, path: Path):
        self._path = path
        self._mtime: float | None = None
        self._token: str | None = None
        self._info: TokenInfo | None = None

    def _refresh(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            self._token, self._info, self._mtime = None, None, None
            return
        if mtime == self._mtime:
            return
        raw = self._path.read_text(encoding="utf-8").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        self._mtime, self._token = mtime, raw or None
        self._info = decode(raw) if raw else None
        if self._info:
            log.info("token loaded: roles=%s expires_in=%ss",
                     list(self._info.roles), self._info.expires_in)

    def token(self) -> str | None:
        self._refresh()
        return self._token

    def info(self) -> TokenInfo | None:
        self._refresh()
        return self._info


def decode(jwt: str) -> TokenInfo | None:
    """Read claims from a JWT payload. No signature check — not ours to verify."""
    parts = jwt.split(".")
    if len(parts) < 2:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, json.JSONDecodeError, ValueError):
        return None
    roles = claims.get("roles") or []
    if isinstance(roles, str):
        roles = [roles]
    return TokenInfo(
        subject=claims.get("sub"), user_id=claims.get("user_id"),
        roles=tuple(str(r) for r in roles), issuer=claims.get("iss"),
        expires_at=claims.get("exp"),
    )


def build(settings) -> TokenSource:
    if settings.genie_bff_mode == "admin":
        return NoToken()
    return StaticFileToken(settings.genie_bff_token_file)
