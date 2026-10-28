"""The calculator's engine is EXACT - checked three ways that do not trust it:

  1. the worked example in the backend spec, number for number
  2. a brute force that tries EVERY schedule (no solver) on hundreds of small random
     markets - plan 1, the status, what a missed target would cost, and the alternatives
  3. an independent full model (one variable per line per date) on medium markets

plus every rule on every plan, the edge cases, and a speed guard at today's size.
"""
from __future__ import annotations

import itertools
import math
import random
import time

import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, milp

from genie.planner import model
from genie.planner.model import Alternatives, Line, Problem, solve, spread

# ── the worked example (design/calculator/availability-api-spec.md section 10) ──

EXAMPLE = (  # contract, IPM, VPM, daily reach, free per date (Nov 1-15)
    ("K-DEL-1", 1000, 1800, 120, [4] * 10 + [0] * 5),
    ("K-DEL-1", 900, 1600, 90, [4] * 15),
    ("K-NOI-1", 800, 1400, 80, [6] * 10 + [0] * 5),
    ("K-GGN-1", 1200, 2000, 100, [10] * 15),
)
EXAMPLE_LINES = tuple(Line(c, (3 * i + 2 * v) * 100, r, tuple(f)) for c, i, v, r, f in EXAMPLE)
BUDGET30 = 35000 * 100 * 30


def rs(cost30: int) -> float:
    return round(cost30 / 3000, 2)


def totals(plan: dict) -> dict[int, int]:
    return {r: sum(xs) for r, xs in plan["x"].items()}


def test_worked_example_with_a_target():
    p = Problem("j", EXAMPLE_LINES, 15, BUDGET30, 9000)
    out = solve(p)
    assert out["status"] == model.TARGET_MET
    plan = out["plans"][0]
    assert (rs(plan["cost30"]), plan["reach"]) == (17920.00, 9000)
    assert totals(plan) == {0: 40, 1: 12, 2: 39}
    assert plan["x"][1] == [1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1]   # Nov 11-15 play

    alts = solve(p, Alternatives("cost", (tuple(plan["contracts"]),), plan["cost30"],
                                 plan["reach"], 2, within_pct=10))
    assert [(rs(a["cost30"]), a["contracts"]) for a in alts["plans"]] == [(18020.00, ["K-DEL-1"])]
    wide = solve(p, Alternatives("cost", (tuple(plan["contracts"]),), plan["cost30"],
                                 plan["reach"], 2, within_pct=100))
    assert [(rs(a["cost30"]), a["contracts"]) for a in wide["plans"]] == [
        (18020.00, ["K-DEL-1"]), (21040.00, ["K-GGN-1", "K-NOI-1"])]


def test_worked_example_budget_only_takes_the_cheapest_of_the_most_reach():
    p = Problem("j", EXAMPLE_LINES, 15, BUDGET30, None)
    out = solve(p)
    assert out["status"] == model.BUDGET_PLAN
    plan = out["plans"][0]
    # 16,540 is the most reach; of the plans that reach it, Rs 34,960 is the cheapest
    assert (rs(plan["cost30"]), plan["reach"]) == (34960.00, 16540)
    alts = solve(p, Alternatives("reach", (tuple(plan["contracts"]),), plan["cost30"],
                                 plan["reach"], 2))
    assert [(rs(a["cost30"]), a["reach"], a["contracts"]) for a in alts["plans"]] == [
        (34956.67, 15830, ["K-DEL-1", "K-GGN-1"]), (31000.00, 15000, ["K-DEL-1", "K-NOI-1"])]


# ── 2. brute force: every schedule, no solver ─────────────────────────────────

def all_schedules(lines, days):
    """Every x, as (cost30, reach, contracts used, every coverable date plays)."""
    cells = [(r, t) for r in range(len(lines)) for t in range(days)]
    coverable = [t for t in range(days) if any(ln.free[t] for ln in lines)]
    out = []
    for combo in itertools.product(*(range(lines[r].free[t] + 1) for r, t in cells)):
        x = dict(zip(cells, combo))
        y = [sum(x[(r, t)] for t in range(days)) for r in range(len(lines))]
        plays = all(any(x[(r, t)] for r in range(len(lines))) for t in coverable)
        out.append((sum(ln.price30 * y[r] for r, ln in enumerate(lines)),
                    sum(ln.reach * y[r] for r, ln in enumerate(lines)),
                    frozenset(lines[r].contract for r in range(len(lines)) if y[r]), plays))
    return out, bool(coverable)


def cheapest(cands):
    """Least cost, then most reach."""
    return min(cands, key=lambda c: (c[0], -c[1]), default=None)


def most_reach(cands):
    """Most reach, then least cost."""
    return min(cands, key=lambda c: (-c[1], c[0]), default=None)


def expected_primary(scheds, has_cover, budget30, target):
    ok = [s for s in scheds if s[3]]
    if not has_cover:
        return model.NO_INVENTORY, None, None
    in_budget = [s for s in ok if s[0] <= budget30]
    if target:
        hit = cheapest([s for s in in_budget if s[1] >= target])
        if hit:
            return model.TARGET_MET, hit, None
        need = cheapest([s for s in ok if s[1] >= target])
        best = most_reach(in_budget)
        if best:
            return model.TARGET_OUT_OF_REACH, best, need
        return model.BUDGET_TOO_LOW, None, cheapest(ok)
    best = most_reach(in_budget)
    if best:
        return model.BUDGET_PLAN, best, None
    return model.BUDGET_TOO_LOW, None, cheapest(ok)


def random_market(rng, max_lines=3, max_days=3, max_free=2, contracts="AB"):
    lines, days = [], rng.randint(1, max_days)
    for _ in range(rng.randint(1, max_lines)):
        lines.append(Line(rng.choice(contracts), rng.randint(1, 9) * 30, rng.randint(1, 9),
                          tuple(rng.randint(0, max_free) for _ in range(days))))
    return tuple(lines), days


def test_plan_one_matches_a_brute_force_on_400_random_markets():
    rng = random.Random(7)
    checked = {s: 0 for s in (model.TARGET_MET, model.BUDGET_PLAN, model.TARGET_OUT_OF_REACH,
                              model.BUDGET_TOO_LOW, model.NO_INVENTORY)}
    for case in range(400):
        lines, days = random_market(rng)
        scheds, has_cover = all_schedules(lines, days)
        top_cost = max(s[0] for s in scheds) or 1
        top_reach = max(s[1] for s in scheds) or 1
        budget30 = rng.randint(0, top_cost)
        target = rng.choice([None, rng.randint(1, top_reach + 5)])
        out = solve(Problem(f"c{case}", lines, days, budget30, target))
        status, best, need = expected_primary(scheds, has_cover, budget30, target)
        assert out["status"] == status, (case, lines, budget30, target)
        checked[status] += 1
        if best:
            plan = out["plans"][0]
            assert (plan["cost30"], plan["reach"]) == best[:2], (case, lines, budget30, target)
        if status == model.TARGET_OUT_OF_REACH:
            assert out["target_needs30"] == (need[0] if need else None)
        if status == model.BUDGET_TOO_LOW and not target:
            assert out["min_budget30"] == need[0]
    assert all(checked.values()), checked          # every outcome was exercised


def test_alternatives_match_a_brute_force():
    rng = random.Random(11)
    compared = 0
    for case in range(300):
        lines, days = random_market(rng, max_lines=4, max_days=2, contracts="ABC")
        scheds, has_cover = all_schedules(lines, days)
        top_cost = max(s[0] for s in scheds) or 1
        top_reach = max(s[1] for s in scheds) or 1
        budget30 = rng.randint(top_cost // 3, top_cost)
        target = rng.choice([None, rng.randint(1, top_reach)])
        p = Problem(f"a{case}", lines, days, budget30, target)
        first = solve(p)
        if not first["plans"]:
            continue
        plan1 = first["plans"][0]
        within = rng.choice([10, 50, 100])
        alts = solve(p, Alternatives(first["mode"], (tuple(plan1["contracts"]),),
                                     plan1["cost30"], plan1["reach"], 2, within))
        shown = [frozenset(plan1["contracts"])]
        for k in range(2):
            ok = [s for s in scheds if s[3] and s[0] <= budget30
                  and all(not used <= s[2] for used in shown)]      # drops >= 1 of each set
            if first["mode"] == "cost":
                ok = [s for s in ok if s[1] >= target
                      and s[0] <= plan1["cost30"] * (100 + within) // 100]
                want = cheapest(ok)
            else:
                ok = [s for s in ok if s[1] >= math.ceil(plan1["reach"] * (100 - within) / 100)]
                want = most_reach(ok)
            got = alts["plans"][k] if k < len(alts["plans"]) else None
            if want is None:
                assert got is None, (case, k)
                break
            assert got is not None and (got["cost30"], got["reach"]) == want[:2], (case, k)
            assert all(not used <= frozenset(got["contracts"]) for used in shown)
            shown.append(frozenset(got["contracts"]))
            compared += 1
    assert compared > 60, compared          # alternatives found and matched (not just "none")


# ── 3. an independent full model ──────────────────────────────────────────────

def full_model(lines, days, budget30, target):
    """One integer per line per date; every coverable date >= 1. Primary objective only."""
    n = len(lines)
    price = np.repeat([ln.price30 for ln in lines], days).astype(float)
    reach = np.repeat([ln.reach for ln in lines], days).astype(float)
    ub = np.array([f for ln in lines for f in ln.free], dtype=float)
    cons = [LinearConstraint(price, -np.inf, budget30)]
    for t in range(days):
        if any(ln.free[t] for ln in lines):
            a = np.zeros(n * days)
            a[[r * days + t for r in range(n)]] = 1
            cons.append(LinearConstraint(a, 1, np.inf))
    if target:
        cons.append(LinearConstraint(reach, target, np.inf))
    res = milp(price if target else -reach, constraints=cons, integrality=np.ones(n * days),
               bounds=Bounds(0, ub), options={"mip_rel_gap": 0})
    return None if res.status != 0 else round(res.fun)


def test_lean_engine_equals_the_full_model_on_medium_markets():
    rng = random.Random(3)
    for case in range(25):
        days = rng.randint(5, 12)
        lines = []
        for _ in range(rng.randint(5, 25)):
            top = rng.randint(1, 6)
            free = [top if rng.random() > 0.3 else 0 for _ in range(days)]
            lines.append(Line(rng.choice("ABCD"), rng.randint(50, 900) * 30,
                              rng.randint(10, 200), tuple(free)))
        lines = tuple(lines)
        cap = sum(ln.price30 * sum(ln.free) for ln in lines)
        budget30 = int(cap * rng.uniform(0.05, 0.6))
        target = rng.choice([None, int(sum(ln.reach * sum(ln.free) for ln in lines) * 0.2)])
        out = solve(Problem(f"m{case}", lines, days, budget30, target, time_limit_s=20))
        full = full_model(lines, days, budget30, target)
        if out["status"] in (model.TARGET_MET,):
            assert out["plans"][0]["cost30"] == full, case
        elif out["status"] == model.BUDGET_PLAN:
            assert out["plans"][0]["reach"] == -full, case
        else:
            assert full is None, (case, out["status"])


# ── every rule, on every plan ─────────────────────────────────────────────────

def test_every_plan_keeps_every_rule():
    rng = random.Random(5)
    for case in range(60):
        days = rng.randint(3, 10)
        lines = tuple(Line(rng.choice("ABC"), rng.randint(50, 900) * 30, rng.randint(10, 200),
                           tuple(rng.randint(0, 4) for _ in range(days))) for _ in range(rng.randint(2, 12)))
        budget30 = int(sum(ln.price30 * sum(ln.free) for ln in lines) * rng.uniform(0.05, 0.9))
        target = rng.choice([None, rng.randint(100, 3000)])
        p = Problem(f"r{case}", lines, days, budget30, target)
        first = solve(p)
        plans = list(first["plans"])
        if plans:
            alts = solve(p, Alternatives(first["mode"], (tuple(plans[0]["contracts"]),),
                                         plans[0]["cost30"], plans[0]["reach"], 2, 50))
            plans += alts["plans"]
        coverable = [t for t in range(days) if any(ln.free[t] for ln in lines)]
        for plan in plans:
            assert plan["cost30"] <= budget30
            for r, xs in plan["x"].items():
                assert all(0 <= v <= f for v, f in zip(xs, lines[r].free))
            for t in coverable:
                assert any(xs[t] for xs in plan["x"].values()), (case, t)
            assert plan["cost30"] == sum(lines[r].price30 * sum(xs) for r, xs in plan["x"].items())
            assert plan["reach"] == sum(lines[r].reach * sum(xs) for r, xs in plan["x"].items())
        for k in range(1, len(plans)):
            for earlier in plans[:k]:
                assert not set(earlier["contracts"]) <= set(plans[k]["contracts"])


def test_spread_is_even_and_keeps_within_free_screens():
    assert spread([0] * 5, (4,) * 5, 12) == [3, 3, 2, 2, 2]
    assert spread([1, 0, 0, 0], (1, 5, 5, 0), 7) == [1, 4, 3, 0]
    assert spread([0, 0], (2, 2), 0) == [0, 0]


# ── edge cases ────────────────────────────────────────────────────────────────

def test_no_free_screens_is_no_inventory():
    out = solve(Problem("e", (Line("A", 300, 5, (0, 0, 0)),), 3, 10 ** 6, None))
    assert out["status"] == model.NO_INVENTORY and out["plans"] == []


def test_budget_too_low_says_what_would_play_every_date():
    out = solve(Problem("e", EXAMPLE_LINES, 15, 1000 * 3000, None))
    assert out["status"] == model.BUDGET_TOO_LOW and out["plans"] == []
    # one Noida screen on Nov 1-10 (Rs 173.33) + one Delhi morning screen on Nov 11-15 (Rs 196.67)
    assert rs(out["min_budget30"]) == round(10 * 173.33333 + 5 * 196.66667, 2)


def test_a_target_beyond_the_budget_says_what_it_would_cost():
    out = solve(Problem("e", EXAMPLE_LINES, 15, 10000 * 3000, 9000))
    assert out["status"] == model.TARGET_OUT_OF_REACH
    assert rs(out["target_needs30"]) == 17920.00
    assert out["plans"][0]["cost30"] <= 10000 * 3000


def test_a_target_beyond_every_free_screen():
    out = solve(Problem("e", EXAMPLE_LINES, 15, BUDGET30, 10 ** 7))
    assert out["status"] == model.TARGET_OUT_OF_REACH
    assert out["target_needs30"] is None and out["target_possible"] is False


def test_a_solve_that_runs_out_of_time_is_never_shown(monkeypatch):
    real = model.milp

    def slow(*a, **kw):
        res = real(*a, **kw)
        res.status = 1                              # as HiGHS reports a time limit
        return res
    monkeypatch.setattr(model, "milp", slow)
    out = solve(Problem("e", EXAMPLE_LINES, 15, BUDGET30, 9000))
    assert out["status"] == model.NOT_PROVEN and out["plans"] == []


def test_the_digest_covers_everything_solved():
    p = Problem("j", EXAMPLE_LINES, 15, BUDGET30, 9000)
    assert model.digest(p) == model.digest(Problem("other-job", EXAMPLE_LINES, 15, BUDGET30, 9000))
    assert model.digest(p) != model.digest(Problem("j", EXAMPLE_LINES, 15, BUDGET30 + 30, 9000))
    assert model.digest(p) != model.digest(p, Alternatives("cost", (("K-DEL-1",),), 1, 1, 2))
    assert solve(p)["digest"] == model.digest(p)


# ── speed, at today's size ────────────────────────────────────────────────────

def market(cities, categories, seed, days=30):
    rng = random.Random(seed)
    lines = []
    for c in range(cities):
        size = 0.5 + rng.random()
        reach = {(k, d): (40 + rng.random() * 360) * size for k in range(categories) for d in range(4)}
        for ci in range(rng.randint(2, 3)):
            ipm = 400 + rng.random() * 1200
            for k in rng.sample(range(categories), k=rng.randint(categories // 3, 2 * categories // 3)):
                top = rng.randint(3, 25)
                for d in range(4):
                    rate = int(ipm * (0.8 + 0.45 * rng.random()))
                    free = [top] * days
                    if rng.random() < 0.4:
                        a = rng.randrange(days)
                        for t in range(a, min(days, a + rng.randint(2, days // 2))):
                            free[t] = max(0, free[t] - rng.randint(1, top))
                    lines.append(Line(f"K{c}-{ci}", (3 * rate + 2 * int(rate * 1.6)) * 100,
                                      round(reach[(k, d)]), tuple(free)))
    return tuple(lines)


def test_ten_cities_by_ten_categories_is_proven_within_the_time_limit():
    lines = market(10, 10, seed=10)
    cap = sum(ln.price30 * sum(ln.free) for ln in lines)
    reach = sum(ln.reach * sum(ln.free) for ln in lines)
    for budget, target in ((0.1, None), (0.9, 0.3)):
        p = Problem("s", lines, 30, int(cap * budget), int(reach * target) if target else None)
        started = time.perf_counter()
        out = solve(p)
        assert out["status"] in (model.BUDGET_PLAN, model.TARGET_MET)
        assert time.perf_counter() - started < 3.5, f"{len(lines)} lines took too long"


@pytest.mark.parametrize("bad", [0, -1])
def test_spread_refuses_more_than_free(bad):
    with pytest.raises(model._Unproven):
        spread([0], (1,), 2 - bad if bad else 2)
