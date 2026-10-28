"""POST /v1/genie/plan — the campaign calculator. SSE, like chat:

    event: start    {"requestId"}
    event: result   {"status", "window", "versions", "notices", "solver"}
    event: plan     one per plan: plan 1, then any alternatives asked for
    event: notice   {"code", "text"}            something found while finding alternatives
    event: done     {"requestId", "latencyMs", "plans"}
    event: error    {"code", "message", "retryable"}   then done

Everything that can be refused is refused BEFORE the stream starts, as a plain HTTP
error with a code: bad input (422), signed out (401), no access (403), too many (429),
busy or the backend down (503), a backend response that breaks the contract (502).
Plan 1 is solved before the stream starts too, so the stream opens with an answer.

The partner is never taken from the request: it is the verified user's own (a
`clientId` hint is accepted only if it is one of that user's grants).
"""
from __future__ import annotations

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
from . import model
from .contract import AvailabilityQuery, ContractError
from .engine import PlanMixUp, Planner, PlannerRefused, PlanUnavailable, prepare
from ..wire import sse as _sse
from .present import notices, plan_json

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/genie", tags=["calculator"])

IST = timezone(timedelta(hours=5, minutes=30))
LOCAL_CLIENT = "local-demo"


class WindowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date = Field(alias="from")
    end: date = Field(alias="to")


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: WindowIn
    budget: Decimal = Field(gt=0, max_digits=13, decimal_places=2)
    target: int | None = Field(default=None, gt=0, le=10 ** 12)
    image_slots: int = Field(alias="imageSlots", ge=0, le=12)
    video_slots: int = Field(alias="videoSlots", ge=0, le=12)
    cities: list[str] = Field(default_factory=list, max_length=500)
    categories: list[str] = Field(default_factory=list, max_length=100)
    dayparts: list[Literal["MORNING", "AFTERNOON", "EVENING", "NIGHT"]] = Field(
        default_factory=list, max_length=4)
    alternatives: int = Field(default=0, ge=0, le=2)
    client_id: str | None = Field(default=None, alias="clientId", max_length=100)


def _fail(status: int, code: str, message: str, *, retry_after: int | None = None) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse({"code": code, "message": message, "retryable": status in (429, 503)},
                        status_code=status, headers=headers)


def _bff_failure(err: BffError, *, rates: bool) -> JSONResponse:
    if err.kind == "signed_out":
        return _fail(401, "SIGNED_OUT", "You're signed out. Sign in again to plan.")
    if err.kind == "forbidden":
        if rates:
            return _fail(403, "NO_RATE_ACCESS",
                         "Your account can't see the rate card, so Genie can't price a plan.")
        return _fail(403, "NO_ACCESS", "Your account doesn't have access to plan campaigns.")
    if err.kind == "not_found":
        return _fail(404, "NOT_FOUND", "Genie couldn't find your screens.")
    if err.kind == "rejected":
        return _fail(400, "BACKEND_REFUSED", str(err))
    return _fail(503, "BACKEND_UNAVAILABLE", "Genie can't read free screens right now. Try again.",
                 retry_after=5)


@router.post("/plan")
async def plan(req: PlanIn, request: Request):
    state = request.app.state
    cfg = state.settings
    inputs = getattr(state, "plan_inputs", None)
    planner: Planner = state.planner
    if inputs is None:
        return _fail(503, "CALCULATOR_NOT_CONNECTED",
                     "The calculator isn't connected to free-screen data yet.")

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
    handed_off = False              # once the stream owns the claim, the stream releases it
    try:
        q = AvailabilityQuery(start, end, req.image_slots, req.video_slots,
                              cities=tuple(sorted(set(req.cities))),
                              categories=tuple(sorted(set(req.categories))),
                              dayparts=tuple(sorted(set(req.dayparts))))
        try:
            avail = await inputs.availability(token, owner, client_id, q)
        except BffError as err:
            return _bff_failure(err, rates=False)
        try:
            rr = await inputs.rates_and_reach(token, owner, client_id,
                                              avail.rates_version, avail.reach_version)
        except BffError as err:
            return _bff_failure(err, rates=True)

        prep = prepare(avail, rr, budget=req.budget, target=req.target,
                       time_limit_s=cfg.genie_plan_time_limit_s)
        try:
            first = await planner.solve(prep.problem)
        except PlanUnavailable as err:
            return _fail(503, "PLANNER_UNAVAILABLE", f"Genie couldn't plan right now: {err}.",
                         retry_after=2)
        except PlanMixUp:
            return _fail(500, "PLAN_REFUSED", "Genie refused a result it couldn't verify. Try again.")
        handed_off = True
    except ContractError as err:
        log.error("planning data broke the contract: %s", err)
        return _fail(502, "BAD_BACKEND_DATA",
                     "The free-screen data didn't match what Genie expects, so no plan was made.")
    finally:
        if not handed_off:
            planner.release(owner)

    log.info("plan %s: %d lines, status %s, %s ms, %s solves", request_id,
             len(prep.problem.lines), first["status"], first["ms"], first["rounds"])

    counted = {"plans": 0}

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {"request_id": request_id})
        yield _sse("result", {
            "status": first["status"],
            "window": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
            "budget": str(req.budget.quantize(Decimal("0.01"))), "target": req.target,
            "imageSlots": req.image_slots, "videoSlots": req.video_slots,
            "versions": {"ratesVersion": rr.rates_version, "reachVersion": rr.reach_version},
            "notices": notices(first, prep, avail, budget=req.budget, target=req.target),
            "solver": {"ms": first["ms"], "solves": first["rounds"],
                       "tieBreakFinished": first.get("tiebreaks_unfinished", 0) == 0}})
        if not first["plans"]:
            return
        plan1 = first["plans"][0]
        yield _sse("plan", plan_json(1, plan1, prep, avail, budget=req.budget,
                                     status=first["status"], target=req.target))
        counted["plans"] = 1
        if not req.alternatives or await request.is_disconnected():
            return
        alt = model.Alternatives(
            mode=first["mode"], shown=(tuple(plan1["contracts"]),), cost30=plan1["cost30"],
            reach=plan1["reach"], count=req.alternatives,
            within_pct=cfg.genie_plan_alternatives_within_pct)
        try:
            more = await planner.solve(prep.problem, alt)
        except (PlanUnavailable, PlanMixUp) as err:
            yield _sse("error", {"code": "ALTERNATIVES_FAILED", "message": str(err),
                                 "retryable": True})
            return
        for found in more["plans"]:
            counted["plans"] += 1
            yield _sse("plan", plan_json(counted["plans"], found, prep, avail, budget=req.budget,
                                         status=first["status"], target=req.target, first=plan1))
        if more["cut_short"]:
            yield _sse("notice", {"code": "ALTERNATIVES_CUT_SHORT",
                                  "text": "Genie ran out of time looking for more alternatives."})
        elif len(more["plans"]) < req.alternatives:
            yield _sse("notice", {
                "code": "NO_MORE_ALTERNATIVES",
                "text": f"No other mix of contracts comes within "
                        f"{cfg.genie_plan_alternatives_within_pct}% of plan 1."})

    async def stream() -> AsyncIterator[str]:
        try:
            try:
                async for chunk in events():
                    yield chunk
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
