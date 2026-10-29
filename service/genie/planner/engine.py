"""Runs plans: solver processes, limits, and the checks that a result belongs to the
request that asked for it.

WHY USERS' PLANS CANNOT MIX, with the solving done in memory:

1. Every input belongs to ONE request. The handler reads the user's own free screens and
   rates (with their own token, under cache keys that carry their verified user id),
   builds an immutable Problem from them, and hands it to the pool as an ARGUMENT.
2. The solver is a pure function (model.solve). It reads only its argument and keeps
   nothing between calls - no module state, no cache, no globals. A worker process that
   solved user A's problem a moment ago holds nothing of it when it solves user B's.
3. The pool returns each result to the Future of the call that submitted it; asyncio
   resumes exactly the request that awaited that Future. No queue is shared by name,
   nothing is looked up by key, so no result can be delivered to the wrong caller.
4. Even so, every result must PROVE it is the answer to this request: it echoes the job
   id it was given and a SHA-256 of the exact problem it solved, recomputed in the worker
   from what it received. Either mismatching refuses the result (PlanMixUp) - nothing is
   shown.
5. Plans are never stored: not in Redis, not in memory beyond the request that streams
   them.

The event loop never solves: it awaits the pool, so chat and other plans keep moving.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import time
import uuid
from collections import deque
from concurrent.futures import Executor, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from . import model
from .contract import Availability, Cell, PlanQuery, RateCard, Reach

log = logging.getLogger(__name__)

STALE_CLAIM_S = 60          # a claim older than this was abandoned (a client that never read)
JOBS_PER_PROCESS = 500      # then the solver process is replaced, so no slow leak can build up


class PlannerRefused(Exception):
    """Not run: busy, too many, or this user already has a plan running."""

    def __init__(self, status: int, code: str, message: str, retry_after: int):
        super().__init__(message)
        self.status, self.code, self.retry_after = status, code, retry_after


class PlanUnavailable(Exception):
    """The solver could not run (a worker died, or it never answered)."""


class PlanMixUp(Exception):
    """A result that does not prove it answers this request. Never shown."""


def _warm() -> None:
    """Each solver process loads scipy once, when it starts - not on a user's first plan."""
    model.solve(model.Problem("warm", (model.Cell("w", 30, 1, 1),), 30, None))


class Planner:
    def __init__(self, workers: int, *, max_in_flight: int, per_user_per_min: int,
                 executor: Executor | None = None, clock=time.monotonic):
        self.workers = workers
        self.max_in_flight = max_in_flight
        self.per_user_per_min = per_user_per_min
        self._clock = clock
        self._pool = executor or self._new_pool()
        self._owns_pool = executor is None
        self._claims: dict[str, float] = {}             # owner -> when their plan started
        self._recent: dict[str, deque[float]] = {}      # owner -> plan start times, last minute

    def _new_pool(self) -> Executor:
        # SPAWN, never fork. On Linux (production) Python forks by default, and forking a
        # running server copies its threads' locks and its open connections into the
        # child - which can deadlock it. A spawned process starts clean: it imports only
        # the solver, and holds nothing of the server's.
        return ProcessPoolExecutor(max_workers=self.workers, initializer=_warm,
                                   mp_context=multiprocessing.get_context("spawn"),
                                   max_tasks_per_child=JOBS_PER_PROCESS)

    def _recycle(self, why: str) -> None:
        """Replace the solver processes: one died, or one stopped answering. A process
        that ignores its time limit is ended, never left holding a worker."""
        log.error("solver pool replaced: %s", why)
        if not self._owns_pool:
            return
        old, self._pool = self._pool, self._new_pool()
        for proc in list(getattr(old, "_processes", {}).values()):
            proc.terminate()
        old.shutdown(wait=False, cancel_futures=True)
        self.warm()

    def warm(self) -> None:
        """Start the solver processes now (each loads scipy once), so no user's plan
        waits for a process to start. Returns at once; runs alongside boot."""
        for _ in range(self.workers):
            self._pool.submit(int)

    def close(self) -> None:
        if self._owns_pool:
            self._pool.shutdown(wait=False, cancel_futures=True)

    @property
    def in_flight(self) -> int:
        return len(self._claims)

    # ── limits ─────────────────────────────────────────────────────────────
    def claim(self, owner: str) -> None:
        """One plan at a time per user, a few a minute per user, and a cap per task.
        Raises PlannerRefused; on success the caller MUST release(owner)."""
        now = self._clock()
        for o, started in list(self._claims.items()):
            if now - started > STALE_CLAIM_S:
                del self._claims[o]
        if owner in self._claims:
            raise PlannerRefused(429, "PLAN_IN_PROGRESS",
                                 "A plan is already being worked out for you.", 2)
        if len(self._recent) > 1000:                    # forget users idle for a minute
            for o, times in list(self._recent.items()):
                if not times or now - times[-1] >= 60:
                    del self._recent[o]
        recent = self._recent.setdefault(owner, deque())
        while recent and now - recent[0] >= 60:
            recent.popleft()
        if len(recent) >= self.per_user_per_min:
            wait = int(60 - (now - recent[0])) + 1
            raise PlannerRefused(429, "TOO_MANY_PLANS",
                                 f"Too many plans in a minute. Try again in {wait} s.", wait)
        if len(self._claims) >= self.max_in_flight:
            raise PlannerRefused(503, "PLANNER_BUSY", "Genie is busy planning. Try again.", 2)
        self._claims[owner] = now
        recent.append(now)

    def release(self, owner: str) -> None:
        self._claims.pop(owner, None)

    # ── solving ────────────────────────────────────────────────────────────
    async def solve(self, problem: model.Problem, alt: model.Alternatives | None = None) -> dict:
        expected = model.digest(problem, alt)
        loop = asyncio.get_running_loop()
        try:
            out = await asyncio.wait_for(
                loop.run_in_executor(self._pool, model.solve, problem, alt),
                timeout=problem.time_limit_s * 3 + 10)   # queueing + solving, generously
        except BrokenProcessPool:
            self._recycle("a solver process died")
            raise PlanUnavailable("the solver stopped unexpectedly") from None
        except asyncio.TimeoutError:
            self._recycle("a solver process did not answer in time")
            raise PlanUnavailable("the solver did not answer in time") from None
        if out.get("job_id") != problem.job_id or out.get("digest") != expected:
            log.error("plan result refused: it does not prove it answers this request")
            raise PlanMixUp("result does not match the request")
        return out


# ── from the backend's data to a Problem ──────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Prepared:
    problem: model.Problem
    cells: tuple[Cell, ...]             # cells[i] is problem.cells[i]
    plays: dict[str, int]               # days each chosen daypart plays (the start day may be short)
    skipped_no_rate: int
    skipped_no_reach: int


def prepare(q: PlanQuery, avail: Availability, rates: RateCard, reach: Reach, *,
            today: date, budget: Decimal, target: int | None, time_limit_s: float) -> Prepared:
    """One line per city x shop type with free screens. Its price and reach are for ONE
    screen over the whole campaign, every chosen daypart:

        price = sum over dayparts (img x IPM + vid x VPM) / 30 x days that daypart plays
        reach = sum over dayparts daily reach x days that daypart plays

    (CAMPAIGN_CREATION_GUIDE.md §12.2: monthly rate x plays / 5,400 per daypart, which is
    the rate / 30 per screen-day on the 60 s loop.) A daypart that has already ended on a
    start day of today does not play that day (§7.1). A cell with no price for a chosen
    daypart - or a zero price for a slot type the campaign uses - or no reach is left out,
    and counted so the user is told; its price is never taken as free."""
    plays = {d: q.days for d in q.dayparts}
    if q.start == today:
        for d in avail.start_date_unavailable:
            if d in plays:
                plays[d] -= 1
    cells, kept = [], []
    no_rate = no_reach = 0
    for cell, free in sorted(avail.counts.items()):
        city, shop = cell
        price30 = people = 0
        priced = reached = True
        for d in q.dayparts:
            rate = rates.rates.get((city, shop, d))
            if rate is None or (q.image_slots and not rate.ipm_paise) \
                    or (q.video_slots and not rate.vpm_paise):
                priced = False
                break
            daily = reach.daily.get((city, shop, d))
            if not daily:
                reached = False
            price30 += (q.image_slots * (rate.ipm_paise or 0)
                        + q.video_slots * (rate.vpm_paise or 0)) * plays[d]
            people += (daily or 0) * plays[d]
        if not priced:
            no_rate += 1
            continue
        if not reached:
            no_reach += 1
            continue
        if price30 <= 0:
            continue                                    # every daypart already over
        cells.append(model.Cell(group=city, price30=price30, reach=people, free=free))
        kept.append(cell)
    problem = model.Problem(job_id=uuid.uuid4().hex, cells=tuple(cells),
                            budget30=int(budget * 100) * 30, target=target,
                            time_limit_s=time_limit_s)
    return Prepared(problem, tuple(kept), plays, no_rate, no_reach)
