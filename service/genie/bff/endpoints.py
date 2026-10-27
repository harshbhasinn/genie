"""The BFF endpoints Genie reads, by name. The paths are in ontology/endpoints.yaml.

Code asks for an endpoint by its key - endpoint("wallet.balance") - and gets the path
template back. A key that is not in the file fails at import, so a typo cannot reach a
request. The file's own rules (GET only, /api/v1 only, each path once) are checked here.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from ..config import REPO_ROOT

ENDPOINTS_FILE = REPO_ROOT / "ontology" / "endpoints.yaml"
API_PREFIX = "/api/v1/"


class EndpointError(Exception):
    pass


@lru_cache(maxsize=1)
def endpoints() -> dict[str, str]:
    """Every endpoint: key -> path template."""
    doc = yaml.safe_load(ENDPOINTS_FILE.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for name, entry in (doc.get("endpoints") or {}).items():
        if set(entry or {}) - {"get", "for"} or not (entry or {}).get("get"):
            raise EndpointError(f"{name}: an endpoint is a `get` path and a `for` line - Genie only reads")
        path = str(entry["get"])
        if not path.startswith(API_PREFIX):
            raise EndpointError(f"{name}: {path} is not under {API_PREFIX}")
        if path in out.values():
            raise EndpointError(f"{name}: {path} is listed twice")
        out[name] = path
    return out


def endpoint(name: str) -> str:
    try:
        return endpoints()[name]
    except KeyError:
        raise EndpointError(f"no endpoint {name!r} in {ENDPOINTS_FILE.name}") from None
