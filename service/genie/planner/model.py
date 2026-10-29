"""The calculator's exact engine. A PURE function of its input: it reads nothing but its
argument, writes nothing anywhere, keeps nothing between calls - which is what lets it run
in a pool of solver processes shared by every user (see engine.py).

The decision, as Adgrid books it (CAMPAIGN_CREATION_GUIDE.md §7.6): n[c] = how many screens
to book in city x shop-type cell c, for the whole window and every chosen daypart.

    0 <= n[c] <= free[c], whole numbers        never more than is free for the whole window
    sum of n >= 1                              a plan books at least one screen
    cost = sum n[c] x price[c] <= budget
    reach = sum n[c] x reach[c]

and it is either the CHEAPEST that reaches the target, or (no target) has the MOST reach.
Ties are broken the way a buyer would: the same reach for less money, or the same money
for more reach. Every booked screen plays every date of the window, so "the campaign plays
every day" holds by construction.

Solved by HiGHS with a relative gap of 0: proven optimal, not "good". Money is whole PAISE
x 30 (a screen-day costs (img x IPM + vid x VPM) / 30 rupees), so every cost is an integer
and every comparison exact. Each plan is checked again in integers before it is returned.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

TARGET_MET = "TARGET_MET"
BUDGET_PLAN = "BUDGET_PLAN"
TARGET_OUT_OF_REACH = "TARGET_OUT_OF_REACH"
BUDGET_TOO_LOW = "BUDGET_TOO_LOW"
NO_INVENTORY = "NO_INVENTORY"
NOT_PROVEN = "NOT_PROVEN"


@dataclass(frozen=True, slots=True)
class Cell:
    group: str                      # the city: alternatives differ by city
    price30: int                    # one screen for the whole campaign, in paise x 30
    reach: int                      # one screen for the whole campaign, in people
    free: int                       # screens free in every date x chosen daypart


@dataclass(frozen=True, slots=True)
class Problem:
    job_id: str
    cells: tuple[Cell, ...]
    budget30: int                   # the most a plan may cost, in paise x 30
    target: int | None
    time_limit_s: float = 3.0


@dataclass(frozen=True, slots=True)
class Alternatives:
    """Find `count` more plans after the ones shown. Each leaves out at least one city used
    by EVERY plan shown before it, and stays within `within_pct` of plan 1."""
    mode: str                       # "cost": cheapest reaching the target; "reach": most reach
    shown: tuple[tuple[str, ...], ...]
    cost30: int                     # plan 1's cost
    reach: int                      # plan 1's reach
    count: int
    within_pct: int = 10


def digest(p: Problem, alt: Alternatives | None = None) -> str:
    """A fingerprint of exactly what is being solved. The caller computes it before
    sending; the worker computes it from what it received; they must match."""
    body = [p.budget30, p.target, p.time_limit_s,
            [[c.group, c.price30, c.reach, c.free] for c in p.cells],
            None if alt is None else [alt.mode, [list(s) for s in alt.shown], alt.cost30,
                                      alt.reach, alt.count, alt.within_pct]]
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()


class _Timeout(Exception):
    pass


class _Unproven(Exception):
    pass


class _Solver:
    def __init__(self, p: Problem, deadline: float):
        self.p = p
        self.n = len(p.cells)
        self.price = np.array([c.price30 for c in p.cells], dtype=float)
        self.reach = np.array([c.reach for c in p.cells], dtype=float)
        self.free = np.array([c.free for c in p.cells], dtype=float)
        self.groups = sorted({c.group for c in p.cells})
        self.group_of = [self.groups.index(c.group) for c in p.cells]
        self.deadline = deadline
        self.tiebreak_s = max(0.2, p.time_limit_s / 6)
        self.tiebreaks_unfinished = 0
        self.solves = 0

    def best(self, *, cheapest: bool, cap30: int | None, floor: int | None,
             drop: tuple[frozenset[str], ...] = ()) -> np.ndarray | None:
        """The proven-best plan under these limits, ties broken the way a buyer would. The
        tie-break only chooses AMONG plans already proven best, so it has a short
        allowance of its own; if that runs out, the first answer stands."""
        n = self.optimise(cheapest, cap30, floor, drop)
        if n is None:
            return None
        saved = self.deadline
        self.deadline = min(saved, time.monotonic() + self.tiebreak_s)
        try:
            if cheapest:     # same money, more reach
                better = self.optimise(False, self.cost30(n), floor, drop)
            else:            # same reach, less money
                better = self.optimise(True, cap30, max(self.reach_of(n), floor or 0), drop)
        except _Timeout:
            self.tiebreaks_unfinished += 1
            better = None
        finally:
            self.deadline = saved
        return n if better is None else better

    def optimise(self, cheapest: bool, cap30: int | None, floor: int | None,
                 drop: tuple[frozenset[str], ...]) -> np.ndarray | None:
        self.solves += 1
        n, ng = self.n, (len(self.groups) if drop else 0)
        nv = n + ng

        def dense(coefs) -> np.ndarray:
            row = np.zeros(nv)
            row[:n] = coefs
            return row

        cons = [LinearConstraint(dense(np.ones(n)), 1, np.inf)]          # at least one screen
        if cap30 is not None:
            cons.append(LinearConstraint(dense(self.price), -np.inf, cap30))
        if floor:
            cons.append(LinearConstraint(dense(self.reach), floor, np.inf))
        if drop:
            rows, cols, vals, hi = [], [], [], []
            k = 0
            for c in range(n):                       # n[c] > 0 only if its city is on
                rows += [k, k]; cols += [c, n + self.group_of[c]]; vals += [1, -self.free[c]]
                hi.append(0)
                k += 1
            for used in drop:                        # leave out >= 1 city of each earlier plan
                for g in used:
                    rows.append(k); cols.append(n + self.groups.index(g)); vals.append(1)
                hi.append(len(used) - 1)
                k += 1
            A = coo_matrix((vals, (rows, cols)), shape=(k, nv)).tocsr()
            cons.append(LinearConstraint(A, -np.inf, np.array(hi, float)))
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise _Timeout
        # presolve off: this model is a handful of rows, and HiGHS 1.12 (scipy >= 1.17) spends
        # ~2 s presolving it at 2,000 cells - 5x the search itself - and overruns its time limit
        # doing so. The search alone proves the same optimum (checked on scipy 1.15 and 1.17).
        res = milp(dense(self.price if cheapest else -self.reach), constraints=cons,
                   integrality=np.ones(nv),
                   bounds=Bounds(np.zeros(nv), np.concatenate([self.free, np.ones(ng)])),
                   options={"time_limit": left, "mip_rel_gap": 0, "presolve": False})
        if res.status == 2:                          # infeasible: no plan keeps these limits
            return None
        if res.status == 1:
            raise _Timeout
        if res.status != 0 or res.x is None:
            raise _Unproven(f"solver status {res.status}")
        return np.round(res.x[:n]).astype(np.int64)

    def cost30(self, n) -> int:
        return sum(self.p.cells[c].price30 * int(n[c]) for c in range(self.n))

    def reach_of(self, n) -> int:
        return sum(self.p.cells[c].reach * int(n[c]) for c in range(self.n))

    def plan(self, n, *, cap30: int | None, floor: int | None) -> dict:
        """Every rule again, in integers, on the plan exactly as it will be shown."""
        chosen = {c: int(n[c]) for c in range(self.n) if n[c] > 0}
        if not chosen:
            raise _Unproven("an empty plan")
        if any(v > self.p.cells[c].free or v < 0 for c, v in chosen.items()):
            raise _Unproven("more screens than are free")
        cost30, reach = self.cost30(n), self.reach_of(n)
        if cap30 is not None and cost30 > cap30:
            raise _Unproven("over budget")
        if floor and reach < floor:
            raise _Unproven("short of the target")
        return {"n": chosen, "cost30": cost30, "reach": reach,
                "groups": sorted({self.p.cells[c].group for c in chosen})}


def solve(p: Problem, alt: Alternatives | None = None) -> dict:
    """Plan 1 (alt None) or the alternatives after it. Runs in a solver process."""
    started = time.perf_counter()
    out: dict = {"job_id": p.job_id, "digest": digest(p, alt), "status": None, "plans": [],
                 "target_needs30": None, "min_budget30": None, "cut_short": False}
    s = _Solver(p, time.monotonic() + p.time_limit_s)
    try:
        if alt is None:
            _primary(s, p, out)
        else:
            _alternatives(s, p, alt, out)
    except _Timeout:
        if alt is None:
            out.update(status=NOT_PROVEN, plans=[])
        else:
            out["cut_short"] = True
    except _Unproven as err:
        if alt is None:
            out.update(status=NOT_PROVEN, plans=[], reason=str(err))
        else:
            out.update(cut_short=True, reason=str(err))
    out["solves"] = s.solves
    out["tiebreaks_unfinished"] = s.tiebreaks_unfinished
    out["ms"] = int((time.perf_counter() - started) * 1000)
    return out


def _primary(s: _Solver, p: Problem, out: dict) -> None:
    live = [c for c in p.cells if c.free > 0]
    if not live:
        out["status"] = NO_INVENTORY
        return
    if p.target:
        n = s.best(cheapest=True, cap30=p.budget30, floor=p.target)
        if n is not None:
            out["status"], out["mode"] = TARGET_MET, "cost"
            out["plans"].append(s.plan(n, cap30=p.budget30, floor=p.target))
            return
        try:                                          # what the target would cost
            need = s.optimise(True, None, p.target, ())
            out["target_needs30"] = None if need is None else s.cost30(need)
            out["target_possible"] = need is not None
        except _Timeout:
            pass
    n = s.best(cheapest=False, cap30=p.budget30, floor=None)
    if n is not None:
        out["status"] = TARGET_OUT_OF_REACH if p.target else BUDGET_PLAN
        out["mode"] = "reach"
        out["plans"].append(s.plan(n, cap30=p.budget30, floor=None))
        return
    out["status"] = BUDGET_TOO_LOW                   # not even one screen fits
    out["min_budget30"] = min(c.price30 for c in live)


def _alternatives(s: _Solver, p: Problem, alt: Alternatives, out: dict) -> None:
    shown = tuple(frozenset(g) for g in alt.shown if g)
    for _ in range(alt.count):
        if alt.mode == "cost":
            cap = min(p.budget30, alt.cost30 * (100 + alt.within_pct) // 100)
            n = s.best(cheapest=True, cap30=cap, floor=p.target, drop=shown)
            floor = p.target
        else:
            floor = -(-alt.reach * (100 - alt.within_pct) // 100)
            cap = p.budget30
            n = s.best(cheapest=False, cap30=cap, floor=floor, drop=shown)
        if n is None:
            return
        plan = s.plan(n, cap30=cap, floor=floor)
        out["plans"].append(plan)
        shown += (frozenset(plan["groups"]),)
