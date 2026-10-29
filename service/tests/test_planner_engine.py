"""The calculator's engine is EXACT - checked against brute forces that do not trust it.

The model is Adgrid's booking model (CAMPAIGN_CREATION_GUIDE.md §7.6): how many screens per
city x shop type, for the whole window. Checked:

  1. a worked example on the demo data, by hand
  2. a brute force over EVERY plan on hundreds of small random markets - the status, plan 1,
     what a missed target would cost, the smallest budget, and the alternatives
  3. every rule on every plan; the price and reach of a cell from the rate card (§12.2)
  4. the edge cases, and speed at today's size and at 100 cities x 20 shop types
"""
from __future__ import annotations

import itertools
import math
import random
import time
from datetime import date, timedelta
from decimal import Decimal

import pytest

from genie.planner import model
from genie.planner.contract import Availability, PlanQuery, Rate, RateCard, Reach
from genie.planner.engine import prepare
from genie.planner.model import Alternatives, Cell, Problem, solve

START = date(2026, 11, 1)


def rs(cost30: int) -> float:
    return round(cost30 / 3000, 2)


# ── 1. a cell's price and reach, from the rate card ──────────────────────────

def test_a_cells_price_and_reach_follow_the_rate_card():
    """§12.2: monthly rate x plays / 5,400 per daypart = rate / 30 per screen-day (60 s loop).
    3 image + 2 video, DEL grocery, MORNING (900 / 1,600) + EVENING (1,000 / 1,800), 15 days:
    (3x900 + 2x1,600 + 3x1,000 + 2x1,800) / 30 x 15 = Rs 6,250 a screen."""
    q = PlanQuery(START, START + timedelta(days=14), ("MORNING", "EVENING"), 3, 2)
    rates = RateCard({("DEL", "GROCERY", "MORNING"): Rate(90000, 160000),
                      ("DEL", "GROCERY", "EVENING"): Rate(100000, 180000)}, 0)
    reach = Reach("r", {("DEL", "GROCERY", "MORNING"): 90, ("DEL", "GROCERY", "EVENING"): 120})
    avail = Availability({("DEL", "GROCERY"): 8}, (), False, None)
    prep = prepare(q, avail, rates, reach, today=START - timedelta(days=3), budget=Decimal(35000),
                   target=None, time_limit_s=3)
    cell = prep.problem.cells[0]
    assert (rs(cell.price30), cell.reach, cell.free) == (6250.00, (90 + 120) * 15, 8)


def test_a_daypart_already_over_today_does_not_play_the_first_day():
    q = PlanQuery(START, START + timedelta(days=1), ("MORNING", "EVENING"), 3, 0)
    rates = RateCard({("DEL", "GROCERY", "MORNING"): Rate(3000, 0),
                      ("DEL", "GROCERY", "EVENING"): Rate(3000, 0)}, 0)
    reach = Reach("r", {("DEL", "GROCERY", "MORNING"): 10, ("DEL", "GROCERY", "EVENING"): 10})
    avail = Availability({("DEL", "GROCERY"): 1}, ("MORNING",), False, None)
    prep = prepare(q, avail, rates, reach, today=START, budget=Decimal(100), target=None,
                   time_limit_s=3)
    assert prep.plays == {"MORNING": 1, "EVENING": 2}
    assert prep.problem.cells[0].reach == 10 * 1 + 10 * 2


def test_cells_without_a_price_or_reach_are_left_out_never_free():
    q = PlanQuery(START, START, ("MORNING",), 3, 2)
    rates = RateCard({("DEL", "GROCERY", "MORNING"): Rate(90000, None),      # no video price
                      ("NOI", "DAIRY", "MORNING"): Rate(90000, 160000)}, 0)
    reach = Reach("r", {("DEL", "GROCERY", "MORNING"): 90})                   # no Noida reach
    avail = Availability({("DEL", "GROCERY"): 3, ("NOI", "DAIRY"): 3, ("GGN", "DAIRY"): 3}, (), False, None)
    prep = prepare(q, avail, rates, reach, today=START - timedelta(days=1), budget=Decimal(1000),
                   target=None, time_limit_s=3)
    assert prep.problem.cells == ()
    assert (prep.skipped_no_rate, prep.skipped_no_reach) == (2, 1)


# ── 2. brute force: every plan, no solver ─────────────────────────────────────

def all_plans(cells):
    """Every n vector with at least one screen: (cost30, reach, cities used)."""
    out = []
    for combo in itertools.product(*(range(c.free + 1) for c in cells)):
        if sum(combo):
            out.append((sum(c.price30 * n for c, n in zip(cells, combo)),
                        sum(c.reach * n for c, n in zip(cells, combo)),
                        frozenset(c.group for c, n in zip(cells, combo) if n)))
    return out


def cheapest(cands):
    return min(cands, key=lambda c: (c[0], -c[1]), default=None)     # least cost, then most reach


def most_reach(cands):
    return min(cands, key=lambda c: (-c[1], c[0]), default=None)     # most reach, then least cost


def random_market(rng, max_cells=4, max_free=3):
    return tuple(Cell(rng.choice("ABC"), rng.randint(1, 30) * 30, rng.randint(1, 40),
                      rng.randint(0, max_free)) for _ in range(rng.randint(1, max_cells)))


def test_plan_one_matches_a_brute_force_on_600_random_markets():
    rng = random.Random(7)
    seen = set()
    for case in range(600):
        cells = random_market(rng)
        plans = all_plans(cells)
        top_cost = max((p[0] for p in plans), default=30)
        top_reach = max((p[1] for p in plans), default=1)
        budget30 = rng.randint(0, top_cost)
        target = rng.choice([None, rng.randint(1, top_reach + 10)])
        out = solve(Problem(f"c{case}", cells, budget30, target))
        if not plans:
            assert out["status"] == model.NO_INVENTORY
            seen.add(model.NO_INVENTORY)
            continue
        fits = [p for p in plans if p[0] <= budget30]
        hit = cheapest([p for p in fits if p[1] >= target]) if target else None
        if hit:
            want = (model.TARGET_MET, hit)
        elif most_reach(fits):
            want = (model.TARGET_OUT_OF_REACH if target else model.BUDGET_PLAN, most_reach(fits))
        else:
            want = (model.BUDGET_TOO_LOW, None)
        assert out["status"] == want[0], (case, cells, budget30, target)
        seen.add(want[0])
        if want[1]:
            got = out["plans"][0]
            assert (got["cost30"], got["reach"]) == want[1][:2], (case, cells, budget30, target)
        if want[0] == model.TARGET_OUT_OF_REACH:
            need = cheapest([p for p in plans if p[1] >= target])
            assert out["target_needs30"] == (need[0] if need else None)
        if want[0] == model.BUDGET_TOO_LOW:
            assert out["min_budget30"] == min(c.price30 for c in cells if c.free)
    assert seen == {model.TARGET_MET, model.BUDGET_PLAN, model.TARGET_OUT_OF_REACH,
                    model.BUDGET_TOO_LOW, model.NO_INVENTORY}


def test_alternatives_match_a_brute_force():
    rng = random.Random(11)
    compared = 0
    for case in range(400):
        cells = tuple(Cell(rng.choice("ABCD"), rng.randint(1, 30) * 30, rng.randint(1, 40),
                           rng.randint(0, 3)) for _ in range(rng.randint(2, 5)))
        plans = all_plans(cells)
        if not plans:
            continue
        budget30 = rng.randint(max(p[0] for p in plans) // 3, max(p[0] for p in plans))
        target = rng.choice([None, rng.randint(1, max(p[1] for p in plans))])
        p = Problem(f"a{case}", cells, budget30, target)
        first = solve(p)
        if not first["plans"]:
            continue
        plan1 = first["plans"][0]
        within = rng.choice([10, 50, 100])
        alts = solve(p, Alternatives(first["mode"], (tuple(plan1["groups"]),), plan1["cost30"],
                                     plan1["reach"], 2, within))
        shown = [frozenset(plan1["groups"])]
        for k in range(2):
            ok = [c for c in plans if c[0] <= budget30 and all(not used <= c[2] for used in shown)]
            if first["mode"] == "cost":
                ok = [c for c in ok if c[1] >= target and c[0] <= plan1["cost30"] * (100 + within) // 100]
                want = cheapest(ok)
            else:
                ok = [c for c in ok if c[1] >= math.ceil(plan1["reach"] * (100 - within) / 100)]
                want = most_reach(ok)
            got = alts["plans"][k] if k < len(alts["plans"]) else None
            if want is None:
                assert got is None, (case, k)
                break
            assert got is not None and (got["cost30"], got["reach"]) == want[:2], (case, k)
            assert all(not used <= frozenset(got["groups"]) for used in shown)
            shown.append(frozenset(got["groups"]))
            compared += 1
    assert compared > 100, compared


# ── 3. every rule, on every plan ─────────────────────────────────────────────

def test_every_plan_keeps_every_rule():
    rng = random.Random(5)
    for case in range(80):
        cells = tuple(Cell(f"C{rng.randint(0, 6)}", rng.randint(500, 9000) * 30, rng.randint(50, 900),
                           rng.randint(0, 40)) for _ in range(rng.randint(3, 30)))
        budget30 = int(sum(c.price30 * c.free for c in cells) * rng.uniform(0.02, 0.9))
        target = rng.choice([None, rng.randint(100, 20000)])
        p = Problem(f"r{case}", cells, budget30, target)
        first = solve(p)
        plans = list(first["plans"])
        if plans:
            plans += solve(p, Alternatives(first["mode"], (tuple(plans[0]["groups"]),),
                                           plans[0]["cost30"], plans[0]["reach"], 2, 50))["plans"]
        for plan in plans:
            assert plan["cost30"] <= budget30 and sum(plan["n"].values()) >= 1
            assert all(0 < n <= cells[c].free for c, n in plan["n"].items())
            assert plan["cost30"] == sum(cells[c].price30 * n for c, n in plan["n"].items())
            assert plan["reach"] == sum(cells[c].reach * n for c, n in plan["n"].items())
        for k in range(1, len(plans)):
            for earlier in plans[:k]:
                assert not set(earlier["groups"]) <= set(plans[k]["groups"])


def test_ties_go_to_the_buyer():
    same_reach = (Cell("A", 300, 10, 1), Cell("B", 600, 10, 1))      # equal reach, A is cheaper
    out = solve(Problem("t", same_reach, 600, None))
    assert out["plans"][0]["cost30"] == 300
    same_cost = (Cell("A", 300, 10, 1), Cell("B", 300, 20, 1))       # equal cost, B reaches more
    out = solve(Problem("t", same_cost, 10 ** 6, 5))
    assert out["plans"][0]["reach"] == 20


# ── 4. edge cases ─────────────────────────────────────────────────────────────

def test_nothing_free_is_no_inventory():
    assert solve(Problem("e", (Cell("A", 300, 5, 0),), 10 ** 6, None))["status"] == model.NO_INVENTORY
    assert solve(Problem("e", (), 10 ** 6, None))["status"] == model.NO_INVENTORY


def test_a_budget_below_one_screen_says_what_one_screen_costs():
    out = solve(Problem("e", (Cell("A", 900, 5, 2), Cell("B", 600, 5, 2)), 500, None))
    assert out["status"] == model.BUDGET_TOO_LOW and out["min_budget30"] == 600


def test_a_target_beyond_every_free_screen():
    out = solve(Problem("e", (Cell("A", 300, 5, 2),), 10 ** 6, 11))
    assert out["status"] == model.TARGET_OUT_OF_REACH
    assert out["target_possible"] is False and out["plans"][0]["reach"] == 10


def test_a_solve_that_runs_out_of_time_is_never_shown(monkeypatch):
    real = model.milp

    def slow(*a, **kw):
        res = real(*a, **kw)
        res.status = 1                              # as HiGHS reports a time limit
        return res
    monkeypatch.setattr(model, "milp", slow)
    out = solve(Problem("e", (Cell("A", 300, 5, 2),), 10 ** 6, 5))
    assert out["status"] == model.NOT_PROVEN and out["plans"] == []


def test_the_digest_covers_everything_solved():
    cells = (Cell("A", 300, 5, 2),)
    p = Problem("j", cells, 900, 5)
    assert model.digest(p) == model.digest(Problem("another-job", cells, 900, 5))
    assert model.digest(p) != model.digest(Problem("j", cells, 930, 5))
    assert model.digest(p) != model.digest(Problem("j", (Cell("A", 300, 5, 3),), 900, 5))
    assert model.digest(p) != model.digest(p, Alternatives("cost", (("A",),), 1, 1, 2))
    assert solve(p)["digest"] == model.digest(p)


# ── speed ─────────────────────────────────────────────────────────────────────

def market(cities: int, shop_types: int, seed: int):
    rng = random.Random(seed)
    return tuple(Cell(f"C{c}", rng.randint(3000, 30000) * 15, rng.randint(40, 400) * 15,
                      rng.randint(0, 200)) for c in range(cities) for _ in range(shop_types))


@pytest.mark.parametrize("cities, shop_types, limit_s", [(10, 10, 1.0), (100, 20, 3.0)])
def test_proven_within_the_time_limit(cities, shop_types, limit_s):
    """At production's own limit (3 s): proven, and in time. Guards the HiGHS version too -
    1.12 (scipy 1.17) took 2.6 s per solve here with presolve on. At 2,000 cells the tie-break
    may run out of its own short allowance; the plan is still proven best on its main goal."""
    cells = market(cities, shop_types, seed=cities)
    total = sum(c.price30 * c.free for c in cells)
    reach = sum(c.reach * c.free for c in cells)
    for budget, target in ((0.1, None), (0.5, None), (0.9, 0.3)):
        p = Problem("s", cells, int(total * budget), int(reach * target) if target else None,
                    time_limit_s=3.0)
        started = time.perf_counter()
        out = solve(p)
        assert out["status"] in (model.BUDGET_PLAN, model.TARGET_MET), out["status"]
        if cities * shop_types <= 100:              # today's size: the tie-break finishes too
            assert out["tiebreaks_unfinished"] == 0
        assert time.perf_counter() - started < limit_s, f"{len(cells)} cells took too long"
