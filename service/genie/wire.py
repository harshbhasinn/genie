"""What leaves Genie for the browser, in one place: plain JSON, field names in camelCase.

Genie's code names things in snake_case (screen_id, narrated_by); the frontend, like the
BFF, reads camelCase (screenId, narratedBy). Responses are converted here, on the way
out, so no module has to think about it - and so the two endpoints cannot drift apart.

Only KEYS change, never values: "LINES_SKIPPED_NO_RATE" or "main_wallet_balance" as a
value stays exactly as it is. This is safe because no response uses data as a key
(tests/test_contract.py checks every response of every golden question).

One request, one JSON response - no streaming. A refusal is a non-2xx status with
{code, message, retryable}; an answer is a 200.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def camel_keys(value):
    if isinstance(value, dict):
        return {camel(k) if isinstance(k, str) else k: camel_keys(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [camel_keys(v) for v in value]
    return value


def reply(payload: dict, status: int = 200, headers: dict | None = None) -> JSONResponse:
    """A JSON response, keys in camelCase."""
    return JSONResponse(camel_keys(payload), status_code=status, headers=headers)


def refuse(status: int, code: str, message: str, *, retry_after: int | None = None) -> JSONResponse:
    """Every refusal, from either endpoint: {code, message, retryable}, plus Retry-After when
    waiting helps."""
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    # Retrying helps after a limit (429), a passing fault (500) or a busy dependency (503);
    # not after bad input (4xx) or data that breaks its contract (502).
    return reply({"code": code, "message": message, "retryable": status in (429, 500, 503)},
                 status=status, headers=headers)
