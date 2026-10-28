"""POST /v1/genie/plan - the calculator endpoint.

The headline test: twelve users plan AT THE SAME TIME through ONE shared solver process,
each with different data (the backend shows half of them fewer contracts) and a different
budget. Every one must get exactly the plan they get when planning alone. Then: the
partner always comes from the verified user, a forged token gets the real user's view,
caches are per user and hold no plan and no token, a result that does not prove it
answers its request is refused, and every failure has a clear code.
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import time
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta

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
from genie.planner.contract import (AvailabilityQuery, ContractError, parse_availability,
                                    parse_rates_and_reach)
from genie.planner.engine import Planner, PlannerRefused, PlanUnavailable, _warm
from genie.planner.router import IST
from genie.planner.source import FixtureSource
from .test_wallet import QA, envelope, jwt

CLIENT = "c-infinity"
EXP = int(time.time()) + 600
USERS = {jwt(sub=f"u-{i}", azp="adg-dashboard", exp=EXP): f"u-{i}" for i in range(12)}
TOKENS = list(USERS)
FORGED = jwt(sub="u-0", azp="adg-dashboard", exp=EXP, forged=True)   # CLAIMS u-0
USERS[FORGED] = "u-1"                                                   # the BFF says u-1
TOMORROW = datetime.now(IST).date() + timedelta(days=1)


def window(days=15):
    return {"from": TOMORROW.isoformat(), "to": (TOMORROW + timedelta(days=days - 1)).isoformat()}


def body(**over):
    b = {"window": window(), "budget": "35000", "target": 9000, "imageSlots": 3, "videoSlots": 2}
    b.update(over)
    return {k: v for k, v in b.items() if v is not None}


class ScopedSource(FixtureSource):
    """The worked example's data, scoped per user the way a backend may scope it: odd
    users see only K-GGN-1. Records every call: (endpoint, token, client)."""

    def __init__(self, *, delay: float = 0.0, fail: dict | None = None, mutate=None):
        super().__init__()
        self.calls: list[tuple[str, str | None, str]] = []
        self.delay, self.fail, self.mutate = delay, fail or {}, mutate

    def _visible(self, token):
        user = USERS.get(token, "u-0")
        return None if int(user.split("-")[1]) % 2 == 0 else {"K-GGN-1"}

    async def availability(self, token, client_id, q):
        self.calls.append(("A", token, client_id))
        if "A" in self.fail:
            raise self.fail["A"]
        await asyncio.sleep(self.delay)
        data = await super().availability(token, client_id, q)
        allowed = self._visible(token)
        if allowed is not None:
            data["rows"] = [r for r in data["rows"] if r["contractCode"] in allowed]
        return self.mutate("A", data) if self.mutate else data

    async def rates_and_reach(self, token, client_id):
        self.calls.append(("B", token, client_id))
        if "B" in self.fail:
            raise self.fail["B"]
        data = await super().rates_and_reach(token, client_id)
        return self.mutate("B", data) if self.mutate else data


def me(request: httpx.Request) -> httpx.Response:
    user = USERS.get(request.headers.get("authorization", "").removeprefix("Bearer "))
    if request.url.path != "/api/v1/me" or user is None:
        return httpx.Response(401, json=envelope(None, success=False))
    return httpx.Response(200, json=envelope({
        "userId": user, "clientId": CLIENT, "primaryRole": "AD_PARTNER_ADMIN",
        "grants": [{"clientId": CLIENT, "clientName": "Infinity", "role": "AD_PARTNER_ADMIN",
                    "permissions": []}]}))


def install(source, planner: Planner | None = None, *, env: str = "local", bff: bool = True):
    app.state.settings = Settings(_env_file=None, genie_env=env)
    app.state.tokens = NoToken()
    app.state.bff = BffClient(QA, transport=httpx.MockTransport(me)) if bff else None
    app.state.sessions = MemorySessionStore()
    app.state.planner = planner or Planner(2, max_in_flight=20, per_user_per_min=100,
                                           executor=ThreadPoolExecutor(2))
    app.state.plan_inputs = (PlanningInputs(app.state.sessions.kv, source)
                             if source is not None else None)
    return app.state


def events(text: str) -> list[tuple[str, dict]]:
    out, name = [], None
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            out.append((name, json.loads(line[6:])))
    return out


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
    return [d for e, d in events(r.text) if e == "plan"]


# ── the worked example, end to end ────────────────────────────────────────────

def test_plan_one_then_the_alternatives_asked_for():
    install(ScopedSource())
    r = one(TOKENS[0], body(alternatives=2))
    assert r.status_code == 200
    names = [e for e, _ in events(r.text)]
    assert names == ["start", "result", "plan", "plan", "notice", "done"]
    result = dict(events(r.text))["result"]
    assert result["status"] == "TARGET_MET" and result["notices"] == []
    assert result["versions"] == {"ratesVersion": "demo-rates-1", "reachVersion": "demo-reach-1"}
    p1, p2 = plans_of(r)
    assert (p1["cost"], p1["budgetLeft"], p1["reach"], p1["screenDays"]) == ("17920.00", "17080.00", 9000, 91)
    assert p1["label"] == "Cheapest plan that reaches 9,000"
    assert p1["contracts"] == ["K-DEL-1", "K-NOI-1"] and p1["daysPlayed"] == 15
    evening = next(ln for ln in p1["lines"] if ln["daypart"] == "EVENING")
    d1, d10 = TOMORROW, TOMORROW + timedelta(days=9)
    span = (f"{d1:%b} {d1.day}–{d10.day}" if d1.month == d10.month
            else f"{d1:%b} {d1.day}–{d10:%b} {d10.day}")
    assert evening["schedule"] == f"4 a day {span}"
    assert (evening["cost"], evening["reach"]) == ("8800.00", 4800)
    assert p2["label"] == "Without K-NOI-1" and p2["vsPlan1"] == {"cost": "+100.00", "reach": 0}
    notice = [d for e, d in events(r.text) if e == "notice"][0]
    assert notice["code"] == "NO_MORE_ALTERNATIVES" and "10%" in notice["text"]
    assert app.state.planner.in_flight == 0                     # released when the stream ended


def test_dates_without_free_screens_are_named():
    install(ScopedSource())
    r = one(TOKENS[0], body(target=None, cities=["DEL"], dayparts=["EVENING"]))
    result = dict(events(r.text))["result"]
    assert result["status"] == "BUDGET_PLAN"
    codes = {n["code"]: n["text"] for n in result["notices"]}
    assert "NO_SCREENS_ON_DATES" in codes and codes["NO_SCREENS_ON_DATES"].startswith("No free screens on")
    assert plans_of(r)[0]["daysPlayed"] == 10


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
    requests = [(t, body(target=None, budget=str(4000 + 2500 * i))) for i, t in enumerate(TOKENS)]

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
        assert float(plan["cost"]) <= 4000 + 2500 * i           # within THEIR budget
        if i % 2:
            assert plan["contracts"] == ["K-GGN-1"]             # only what THEY may see
    # every read went out with the user's own token, for the partner `me` named
    assert sorted(t for kind, t, _ in source.calls if kind == "A") == sorted(TOKENS)
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
    assert plan["contracts"] == ["K-GGN-1"]                     # u-1's view, not u-0's
    keys = list(app.state.sessions.kv._items)
    assert any(":user:u-1:" in k for k in keys) and not any(":user:u-0:" in k for k in keys)


def test_caches_are_per_user_and_hold_no_plan_and_no_token():
    source = ScopedSource()
    state = install(source)
    one(TOKENS[0], body())
    one(TOKENS[0], body(budget="30000"))                        # same window: read from cache
    one(TOKENS[2], body())                                      # same partner, another user
    a_reads = [t for kind, t, _ in source.calls if kind == "A"]
    assert a_reads == [TOKENS[0], TOKENS[2]]                    # B never reused A's entry
    kv = state.sessions.kv._items
    plan_keys = [k for k in kv if ":plan:" in k]
    assert plan_keys and all(":plan:free:user:" in k or ":plan:rr:user:" in k for k in plan_keys)
    for key, (_, value) in kv.items():
        text = json.dumps(unpack(value)) if value.startswith(PACKED) else value
        assert not any(t in text or t in key for t in TOKENS), "a token was stored"
        assert '"lines"' not in text and "budgetLeft" not in text, "a plan was stored"


def test_a_tampered_cache_value_is_refused_and_read_fresh():
    source = ScopedSource()
    state = install(source)
    one(TOKENS[0], body())
    kv = state.sessions.kv._items
    key = next(k for k in kv if ":plan:free:" in k)
    expires, value = kv[key]
    bad = unpack(value)
    bad["rows"][0]["freeDevices"] = [99]                        # the wrong length
    kv[key] = (expires, pack(bad))
    assert one(TOKENS[0], body()).status_code == 200
    assert [k for k, *_ in source.calls].count("A") == 2        # read again, not planned from


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
    assert "plan" not in r.text.split("code")[0]
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
    ({"B": BffError("forbidden", "no")}, 403, "NO_RATE_ACCESS"),
    ({"A": BffError("forbidden", "no")}, 403, "NO_ACCESS"),
    ({"A": BffError("signed_out", "no")}, 401, "SIGNED_OUT"),
    ({"A": BffError("unavailable", "down")}, 503, "BACKEND_UNAVAILABLE"),
    ({"A": BffError("rejected", "bad")}, 400, "BACKEND_REFUSED"),
])
def test_backend_failures(fail, status, code):
    install(ScopedSource(fail=fail))
    r = one(TOKENS[0], body())
    assert (r.status_code, r.json()["code"]) == (status, code)
    assert app.state.planner.in_flight == 0


def test_data_that_breaks_the_contract_is_never_planned_from():
    def short(kind, data):
        if kind == "A":
            data["rows"][0]["freeDevices"] = data["rows"][0]["freeDevices"][:-1]
        return data
    install(ScopedSource(mutate=short))
    r = one(TOKENS[0], body())
    assert (r.status_code, r.json()["code"]) == (502, "BAD_BACKEND_DATA")


def test_lines_without_a_rate_are_skipped_and_the_user_is_told():
    def no_noida_rate(kind, data):
        if kind == "B":
            data["rates"] = [r for r in data["rates"] if r["contractCode"] != "K-NOI-1"]
        return data
    install(ScopedSource(mutate=no_noida_rate))
    r = one(TOKENS[0], body())
    result = dict(events(r.text))["result"]
    assert {n["code"] for n in result["notices"]} == {"LINES_SKIPPED_NO_RATE"}
    assert "K-NOI-1" not in plans_of(r)[0]["contracts"]


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
                                  {"alternatives": 3}, {"surprise": 1}, {"dayparts": ["NOON"]}])
def test_malformed_requests_are_refused(over):
    install(ScopedSource())
    assert one(TOKENS[0], body(**over)).status_code == 422


def test_demo_data_can_never_run_outside_local():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, genie_env="qa", genie_plan_source="fixture")
    assert Settings(_env_file=None, genie_env="local", genie_plan_source="fixture")


# ── the backend contract, field by field ──────────────────────────────────────

Q = AvailabilityQuery(TOMORROW, TOMORROW + timedelta(days=2), 3, 2)


def good_a():
    return {"clientId": CLIENT,
            "window": {"from": Q.start.isoformat(), "to": Q.end.isoformat(), "days": 3},
            "imageSlots": 3, "videoSlots": 2, "ratesVersion": "r1", "reachVersion": "v1",
            "generatedAt": "2026-10-28T09:00:00Z",
            "rows": [{"contractId": "k1", "contractCode": "K-1", "city": "DEL",
                      "category": "GROCERY", "daypart": "MORNING", "freeDevices": [1, 0, 2]}]}


def good_b():
    return {"clientId": CLIENT, "currency": "INR", "ratesVersion": "r1", "reachVersion": "v1",
            "rates": [{"contractId": "k1", "contractCode": "K-1", "city": "DEL", "category": "GROCERY",
                       "daypart": "MORNING", "imageSlotRatePerMonth": "900.50",
                       "videoSlotRatePerMonth": "1600"}],
            "reach": [{"city": "DEL", "category": "GROCERY", "daypart": "MORNING",
                       "dailyReachPerDevice": 90}]}


def test_good_responses_parse():
    a = parse_availability(good_a(), Q, CLIENT)
    assert a.rows[0].free == (1, 0, 2) and a.days == 3
    b = parse_rates_and_reach(good_b(), CLIENT)
    assert b.rates[("k1", "DEL", "GROCERY", "MORNING")].ipm_paise == 90050
    assert b.reach[("DEL", "GROCERY", "MORNING")] == 90


@pytest.mark.parametrize("breakage", [
    lambda d: d.update(clientId="someone-else"),
    lambda d: d["window"].update(days=4),
    lambda d: d["window"].update(to=(TOMORROW + timedelta(days=5)).isoformat()),
    lambda d: d.update(imageSlots=6),
    lambda d: d["rows"][0].update(freeDevices=[1, 0]),
    lambda d: d["rows"][0].update(freeDevices=[1, -1, 2]),
    lambda d: d["rows"][0].update(freeDevices=[1, True, 2]),
    lambda d: d["rows"][0].update(freeDevices=[1, 1.5, 2]),
    lambda d: d["rows"][0].update(daypart="NOON"),
    lambda d: d["rows"][0].pop("contractId"),
    lambda d: d["rows"].append(dict(d["rows"][0])),
    lambda d: d.pop("ratesVersion"),
])
def test_availability_that_breaks_the_contract_is_refused(breakage):
    data = good_a()
    breakage(data)
    with pytest.raises(ContractError):
        parse_availability(data, Q, CLIENT)


@pytest.mark.parametrize("breakage", [
    lambda d: d.update(currency="USD"),
    lambda d: d["rates"][0].update(imageSlotRatePerMonth="900.505"),
    lambda d: d["rates"][0].update(imageSlotRatePerMonth=900),
    lambda d: d["rates"][0].update(videoSlotRatePerMonth="-1"),
    lambda d: d["rates"][0].update(videoSlotRatePerMonth="NaN"),
    lambda d: d["reach"][0].update(dailyReachPerDevice="90"),
    lambda d: d["reach"].append(dict(d["reach"][0])),
    lambda d: d.update(rates=None),
])
def test_rates_that_break_the_contract_are_refused(breakage):
    data = good_b()
    breakage(data)
    with pytest.raises(ContractError):
        parse_rates_and_reach(data, CLIENT)


def test_the_solver_runs_in_a_separate_process_and_keeps_nothing(one_solver_process):
    """The same worker solves two different problems back to back; the second answer is
    exactly what that problem gives on its own - nothing carried over from the first."""
    lines = tuple(model.Line("A", 300 * (i + 1), 10 * (i + 1), (2, 2, 2)) for i in range(3))
    p1 = model.Problem("one", lines, 3, 10 ** 6, None)
    p2 = model.Problem("two", lines[:1], 3, 900 * 30, None)
    first = one_solver_process.submit(model.solve, p1).result()
    second = one_solver_process.submit(model.solve, p2).result()
    assert (first["job_id"], second["job_id"]) == ("one", "two")
    assert second == {**model.solve(p2), "ms": second["ms"]}


# ── the architecture review's fixes ──────────────────────────────────────────

def test_solver_processes_are_spawned_never_forked_and_replaced_after_a_while():
    p = Planner(1, max_in_flight=1, per_user_per_min=1)
    try:
        pool = p._pool
        assert pool._mp_context.get_start_method() == "spawn"
        assert pool._max_tasks_per_child == 500
    finally:
        p.close()


def test_a_solver_that_never_answers_is_replaced():
    class Hung(Executor):
        def submit(self, fn, /, *args, **kwargs):
            return Future()                                     # never completes
    p = Planner(1, max_in_flight=1, per_user_per_min=1, executor=Hung())
    recycled = []
    p._recycle = recycled.append
    problem = model.Problem("h", (model.Line("A", 30, 1, (1,)),), 1, 30, None, time_limit_s=0.5)

    async def go():
        import genie.planner.engine as engine_module
        real = engine_module.asyncio.wait_for

        async def quick(aw, timeout):
            return await real(aw, 0.05)
        engine_module.asyncio.wait_for = quick
        try:
            with pytest.raises(PlanUnavailable):
                await p.solve(problem)
        finally:
            engine_module.asyncio.wait_for = real
    asyncio.run(go())
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


def test_cached_inputs_are_stored_compressed():
    state = install(ScopedSource())
    one(TOKENS[0], body())
    values = [v for k, (_, v) in state.sessions.kv._items.items() if ":plan:" in k]
    assert values and all(v.startswith(PACKED) for v in values)
    assert unpack(pack({"a": [1, 2]})) == {"a": [1, 2]}


def test_genie_env_must_be_set_and_limits_must_be_sane(monkeypatch):
    monkeypatch.delenv("GENIE_ENV", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)                                # forgetting it stops the service
    for bad in ({"genie_plan_time_limit_s": 0}, {"genie_plan_alternatives_within_pct": 150},
                {"genie_plan_workers": 0}):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, genie_env="local", **bad)
