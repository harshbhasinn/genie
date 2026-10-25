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
    # Keycloak's authorised party - the client_id the refresh endpoint wants.
    client_id: str | None = None

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
        # (mtime_ns, size): mtime alone misses a rewrite inside one clock tick
        # (~15 ms on Windows), which served a renewed login's EXPIRED predecessor.
        self._stamp: tuple[int, int] | None = None
        self._token: str | None = None
        self._info: TokenInfo | None = None

    def _refresh(self) -> None:
        try:
            st = self._path.stat()
        except OSError:
            self._token, self._info, self._stamp = None, None, None
            return
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == self._stamp:
            return
        raw = self._path.read_text(encoding="utf-8").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        self._stamp, self._token = stamp, raw or None
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
        expires_at=claims.get("exp"), client_id=claims.get("azp"),
    )


def bearer(header: str | None) -> str | None:
    """The token in an `Authorization: Bearer …` header, or None."""
    if not header:
        return None
    scheme, _, value = header.strip().partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


# Renew this long before expiry, so a request never leaves with a token that dies
# on the way. QA access tokens live about five minutes.
REFRESH_MARGIN_S = 30

# What the dashboard frontend on QA itself sends on its auth calls (read from the QA
# bundle): the Keycloak client is "adg-dashboard". The token's own azp wins.
DASHBOARD_CLIENT_ID = "adg-dashboard"


def auth_headers(info: TokenInfo | None, portal_id: str) -> dict[str, str]:
    """Headers for /v1/auth/refresh-token and /logout: client_id is a HEADER (spec), not a query."""
    headers = {"client_id": (info.client_id if info and info.client_id else DASHBOARD_CLIENT_ID)}
    if portal_id:
        headers["x-portal-id"] = portal_id
    return headers


class RefreshingFileToken(StaticFileToken):
    """Dev only: the static file, renewed from a refresh token when it is about to expire.

    Both files live in secrets/ and are gitignored (*.jwt, *.token). A renewal
    rewrites both, because Keycloak rotates the refresh token too. Renewal goes to
    the configured BFF, which config has already restricted to QA.
    """
    name = "static-file+refresh"

    def __init__(self, path: Path, refresh_path: Path, bff_base: str, timeout: float = 6.0,
                 portal_id: str = "", transport=None):
        super().__init__(path)
        self._transport = transport          # tests answer the refresh call without a network
        self._refresh_path = refresh_path
        self._base = bff_base.rstrip("/")
        self._timeout = timeout
        self._portal_id = portal_id
        self.last_refresh_error: str | None = None

    def _refresh_token(self) -> str | None:
        try:
            raw = self._refresh_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return raw or None

    @property
    def can_refresh(self) -> bool:
        return self._refresh_token() is not None

    async def fresh(self) -> str | None:
        """The access token, renewed first if it is about to expire."""
        info = self.info()
        if info is not None and (info.expires_in is None or info.expires_in > REFRESH_MARGIN_S):
            return self.token()
        refresh = self._refresh_token()
        if not refresh:
            return self.token()          # nothing to renew with; the caller gets a clean 401
        import httpx                      # local: tokens.py is otherwise dependency-free
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport,
                                         headers=auth_headers(info, self._portal_id)) as http:
                resp = await http.post(f"{self._base}/v1/auth/refresh-token",
                                       json={"refreshToken": refresh})
        except httpx.HTTPError as err:
            self.last_refresh_error = f"refresh failed: {type(err).__name__}"
            log.warning(self.last_refresh_error)
            return self.token()
        if resp.status_code != 200:
            self.last_refresh_error = f"refresh answered {resp.status_code} - log in again"
            log.warning(self.last_refresh_error)
            return self.token()
        data = (resp.json() or {}).get("data") or {}
        access, new_refresh = data.get("accessToken"), data.get("refreshToken")
        if not access:
            self.last_refresh_error = "refresh returned no access token"
            log.warning(self.last_refresh_error)
            return self.token()
        _write_secret(self._path, access)
        self._stamp = None              # re-read: this process just changed the file
        if new_refresh:
            _write_secret(self._refresh_path, new_refresh)
        self.last_refresh_error = None
        log.info("access token renewed")
        return self.token()


def _write_secret(path: Path, value: str) -> None:
    """Write via a temp file and rename, so a reader never sees half a token."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # "bff.tmp.jwt", not "bff.jwt.tmp": the temp file keeps the extension the
    # gitignore matches on, so even a crash mid-write leaves nothing committable.
    tmp = path.with_name(f"{path.stem}.tmp{path.suffix}")
    tmp.write_text(value.strip() + "\n", encoding="utf-8")
    tmp.replace(path)


def build(settings) -> TokenSource:
    if settings.genie_bff_mode == "admin":
        return NoToken()
    # QA and prod: the ONLY token is the one the dashboard forwards with each
    # request. A token file there would answer every user as whoever's login sat
    # in it — so outside local development the file is never read, even if present.
    if settings.genie_env != "local":
        return NoToken()
    return RefreshingFileToken(settings.genie_bff_token_file, settings.genie_bff_refresh_file,
                               settings.bff_base, settings.genie_bff_timeout_s,
                               settings.genie_bff_portal_id)
