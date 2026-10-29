"""POST /v1/genie/plan - the calculator endpoint, on Adgrid's booking model.

The headline test: twelve users plan AT THE SAME TIME through ONE shared solver process,
each with different data (the backend shows half of them only one city) and a different
budget. Every one must get exactly the plan they get when planning alone. Then: the
partner always comes from the verified user, a forged token gets the real user's view,
caches are per user and hold no plan and no token, a result that does not prove it
answers its request is refused, every plan is priced by simulate-cost and never shown
over budget, the real BFF reads are sent as the guide describes, and every failure has
a clear code.
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import time
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from genie.auth.tokens import NoToken
from genie.bff import BffClient, BffError
from genie.chat.session import MemorySessionStore
from genie.config import Settings
from genie.main import app
from genie.planner import model
from genie.planner.cache import PACKED, PlanningInputs, pack, unpack
from genie.planner.contract import (ContractError, PlanQuery, parse_availability, parse_quote,
                                    parse_rate_cards, parse_reach)
from genie.planner.engine import Planner, PlannerRefused, PlanUnavailable, _warm
from genie.planner.router import IST
from genie.planner.source import BffPlanningSource, FixtureReach, FixtureSource
from .test_wallet import QA, envelope, jwt

CLIENT = "c-infinity"
BRAND = "brand-fizz"
EXP = int(time.time()) + 600
USERS = {jwt(sub=f"u-{i}", azp="adg-dashboard", exp=EXP): f"u-{i}" for i in range(12)}
TOKENS = list(USERS)
FORGED = jwt(sub="u-0", azp="adg-dashboard", exp=EXP, forged=True)   # CLAIMS u-0
USERS[FORGED] = "u-1"                                                   # the BFF says u-1
TOMORROW = datetime.now(IST).date() + timedelta(days=1)


def window(days=15):
    return {"from": TOMORROW.isoformat(), "to": (TOMORROW + timedelta(days=days - 1)).isoformat()}


def body(**over):
    b = {"window": window(), "dayparts": ["MORNING", "EVENING"], "imageSlots": 3, "videoSlots": 2,
         "advertisedBrandId": BRAND, "budget": "35000", "target": 15000}
    b.update(over)
    return {k: v for k, v in b.items() if v is not None}


class ScopedSource(FixtureSource):
    """The demo data, scoped per user the way a backend may scope it: odd users see only
    Gurugram. Records every call: (read, token, client). `surcharge` lifts every quote."""

    def __init__(self, *, delay: float = 0.0, fail: dict | None = None, mutate=None,
                 surcharge: Decimal = Decimal(1)):
        super().__init__()
        self.calls: list[tuple[str, str | None, str]] = []
        self.delay, self.fail, self.mutate, self.surcharge = delay, fail or {}, mutate, surcharge

    def _record(self, kind, token, client_id):
        self.calls.append((kind, token, client_id))
        if kind in self.fail:
            raise self.fail[kind]

    async def availability(self, token, client_id, q):
        self._record("free", token, client_id)
        await asyncio.sleep(self.delay)
        data = await super().availability(token, client_id, q)
        user = USERS.get(token, "u-0")
        if int(user.split("-")[1]) % 2:
            data["cities"] = [c for c in data["cities"] if c["city"] == "GGN"]
        return self.mutate("free", data) if self.mutate else data

    async def rate_cards(self, token, client_id):
        self._record("rates", token, client_id)
        data = await super().rate_cards(token, client_id)
        return self.mutate("rates", data) if self.mutate else data

    async def quote(self, token, client_id, brand_id, q, selections):
        self._record("quote", token, client_id)
        data = await super().quote(token, client_id, brand_id, q, selections)
        total = (Decimal(data["totalCost"]) * self.surcharge).quantize(Decimal("0.01"))
        return {"totalCost": str(total), "adgridToAdPartnerTotalCost": "1.00"}


class Reach(FixtureReach):
    def __init__(self, fail=None):
        super().__init__()
        self.fail = fail

    async def reach(self, token, client_id):
        if self.fail:
            raise self.fail
        return await super().reach(token, client_id)


def me(request: httpx.Request) -> httpx.Response:
    user = USERS.get(request.headers.get("authorization", "").removeprefix("Bearer "))
    if request.url.path != "/api/v1/me" or user is None:
        return httpx.Response(401, json=envelope(None, success=False))
    return httpx.Response(200, json=envelope({
        "userId": user, "clientId": CLIENT, "primaryRole": "AD_PARTNER_ADMIN",
        "grants": [{"clientId": CLIENT, "clientName": "Infinity", "role": "AD_PARTNER_ADMIN",
                    "permissions": []}]}))


def install(source, planner: Planner | None = None, *, env: str = "local", bff: bool = True,
            reach=None):
    app.state.settings = Settings(_env_file=None, genie_env=env)
    app.state.tokens = NoToken()
    app.state.bff = BffClient(QA, transport=httpx.MockTransport(me)) if bff else None
    app.state.sessions = MemorySessionStore()
    app.state.planner = planner or Planner(2, max_in_flight=20, per_user_per_min=100,
                                           executor=ThreadPoolExecutor(2))
    app.state.plan_inputs = (PlanningInputs(app.state.sessions.kv, source, reach or Reach())
                             if source is not None else None)
    return app.state


def result(r: httpx.Response) -> dict:
    """The calculator's one JSON answer."""
    assert r.headers["content-type"].startswith("application/json"), r.text[:200]
    return r.json()


async def post(c: httpx.AsyncClient, token: str | None, payload: dict) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await c.post("/v1/genie/plan", json=payload, headers=headers)


def run(*calls):
    """POST each (token, payload) concurrently; responses in order."""
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://genie", timeout=120) as c:
            return await asyncio.gather(*(post(c, t, p) for t, p in calls))
    return asyncio.run(go())


def one(token, payload):
    return run((token, payload))[0]


def plans_of(r: httpx.Response) -> list[dict]:
    return result(r)["plans"]


# ── end to end, on the demo data ──────────────────────────────────────────────

def test_a_plan_is_a_campaign_the_wizard_can_book():
    install(ScopedSource())
    r = one(TOKENS[0], body())
    assert r.status_code == 200
    answer = result(r)
    assert set(answer) == {"requestId", "status", "window", "dayparts", "imageSlots", "videoSlots",
                           "advertisedBrandId", "budget", "target", "reachVersion", "notices",
                           "plans", "solver", "latencyMs"}
    assert answer["status"] == "TARGET_MET" and answer["notices"] == [] and len(answer["plans"]) == 1
    assert answer["dayparts"] == ["MORNING", "EVENING"] and answer["reachVersion"] == "demo-reach-1"
    p1 = plans_of(r)[0]
    # 5 Delhi grocery screens: (3x900 + 2x1,600 + 3x1,000 + 2x1,800) / 30 x 15 = Rs 6,250 each,
    # reach (90 + 120) x 15 = 3,150 each - the cheapest way to 15,000 (checked by brute force
    # in test_planner_engine.py)
    assert (p1["cost"], p1["budgetLeft"], p1["reach"], p1["screens"]) == ("31250.00", "3750.00", 15750, 5)
    assert p1["label"] == "Cheapest plan that reaches 15,000" and p1["cities"] == ["Delhi"]
    assert p1["lines"] == [{"city": "DEL", "cityName": "Delhi", "shopType": "GROCERY",
                            "shopTypeName": "Grocery", "screens": 5, "reach": 15750,
                            "listCost": "31250.00"}]
    assert p1["campaign"] == {
        "startDate": TOMORROW.isoformat(), "endDate": (TOMORROW + timedelta(days=14)).isoformat(),
        "timeSlots": ["MORNING", "EVENING"], "imageSlotsRequested": 3, "videoSlotsRequested": 2,
        "deviceTier": "NON_PREMIUM", "advertisedBrandId": BRAND,
        "selections": [{"city": "DEL", "shopType": "GROCERY", "deviceCount": 5}]}
    assert app.state.planner.in_flight == 0                     # released when the stream ended


def test_alternatives_leave_out_a_city_and_say_how_they_compare():
    install(ScopedSource())
    r = one(TOKENS[0], body(target=None, alternatives=2))
    p1, p2, p3 = plans_of(r)
    assert p1["label"] == "Most reach for ₹35,000" and set(p1["cities"]) == {"Delhi", "Mumbai", "Noida"}
    assert (p1["cost"], p1["reach"]) == ("34175.00", 16500)
    assert p2["label"] == "Without Noida" and p2["vsPlan1"] == {"cost": "-350.00", "reach": -75}
    assert p3["label"] == "Without Mumbai" and p3["vsPlan1"] == {"cost": "+700.00", "reach": -300}
    for p in (p2, p3):
        assert float(p["cost"]) <= 35000 and p["reach"] >= 0.9 * p1["reach"]


def test_the_quote_is_the_price_and_a_plan_over_budget_is_never_shown():
    """Surcharge days lift every quote 10%: plan 1 is solved again under a tighter cap until
    its QUOTE fits, and that quote is the price shown."""
    source = ScopedSource(surcharge=Decimal("1.10"))
    install(source)
    r = one(TOKENS[0], body(target=None))
    p1 = plans_of(r)[0]
    assert float(p1["cost"]) <= 35000
    assert p1["note"].startswith("The price includes ₹")
    assert sum(1 for kind, *_ in source.calls if kind == "quote") >= 2      # priced, re-solved, priced
    source = ScopedSource(surcharge=Decimal("2.9"))
    install(source)
    r = one(TOKENS[0], body(target=None, budget="7000"))
    answer = result(r)
    assert answer["status"] == "QUOTE_OVER_BUDGET" and plans_of(r) == []
    assert "QUOTE_OVER_BUDGET" in {n["code"] for n in answer["notices"]}


@pytest.mark.parametrize("factor", ["5", "0.2", "180"])
def test_a_quote_that_disagrees_with_the_rate_card_plans_nothing(factor):
    """180x is what a per-play rate read as a monthly one would look like: refused, never
    planned from."""
    install(ScopedSource(surcharge=Decimal(factor)))
    r = one(TOKENS[0], body(target=None))
    assert (r.status_code, r.json()["code"]) == (502, "BAD_BACKEND_DATA")
    assert app.state.planner.in_flight == 0


def test_the_quote_sends_one_row_per_daypart():
    q = PlanQuery(TOMORROW, TOMORROW, ("MORNING", "EVENING"), 1, 0)
    sent = q.quote_body(CLIENT, BRAND, [{"city": "DEL", "shopType": "GROCERY", "deviceCount": 2}])
    assert sent["selections"] == [
        {"city": "DEL", "shopType": "GROCERY", "deviceCount": 2, "timeSlot": "MORNING"},
        {"city": "DEL", "shopType": "GROCERY", "deviceCount": 2, "timeSlot": "EVENING"}]


def test_the_partner_side_of_the_price_never_reaches_the_user():
    install(ScopedSource())
    r = one(TOKENS[0], body())
    assert "adgrid" not in r.text.lower() and "1.00" not in r.text


# ── no mix-up: twelve users, one solver process, all at once ─────────────────

@pytest.fixture(scope="module")
def one_solver_process():
    # spawned, as production runs it (engine.Planner never forks)
    pool = ProcessPoolExecutor(max_workers=1, initializer=_warm,
                               mp_context=multiprocessing.get_context("spawn"))
    pool.submit(int).result()                                   # started and warm
    yield pool
    pool.shutdown()


def test_twelve_users_at_once_through_one_solver_process_each_get_their_own_plan(one_solver_process):
    requests = [(t, body(target=None, budget=str(8000 + 2500 * i))) for i, t in enumerate(TOKENS)]

    install(ScopedSource())                                     # each user alone, one by one
    alone = [plans_of(one(t, p)) for t, p in requests]

    source = ScopedSource(delay=0.02)                           # now all together, interleaved
    install(source, Planner(1, max_in_flight=20, per_user_per_min=100,
                            executor=one_solver_process))
    together = run(*requests)

    for i, (r, mine) in enumerate(zip(together, alone)):
        assert r.status_code == 200, r.text
        assert plans_of(r) == mine, f"user {i} got a different plan when others planned too"
        plan = plans_of(r)[0]
        assert float(plan["cost"]) <= 8000 + 2500 * i           # within THEIR budget
        if i % 2:
            assert plan["cities"] == ["Gurugram"]               # only what THEY may see
    assert sorted(t for kind, t, _ in source.calls if kind == "free") == sorted(TOKENS)
    assert {c for _, _, c in source.calls} == {CLIENT}


def test_the_partner_comes_from_the_verified_user_never_the_request():
    source = ScopedSource()
    install(source)
    r = one(TOKENS[0], body(clientId="someone-else"))
    assert r.status_code == 403 and r.json()["code"] == "NO_ACCESS"
    assert source.calls == []                                   # nothing was read at all
    assert one(TOKENS[0], body(clientId=CLIENT)).status_code == 200


def test_a_forged_token_gets_the_real_users_view():
    install(ScopedSource())
    plan = plans_of(one(FORGED, body(target=None)))[0]          # claims u-0; the BFF says u-1
    assert plan["cities"] == ["Gurugram"]                       # u-1's view, not u-0's
    keys = list(app.state.sessions.kv._items)
    assert any(":user:u-1:" in k for k in keys) and not any(":user:u-0:" in k for k in keys)


def test_caches_are_per_user_compressed_and_hold_no_plan_and_no_token():
    source = ScopedSource()
    state = install(source)
    one(TOKENS[0], body())
    one(TOKENS[0], body(budget="30000"))                        # same question: inputs cached
    one(TOKENS[2], body())                                      # same partner, another user
    assert [t for kind, t, _ in source.calls if kind == "free"] == [TOKENS[0], TOKENS[2]]
    assert [t for kind, t, _ in source.calls if kind == "quote"].count(TOKENS[0]) == 2  # never cached
    kv = state.sessions.kv._items
    plan_keys = [k for k in kv if ":plan:" in k]
    assert plan_keys and all(k.split(":plan:")[1].split(":")[1] == "user" for k in plan_keys)
    for key, (_, value) in kv.items():
        if ":plan:" in key:
            assert value.startswith(PACKED)
        text = json.dumps(unpack(value)) if value.startswith(PACKED) else value
        assert not any(t in text or t in key for t in TOKENS), "a token was stored"
        assert "selections" not in text and "budgetLeft" not in text, "a plan was stored"


def test_a_tampered_cache_value_is_refused_and_read_fresh():
    source = ScopedSource()
    state = install(source)
    one(TOKENS[0], body())
    kv = state.sessions.kv._items
    key = next(k for k in kv if ":plan:free:" in k)
    expires, value = kv[key]
    bad = unpack(value)
    bad["cities"][0]["shopTypes"][0]["count"] = -4
    kv[key] = (expires, pack(bad))
    assert one(TOKENS[0], body()).status_code == 200
    assert [k for k, *_ in source.calls].count("free") == 2     # read again, not planned from


class Tampering(Executor):
    """Runs the solve, then hands back a result for another job (or another problem)."""

    def __init__(self, field: str):
        self.field = field

    def submit(self, fn, /, *args, **kwargs):
        f: Future = Future()
        out = fn(*args, **kwargs)
        out[self.field] = "0" * 32
        f.set_result(out)
        return f


@pytest.mark.parametrize("field", ["job_id", "digest"])
def test_a_result_that_does_not_prove_it_is_this_requests_is_refused(field):
    install(ScopedSource(), Planner(1, max_in_flight=4, per_user_per_min=100,
                                    executor=Tampering(field)))
    r = one(TOKENS[0], body())
    assert r.status_code == 500 and r.json()["code"] == "PLAN_REFUSED"
    assert app.state.planner.in_flight == 0


# ── who may plan ──────────────────────────────────────────────────────────────

def test_an_unverified_caller_gets_nothing():
    source = ScopedSource()
    install(source, env="qa")
    assert one(None, body()).json()["code"] == "SIGNED_OUT"
    assert one("not-a-real-token", body()).status_code == 401
    install(source, env="qa", bff=False)                        # deployed, no BFF: no one is verified
    assert one(TOKENS[0], body()).status_code == 401
    assert source.calls == []


def test_a_local_run_without_a_bff_plans_with_the_demo_partner():
    source = ScopedSource()
    install(source, bff=False)
    assert one(None, body()).status_code == 200
    assert source.calls[0][2] == "local-demo"


# ── limits ────────────────────────────────────────────────────────────────────

def test_one_plan_at_a_time_per_user_a_few_a_minute_and_a_cap_per_task():
    now = [0.0]
    p = Planner(1, max_in_flight=2, per_user_per_min=3, executor=ThreadPoolExecutor(1),
                clock=lambda: now[0])
    p.claim("user:a")
    with pytest.raises(PlannerRefused) as e:
        p.claim("user:a")
    assert (e.value.status, e.value.code) == (429, "PLAN_IN_PROGRESS")
    p.claim("user:b")
    with pytest.raises(PlannerRefused) as e:
        p.claim("user:c")
    assert (e.value.status, e.value.code) == (503, "PLANNER_BUSY")
    p.release("user:a"); p.release("user:b")
    p.claim("user:a"); p.release("user:a")
    p.claim("user:a"); p.release("user:a")
    with pytest.raises(PlannerRefused) as e:                    # 4th in a minute
        p.claim("user:a")
    assert (e.value.status, e.value.code) == (429, "TOO_MANY_PLANS") and e.value.retry_after > 0
    now[0] = 61
    p.claim("user:a")                                           # a minute later: fine
    now[0] = 200
    p.claim("user:a")                                           # an abandoned claim expires


def test_limits_reach_the_caller_with_retry_after():
    install(ScopedSource(), Planner(1, max_in_flight=4, per_user_per_min=1,
                                    executor=ThreadPoolExecutor(1)))
    assert one(TOKENS[0], body()).status_code == 200
    r = one(TOKENS[0], body())
    assert r.status_code == 429 and r.json()["code"] == "TOO_MANY_PLANS"
    assert int(r.headers["Retry-After"]) > 0


# ── failures, each with a code ────────────────────────────────────────────────

@pytest.mark.parametrize("fail, status, code", [
    ({"rates": BffError("forbidden", "no")}, 403, "NO_RATE_ACCESS"),
    ({"free": BffError("forbidden", "no")}, 403, "NO_ACCESS"),
    ({"quote": BffError("forbidden", "no")}, 403, "NO_ACCESS"),
    ({"free": BffError("signed_out", "no")}, 401, "SIGNED_OUT"),
    ({"free": BffError("unavailable", "down")}, 503, "BACKEND_UNAVAILABLE"),
    ({"quote": BffError("unavailable", "down")}, 503, "BACKEND_UNAVAILABLE"),
    ({"rates": BffError("rejected", "bad")}, 400, "BACKEND_REFUSED"),
])
def test_backend_failures(fail, status, code):
    install(ScopedSource(fail=fail))
    r = one(TOKENS[0], body())
    assert (r.status_code, r.json()["code"]) == (status, code)
    assert app.state.planner.in_flight == 0


def test_no_reach_source_answer_is_a_clear_failure():
    install(ScopedSource(), reach=Reach(fail=BffError("unavailable", "down")))
    r = one(TOKENS[0], body())
    assert (r.status_code, r.json()["code"]) == (503, "BACKEND_UNAVAILABLE")


def test_data_that_breaks_the_contract_is_never_planned_from():
    def broken(kind, data):
        if kind == "free":
            data["cities"][0]["shopTypes"][0]["count"] = "lots"
        return data
    install(ScopedSource(mutate=broken))
    r = one(TOKENS[0], body())
    assert (r.status_code, r.json()["code"]) == (502, "BAD_BACKEND_DATA")


def test_cells_without_a_price_are_left_out_and_the_user_is_told():
    def no_delhi_grocery(kind, data):
        if kind == "rates":
            data["rateCards"] = [r for r in data["rateCards"]
                                 if not (r["city"] == "DEL" and r["shopType"] == "GROCERY")]
        return data
    install(ScopedSource(mutate=no_delhi_grocery))
    r = one(TOKENS[0], body())
    answer = result(r)
    assert {n["code"] for n in answer["notices"]} == {"LINES_SKIPPED_NO_RATE"}
    assert all(ln["city"] != "DEL" or ln["shopType"] != "GROCERY" for ln in plans_of(r)[0]["lines"])


def test_a_start_date_with_every_daypart_over_is_refused_with_the_earliest_start():
    def closed(kind, data):
        if kind == "free":
            data.update(startDateFullyClosed=True, earliestStartDate="2026-12-02")
        return data
    install(ScopedSource(mutate=closed))
    r = one(TOKENS[0], body())
    assert (r.status_code, r.json()["code"]) == (422, "START_DATE_CLOSED")
    assert "2026-12-02" in r.json()["message"]


def test_no_free_screens_is_a_clear_answer():
    install(ScopedSource())
    r = one(TOKENS[0], body(cities=["BLR"]))
    answer = result(r)
    assert answer["status"] == "NO_INVENTORY" and plans_of(r) == []


def test_the_calculator_says_when_it_is_not_connected():
    install(None)
    r = one(TOKENS[0], body())
    assert (r.status_code, r.json()["code"]) == (503, "CALCULATOR_NOT_CONNECTED")


@pytest.mark.parametrize("over, code", [
    ({"window": {"from": (TOMORROW - timedelta(days=3)).isoformat(),
                 "to": TOMORROW.isoformat()}}, "INVALID_WINDOW"),
    ({"window": {"from": TOMORROW.isoformat(),
                 "to": (TOMORROW - timedelta(days=1)).isoformat()}}, "INVALID_WINDOW"),
    ({"window": window(91)}, "WINDOW_TOO_LONG"),
    ({"imageSlots": 0, "videoSlots": 0}, "INVALID_SLOTS"),
])
def test_bad_input_is_refused_with_a_code(over, code):
    source = ScopedSource()
    install(source)
    r = one(TOKENS[0], body(**over))
    assert (r.status_code, r.json()["code"]) == (422, code)
    assert source.calls == []


@pytest.mark.parametrize("over", [{"budget": "0"}, {"budget": "10.001"}, {"target": 0},
                                  {"alternatives": 3}, {"surprise": 1}, {"dayparts": ["NOON"]},
                                  {"dayparts": []}, {"advertisedBrandId": ""}])
def test_malformed_requests_are_refused(over):
    install(ScopedSource())
    assert one(TOKENS[0], body(**over)).status_code == 422


def test_the_brand_is_required():
    install(ScopedSource())
    b = body()
    del b["advertisedBrandId"]
    assert one(TOKENS[0], b).status_code == 422


def test_demo_data_can_never_run_outside_local():
    for field in ("genie_plan_source", "genie_plan_reach"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, genie_env="qa", **{field: "fixture"})
    assert Settings(_env_file=None, genie_env="qa", genie_plan_source="bff")
    assert Settings(_env_file=None, genie_env="local", genie_plan_source="fixture",
                    genie_plan_reach="fixture")


# ── the real BFF reads, sent as the guide describes ──────────────────────────

class RecordingBff:
    """Answers the three planning reads like the BFF, and records what was sent."""

    def __init__(self):
        self.sent: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        path = request.url.path
        if path == "/api/v1/campaigns/availability-search":
            return httpx.Response(200, json=envelope({"totalCount": 4, "cities": [
                {"city": "DEL", "totalDevices": 4, "shopTypes": [{"shopType": "GROCERY", "count": 4}]}],
                "startDateUnavailableSlots": [], "startDateFullyClosed": False}))
        if path == "/api/v1/client-rate-cards":
            return httpx.Response(200, json=envelope({"rateCards": [
                {"clientId": None, "city": "DEL", "shopType": "GROCERY", "timeSlot": "MORNING",
                 "imageSlotRate": "900.00", "videoSlotRate": "1600.00", "rateCardType": "BROADCAST"}]}))
        if path == "/api/v1/campaigns/simulate-cost":
            return httpx.Response(200, json=envelope({"totalCost": "2950.00",
                                                      "adgridToAdPartnerTotalCost": "999.00"}))
        return httpx.Response(404, json=envelope(None, success=False))


def test_the_bff_source_reads_as_the_user_and_saves_nothing():
    fake = RecordingBff()
    source = BffPlanningSource(BffClient(QA, portal_id="AGENCY_PORTAL", transport=httpx.MockTransport(fake)))
    q = PlanQuery(TOMORROW, TOMORROW + timedelta(days=14), ("MORNING",), 3, 2, cities=("DEL",))

    async def go():
        a = await source.availability("tok", CLIENT, q)
        r = await source.rate_cards("tok", CLIENT)
        s = await source.quote("tok", CLIENT, BRAND, q, [{"city": "DEL", "shopType": "GROCERY",
                                                         "deviceCount": 1}])
        return a, r, s
    a, r, s = asyncio.run(go())
    search, rates, quote = fake.sent
    assert (search.method, rates.method, quote.method) == ("POST", "GET", "POST")
    for req in fake.sent:
        assert req.headers["authorization"] == "Bearer tok" and req.headers["x-portal-id"] == "AGENCY_PORTAL"
    assert json.loads(search.content) == {
        "clientId": CLIENT, "startDate": q.start.isoformat(), "endDate": q.end.isoformat(),
        "timeSlots": ["MORNING"], "imageSlotsRequested": 3, "videoSlotsRequested": 2,
        "deviceTier": "NON_PREMIUM", "page": 0, "size": 200, "cities": ["DEL"]}
    assert dict(rates.url.params) == {"parentClientId": CLIENT, "rateCardType": "BROADCAST"}
    sent = json.loads(quote.content)
    assert "campaignId" not in sent and sent["advertisedBrandId"] == BRAND
    # one row per daypart, each with its timeSlot (§7.5; QA refuses a row without one)
    assert sent["selections"] == [{"city": "DEL", "shopType": "GROCERY", "deviceCount": 1,
                                   "timeSlot": "MORNING"}]
    assert parse_quote(s) == Decimal("2950.00")                 # the brand price only
    assert parse_availability(a, q).counts == {("DEL", "GROCERY"): 4}
    assert parse_rate_cards(r, CLIENT, BRAND).rates[("DEL", "GROCERY", "MORNING")].ipm_paise == 90000


# ── the backend contract, field by field ──────────────────────────────────────

Q = PlanQuery(TOMORROW, TOMORROW + timedelta(days=2), ("MORNING",), 3, 2)


def good_free():
    return {"totalCount": 5, "cities": [{"city": "DEL", "totalDevices": 5, "shopTypes": [
        {"shopType": "GROCERY", "count": 3}, {"shopType": "PHARMACY", "count": 2}]}],
            "startDateUnavailableSlots": ["MORNING"], "startDateFullyClosed": False,
            "earliestStartDate": None}


def good_rates():
    row = {"parentClientId": CLIENT, "city": "DEL", "shopType": "GROCERY", "timeSlot": "MORNING",
           "imageSlotRate": "900.50", "videoSlotRate": "1600", "rateCardType": "BROADCAST"}
    return {"rateCards": [{**row, "clientId": None},
                          {**row, "clientId": BRAND, "imageSlotRate": "1000.00"},
                          {**row, "clientId": "another-brand", "imageSlotRate": "1.00"},
                          {**row, "clientId": None, "timeSlot": "EVENING", "rateCardType": "TARGETED"}]}


def test_good_responses_parse_and_the_brands_own_price_wins():
    a = parse_availability(good_free(), Q)
    assert a.counts == {("DEL", "GROCERY"): 3, ("DEL", "PHARMACY"): 2}
    assert a.start_date_unavailable == ("MORNING",)
    card = parse_rate_cards(good_rates(), CLIENT, BRAND)
    assert card.rates == {("DEL", "GROCERY", "MORNING"): card.rates[("DEL", "GROCERY", "MORNING")]}
    assert card.rates[("DEL", "GROCERY", "MORNING")].ipm_paise == 100000 and card.brand_specific == 1
    partner_only = parse_rate_cards(good_rates(), CLIENT, "brand-without-rows")
    assert partner_only.rates[("DEL", "GROCERY", "MORNING")].ipm_paise == 90050
    reach = parse_reach({"reachVersion": "v1", "reach": [
        {"city": "DEL", "shopType": "GROCERY", "daypart": "MORNING", "dailyReachPerDevice": 90}]})
    assert reach.daily == {("DEL", "GROCERY", "MORNING"): 90}


@pytest.mark.parametrize("breakage", [
    lambda d: d["cities"][0]["shopTypes"][0].update(count=-1),
    lambda d: d["cities"][0]["shopTypes"][0].update(count=True),
    lambda d: d["cities"][0]["shopTypes"][0].pop("shopType"),
    lambda d: d["cities"][0]["shopTypes"].append({"shopType": "GROCERY", "count": 1}),
    lambda d: d["cities"][0].pop("city"),
    lambda d: d.update(cities="DEL"),
    lambda d: d.update(startDateUnavailableSlots=["NOON"]),
    lambda d: d.update(startDateFullyClosed="no"),
])
def test_availability_that_breaks_the_contract_is_refused(breakage):
    data = good_free()
    breakage(data)
    with pytest.raises(ContractError):
        parse_availability(data, Q)


@pytest.mark.parametrize("breakage", [
    lambda d: d["rateCards"][0].update(imageSlotRate="900.505"),
    lambda d: d["rateCards"][0].update(imageSlotRate=900),
    lambda d: d["rateCards"][0].update(videoSlotRate="-1"),
    lambda d: d["rateCards"][0].update(videoSlotRate="NaN"),
    lambda d: d["rateCards"][0].update(timeSlot="NOON"),
    lambda d: d["rateCards"].append(dict(d["rateCards"][0])),
    lambda d: d.update(rateCards={"a": 1}),
])
def test_rate_cards_that_break_the_contract_are_refused(breakage):
    data = good_rates()
    breakage(data)
    with pytest.raises(ContractError):
        parse_rate_cards(data, CLIENT, BRAND)


@pytest.mark.parametrize("bad", [{}, {"totalCost": 10}, {"totalCost": "-1"}, {"totalCost": "1.234"}])
def test_a_quote_that_breaks_the_contract_is_refused(bad):
    with pytest.raises(ContractError):
        parse_quote(bad)


# ── the architecture review's fixes ──────────────────────────────────────────

def test_solver_processes_are_spawned_never_forked_and_replaced_after_a_while():
    p = Planner(1, max_in_flight=1, per_user_per_min=1)
    try:
        assert p._pool._mp_context.get_start_method() == "spawn"
        assert p._pool._max_tasks_per_child == 500
    finally:
        p.close()


def test_a_solver_that_never_answers_is_replaced(monkeypatch):
    class Hung(Executor):
        def submit(self, fn, /, *args, **kwargs):
            return Future()                                     # never completes
    p = Planner(1, max_in_flight=1, per_user_per_min=1, executor=Hung())
    recycled = []
    p._recycle = recycled.append
    problem = model.Problem("h", (model.Cell("A", 30, 1, 1),), 30, None, time_limit_s=0.5)
    import genie.planner.engine as engine_module
    real = asyncio.wait_for

    async def quick(aw, timeout):
        return await real(aw, 0.05)
    monkeypatch.setattr(engine_module.asyncio, "wait_for", quick)
    with pytest.raises(PlanUnavailable):
        asyncio.run(p.solve(problem))
    assert recycled == ["a solver process did not answer in time"]


def test_the_rate_limit_table_forgets_idle_users():
    now = [0.0]
    p = Planner(1, max_in_flight=5, per_user_per_min=10, executor=ThreadPoolExecutor(1),
                clock=lambda: now[0])
    for i in range(1001):
        p.claim(f"user:{i}")
        p.release(f"user:{i}")
    now[0] = 120
    p.claim("user:new")
    assert len(p._recent) == 1


def test_genie_env_must_be_set_and_limits_must_be_sane(monkeypatch):
    monkeypatch.delenv("GENIE_ENV", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)                                # forgetting it stops the service
    for bad in ({"genie_plan_time_limit_s": 0}, {"genie_plan_alternatives_within_pct": 150},
                {"genie_plan_workers": 0}, {"genie_plan_source": "sql"}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, genie_env="local", **bad)


def test_the_solver_runs_in_a_separate_process_and_keeps_nothing(one_solver_process):
    """The same worker solves two different problems back to back; the second answer is
    exactly what that problem gives on its own - nothing carried over from the first."""
    cells = tuple(model.Cell("A", 300 * (i + 1), 10 * (i + 1), 2) for i in range(3))
    p1 = model.Problem("one", cells, 10 ** 6, None)
    p2 = model.Problem("two", cells[:1], 900, None)
    first = one_solver_process.submit(model.solve, p1).result()
    second = one_solver_process.submit(model.solve, p2).result()
    assert (first["job_id"], second["job_id"]) == ("one", "two")
    assert second == {**model.solve(p2), "ms": second["ms"]}
