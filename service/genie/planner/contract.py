"""The calculator's data, exactly as design/calculator/availability-api-spec.md defines it.

Two backend responses come in (A: free screens per date, B: rates and reach), and both
are checked field by field before anything is planned from them. A response that breaks
the contract is refused whole (ContractError) - a plan is never priced from a half-read
or misread response.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

DAYPARTS = ("MORNING", "AFTERNOON", "EVENING", "NIGHT")


class ContractError(ValueError):
    """A backend response that does not match the agreed contract."""


# ── what the backend is asked ─────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class AvailabilityQuery:
    start: date
    end: date
    image_slots: int
    video_slots: int
    cities: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    dayparts: tuple[str, ...] = ()
    contract_ids: tuple[str, ...] = ()

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def params(self) -> list[tuple[str, str]]:
        """Query parameters for endpoint A; repeatable filters repeat."""
        out = [("from", self.start.isoformat()), ("to", self.end.isoformat()),
               ("imageSlots", str(self.image_slots)), ("videoSlots", str(self.video_slots))]
        for name, values in (("cities", self.cities), ("categories", self.categories),
                             ("dayparts", self.dayparts), ("contractIds", self.contract_ids)):
            out += [(name, v) for v in values]
        return out

    def key(self) -> str:
        """Stable text for a cache key: the same query always gives the same text."""
        return "&".join(f"{k}={v}" for k, v in self.params())


# ── endpoint A: free screens ──────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Row:
    contract_id: str
    contract_code: str
    city: str
    city_name: str | None
    category: str
    daypart: str
    free: tuple[int, ...]               # free[i] = free devices on start + i days
    total_devices: int | None

    @property
    def rate_key(self) -> tuple[str, str, str, str]:
        return (self.contract_id, self.city, self.category, self.daypart)

    @property
    def reach_key(self) -> tuple[str, str, str]:
        return (self.city, self.category, self.daypart)


@dataclass(frozen=True, slots=True)
class Availability:
    client_id: str
    start: date
    end: date
    image_slots: int
    video_slots: int
    rates_version: str
    reach_version: str
    generated_at: str
    rows: tuple[Row, ...]

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


# ── endpoint B: rates and reach ───────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Rate:
    ipm_paise: int                      # image slot, per device, per 30 days
    vpm_paise: int                      # video slot, the same


@dataclass(frozen=True, slots=True)
class RatesAndReach:
    client_id: str
    currency: str
    rates_version: str
    reach_version: str
    rates: dict[tuple[str, str, str, str], Rate]     # contract x city x category x daypart
    reach: dict[tuple[str, str, str], int]           # city x category x daypart


# ── parsing ───────────────────────────────────────────────────────────────────

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


def _date(d: dict, name: str, where: str) -> date:
    try:
        return date.fromisoformat(_text(d, name, where))
    except ValueError:
        raise ContractError(f"{where}.{name} must be a date YYYY-MM-DD") from None


def _paise(v, where: str) -> int:
    """A decimal string ("1000.00") as whole paise. More than 2 decimals is refused."""
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


def parse_availability(data, q: AvailabilityQuery, client_id: str) -> Availability:
    """Endpoint A's `data`, checked against the question that was asked."""
    if not isinstance(data, dict):
        raise ContractError("availability: data must be an object")
    if _text(data, "clientId", "availability") != client_id:
        raise ContractError("availability: clientId is not the partner that was asked for")
    window = data.get("window")
    if not isinstance(window, dict):
        raise ContractError("availability.window must be an object")
    start, end = _date(window, "from", "window"), _date(window, "to", "window")
    if (start, end) != (q.start, q.end):
        raise ContractError("availability: window is not the window that was asked for")
    days = _int(window, "days", "window", low=1)
    if days != q.days:
        raise ContractError(f"availability: window.days is {days}, the window has {q.days}")
    if (_int(data, "imageSlots", "availability"), _int(data, "videoSlots", "availability")) \
            != (q.image_slots, q.video_slots):
        raise ContractError("availability: slots are not the slots that were asked for")
    rows_in = data.get("rows")
    if not isinstance(rows_in, list):
        raise ContractError("availability.rows must be a list")
    rows: list[Row] = []
    seen: set[tuple[str, str, str, str]] = set()
    for i, r in enumerate(rows_in):
        where = f"rows[{i}]"
        if not isinstance(r, dict):
            raise ContractError(f"{where} must be an object")
        daypart = _text(r, "daypart", where)
        if daypart not in DAYPARTS:
            raise ContractError(f"{where}.daypart {daypart!r} is not one of {', '.join(DAYPARTS)}")
        free = r.get("freeDevices")
        if not isinstance(free, list) or len(free) != days:
            raise ContractError(f"{where}.freeDevices must be a list of {days} integers")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in free):
            raise ContractError(f"{where}.freeDevices must hold integers >= 0")
        row = Row(contract_id=_text(r, "contractId", where),
                  contract_code=_text(r, "contractCode", where),
                  city=_text(r, "city", where),
                  city_name=_text(r, "cityName", where, required=False),
                  category=_text(r, "category", where), daypart=daypart, free=tuple(free),
                  total_devices=_int(r, "totalDevices", where, required=False))
        if row.rate_key in seen:
            raise ContractError(f"{where}: the same contract x city x category x daypart twice")
        seen.add(row.rate_key)
        rows.append(row)
    return Availability(client_id=client_id, start=start, end=end,
                        image_slots=q.image_slots, video_slots=q.video_slots,
                        rates_version=_text(data, "ratesVersion", "availability"),
                        reach_version=_text(data, "reachVersion", "availability"),
                        generated_at=_text(data, "generatedAt", "availability"),
                        rows=tuple(rows))


def parse_rates_and_reach(data, client_id: str) -> RatesAndReach:
    """Endpoint B's `data`."""
    if not isinstance(data, dict):
        raise ContractError("rates-and-reach: data must be an object")
    if _text(data, "clientId", "rates-and-reach") != client_id:
        raise ContractError("rates-and-reach: clientId is not the partner that was asked for")
    currency = _text(data, "currency", "rates-and-reach")
    if currency != "INR":
        raise ContractError(f"rates-and-reach: currency {currency!r} - only INR is planned")
    rates: dict[tuple[str, str, str, str], Rate] = {}
    for i, r in enumerate(data.get("rates") if isinstance(data.get("rates"), list) else _bad("rates")):
        where = f"rates[{i}]"
        if not isinstance(r, dict):
            raise ContractError(f"{where} must be an object")
        daypart = _text(r, "daypart", where)
        if daypart not in DAYPARTS:
            raise ContractError(f"{where}.daypart {daypart!r} is not a daypart")
        key = (_text(r, "contractId", where), _text(r, "city", where),
               _text(r, "category", where), daypart)
        if key in rates:
            raise ContractError(f"{where}: the same contract x city x category x daypart twice")
        rates[key] = Rate(_paise(r.get("imageSlotRatePerMonth"), f"{where}.imageSlotRatePerMonth"),
                          _paise(r.get("videoSlotRatePerMonth"), f"{where}.videoSlotRatePerMonth"))
    reach: dict[tuple[str, str, str], int] = {}
    for i, r in enumerate(data.get("reach") if isinstance(data.get("reach"), list) else _bad("reach")):
        where = f"reach[{i}]"
        if not isinstance(r, dict):
            raise ContractError(f"{where} must be an object")
        daypart = _text(r, "daypart", where)
        if daypart not in DAYPARTS:
            raise ContractError(f"{where}.daypart {daypart!r} is not a daypart")
        key = (_text(r, "city", where), _text(r, "category", where), daypart)
        if key in reach:
            raise ContractError(f"{where}: the same city x category x daypart twice")
        reach[key] = _int(r, "dailyReachPerDevice", where)
    return RatesAndReach(client_id=client_id, currency=currency,
                         rates_version=_text(data, "ratesVersion", "rates-and-reach"),
                         reach_version=_text(data, "reachVersion", "rates-and-reach"),
                         rates=rates, reach=reach)


def _bad(name: str):
    raise ContractError(f"rates-and-reach.{name} must be a list")
