"""POST /v1/genie/calculator, end to end against a fake BFF serving calculator-inputs.

No network. The promises:

  · the plan comes back priced as billing prices it, with the wizard prefill beside it
  · the read is made AS THE USER, for a client they work for, with lists as repeated params
  · the rates are cached per client and brand, sent back as knownRatesVersion, and replaced
    when the server's version moves; a server that kept them back for a version Genie does not
    hold is asked once more, in full
  · until the BFF serves calculator-inputs, the endpoint says so (503) and plans nothing
  · every failure keeps its meaning: signed out, not yours, refused, malformed, down
  · a Redis that fails costs the cache, never the plan
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from genie.auth.tokens import NoToken
from genie.calculator import source
from genie.chat.responder import Responder
from genie.chat.session import SessionStore
from genie.data.fields import today
from genie.main import app
from genie.ontology import load
from genie.store import MemoryKV, RedisKV
from .test_calculator import group, rate
from .test_wallet import CLIENT, client_for, envelope, jwt

REPO = Path(__file__).resolve().parents[2]
INPUTS = "/api/v1/campaigns/calculator-inputs"
TOKEN = jwt(sub="u-1", azp="adg-dashboard", exp=int(time.time()) + 300)
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BRAND = "brd-1"
START = today() + timedelta(days=1)


def body(**kw):
    return {"advertised_brand_id": BRAND, "start_date": START.isoformat(),
            "end_date": (START + timedelta(days=2)).isoformat(),
            "time_slots": ["MORNING", "EVENING"], "image_slots": 2, "video_slots": 1,
            "budget": "10000000", **kw}


class FakeBff:
    """`me` for u-1 at CLIENT, and calculator-inputs over whatever dates were asked for."""

    def __init__(self, version="r-1", devices=4, status=None, malformed=False, keep_back=False):
        self.version, self.devices = version, devices
        self.status, self.malformed, self.keep_back = status, malformed, keep_back
        self.reads: list[httpx.QueryParams] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/me":
            return httpx.Response(200, json=envelope({
                "userId": "u-1", "clientId": CLIENT, "primaryRole": "AD_PARTNER_ADMIN",
                "grants": [{"clientId": CLIENT, "clientName": "Infinity", "role": "AD_PARTNER_ADMIN",
                            "permissions": ["VIEW_CAMPAIGN"]}]}))
        if request.url.path != INPUTS:
            return httpx.Response(404, json=envelope(None, False))
        q = request.url.params
        self.reads.append(q)
        if self.status:
            return httpx.Response(self.status, json=envelope(None, False))
        start, end = date.fromisoformat(q["startDate"]), date.fromisoformat(q["endDate"])
        n = (end - start).days + 1
        days = [(start + timedelta(days=k)).isoformat() for k in range(n)]
        g = group("g1", devices=self.devices, n=n, slots=tuple(q.get_list("timeSlots")))
        if self.malformed:
            g["free"]["MORNING"]["image"] = g["free"]["MORNING"]["image"][:-1]
        unchanged = self.keep_back or q.get("knownRatesVersion") == self.version
        data = {"snapshotAt": "2026-10-28T11:04:12+05:30", "days": days,
                "versions": {"rates": self.version, "reach": None},
                "availability": {"groups": [g], "closedCells": []}, "surcharges": [],
                "ratesUnchanged": unchanged,
                "rates": None if unchanged else {
                    "unit": "INR per slot per month per daypart", "playsPerMonth": 5400,
                    "cells": [rate(slot=s) for s in ("MORNING", "AFTERNOON", "EVENING", "NIGHT")],
                    "missing": []}}
        if unchanged and q.get("knownRatesVersion") != self.version:
            self.keep_back = False                 # lied once; the retry gets everything
        return httpx.Response(200, json=envelope(data))


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


@pytest.fixture
def served(monkeypatch):
    """The BFF serves calculator-inputs (as it will once it is in the spec)."""
    monkeypatch.setattr(source, "endpoints", lambda: {source.INPUTS_ENDPOINT: INPUTS})


@contextmanager
def serve(ontology, fake: FakeBff, kv=None):
    kv = kv or MemoryKV()
    with TestClient(app) as c:
        app.state.ontology, app.state.provider = ontology, None
        app.state.tokens, app.state.bff = NoToken(), client_for(fake)
        app.state.sessions = SessionStore(kv)
        app.state.responder = Responder(ontology, None, bff=app.state.bff)
        yield c, kv


# ── the plan ──────────────────────────────────────────────────────────────


def test_a_plan_priced_as_billing_prices_it_with_the_prefill(ontology, served):
    with serve(ontology, FakeBff()) as (c, _):
        r = c.post("/v1/genie/calculator", headers=AUTH, json=body())
    assert r.status_code == 200, r.text
    out = r.json()
    p = out["plan"]
    assert p["screens"] == 4 and p["limited_by"] == "inventory" and p["optimal"] is True
    assert p["price"] == {"amount": "47520.00", "text": "₹47,520"}     # guide §12.3 example A
    assert p["plays"] == 4 * 3 * 180 * 3 * 2
    assert out["basis"] == "brand_price" and out["rates_version"] == "r-1"
    assert out["prefill"]["selections"] == [{"city": "DEL", "shopType": "GROCERY", "deviceCount": 4}]
    assert out["prefill"]["startDate"] == START.isoformat()


def test_the_read_is_the_users_own_with_repeated_list_params(ontology, served):
    fake = FakeBff()
    with serve(ontology, fake) as (c, _):
        c.post("/v1/genie/calculator", headers=AUTH, json=body(cities=["DEL", "GGN"]))
    (q,) = fake.reads
    assert q["clientId"] == CLIENT and q["advertisedBrandId"] == BRAND
    assert q.get_list("timeSlots") == ["MORNING", "EVENING"]
    assert q.get_list("cities") == ["DEL", "GGN"]
    assert q["deviceTier"] == "NON_PREMIUM" and "knownRatesVersion" not in q


def test_a_budget_too_small_plans_nothing_and_says_what_the_smallest_costs(ontology, served):
    with serve(ontology, FakeBff()) as (c, _):
        out = c.post("/v1/genie/calculator", headers=AUTH, json=body(budget="100")).json()
    assert out["plan"]["screens"] == 0 and out["prefill"] is None
    assert out["plan"]["smallest_price"]["amount"] == "11880.00"


# ── the rates cache ───────────────────────────────────────────────────────


def test_rates_are_cached_and_sent_back_as_the_known_version(ontology, served):
    fake = FakeBff()
    with serve(ontology, fake) as (c, kv):
        first = c.post("/v1/genie/calculator", headers=AUTH, json=body()).json()
        second = c.post("/v1/genie/calculator", headers=AUTH, json=body()).json()
        held = kv._items.get(f"genie:calc:rates:{CLIENT}:{BRAND}")
    assert fake.reads[1]["knownRatesVersion"] == "r-1"
    assert second["plan"] == first["plan"]
    assert held is not None and TOKEN not in held[1]


def test_a_moved_version_replaces_the_cached_rates(ontology, served):
    fake = FakeBff(version="r-1")
    with serve(ontology, fake) as (c, kv):
        c.post("/v1/genie/calculator", headers=AUTH, json=body())
        fake.version = "r-2"
        out = c.post("/v1/genie/calculator", headers=AUTH, json=body()).json()
        held = kv._items[f"genie:calc:rates:{CLIENT}:{BRAND}"][1]
    assert out["rates_version"] == "r-2" and '"version": "r-2"' in held


def test_rates_kept_back_for_a_version_genie_does_not_hold_are_read_again_in_full(ontology, served):
    kv = MemoryKV()
    with serve(ontology, FakeBff(version="r-1"), kv) as (c, _):
        c.post("/v1/genie/calculator", headers=AUTH, json=body())      # holds r-1
    fake = FakeBff(version="r-2", keep_back=True)                         # says "unchanged" for r-2
    with serve(ontology, fake, kv) as (c, _):
        r = c.post("/v1/genie/calculator", headers=AUTH, json=body())
    assert r.status_code == 200 and r.json()["rates_version"] == "r-2"
    assert [q.get("knownRatesVersion") for q in fake.reads] == ["r-1", None]


def test_a_failing_redis_costs_the_cache_never_the_plan(ontology, served):
    class Down:
        async def get(self, *a, **k): raise ConnectionError("down")
        async def set(self, *a, **k): raise ConnectionError("down")
        async def delete(self, *a, **k): raise ConnectionError("down")
        async def ping(self): raise ConnectionError("down")
        async def aclose(self): pass
    with serve(ontology, FakeBff(), RedisKV("redis://unused", client=Down())) as (c, _):
        r = c.post("/v1/genie/calculator", headers=AUTH, json=body())
    assert r.status_code == 200 and r.json()["plan"]["screens"] == 4


# ── failures keep their meaning ───────────────────────────────────────────


def test_until_the_bff_serves_it_the_calculator_says_so(ontology):
    fake = FakeBff()
    with serve(ontology, fake) as (c, _):
        r = c.post("/v1/genie/calculator", headers=AUTH, json=body())
    assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE"
    assert fake.reads == []


def test_without_a_token_nobody_is_asked(ontology, served):
    fake = FakeBff()
    with serve(ontology, fake) as (c, _):
        r = c.post("/v1/genie/calculator", json=body())
    assert r.status_code == 401 and fake.reads == []


def test_a_client_the_user_does_not_hold_is_refused_before_any_read(ontology, served):
    fake = FakeBff()
    with serve(ontology, fake) as (c, _):
        r = c.post("/v1/genie/calculator", headers=AUTH, json=body(client_id="someone-else"))
    assert r.status_code == 403 and fake.reads == []


@pytest.mark.parametrize("status, expect, code", [
    (403, 403, "FORBIDDEN"), (400, 422, "REJECTED"), (500, 503, "UNAVAILABLE"), (401, 401, "SIGNED_OUT"),
])
def test_bff_refusals_keep_their_meaning(ontology, served, status, expect, code):
    with serve(ontology, FakeBff(status=status)) as (c, _):
        r = c.post("/v1/genie/calculator", headers=AUTH, json=body())
    assert r.status_code == expect and r.json()["error"]["code"] == code


def test_a_malformed_snapshot_builds_no_plan(ontology, served):
    with serve(ontology, FakeBff(malformed=True)) as (c, _):
        r = c.post("/v1/genie/calculator", headers=AUTH, json=body())
    assert r.status_code == 502 and r.json()["error"]["code"] == "BAD_INPUTS"


@pytest.mark.parametrize("change, why", [
    (dict(start_date=(today() - timedelta(days=1)).isoformat()), "past"),
    (dict(end_date=(START - timedelta(days=1)).isoformat()), "ends before"),
    (dict(end_date=(START + timedelta(days=92)).isoformat()), "at most 92"),
    (dict(target_plays=1000), "not both"),
    (dict(budget=None), "not both and not neither"),
    (dict(image_slots=0, video_slots=0), "at least one"),
    (dict(time_slots=["MORNING", "MORNING"]), "each time slot once"),
])
def test_asks_that_make_no_plan_are_refused_before_any_read(ontology, served, change, why):
    fake = FakeBff()
    with serve(ontology, fake) as (c, _):
        r = c.post("/v1/genie/calculator", headers=AUTH, json=body(**change))
    assert r.status_code == 400 and why in r.json()["error"]["message"] and fake.reads == []
