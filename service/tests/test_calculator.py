"""The calculator engine, against calculator-inputs snapshots shaped like CALCULATOR-INPUTS-API.md §4.2.

No network, no model. The promises:

  · the brand price is billing's own arithmetic: the guide's worked example (§12.3 A) to the paisa
  · a screen counts only if it has room in EVERY chosen cell - per-day counts are never added
  · closed cells (already over today) are neither checked nor billed
  · the plan never goes over the budget, and buys the most plays it can (checked by brute force)
  · places with no rate are left out and said so; "too small" carries the cheapest campaign
  · the rates half survives the cache round trip, and a stale cached copy is refused
"""
from __future__ import annotations

import itertools
import random
from datetime import date, timedelta
from decimal import Decimal

import pytest

from genie.calculator.engine import (BUDGET, INVENTORY, NO_FREE_SCREENS, NO_RATE, NOTHING_BOOKABLE,
                                     TARGET, Ask, AskError, alternatives, fits, plan, prefill, rows,
                                     campaign_days)
from genie.calculator.inputs import InputsError, Rates, parse

START = date(2026, 11, 1)


def days(n: int) -> list[str]:
    return [(START + timedelta(days=k)).isoformat() for k in range(n)]


def group(gid, city="DEL", shop="GROCERY", devices=4, n=3, image=6, video=4, slots=("MORNING", "EVENING"),
          free=None, loop=60):
    """`free` overrides per slot: {"EVENING": ([...image], [...video])}."""
    out = {}
    for s in slots:
        img, vid = (free or {}).get(s, ([image] * n, [video] * n))
        out[s] = {"image": list(img), "video": list(vid)}
    return {"id": gid, "city": city, "shopType": shop, "loopSeconds": loop, "devices": devices,
            "contractId": "CTR-MP0001-001", "free": out}


def rate(city="DEL", shop="GROCERY", slot="MORNING", image="16200.00", video="27000.00"):
    return {"city": city, "shopType": shop, "timeSlot": slot, "imageMonthly": image,
            "videoMonthly": video, "source": "BRAND"}


def snapshot(groups, cells=None, n=3, missing=(), surcharges=(), closed=(), version="r-1"):
    cells = cells if cells is not None else [rate(slot=s) for s in ("MORNING", "EVENING")]
    return {"snapshotAt": "2026-10-28T11:04:12+05:30", "days": days(n),
            "versions": {"rates": version, "reach": None},
            "availability": {"groups": groups, "closedCells": list(closed)},
            "surcharges": list(surcharges), "ratesUnchanged": False,
            "rates": {"unit": "INR per slot per month per daypart", "playsPerMonth": 5400,
                      "cells": cells, "missing": list(missing)}}


def ask(n=3, slots=("MORNING", "EVENING"), image=2, video=1, **kw):
    return Ask(START, START + timedelta(days=n - 1), slots, image, video, **kw)


# ── price: billing's arithmetic ──────────────────────────────────────────


def test_the_guides_worked_example_to_the_paisa():
    """§12.3 A: 4 GROCERY screens in DEL, 3 days, MORNING + EVENING, 2 image + 1 video,
    image 16,200 / video 27,000 a month -> the brand pays 47,520.00."""
    inputs = parse(snapshot([group("g1")]))
    (row,), _ = rows(inputs, ask(budget=Decimal("1e9")))
    assert row.price(4) == Decimal("47520.00")
    assert row.plays_per_screen == 3 * 180 * 3 * 2        # (2+1) slots x 180 loops x 3 days x 2 dayparts


def test_a_surcharge_day_is_billed_at_its_multiplier():
    """§12.3: 1.5x on the middle day -> effectiveDays 3.5 -> 55,440.00. Plays are not multiplied."""
    inputs = parse(snapshot([group("g1")], surcharges=[{"date": days(3)[1], "multiplier": "1.5"}]))
    (row,), _ = rows(inputs, ask(budget=Decimal("1e9")))
    assert row.price(4) == Decimal("55440.00")
    assert row.plays_per_screen == 3 * 180 * 3 * 2


def test_each_leg_is_rounded_once_not_each_screen():
    """Like §12.3 B's thirds: a leg is priced for all its screens, then rounded once."""
    cells = [rate(slot="MORNING", image="10000.00", video="10000.00")]
    inputs = parse(snapshot([group("g1", n=1, slots=("MORNING",))], cells=cells, n=1))
    (row,), _ = rows(inputs, ask(n=1, slots=("MORNING",), image=1, video=0, budget=Decimal("1e9")))
    # one screen: 10,000 x 180 / 5,400 = 333.333... -> 333.33; three screens: 1,000.00, not 999.99
    assert row.price(1) == Decimal("333.33")
    assert row.price(3) == Decimal("1000.00")


# ── fit: every chosen cell, never a sum of days ──────────────────────────


def test_screens_free_in_different_dayparts_do_not_add_up():
    """D1 is free only in the morning, D2 only in the evening: a morning+evening campaign gets neither."""
    d1 = group("d1", devices=1, free={"EVENING": ([0, 0, 0], [0, 0, 0])})
    d2 = group("d2", devices=1, free={"MORNING": ([0, 0, 0], [0, 0, 0])})
    inputs = parse(snapshot([d1, d2]))
    usable, excluded = rows(inputs, ask(budget=Decimal("1e9")))
    assert usable == [] and [e.reason for e in excluded] == [NO_FREE_SCREENS]
    morning_only, _ = rows(inputs, ask(slots=("MORNING",), budget=Decimal("1e9")))
    assert morning_only[0].available == 1


def test_the_tightest_day_decides():
    g = group("g", free={"MORNING": ([6, 1, 6], [4, 4, 4])})
    inputs = parse(snapshot([g]))
    k = campaign_days(inputs, ask(budget=Decimal(1)))
    assert not fits(inputs.groups[0], inputs, ask(budget=Decimal(1)), k)              # 2 images, day 2 has 1
    assert fits(inputs.groups[0], inputs, ask(image=1, budget=Decimal(1)), k)


def test_a_lease_ending_mid_range_allows_a_shorter_flight():
    g = group("g", n=5, free={"MORNING": ([6, 6, 6, 0, 0], [4, 4, 4, 0, 0]),
                              "EVENING": ([6, 6, 6, 0, 0], [4, 4, 4, 0, 0])})
    inputs = parse(snapshot([g], n=5))
    assert plan(inputs, ask(n=5, budget=Decimal("1e9"))).limited_by == NOTHING_BOOKABLE
    assert plan(inputs, ask(n=3, budget=Decimal("1e9"))).screens == 4


def test_closed_cells_are_neither_checked_nor_billed():
    """Asked at 14:00 for today: today's MORNING is over. Its 0 must not sink the screen,
    and it is not billed or counted as plays."""
    g = group("g", free={"MORNING": ([0, 6, 6], [0, 4, 4])})
    inputs = parse(snapshot([g], closed=[{"date": days(3)[0], "timeSlot": "MORNING"}]))
    (row,), _ = rows(inputs, ask(budget=Decimal("1e9")))
    assert row.available == 4
    assert row.plays_per_screen == 3 * 180 * (2 + 3)          # 2 morning days + 3 evening days
    assert row.price(4) == Decimal("47520.00") * 5 / 6


# ── choose: never over budget, the most plays it can buy ─────────────────


def test_cheapest_screens_first_and_never_over_budget():
    groups = [group("a", city="DEL", devices=10), group("b", city="GGN", devices=10)]
    cells = [rate(city="DEL", slot=s) for s in ("MORNING", "EVENING")] + \
            [rate(city="GGN", slot=s, image="8100.00", video="13500.00") for s in ("MORNING", "EVENING")]
    inputs = parse(snapshot(groups, cells=cells))
    p = plan(inputs, ask(budget=Decimal("40000")))
    # GGN is half DEL's price: 5,940 a screen vs 11,880. All 10 GGN = 59,400 > 40,000 -> 6 GGN.
    assert [(l.city, l.screens) for l in p.lines] == [("GGN", 6)]
    assert p.price == Decimal("35640.00") and p.price <= p.budget and p.limited_by == BUDGET
    assert p.optimal


def test_inventory_bound_when_the_budget_outruns_the_screens():
    p = plan(parse(snapshot([group("g", devices=4)])), ask(budget=Decimal("1e7")))
    assert p.screens == 4 and p.limited_by == INVENTORY


def test_a_budget_too_small_for_one_screen_says_what_the_smallest_costs():
    p = plan(parse(snapshot([group("g")])), ask(budget=Decimal("100")))
    assert p.screens == 0 and p.limited_by == BUDGET and p.smallest_price == Decimal("11880.00")


def test_target_plays_buys_just_enough():
    p = plan(parse(snapshot([group("g", devices=10)])), ask(target_plays=10_000))
    per_screen = 3 * 180 * 3 * 2                              # 3,240
    assert p.screens == 4 and p.plays == 4 * per_screen and p.limited_by == TARGET


def test_target_out_of_reach_takes_everything_and_says_so():
    p = plan(parse(snapshot([group("g", devices=2)])), ask(target_plays=10**9))
    assert p.screens == 2 and p.limited_by == INVENTORY


@pytest.mark.parametrize("seed", range(40))
def test_the_plan_is_the_best_any_choice_could_do(seed):
    """Brute force over every screen count in every place: none buys more plays within budget."""
    rnd = random.Random(seed)
    places = [("DEL", "GROCERY"), ("GGN", "PHARMACY"), ("NOI", "DAIRY")]
    groups, cells = [], []
    for city, shop in places:
        groups.append(group(f"{city}", city=city, shop=shop, devices=rnd.randint(0, 5)))
        for s in ("MORNING", "EVENING"):
            cells.append(rate(city=city, shop=shop, slot=s,
                              image=f"{rnd.randint(3000, 30000)}.{rnd.randint(0, 99):02d}",
                              video=f"{rnd.randint(3000, 30000)}.{rnd.randint(0, 99):02d}"))
    inputs = parse(snapshot(groups, cells=cells))
    a = ask(budget=Decimal(rnd.randint(5_000, 150_000)))
    p = plan(inputs, a)
    usable, _ = rows(inputs, a)
    best = max((sum(n * r.plays_per_screen for n, r in zip(combo, usable))
                for combo in itertools.product(*(range(r.available + 1) for r in usable))
                if sum(r.price(n) for n, r in zip(combo, usable)) <= a.budget), default=0)
    assert p.price <= a.budget
    assert p.plays == best


# ── what is left out, and why ────────────────────────────────────────────


def test_a_place_with_a_missing_rate_is_left_out_and_named():
    groups = [group("a", city="DEL"), group("b", city="GGN")]
    cells = [rate(city="DEL", slot=s) for s in ("MORNING", "EVENING")] + [rate(city="GGN", slot="MORNING")]
    inputs = parse(snapshot(groups, cells=cells,
                            missing=[{"city": "GGN", "shopType": "GROCERY", "timeSlot": "EVENING"}]))
    p = plan(inputs, ask(budget=Decimal("1e7")))
    assert p.cities == ("DEL",)
    assert [(e.city, e.reason) for e in p.excluded] == [("GGN", NO_RATE)]


def test_filters_narrow_the_places():
    groups = [group("a", city="DEL"), group("b", city="GGN")]
    cells = [rate(city=c, slot=s) for c in ("DEL", "GGN") for s in ("MORNING", "EVENING")]
    p = plan(parse(snapshot(groups, cells=cells)), ask(budget=Decimal("1e7"), cities=frozenset({"GGN"})))
    assert p.cities == ("GGN",)


def test_alternatives_drop_the_biggest_city_then_the_next():
    groups = [group("a", city="DEL", devices=6), group("b", city="GGN", devices=3), group("c", city="NOI", devices=1)]
    cells = [rate(city=c, slot=s) for c in ("DEL", "GGN", "NOI") for s in ("MORNING", "EVENING")]
    inputs = parse(snapshot(groups, cells=cells))
    a = ask(budget=Decimal("1e7"))
    alts = alternatives(inputs, a, plan(inputs, a))
    assert [(city, alt.cities) for city, alt in alts] == [("DEL", ("GGN", "NOI")), ("GGN", ("DEL", "NOI"))]


# ── the hand-off ─────────────────────────────────────────────────────────


def test_prefill_is_the_wizards_shape():
    a = ask(budget=Decimal("1e7"))
    out = prefill(plan(parse(snapshot([group("g")])), a), a)
    assert out == {"deliveryMode": "BROADCAST", "deviceTier": "NON_PREMIUM",
                   "startDate": "2026-11-01", "endDate": "2026-11-03",
                   "timeSlots": ["MORNING", "EVENING"], "imageSlotsRequested": 2, "videoSlotsRequested": 1,
                   "selections": [{"city": "DEL", "shopType": "GROCERY", "deviceCount": 4}]}


# ── asks and snapshots that must be refused ──────────────────────────────


@pytest.mark.parametrize("bad, why", [
    (dict(image=0, video=0), "at least one"),
    (dict(budget=None), "budget or a number of plays"),
    (dict(target_plays=5), "not both"),
    (dict(budget=Decimal(0)), "more than zero"),
    (dict(slots=("MORNING", "MORNING")), "distinct"),
    (dict(n=9), "outside the snapshot"),
])
def test_bad_asks_are_refused(bad, why):
    kw = {"budget": Decimal(1000), **bad}
    with pytest.raises(AskError, match=why):
        plan(parse(snapshot([group("g")])), ask(**kw))


@pytest.mark.parametrize("mutate, why", [
    (lambda s: s["availability"]["groups"][0]["free"]["MORNING"]["image"].pop(), "one value per day"),
    (lambda s: s["availability"]["groups"][0]["free"]["MORNING"]["image"].__setitem__(0, -1), "not a count"),
    (lambda s: s["availability"]["groups"][0]["free"].__setitem__("DUSK", {}), "unknown time slot"),
    (lambda s: s["rates"]["cells"].append(rate(slot="MORNING")), "listed twice"),
    (lambda s: s["versions"].__setitem__("rates", ""), "without a version"),
    (lambda s: s["days"].reverse(), "consecutive"),
])
def test_a_malformed_snapshot_is_refused(mutate, why):
    s = snapshot([group("g")])
    mutate(s)
    with pytest.raises(InputsError, match=why):
        parse(s)


# ── the rates cache ──────────────────────────────────────────────────────


def test_rates_survive_the_cache_round_trip():
    rates = parse(snapshot([group("g")])).rates
    assert Rates.from_json(rates.to_json()) == rates


def test_unchanged_rates_come_from_the_held_copy_only_if_it_is_that_version():
    held = parse(snapshot([group("g")], version="r-1")).rates
    kept_back = {**snapshot([group("g")], version="r-1"), "ratesUnchanged": True, "rates": None}
    assert parse(kept_back, cached=held).rates is held
    with pytest.raises(InputsError, match="not that version"):
        parse({**kept_back, "versions": {"rates": "r-2"}}, cached=held)
    with pytest.raises(InputsError, match="not that version"):
        parse(kept_back, cached=None)
