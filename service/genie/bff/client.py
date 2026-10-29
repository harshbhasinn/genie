"""The read path to the AdGrid dashboard BFF.

Every live figure Genie quotes comes through here, read AS THE USER: the token
is theirs, so the BFF's own @RequireAccess gate decides what they may see and
Genie can never see more than they could by clicking.

Four rules, each one a past incident somewhere:

  · QA only. The host is checked again here, not just in config, because this is
    the line that attaches a person's token to a request.
  · The token never reaches a log, an exception message or a response.
  · Errors keep their meaning. 401 is "signed out", 403 is "not yours to see",
    404 is "not found" — none of them is zero, and none of them is retried as if
    it were a network blip.
  · Nothing is cached across users. This client caches nothing at all.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import is_allowed_bff_host

log = logging.getLogger(__name__)


class BffError(Exception):
    """A read that did not produce data, with a kind the answer can be built from."""

    def __init__(self, kind: str, message: str, *, status: int | None = None,
                 endpoint: str | None = None):
        super().__init__(message)
        # signed_out | forbidden | not_found | rejected | unavailable | bad_response
        self.kind = kind
        self.status = status
        self.endpoint = endpoint    # the path TEMPLATE, never the filled-in path

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"BffError({self.kind}, {self.status}, {self.endpoint})"


@dataclass(frozen=True, slots=True)
class BffResponse:
    data: Any
    endpoint: str
    ms: int


class BffClient:
    def __init__(self, base: str, *, portal_id: str = "", timeout: float = 6.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not is_allowed_bff_host(base):
            raise ValueError("BffClient refuses a non-QA host")
        self._base = base.rstrip("/")
        headers = {"Accept": "application/json"}
        if portal_id:
            # Required on every call; the BFF answers PORTAL_ID_REQUIRED without it.
            headers["x-portal-id"] = portal_id
        # `transport` exists so tests can answer requests without a network.
        self._client = httpx.AsyncClient(base_url=self._base, timeout=timeout,
                                         transport=transport, headers=headers)

    @property
    def base(self) -> str:
        return self._base

    async def close(self) -> None:
        await self._client.aclose()

    async def get_all(self, token: str | None, reads: dict[str, dict]
                      ) -> tuple[dict[str, dict], dict[str, str]]:
        """Several reads at once, keyed by template: (data for those that answered,
        BffError kind for those that did not). One failure never sinks the others."""
        results = await asyncio.gather(*(self.get(t, token=token, **kw) for t, kw in reads.items()),
                                       return_exceptions=True)
        got: dict[str, dict] = {}
        problems: dict[str, str] = {}
        for template, res in zip(reads, results):
            if isinstance(res, BffError):
                problems[template] = res.kind
            elif isinstance(res, BaseException):
                problems[template] = "unavailable"
            else:
                got[template] = res.data or {}
        return got, problems

    async def get(self, template: str, *, token: str | None,
                  path_params: dict[str, str] | None = None,
                  params: dict[str, Any] | None = None) -> BffResponse:
        """GET `template` (a path from ontology/endpoints.yaml) and unwrap it.

        The template is what gets logged and reported, so a client id or user id
        never ends up in a log line either.
        """
        return await self._send("GET", template, token=token, path_params=path_params,
                                params=params)

    async def post_read(self, template: str, *, token: str | None, body: dict,
                        path_params: dict[str, str] | None = None) -> BffResponse:
        """POST to one of the READ-ONLY POST endpoints (the availability search, the price
        preview): a question too long for a query string. Nothing else may come through
        here - the endpoint file marks each one `read_only` and a test holds the list."""
        return await self._send("POST", template, token=token, path_params=path_params,
                                body=body)

    async def _send(self, method: str, template: str, *, token: str | None,
                    path_params: dict[str, str] | None = None,
                    params: dict[str, Any] | None = None,
                    body: dict | None = None) -> BffResponse:
        if not token:
            raise BffError("signed_out", "no token for this request", endpoint=template)
        path = template.format(**(path_params or {}))
        started = time.perf_counter()
        try:
            resp = await self._client.request(method, path, params=params, json=body,
                                              headers={"Authorization": f"Bearer {token}"})
        except httpx.TimeoutException:
            raise BffError("unavailable", "the dashboard API timed out", endpoint=template)
        except httpx.HTTPError as err:
            raise BffError("unavailable", f"the dashboard API could not be reached "
                                          f"({type(err).__name__})", endpoint=template)
        ms = int((time.perf_counter() - started) * 1000)
        log.info("bff %s %s -> %s in %sms", method, template, resp.status_code, ms)

        if resp.status_code == 401:
            raise BffError("signed_out", "the session has expired", status=401, endpoint=template)
        if resp.status_code == 403:
            raise BffError("forbidden", "not permitted for this user", status=403, endpoint=template)
        if resp.status_code == 404:
            raise BffError("not_found", "not found", status=404, endpoint=template)
        if 400 <= resp.status_code < 500:
            # The request itself was refused (400, 422...): most likely OUR parameters,
            # not the dashboard being down - so it is not reported as "unavailable".
            log.warning("BFF refused the request: %s %s", resp.status_code, template)
            raise BffError("rejected", f"the dashboard API refused the request ({resp.status_code})",
                           status=resp.status_code, endpoint=template)
        if resp.status_code >= 500:
            raise BffError("unavailable", f"the dashboard API answered {resp.status_code}",
                           status=resp.status_code, endpoint=template)
        try:
            body = resp.json()
        except ValueError:
            raise BffError("bad_response", "the response was not JSON",
                           status=resp.status_code, endpoint=template)
        if isinstance(body, dict) and body.get("code") == "PORTAL_ID_REQUIRED":
            raise BffError("bad_response", "GENIE_BFF_PORTAL_ID is not set in .env",
                           status=resp.status_code, endpoint=template)
        if isinstance(body, dict) and body.get("code") == "PORTAL_ACCESS_DENIED":
            # The portal id was accepted, but this account's role may not use that
            # portal — e.g. an agency user against ADOPS_PORTAL. Not a permission
            # problem with the data, so say exactly what to fix.
            raise BffError("forbidden", "this account's role is not permitted on the portal in "
                                        "GENIE_BFF_PORTAL_ID", status=resp.status_code,
                           endpoint=template)
        # The BFF wraps everything as {success, code, message, data}. A 200 with
        # success=false is a failure, not an empty answer.
        if isinstance(body, dict) and "success" in body:
            if body.get("success") is False:
                raise BffError("bad_response", str(body.get("message") or "request failed")[:200],
                               status=resp.status_code, endpoint=template)
            return BffResponse(body.get("data"), template, ms)
        return BffResponse(body, template, ms)
