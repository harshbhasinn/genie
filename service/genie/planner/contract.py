"""The calculator's data, in the shapes the BFF already serves.

How Adgrid books a non-premium campaign (CAMPAIGN_CREATION_GUIDE.md §7.2-§7.6, §12.2): one
window, ONE set of dayparts, one image/video slot choice, and `selections` of "N screens in
city X, shop type Y". The same screens play every date and every chosen daypart; a screen
qualifies only if it is free in all of them. Contracts are chosen by the backend at the
hold, never by the user. So the calculator decides one thing: how many screens per
city x shop type.

Four reads feed it, each checked field by field before anything is planned from it:

  availability-search   free screens per city x shop type, for the whole window   (BFF)
  client-rate-cards     the partner's price to the brand, city x shop type x daypart (BFF)
  simulate-cost         the exact brand price of a plan - surcharges, rounding    (BFF)
  reach                 people one screen reaches a day, city x shop type x daypart (no API yet)

A response that breaks its contract is refused whole (ContractError): a plan is never
priced from a half-read or misread response.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

DAYPARTS = ("MORNING", "AFTERNOON", "EVENING", "NIGHT")
DEVICE_TIER = "NON_PREMIUM"          # v1 plans the city x shop-type tier only (premium books by store)
SEARCH_PAGE_SIZE = 200


class ContractError(ValueError):
    """A backend response that does not match the agreed contract."""


Cell = tuple[str, str]                       # (city, shop type)


# ── what is asked ─────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class PlanQuery:
    start: date
    end: date
    dayparts: tuple[str, ...]                # in DAYPARTS order
    image_slots: int
    video_slots: int
    cities: tuple[str, ...] = ()
    shop_types: tuple[str, ...] = ()

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def dates(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range(self.days)]

    def search_body(self, client_id: str) -> dict:
        """availability-search's request (§7.2)."""
        body = {"clientId": client_id, "startDate": self.start.isoformat(),
                "endDate": self.end.isoformat(), "timeSlots": list(self.dayparts),
                "imageSlotsRequested": self.image_slots, "videoSlotsRequested": self.video_slots,
                "deviceTier": DEVICE_TIER, "page": 0, "size": SEARCH_PAGE_SIZE}
        if self.cities:
            body["cities"] = list(self.cities)
        if self.shop_types:
            body["shopTypes"] = list(self.shop_types)
        return body

    def quote_body(self, client_id: str, brand_id: str, selections: list[dict]) -> dict:
        """simulate-cost's request (§7.5). No campaignId, ever: without one, billing saves
        nothing - that is what makes this a read.

        Its selections are ONE ROW PER DAYPART, each with a `timeSlot` (§7.5; QA refuses a
        row without one: "selections[0].timeSlot: must not be null", 2026-10-29). The
        create-campaign selections the wizard takes have no daypart (§7.6), so the plan's
        own `campaign` block keeps them without."""
        return {"clientId": client_id, "advertisedBrandId": brand_id,
                "startDate": self.start.isoformat(), "endDate": self.end.isoformat(),
                "timeSlots": list(self.dayparts), "imageSlotsRequested": self.image_slots,
                "videoSlotsRequested": self.video_slots, "deviceTier": DEVICE_TIER,
                "selections": [{**s, "timeSlot": d} for s in selections for d in self.dayparts]}

    def key(self) -> str:
        """Stable text for a cache key: the same question always gives the same text."""
        return "|".join([self.start.isoformat(), self.end.isoformat(), ",".join(self.dayparts),
                         str(self.image_slots), str(self.video_slots), ",".join(self.cities),
                         ",".join(self.shop_types)])


# ── parsed answers ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Availability:
    counts: dict[Cell, int]                  # screens free in every date x chosen daypart
    start_date_unavailable: tuple[str, ...]  # dayparts on the start day that have already ended
    start_date_fully_closed: bool
    earliest_start: str | None


@dataclass(frozen=True, slots=True)
class Rate:
    ipm_paise: int | None                    # image slot, per screen, per month - None: not set
    vpm_paise: int | None


@dataclass(frozen=True, slots=True)
class RateCard:
    rates: dict[tuple[str, str, str], Rate]  # (city, shop type, daypart)
    brand_specific: int                      # how many came from the brand's own rows


@dataclass(frozen=True, slots=True)
class Reach:
    version: str
    daily: dict[tuple[str, str, str], int]   # (city, shop type, daypart) -> people per screen a day


# ── field checks ──────────────────────────────────────────────────────────────

def _text(d: dict, name: str, where: str, *, required: bool = True) -> str | None:
    v = d.get(name)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise ContractError(f"{where}.{name} must be a non-empty string")
    return v


def _int(d: dict, name: str, where: str, *, required: bool = True, low: int = 0) -> int | None:
    v = d.get(name)
    if v is None and not required:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < low:
        raise ContractError(f"{where}.{name} must be an integer >= {low}")
    return v


def _list(d: dict, name: str, where: str) -> list:
    v = d.get(name)
    if v is None:
        return []
    if not isinstance(v, list):
        raise ContractError(f"{where}.{name} must be a list")
    return v


def _money_paise(v, where: str, *, required: bool) -> int | None:
    """A decimal string ("1000.00") as whole paise. More than 2 decimals is refused."""
    if v is None and not required:
        return None
    if not isinstance(v, str):
        raise ContractError(f"{where} must be a decimal string")
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise ContractError(f"{where} is not a number") from None
    if not d.is_finite() or d < 0:
        raise ContractError(f"{where} must be a number >= 0")
    paise = d * 100
    if paise != paise.to_integral_value():
        raise ContractError(f"{where} has more than 2 decimal places")
    return int(paise)


def _daypart(v, where: str) -> str:
    if v not in DAYPARTS:
        raise ContractError(f"{where} {v!r} is not one of {', '.join(DAYPARTS)}")
    return v


# ── parsers ───────────────────────────────────────────────────────────────────

def parse_availability(data, q: PlanQuery) -> Availability:
    """availability-search's `data`, non-premium shape: cities -> shop types -> count."""
    if not isinstance(data, dict):
        raise ContractError("availability: data must be an object")
    counts: dict[Cell, int] = {}
    for i, c in enumerate(_list(data, "cities", "availability")):
        where = f"cities[{i}]"
        if not isinstance(c, dict):
            raise ContractError(f"{where} must be an object")
        city = _text(c, "city", where)
        for j, s in enumerate(_list(c, "shopTypes", where)):
            w = f"{where}.shopTypes[{j}]"
            if not isinstance(s, dict):
                raise ContractError(f"{w} must be an object")
            cell = (city, _text(s, "shopType", w))
            if cell in counts:
                raise ContractError(f"{w}: {cell[0]} x {cell[1]} twice")
            count = _int(s, "count", w)
            if (q.cities and city not in q.cities) or (q.shop_types and cell[1] not in q.shop_types):
                continue                                  # the filter is ours too
            if count:
                counts[cell] = count
    closed = data.get("startDateFullyClosed")
    if closed is not None and not isinstance(closed, bool):
        raise ContractError("availability.startDateFullyClosed must be true or false")
    ended = tuple(_daypart(v, "availability.startDateUnavailableSlots[]")
                  for v in _list(data, "startDateUnavailableSlots", "availability"))
    return Availability(counts=counts, start_date_unavailable=ended,
                        start_date_fully_closed=bool(closed),
                        earliest_start=_text(data, "earliestStartDate", "availability", required=False))


def parse_rate_cards(data, partner_id: str, brand_id: str) -> RateCard:
    """client-rate-cards' `data`. The brand's own row wins over the partner-wide row
    (§12.2: brand-specific -> partner-wide); other brands' rows are not this brand's price."""
    if not isinstance(data, dict):
        raise ContractError("rate cards: data must be an object")
    brand: dict[tuple[str, str, str], Rate] = {}
    partner: dict[tuple[str, str, str], Rate] = {}
    for i, r in enumerate(_list(data, "rateCards", "rate cards")):
        where = f"rateCards[{i}]"
        if not isinstance(r, dict):
            raise ContractError(f"{where} must be an object")
        kind = r.get("rateCardType")
        if kind not in (None, "BROADCAST"):
            continue                                       # TARGETED: CPMs, not this plan
        owner = r.get("clientId")
        if owner == brand_id:
            book = brand
        elif owner in (None, "", partner_id):
            book = partner
        else:
            continue                                       # another brand's price
        key = (_text(r, "city", where), _text(r, "shopType", where),
               _daypart(r.get("timeSlot"), f"{where}.timeSlot"))
        if key in book:
            raise ContractError(f"{where}: the same city x shop type x daypart twice")
        book[key] = Rate(_money_paise(r.get("imageSlotRate"), f"{where}.imageSlotRate", required=False),
                         _money_paise(r.get("videoSlotRate"), f"{where}.videoSlotRate", required=False))
    return RateCard(rates={**partner, **brand}, brand_specific=len(brand))


def parse_reach(data) -> Reach:
    """The reach table (no BFF endpoint yet):
    {"reachVersion", "reach": [{city, shopType, daypart, dailyReachPerDevice}]}."""
    if not isinstance(data, dict):
        raise ContractError("reach: data must be an object")
    daily: dict[tuple[str, str, str], int] = {}
    for i, r in enumerate(_list(data, "reach", "reach")):
        where = f"reach[{i}]"
        if not isinstance(r, dict):
            raise ContractError(f"{where} must be an object")
        key = (_text(r, "city", where), _text(r, "shopType", where),
               _daypart(r.get("daypart"), f"{where}.daypart"))
        if key in daily:
            raise ContractError(f"{where}: the same city x shop type x daypart twice")
        daily[key] = _int(r, "dailyReachPerDevice", where)
    return Reach(version=_text(data, "reachVersion", "reach"), daily=daily)


def parse_quote(data) -> Decimal:
    """simulate-cost's brand price (`totalCost`), in rupees. Only that field is read: the
    response may also carry Adgrid's own leg, which a partner must never be shown."""
    if not isinstance(data, dict):
        raise ContractError("quote: data must be an object")
    paise = _money_paise(data.get("totalCost"), "quote.totalCost", required=True)
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))
