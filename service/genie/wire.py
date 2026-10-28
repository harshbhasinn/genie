"""What leaves Genie for the browser, in one place: field names in camelCase.

Genie's code names things in snake_case (screen_id, narrated_by); the frontend, like the
BFF, reads camelCase (screenId, narratedBy). Responses are converted here, on the way
out, so no module has to think about it - and so the two endpoints cannot drift apart.

Only KEYS change, never values: "LINES_SKIPPED_NO_RATE" or "main_wallet_balance" as a
value stays exactly as it is. This is safe because no response uses data as a key
(tests/test_contract.py checks every event of every stream).
"""
from __future__ import annotations

import json


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def camel_keys(value):
    if isinstance(value, dict):
        return {camel(k) if isinstance(k, str) else k: camel_keys(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [camel_keys(v) for v in value]
    return value


def sse(event: str, payload: dict) -> str:
    """One server-sent event, keys in camelCase."""
    return f"event: {event}\ndata: {json.dumps(camel_keys(payload), ensure_ascii=False)}\n\n"
