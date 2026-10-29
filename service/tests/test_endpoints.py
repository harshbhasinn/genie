"""The endpoint file is the only place a BFF path is written - and it agrees with the spec.

  - every path in ontology/endpoints.yaml is in reference/bff-openapi.json with its method
  - the only POSTs are the read-only ones named here, each checked against the guide
  - every entry is used by the code: a dead entry is an endpoint nobody reads
  - no path is spelled in the service's code
  - the file's own rules hold: reads only, /api/v1 only, each path once
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from genie.bff import endpoints as registry
from genie.bff.endpoints import EndpointError, endpoint, endpoints, method_of

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "service" / "genie"
SPEC = json.loads((ROOT / "reference" / "bff-openapi.json").read_text(encoding="utf-8"))
USED = re.compile(r'endpoint\("([^"]+)"\)')
PATH = re.compile(r"/api/v1/[A-Za-z{]")


def code_files():
    return [p for p in CODE.rglob("*.py") if p.name != "endpoints.py"]


# The POSTs Genie may send. Each is a READ: CAMPAIGN_CREATION_GUIDE.md §7.2 (the search
# reads availability) and §7.5 ("Nothing is saved. No campaignId is sent, so billing
# writes nothing"). Adding one here is a reviewed decision, not an edit to the yaml.
READ_ONLY_POSTS = {
    "campaigns.availability_search": "/api/v1/campaigns/availability-search",
    "campaigns.simulate_cost": "/api/v1/campaigns/simulate-cost",
}


def test_every_endpoint_is_in_the_spec_with_its_method():
    missing = [f"{name}: {method_of(name).upper()} {path}" for name, path in endpoints().items()
               if method_of(name) not in SPEC["paths"].get(path, {})]
    assert not missing, f"not in the spec: {missing}"


def test_the_only_posts_are_the_read_only_ones():
    posts = {name: path for name, path in endpoints().items() if method_of(name) == "post"}
    assert posts == READ_ONLY_POSTS


def test_every_endpoint_is_used_and_every_use_is_listed():
    used = {name for p in code_files() for name in USED.findall(p.read_text(encoding="utf-8"))}
    assert set(endpoints()) - used == set(), "listed but never read"
    assert used - set(endpoints()) == set(), "read but not listed"


def test_no_path_is_written_in_the_code():
    found = [f"{p.relative_to(CODE)}:{i}" for p in code_files()
             for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if PATH.search(line)]
    assert not found, f"a BFF path belongs in ontology/endpoints.yaml: {found}"


@pytest.mark.parametrize("body, why", [
    ("x:\n  post: /api/v1/campaigns\n", "only reads"),
    ("x:\n  post: /api/v1/campaigns\n  read_only: false\n", "only reads"),
    ("x:\n  get: /api/v1/me\n  post: /api/v1/me\n", "only reads"),
    ("x:\n  get: /api/v2/campaigns\n", "not under /api/v1/"),
    ("x:\n  get: /api/v1/me\ny:\n  get: /api/v1/me\n", "listed twice"),
])
def test_the_files_rules_are_enforced(tmp_path, monkeypatch, body, why):
    f = tmp_path / "endpoints.yaml"
    f.write_text("endpoints:\n" + "".join("  " + line + "\n" for line in body.splitlines()), encoding="utf-8")
    monkeypatch.setattr(registry, "ENDPOINTS_FILE", f)
    endpoints.cache_clear()
    try:
        with pytest.raises(EndpointError, match=why):
            endpoints()
    finally:
        endpoints.cache_clear()


def test_an_unknown_name_fails_loudly():
    with pytest.raises(EndpointError, match="no endpoint 'wallet.balanse'"):
        endpoint("wallet.balanse")
