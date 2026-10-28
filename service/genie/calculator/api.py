"""POST /v1/genie/calculator - the campaign that buys the most plays for a budget.

Plain JSON in and out, no model, and the same snapshot always gives the same plan. The price is
what the brand pays, worked out as billing works it out (engine.py); the launch wizard's own
preview confirms it when the user opens the prefilled form. Genie never submits anything.

Errors are {"error": {"code", "message"}} with the status that says what to do about them:

  401 SIGNED_OUT        no token, or the BFF would not say whose it is
  403 FORBIDDEN         not a client this user works for, or the BFF refused the read
  400 BAD_ASK           the request makes no plan (dates, slots, budget)
  422 REJECTED          the BFF refused the parameters (e.g. a brand not under this client)
  502 BAD_INPUTS        the snapshot came back malformed - no plan is built on a misread
  503 UNAVAILABLE       the BFF is down, or does not serve calculator-inputs yet
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth.request import identity_for, token_for
from ..bff import BffError
from ..data.fields import today
from ..data.money import inr
from .engine import Ask, AskError, Plan, alternatives, plan, prefill
from .inputs import InputsError
from .source import InputsUnavailable, fetch

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/genie", tags=["calculator"])

MAX_DAYS = 92
TimeSlot = Literal["MORNING", "AFTERNOON", "EVENING", "NIGHT"]

_BFF_STATUS = {"signed_out": (401, "SIGNED_OUT"), "forbidden": (403, "FORBIDDEN"),
               "not_found": (404, "NOT_FOUND"), "rejected": (422, "REJECTED"),
               "unavailable": (503, "UNAVAILABLE"), "bad_response": (502, "BAD_INPUTS")}


class CalculatorRequest(BaseModel):
    advertised_brand_id: str = Field(min_length=1)
    start_date: date
    end_date: date
    time_slots: list[TimeSlot] = Field(min_length=1, max_length=4)
    image_slots: int = Field(ge=0, le=6)          # a 60 s loop has 6 image and 4 video slots
    video_slots: int = Field(ge=0, le=4)
    budget: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    target_plays: int | None = Field(default=None, gt=0)
    cities: list[str] | None = None
    shop_types: list[str] | None = None
    client_id: str | None = None                  # default: the user's own client
    alternatives: bool = True


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _money(d: Decimal | None) -> dict | None:
    return None if d is None else {"amount": f"{d:.2f}", "text": inr(d)}


def _plan_json(p: Plan) -> dict:
    return {
        "screens": p.screens, "plays": p.plays, "price": _money(p.price), "cpm": _money(p.cpm),
        "budget": _money(p.budget), "target_plays": p.target_plays,
        "limited_by": p.limited_by, "optimal": p.optimal,
        "smallest_price": _money(p.smallest_price),
        "lines": [{"city": line.city, "shop_type": line.shop_type, "screens": line.screens,
                   "available": line.available, "plays": line.plays, "price": _money(line.price)}
                  for line in p.lines],
        "excluded": [{"city": e.city, "shop_type": e.shop_type, "reason": e.reason}
                     for e in p.excluded],
    }


def _check_dates(req: CalculatorRequest) -> str | None:
    if req.end_date < req.start_date:
        return "the campaign ends before it starts"
    if req.start_date < today():
        return "the campaign cannot start in the past"
    if (req.end_date - req.start_date) >= timedelta(days=MAX_DAYS):
        return f"plan at most {MAX_DAYS} days at a time"
    if len(set(req.time_slots)) != len(req.time_slots):
        return "each time slot once"
    if (req.budget is None) == (req.target_plays is None):
        return "give a budget or a number of plays, not both and not neither"
    if req.image_slots + req.video_slots == 0:
        return "ask for at least one image or video slot"
    return None


@router.post("/calculator")
async def calculator(req: CalculatorRequest, request: Request):
    problem = _check_dates(req)
    if problem:
        return _error(400, "BAD_ASK", problem)

    bff = getattr(request.app.state, "bff", None)
    if bff is None:
        return _error(503, "UNAVAILABLE", "this Genie has no dashboard API to read from")
    token = await token_for(request)
    identity = await identity_for(request, token)
    if not token or identity is None:
        return _error(401, "SIGNED_OUT", "sign in to the dashboard again")
    grant = identity.grant_for(req.client_id)
    if grant is None:
        return _error(403, "FORBIDDEN", "that is not a client you work for")

    store = request.app.state.sessions
    try:
        inputs = await fetch(bff, store.kv, store.prefix, token, client_id=grant.client_id,
                             brand_id=req.advertised_brand_id, start=req.start_date,
                             end=req.end_date, time_slots=tuple(req.time_slots),
                             cities=tuple(req.cities) if req.cities else None,
                             shop_types=tuple(req.shop_types) if req.shop_types else None)
    except InputsUnavailable as err:
        return _error(503, "UNAVAILABLE", str(err))
    except BffError as err:
        status, code = _BFF_STATUS.get(err.kind, (503, "UNAVAILABLE"))
        return _error(status, code, str(err))
    except InputsError as err:
        log.warning("calculator-inputs was malformed: %s", err)
        return _error(502, "BAD_INPUTS", "the inventory snapshot could not be read")

    ask = Ask(req.start_date, req.end_date, tuple(req.time_slots), req.image_slots, req.video_slots,
              budget=req.budget, target_plays=req.target_plays,
              cities=frozenset(req.cities) if req.cities else None,
              shop_types=frozenset(req.shop_types) if req.shop_types else None)
    try:
        best = plan(inputs, ask)
        alts = alternatives(inputs, ask, best) if req.alternatives and best.screens else []
    except AskError as err:
        return _error(400, "BAD_ASK", str(err))

    return {
        "plan": _plan_json(best),
        "alternatives": [{"without_city": city, "plan": _plan_json(p)} for city, p in alts],
        "prefill": prefill(best, ask) if best.screens else None,
        "basis": "brand_price",
        "snapshot_at": inputs.snapshot_at,
        "rates_version": inputs.rates.version,
    }
