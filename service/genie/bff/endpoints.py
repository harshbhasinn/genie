"""The BFF endpoints Genie reads, by name. The paths are in ontology/endpoints.yaml.

Code asks for an endpoint by its key - endpoint("wallet.balance") - and gets the path
template back. A key that is not in the file fails at import, so a typo cannot reach a
request. The file's own rules (reads only, /api/v1 only, each path once) are checked here.

Reads only: an entry is a `get`, or a `post` that says `read_only: true`. A few BFF reads
are POSTs because their question is long (the campaign availability search, the price
preview); they change nothing. tests/test_endpoints.py holds the list of POSTs that may
appear, so a new one is a reviewed change, never a quiet edit to this file.
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
def _load() -> tuple[dict[str, str], dict[str, str]]:
    doc = yaml.safe_load(ENDPOINTS_FILE.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    methods: dict[str, str] = {}
    for name, entry in (doc.get("endpoints") or {}).items():
        entry = entry or {}
        if set(entry) - {"get", "post", "read_only", "for"} or len({"get", "post"} & set(entry)) != 1:
            raise EndpointError(f"{name}: an endpoint is one `get` (or a read_only `post`) and a "
                                f"`for` line - Genie only reads")
        if "post" in entry and entry.get("read_only") is not True:
            raise EndpointError(f"{name}: a `post` must say read_only: true - Genie only reads")
        method = "get" if "get" in entry else "post"
        path = str(entry[method])
        if not path.startswith(API_PREFIX):
            raise EndpointError(f"{name}: {path} is not under {API_PREFIX}")
        if path in out.values():
            raise EndpointError(f"{name}: {path} is listed twice")
        out[name] = path
        methods[name] = method
    return out, methods


def endpoints() -> dict[str, str]:
    """Every endpoint: key -> path template."""
    return _load()[0]


endpoints.cache_clear = _load.cache_clear       # tests swap the file


def method_of(name: str) -> str:
    """'get', or 'post' for one of the read-only POSTs."""
    endpoint(name)
    return _load()[1][name]


def endpoint(name: str) -> str:
    try:
        return endpoints()[name]
    except KeyError:
        raise EndpointError(f"no endpoint {name!r} in {ENDPOINTS_FILE.name}") from None
