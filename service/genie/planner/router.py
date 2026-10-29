"""POST /v1/genie/plan — the campaign calculator. SSE, like chat:

    event: start    {"requestId"}
    event: result   {"status", "window", "dayparts", "notices", "solver", ...}
    event: plan     one per plan: plan 1, then any alternatives asked for
    event: notice   {"code", "text"}            something found while finding alternatives
    event: done     {"requestId", "latencyMs", "plans"}
    event: error    {"code", "message", "retryable"}   then done

A plan is a campaign as Adgrid books it (CAMPAIGN_CREATION_GUIDE.md §7.6): how many screens
per city x shop type, for the whole window and every chosen daypart. Its price is the
QUOTE from simulate-cost - surcharge days and billing's rounding included - so it is the
price the wizard will show. A plan whose quote is over the budget is never shown: Genie
solves again under a tighter cap (at most three times).

Everything that can be refused is refused BEFORE the stream starts, as a plain HTTP error
with a code: bad input (422), signed out (401), no access (403), too many (429), busy or the
backend down (503), a backend response that breaks the contract (502). Plan 1 is solved and
priced before the stream starts too, so the stream opens with an answer.

The partner is never taken from the request: it is the verified user's own (a `clientId`
hint is accepted only if it is one of that user's grants).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..auth.caller import identity_for, is_local_without_bff, owner_for, token_for
from ..bff import BffError
from ..wire import sse as _sse
from . import model
from .contract import DAYPARTS, ContractError, PlanQuery
from .engine import PlanMixUp, Planner, PlannerRefused, PlanUnavailable, prepare
from .present import notices, plan_json, selections

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/genie", tags=["calculator"])

IST = timezone(timedelta(hours=5, minutes=30))
LOCAL_CLIENT = "local-demo"
QUOTE_ATTEMPTS = 3
QUOTE_OVER_BUDGET = "QUOTE_OVER_BUDGET"
Daypart = Literal["MORNING", "AFTERNOON", "EVENING", "NIGHT"]


class WindowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date = Field(alias="from")
    end: date = Field(alias="to")


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: WindowIn
    dayparts: list[Daypart] = Field(min_length=1, max_length=4)
    image_slots: int = Field(alias="imageSlots", ge=0, le=12)
    video_slots: int = Field(alias="videoSlots", ge=0, le=12)
    advertised_brand_id: str = Field(alias="advertisedBrandId", min_length=1, max_length=100)
    budget: Decimal = Field(gt=0, max_digits=13, decimal_places=2)
    target: int | None = Field(default=None, gt=0, le=10 ** 12)
    cities: list[str] = Field(default_factory=list, max_length=100)
    shop_types: list[str] = Field(default_factory=list, alias="shopTypes", max_length=100)
    alternatives: int = Field(default=0, ge=0, le=2)
    client_id: str | None = Field(default=None, alias="clientId", max_length=100)


def _fail(status: int, code: str, message: str, *, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse({"code": code, "message": message, "retryable": status in (429, 503)},
                        status_code=status, headers=headers)


def _bff_failure(err: BffError, what: str) -> JSONResponse:
    if err.kind == "signed_out":
        return _fail(401, "SIGNED_OUT", "You're signed out. Sign in again to plan.")
    if err.kind == "forbidden":
        if what == "rates":
            return _fail(403, "NO_RATE_ACCESS",
                         "Your account can't see the brand's rate card, so Genie can't price a plan.")
        if what == "quote":
            return _fail(403, "NO_ACCESS", "Your account can't price campaigns for this brand.")
        return _fail(403, "NO_ACCESS", "Your account doesn't have access to plan campaigns.")
    if err.kind == "not_found":
        return _fail(404, "NOT_FOUND", "Genie couldn't find that brand's screens or prices.")
    if err.kind == "rejected":
        return _fail(400, "BACKEND_REFUSED", str(err))
    return _fail(503, "BACKEND_UNAVAILABLE", "Genie can't read free screens or prices right now. "
                                             "Try again.", retry_after=5)


class _Refused(Exception):
    def __init__(self, response: JSONResponse):
        self.response = response


@router.post("/plan")
async def plan(req: PlanIn, request: Request):
    state = request.app.state
    cfg = state.settings
    inputs = getattr(state, "plan_inputs", None)
    planner: Planner = state.planner
    if inputs is None:
        return _fail(503, "CALCULATOR_NOT_CONNECTED",
                     "The calculator isn't connected to screen, price and reach data yet.")

    # ── the inputs, checked ───────────────────────────────────────────────
    start, end = req.window.start, req.window.end
    today = datetime.now(IST).date()
    if req.image_slots == 0 and req.video_slots == 0:
        return _fail(422, "INVALID_SLOTS", "Choose at least one image or video slot.")
    if end < start:
        return _fail(422, "INVALID_WINDOW", "The campaign must end on or after its start date.")
    if start < today:
        return _fail(422, "INVALID_WINDOW", "The campaign can't start in the past.")
    days = (end - start).days + 1
    if days > cfg.genie_plan_max_window_days:
        return _fail(422, "WINDOW_TOO_LONG",
                     f"A plan can cover at most {cfg.genie_plan_max_window_days} days.")
    q = PlanQuery(start, end, tuple(d for d in DAYPARTS if d in req.dayparts),
                  req.image_slots, req.video_slots,
                  cities=tuple(sorted(set(req.cities))), shop_types=tuple(sorted(set(req.shop_types))))
    brand = req.advertised_brand_id

    # ── who is asking: the verified user and THEIR partner ────────────────
    sessions = state.sessions
    token = await token_for(request)
    identity = await identity_for(request, sessions.kv, sessions.prefix, token)
    owner = owner_for(request, identity)
    if owner is None:
        return _fail(401, "SIGNED_OUT", "Genie couldn't verify who you are. Sign in again.")
    if identity is not None:
        grant = identity.grant_for(req.client_id)
        if grant is None:
            return _fail(403, "NO_ACCESS", "You don't have access to plan for that partner.")
        client_id = grant.client_id
    elif is_local_without_bff(request):
        client_id = LOCAL_CLIENT
    else:                                                       # pragma: no cover - owner_for
        return _fail(401, "SIGNED_OUT", "Genie couldn't verify who you are. Sign in again.")

    try:
        planner.claim(owner)
    except PlannerRefused as err:
        return _fail(err.status, err.code, str(err), retry_after=err.retry_after)

    started = time.perf_counter()
    request_id = uuid.uuid4().hex[:12]

    async def quote_of(found: dict) -> Decimal:
        try:
            return await inputs.quote(token, client_id, brand, q, selections(found, prep))
        except BffError as err:
            raise _Refused(_bff_failure(err, "quote")) from None

    async def solve_within_budget(problem: model.Problem) -> tuple[dict, Decimal | None, model.Problem]:
        """Plan 1, priced. If surcharge days lift the quote over the budget, solve again with
        the cap scaled down by budget / quote - never show a plan over budget."""
        cap, out = problem.budget30, None
        budget_paise = int(req.budget * 100)
        for attempt in range(QUOTE_ATTEMPTS):
            problem = dataclasses.replace(problem, job_id=uuid.uuid4().hex, budget30=cap)
            out = await planner.solve(problem)
            if not out["plans"]:
                if attempt and out["status"] in (model.BUDGET_TOO_LOW, model.TARGET_OUT_OF_REACH):
                    break               # list prices fit, the surcharged quote does not
                return out, None, problem
            quote = await quote_of(out["plans"][0])
            if quote <= req.budget:
                return out, quote, problem
            cap = min(cap - 30, cap * budget_paise // int(quote * 100))
        return {**out, "status": QUOTE_OVER_BUDGET, "plans": []}, None, problem

    handed_off = False              # once the stream owns the claim, the stream releases it
    try:
        reads = await asyncio.gather(inputs.availability(token, owner, client_id, q),
                                     inputs.rate_cards(token, owner, client_id, brand),
                                     inputs.reach(token, owner, client_id), return_exceptions=True)
        for what, got in zip(("free", "rates", "reach"), reads):
            if isinstance(got, BffError):
                return _bff_failure(got, what)
            if isinstance(got, BaseException):
                raise got
        avail, rates, reach = reads
        if avail.start_date_fully_closed:
            later = f" The earliest start is {avail.earliest_start}." if avail.earliest_start else ""
            return _fail(422, "START_DATE_CLOSED",
                         f"Every chosen daypart on {start.isoformat()} has already ended.{later}")
        prep = prepare(q, avail, rates, reach, today=today, budget=req.budget, target=req.target,
                       time_limit_s=cfg.genie_plan_time_limit_s)
        try:
            first, first_quote, problem = await solve_within_budget(prep.problem)
        except PlanUnavailable as err:
            return _fail(503, "PLANNER_UNAVAILABLE", f"Genie couldn't plan right now: {err}.",
                         retry_after=2)
        except PlanMixUp:
            return _fail(500, "PLAN_REFUSED", "Genie refused a result it couldn't verify. Try again.")
        handed_off = True
    except _Refused as refused:
        return refused.response
    except ContractError as err:
        log.error("planning data broke the contract: %s", err)
        return _fail(502, "BAD_BACKEND_DATA",
                     "The screen or price data didn't match what Genie expects, so no plan was made.")
    finally:
        if not handed_off:
            planner.release(owner)

    log.info("plan %s: %d cells, status %s, %s ms, %s solves", request_id,
             len(prep.problem.cells), first["status"], first["ms"], first["solves"])
    counted = {"plans": 0}
    within = cfg.genie_plan_alternatives_within_pct

    async def events() -> AsyncIterator[str]:
        found_notices = notices(first, prep, q, budget=req.budget, target=req.target)
        if first["status"] == QUOTE_OVER_BUDGET:
            found_notices.append({"code": QUOTE_OVER_BUDGET,
                                  "text": "With surcharge days, no plan Genie found fits the budget. "
                                          "Try a slightly bigger budget."})
        yield _sse("start", {"request_id": request_id})
        yield _sse("result", {
            "status": first["status"],
            "window": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
            "dayparts": list(q.dayparts), "imageSlots": q.image_slots, "videoSlots": q.video_slots,
            "advertisedBrandId": brand, "budget": str(req.budget.quantize(Decimal("0.01"))),
            "target": req.target, "reachVersion": reach.version, "notices": found_notices,
            "solver": {"ms": first["ms"], "solves": first["solves"],
                       "tieBreakFinished": first.get("tiebreaks_unfinished", 0) == 0}})
        if not first["plans"]:
            return
        plan1 = first["plans"][0]
        yield _sse("plan", plan_json(1, plan1, prep, q, budget=req.budget, quote=first_quote,
                                     status=first["status"], target=req.target, brand_id=brand))
        counted["plans"] = 1
        if not req.alternatives or await request.is_disconnected():
            return
        alt = model.Alternatives(mode=first["mode"], shown=(tuple(plan1["groups"]),),
                                 cost30=plan1["cost30"], reach=plan1["reach"],
                                 count=req.alternatives, within_pct=within)
        try:
            more = await planner.solve(problem, alt)
        except (PlanUnavailable, PlanMixUp) as err:
            yield _sse("error", {"code": "ALTERNATIVES_FAILED", "message": str(err), "retryable": True})
            return
        over = 0
        for found in more["plans"]:
            quote = await quote_of(found)
            if quote > req.budget:
                over += 1
                continue
            counted["plans"] += 1
            yield _sse("plan", plan_json(counted["plans"], found, prep, q, budget=req.budget,
                                         quote=quote, status=first["status"], target=req.target,
                                         brand_id=brand, first=plan1, first_quote=first_quote))
        if more["cut_short"]:
            yield _sse("notice", {"code": "ALTERNATIVES_CUT_SHORT",
                                  "text": "Genie ran out of time looking for more alternatives."})
        elif counted["plans"] - 1 < req.alternatives:
            text = f"No other mix of cities comes within {within}% of plan 1"
            text += " and the budget, once surcharge days are priced." if over else "."
            yield _sse("notice", {"code": "NO_MORE_ALTERNATIVES", "text": text})

    async def stream() -> AsyncIterator[str]:
        try:
            try:
                async for chunk in events():
                    yield chunk
            except _Refused as refused:              # a quote refused part-way (alternatives)
                body = refused.response.body.decode()
                log.warning("plan %s: an alternative could not be priced: %s", request_id, body)
                yield _sse("error", {"code": "ALTERNATIVES_FAILED",
                                     "message": "Genie couldn't price the alternatives.",
                                     "retryable": True})
            except Exception:  # noqa: BLE001 - the stream must not end without saying so
                log.exception("plan %s failed part-way", request_id)
                yield _sse("error", {"code": "INTERNAL_ERROR",
                                     "message": "Genie couldn't finish this plan. Try again.",
                                     "retryable": True})
            yield _sse("done", {"request_id": request_id, "plans": counted["plans"],
                                "latency_ms": int((time.perf_counter() - started) * 1000)})
        finally:
            planner.release(owner)      # however the stream ends, including a closed tab

    return StreamingResponse(
        stream(), media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})
