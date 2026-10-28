"""The calculator's exact engine. A PURE function of its input: it reads nothing but its
argument, writes nothing anywhere, keeps nothing between calls. That is what lets it run
in a pool of solver processes shared by every user (see engine.py).

The decision: x[r][t] = screens of line r on date t (a whole number). Every plan keeps
    0 <= x[r][t] <= free[r][t]
    cost <= budget
    every date on which any screen is free plays at least one screen
and is either the CHEAPEST that reaches the target, or (no target) has the MOST reach.

Cost and reach depend only on y[r] = sum over t of x[r][t]. So the solver works on y
("lean"): it solves for y, then checks with a matching whether y can be laid out so every
date plays. Dates that cannot get explicit "line r plays on date t" variables, and it
solves again. Each solve is a RELAXATION of the full problem (it checks only some dates),
so the first y that passes the check is optimal for the full problem. At most one round
per date. Solved by HiGHS with a relative gap of 0: proven optimal, not "good".

Money is whole PAISE x 30 throughout (the price of a screen-day is (img*IPM + vid*VPM)/30
rupees), so every cost is an integer and every comparison is exact. Each plan is checked
again with integers before it is returned; a plan that fails is never returned.
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
class Line:
    contract: str                   # contract id
    price30: int                    # paise for 30 screen-days: img*IPM + vid*VPM, in paise
    reach: int                      # people one screen reaches in a day here
    free: tuple[int, ...]           # free screens per date


@dataclass(frozen=True, slots=True)
class Problem:
    job_id: str
    lines: tuple[Line, ...]
    days: int
    budget30: int                   # budget in paise x 30
    target: int | None
    time_limit_s: float = 3.0


@dataclass(frozen=True, slots=True)
class Alternatives:
    """Find `count` more plans after the ones shown. Each drops at least one contract
    used by EVERY plan shown before it, and stays within `within_pct` of plan 1."""
    mode: str                       # "cost": cheapest reaching the target; "reach": most reach
    shown: tuple[tuple[str, ...], ...]
    cost30: int                     # plan 1's cost
    reach: int                      # plan 1's reach
    count: int
    within_pct: int = 10


def digest(p: Problem, alt: Alternatives | None = None) -> str:
    """A fingerprint of exactly what is being solved. The caller computes it before
    sending; the worker computes it from what it received; they must match."""
    body = [p.days, p.budget30, p.target, p.time_limit_s,
            [[ln.contract, ln.price30, ln.reach, list(ln.free)] for ln in p.lines],
            None if alt is None else [alt.mode, [list(s) for s in alt.shown], alt.cost30,
                                      alt.reach, alt.count, alt.within_pct]]
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()


class _Timeout(Exception):
    pass


class _Unproven(Exception):
    pass


def spread(base: list[int], cap: tuple[int, ...], k: int) -> list[int]:
    """Add k screens to `base` as evenly as possible within `cap`: raise the lowest dates
    first; ties go to the earliest date."""
    total0 = sum(base)

    def filled(level: int) -> int:
        return sum(min(c, max(b, level)) for b, c in zip(base, cap)) - total0

    lo, hi = 0, max(cap, default=0)
    while lo < hi:                                   # highest level that adds <= k
        mid = (lo + hi + 1) // 2
        if filled(mid) <= k:
            lo = mid
        else:
            hi = mid - 1
    x = [min(c, max(b, lo)) for b, c in zip(base, cap)]
    rest = k - (sum(x) - total0)
    for t in range(len(x)):
        if rest == 0:
            break
        if x[t] == lo and x[t] < cap[t]:
            x[t] += 1
            rest -= 1
    if rest:
        raise _Unproven("more screen-days than free screens")
    return x


class _Solver:
    def __init__(self, p: Problem, deadline: float):
        self.p = p
        self.n, self.D = len(p.lines), p.days
        self.free = np.array([ln.free for ln in p.lines], dtype=np.int64).reshape(self.n, self.D)
        self.total = self.free.sum(axis=1)
        self.price = np.array([ln.price30 for ln in p.lines], dtype=float)
        self.reach = np.array([ln.reach for ln in p.lines], dtype=float)
        self.coverable = [t for t in range(self.D) if self.free[:, t].sum() > 0]
        self.contracts = sorted({ln.contract for ln in p.lines})
        self.contract_of = [self.contracts.index(ln.contract) for ln in p.lines]
        # For the every-date layout: try the line free on the FEWEST dates first (it has
        # the least choice), then input order - deterministic, and readable schedules.
        free_dates = (self.free > 0).sum(axis=1)
        self.rows_for = {t: sorted(np.nonzero(self.free[:, t])[0].tolist(),
                                   key=lambda r: (free_dates[r], r)) for t in self.coverable}
        self.deadline = deadline
        self.tiebreak_s = max(0.2, p.time_limit_s / 6)
        self.tiebreaks_unfinished = 0
        self.rounds = 0

    # ── the exact search ──────────────────────────────────────────────────
    def best(self, *, cheapest: bool, budget30: int | None, target: int | None,
             drop: tuple[frozenset[str], ...] = (), cost_cap30: int | None = None,
             reach_floor: int | None = None) -> np.ndarray | None:
        """The best plan, ties broken the way a buyer would: of the plans with the most
        reach, the cheapest; of the cheapest plans, the one with the most reach. Two
        exact solves - the second keeps the first's optimum and improves the other side."""
        y = self.optimise(cheapest=cheapest, budget30=budget30, target=target, drop=drop,
                          cost_cap30=cost_cap30, reach_floor=reach_floor)
        if y is None:
            return None
        # The tie-break only chooses AMONG plans already proven best on the main goal, so
        # it gets a short allowance of its own; if that runs out, the first answer stands
        # (still proven best on the main goal - only the tie is unbroken).
        saved = self.deadline
        self.deadline = min(saved, time.monotonic() + self.tiebreak_s)
        try:
            if cheapest:
                better = self.optimise(cheapest=False, budget30=budget30, target=target,
                                       drop=drop, cost_cap30=self.cost30(y),
                                       reach_floor=reach_floor)
            else:
                better = self.optimise(cheapest=True, budget30=budget30, target=target,
                                       drop=drop, cost_cap30=cost_cap30,
                                       reach_floor=max(self.reach_of(y), reach_floor or 0))
        except _Timeout:
            self.tiebreaks_unfinished += 1
            better = None
        finally:
            self.deadline = saved
        return y if better is None else better

    def optimise(self, *, cheapest: bool, budget30: int | None, target: int | None,
                 drop: tuple[frozenset[str], ...] = (), cost_cap30: int | None = None,
                 reach_floor: int | None = None) -> np.ndarray | None:
        """The proven-best y under these limits, or None if no plan keeps them."""
        forced: list[int] = []
        while True:
            y = self._milp(forced, cheapest, budget30, target, drop, cost_cap30, reach_floor)
            if y is None:
                return None
            _, uncovered = self._cover(y, forced)
            if not uncovered:
                return y
            forced += uncovered

    def _milp(self, forced, cheapest, budget30, target, drop, cost_cap30, reach_floor):
        self.rounds += 1
        n = self.n
        pairs = [(r, t) for t in forced for r in self.rows_for[t]]
        nu = len(self.contracts) if drop else 0
        nv = n + nu + len(pairs)
        cons: list[LinearConstraint] = []

        def dense(coefs: np.ndarray) -> np.ndarray:
            row = np.zeros(nv)
            row[:n] = coefs
            return row

        caps = [v for v in (budget30, cost_cap30) if v is not None]
        if caps:
            cons.append(LinearConstraint(dense(self.price), -np.inf, min(caps)))
        floor = max(v for v in (target, reach_floor, 0) if v is not None)
        if floor:
            cons.append(LinearConstraint(dense(self.reach), floor, np.inf))

        rows, cols, vals, lo, hi = [], [], [], [], []
        k = 0
        if pairs:
            day_row = {t: i for i, t in enumerate(forced)}
            for j, (r, t) in enumerate(pairs):              # each forced date: some line plays
                rows.append(day_row[t]); cols.append(n + nu + j); vals.append(1)
            k = len(forced)
            lo += [1] * k; hi += [np.inf] * k
            line_row = {}
            for j, (r, t) in enumerate(pairs):              # a line plays <= y[r] of those dates
                if r not in line_row:
                    line_row[r] = k
                    rows.append(k); cols.append(r); vals.append(-1)
                    lo.append(-np.inf); hi.append(0)
                    k += 1
                rows.append(line_row[r]); cols.append(n + nu + j); vals.append(1)
        if drop:
            for r in range(n):                              # y[r] > 0 only if its contract is on
                rows += [k, k]; cols += [r, n + self.contract_of[r]]
                vals += [1, -float(self.total[r])]
                lo.append(-np.inf); hi.append(0)
                k += 1
            for used in drop:                               # switch off >= 1 of each earlier set
                for c in used:
                    rows.append(k); cols.append(n + self.contracts.index(c)); vals.append(1)
                lo.append(-np.inf); hi.append(len(used) - 1)
                k += 1
        if k:
            A = coo_matrix((vals, (rows, cols)), shape=(k, nv)).tocsr()
            cons.append(LinearConstraint(A, np.array(lo, float), np.array(hi, float)))

        objective = dense(self.price if cheapest else -self.reach)
        upper = np.concatenate([self.total.astype(float), np.ones(nu + len(pairs))])
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise _Timeout
        res = milp(objective, constraints=cons, integrality=np.ones(nv),
                   bounds=Bounds(np.zeros(nv), upper),
                   options={"time_limit": left, "mip_rel_gap": 0, "presolve": True})
        if res.status == 2:                                 # infeasible: no plan keeps these limits
            return None
        if res.status == 1:
            raise _Timeout
        if res.status != 0 or res.x is None:
            raise _Unproven(f"solver status {res.status}")
        return np.round(res.x[:n]).astype(np.int64)

    # ── laying y out over the dates ───────────────────────────────────────
    def _cover(self, y: np.ndarray, first: list[int]) -> tuple[dict[int, int], list[int]]:
        """One screen on every date that can play, from lines with screen-days to spare:
        a maximum matching of dates to lines (a line covers at most y[r] dates). Dates
        in `first` are matched first; a matched date never loses its line, so they stay
        covered. Returns (date -> line, dates left uncovered)."""
        cap = [int(v) for v in y]
        load = [0] * self.n
        held: dict[int, list[int]] = {}
        holder: dict[int, int] = {}

        def attempt(t: int, seen: set[int]) -> bool:
            # Path length <= dates: each step moves a date held by a not-yet-seen line.
            for r in self.rows_for[t]:
                if cap[r] == 0 or r in seen:
                    continue
                seen.add(r)
                if load[r] < cap[r]:
                    held.setdefault(r, []).append(t)
                    load[r] += 1
                    holder[t] = r
                    return True
                for t2 in held.get(r, []):
                    if attempt(t2, seen):                   # t2 moved to another line
                        held[r].remove(t2)
                        held[r].append(t)
                        holder[t] = r
                        return True
            return False

        order = list(first) + [t for t in self.coverable if t not in set(first)]
        uncovered = [t for t in order if not attempt(t, set())]
        return holder, uncovered

    def plan(self, y: np.ndarray, *, budget30: int | None, target: int | None) -> dict:
        holder, uncovered = self._cover(y, [])
        if uncovered:
            raise _Unproven("a date cannot play")
        x: dict[int, list[int]] = {}
        for r in range(self.n):
            if y[r] <= 0:
                continue
            base = [1 if holder.get(t) == r else 0 for t in range(self.D)]
            x[r] = spread(base, self.p.lines[r].free, int(y[r]) - sum(base))
        return self._checked(y, x, budget30=budget30, target=target)

    def _checked(self, y, x, *, budget30, target) -> dict:
        """Every rule again, in integers, on the plan exactly as it will be shown."""
        lines = self.p.lines
        for r, xs in x.items():
            if sum(xs) != y[r] or any(v < 0 or v > f for v, f in zip(xs, lines[r].free)):
                raise _Unproven("a line exceeds its free screens")
        if any(y[r] > 0 and r not in x for r in range(self.n)):
            raise _Unproven("a line lost its screens")
        for t in self.coverable:
            if not any(xs[t] for xs in x.values()):
                raise _Unproven("a date does not play")
        cost30 = sum(lines[r].price30 * int(y[r]) for r in x)
        reach = sum(lines[r].reach * int(y[r]) for r in x)
        if budget30 is not None and cost30 > budget30:
            raise _Unproven("over budget")
        if target and reach < target:
            raise _Unproven("short of the target")
        return {"x": {r: xs for r, xs in sorted(x.items())}, "cost30": cost30, "reach": reach,
                "contracts": sorted({lines[r].contract for r in x})}

    def cost30(self, y: np.ndarray) -> int:
        return sum(self.p.lines[r].price30 * int(y[r]) for r in range(self.n))

    def reach_of(self, y: np.ndarray) -> int:
        return sum(self.p.lines[r].reach * int(y[r]) for r in range(self.n))


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
        out.update(status=NOT_PROVEN, plans=[] if alt is None else out["plans"],
                   reason=str(err), cut_short=alt is not None)
    out["rounds"] = s.rounds
    out["tiebreaks_unfinished"] = s.tiebreaks_unfinished
    out["ms"] = int((time.perf_counter() - started) * 1000)
    return out


def _primary(s: _Solver, p: Problem, out: dict) -> None:
    if not s.coverable:
        out["status"] = NO_INVENTORY
        return
    y = None
    if p.target:
        y = s.best(cheapest=True, budget30=p.budget30, target=p.target)
        if y is not None:
            out["status"], out["mode"] = TARGET_MET, "cost"
            out["plans"].append(s.plan(y, budget30=p.budget30, target=p.target))
            return
        try:                                            # what the target would cost
            need = s.optimise(cheapest=True, budget30=None, target=p.target)
            out["target_needs30"] = None if need is None else s.cost30(need)
            out["target_possible"] = need is not None
        except _Timeout:
            pass
    y = s.best(cheapest=False, budget30=p.budget30, target=None)
    if y is not None:
        out["status"] = TARGET_OUT_OF_REACH if p.target else BUDGET_PLAN
        out["mode"] = "reach"
        out["plans"].append(s.plan(y, budget30=p.budget30, target=None))
        return
    out["status"] = BUDGET_TOO_LOW                     # cannot play every date
    cheapest = s.optimise(cheapest=True, budget30=None, target=None)
    out["min_budget30"] = None if cheapest is None else s.cost30(cheapest)


def _alternatives(s: _Solver, p: Problem, alt: Alternatives, out: dict) -> None:
    shown = tuple(frozenset(c) for c in alt.shown if c)
    for _ in range(alt.count):
        if alt.mode == "cost":
            cap = alt.cost30 * (100 + alt.within_pct) // 100
            y = s.best(cheapest=True, budget30=p.budget30, target=p.target, drop=shown,
                           cost_cap30=cap)
        else:
            floor = -(-alt.reach * (100 - alt.within_pct) // 100)
            y = s.best(cheapest=False, budget30=p.budget30, target=None, drop=shown,
                           reach_floor=floor)
        if y is None:
            return
        plan = s.plan(y, budget30=p.budget30, target=p.target if alt.mode == "cost" else None)
        out["plans"].append(plan)
        shown += (frozenset(plan["contracts"]),)
