"""Which screens fit, what they cost the brand, and the campaign that buys the most plays.

Three rules carry the whole engine (CALCULATOR-INPUTS-API.md §5):

  FIT     a campaign books the same screens on every chosen day and daypart, so a group of
          screens counts only if its tightest cell still has room: free >= asked, in every
          open cell (guide §7.2 step 5). Per-day counts are never added up.
  PRICE   the brand leg is billing's own formula - monthly rate x plays / plays per month,
          divide last, each image and video leg rounded once (guide §12.2) - and the brand
          rate is keyed by city x shop type x daypart only, so it does not depend on WHICH
          screens the booking takes. Genie's figure is the bill, not a guess at it.
  CHOOSE  every screen in a plan plays the same loop for the same cells, so every screen buys
          the same plays; the most plays for a budget is then the most screens, which is the
          cheapest screens first. Greedy is exact here, not a heuristic - and `optimal` says
          when that premise does not hold.

Cells already over today (closedCells) are neither checked nor counted, as search drops them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .inputs import TIME_SLOTS, Group, Inputs, RateCell

DAYPART_SECONDS = 3 * 60 * 60
CENT = Decimal("0.01")

NO_RATE = "NO_RATE"                   # a chosen daypart has no brand rate: create would refuse it
NO_FREE_SCREENS = "NO_FREE_SCREENS"   # screens exist, but none has room in every chosen cell
NO_OPEN_CELLS = "NO_OPEN_CELLS"       # every chosen cell is already over

BUDGET = "budget"                     # the next screen would cost more than is left
INVENTORY = "inventory"               # every screen that fits is in the plan
TARGET = "target"                     # the plays asked for are bought
NOTHING_BOOKABLE = "nothing_bookable"


class AskError(ValueError):
    pass


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Ask:
    """What the user wants. Exactly one of `budget` (most plays for it) or `target_plays`
    (cheapest way to buy them)."""
    start: date
    end: date
    time_slots: tuple[str, ...]
    image_slots: int
    video_slots: int
    budget: Decimal | None = None
    target_plays: int | None = None
    cities: frozenset[str] | None = None
    shop_types: frozenset[str] | None = None


def campaign_days(inputs: Inputs, ask: Ask) -> tuple[int, ...]:
    """Indexes into inputs.days of the days the campaign runs. Raises AskError on a bad ask."""
    if ask.start > ask.end:
        raise AskError("the campaign ends before it starts")
    if ask.start not in inputs.days or ask.end not in inputs.days:
        raise AskError(f"{ask.start} to {ask.end} is outside the snapshot "
                       f"({inputs.days[0]} to {inputs.days[-1]})")
    if not ask.time_slots or len(set(ask.time_slots)) != len(ask.time_slots) \
            or any(s not in TIME_SLOTS for s in ask.time_slots):
        raise AskError(f"time slots must be distinct values of {TIME_SLOTS}")
    if ask.image_slots < 0 or ask.video_slots < 0 or ask.image_slots + ask.video_slots == 0:
        raise AskError("ask for at least one image or video slot")
    if (ask.budget is None) == (ask.target_plays is None):
        raise AskError("ask for a budget or a number of plays, not both and not neither")
    if ask.budget is not None and ask.budget <= 0:
        raise AskError("the budget must be more than zero")
    if ask.target_plays is not None and ask.target_plays <= 0:
        raise AskError("the plays asked for must be more than zero")
    return tuple(k for k, d in enumerate(inputs.days) if ask.start <= d <= ask.end)


def fits(group: Group, inputs: Inputs, ask: Ask, days: tuple[int, ...]) -> bool:
    """Room for the ask in every open cell of the campaign."""
    for slot in ask.time_slots:
        image, video = group.image.get(slot), group.video.get(slot)
        if image is None or video is None:
            return False                       # the snapshot was not asked about this daypart
        for k in days:
            if (inputs.days[k], slot) in inputs.closed:
                continue
            if image[k] < ask.image_slots or video[k] < ask.video_slots:
                return False
    return True


@dataclass(frozen=True, slots=True)
class _Leg:
    slot: str
    rate: RateCell
    loops: int                # plays of one slot in one daypart, for the price
    effective_days: Decimal   # Σ the day's multiplier over the campaign's open days


@dataclass(frozen=True, slots=True)
class Row:
    """One (city, shop type) a plan can draw screens from."""
    city: str
    shop_type: str
    available: int
    plays_per_screen: int
    unit_price: Decimal       # unrounded: for ranking; prices are always worked out by price()
    legs: tuple[_Leg, ...]
    image_slots: int
    video_slots: int
    plays_per_month: int

    def price(self, screens: int) -> Decimal:
        """The brand price of `screens` screens, rounded the way billing rounds it."""
        total = Decimal(0)
        for leg in self.legs:
            for monthly, slots in ((leg.rate.image_monthly, self.image_slots),
                                   (leg.rate.video_monthly, self.video_slots)):
                if slots:
                    plays = Decimal(screens * slots * leg.loops) * leg.effective_days
                    total += _round(monthly * plays / self.plays_per_month)
        return total


@dataclass(frozen=True, slots=True)
class Excluded:
    city: str
    shop_type: str
    reason: str


def rows(inputs: Inputs, ask: Ask) -> tuple[list[Row], list[Excluded]]:
    """Every (city, shop type) with screens, as a Row the plan can use or an Excluded saying why not."""
    days = campaign_days(inputs, ask)
    by_place: dict[tuple[str, str], list[Group]] = {}
    for g in inputs.groups:
        if (ask.cities is None or g.city in ask.cities) and \
                (ask.shop_types is None or g.shop_type in ask.shop_types):
            by_place.setdefault((g.city, g.shop_type), []).append(g)

    usable, excluded = [], []
    for (city, shop), groups in sorted(by_place.items()):
        cells = [(city, shop, s) for s in ask.time_slots]
        if any(c in inputs.rates.missing or c not in inputs.rates.cells for c in cells):
            excluded.append(Excluded(city, shop, NO_RATE))
            continue
        fitting = [g for g in groups if g.devices and fits(g, inputs, ask, days)]
        if not fitting:
            excluded.append(Excluded(city, shop, NO_FREE_SCREENS))
            continue
        # The booking, not the plan, picks which screens. If loops differ within a place,
        # price at the shortest loop (most plays billed) and count plays at the longest.
        loops_billed = DAYPART_SECONDS // min(g.loop_seconds for g in fitting)
        loops_played = DAYPART_SECONDS // max(g.loop_seconds for g in fitting)
        legs, plays = [], 0
        for slot in ask.time_slots:
            open_days = [inputs.days[k] for k in days if (inputs.days[k], slot) not in inputs.closed]
            plays += (ask.image_slots + ask.video_slots) * loops_played * len(open_days)
            legs.append(_Leg(slot, inputs.rates.cells[(city, shop, slot)], loops_billed,
                             sum((inputs.multiplier(d) for d in open_days), Decimal(0))))
        if plays == 0:
            excluded.append(Excluded(city, shop, NO_OPEN_CELLS))
            continue
        unit = sum(((ask.image_slots * leg.rate.image_monthly + ask.video_slots * leg.rate.video_monthly)
                    * leg.loops * leg.effective_days for leg in legs), Decimal(0)) \
            / inputs.rates.plays_per_month
        usable.append(Row(city, shop, sum(g.devices for g in fitting), plays, unit, tuple(legs),
                          ask.image_slots, ask.video_slots, inputs.rates.plays_per_month))
    return usable, excluded


@dataclass(frozen=True, slots=True)
class Line:
    city: str
    shop_type: str
    screens: int
    available: int
    plays: int
    price: Decimal


@dataclass(frozen=True, slots=True)
class Plan:
    lines: tuple[Line, ...]
    budget: Decimal | None
    target_plays: int | None
    limited_by: str
    optimal: bool                  # False only when places buy different plays per screen
    smallest_price: Decimal | None  # the cheapest one-screen campaign: what "too small" means
    excluded: tuple[Excluded, ...] = field(default=())

    @property
    def screens(self) -> int:
        return sum(line.screens for line in self.lines)

    @property
    def plays(self) -> int:
        return sum(line.plays for line in self.lines)

    @property
    def price(self) -> Decimal:
        return sum((line.price for line in self.lines), Decimal(0))

    @property
    def cpm(self) -> Decimal | None:
        """What 1,000 plays cost the brand, on this plan."""
        return _round(self.price * 1000 / self.plays) if self.plays else None

    @property
    def cities(self) -> tuple[str, ...]:
        return tuple(sorted({line.city for line in self.lines}))


def _rank(row: Row) -> tuple:
    return (row.unit_price / row.plays_per_screen, row.unit_price, row.city, row.shop_type)


def plan(inputs: Inputs, ask: Ask) -> Plan:
    usable, excluded = rows(inputs, ask)
    smallest = min((r.price(1) for r in usable), default=None)
    optimal = len({r.plays_per_screen for r in usable}) <= 1
    if not usable:
        return Plan((), ask.budget, ask.target_plays, NOTHING_BOOKABLE, optimal, None, tuple(excluded))

    order = sorted(usable, key=_rank)
    take: dict[int, int] = {}
    if ask.budget is not None:
        left = ask.budget
        for i, row in enumerate(order):
            n = row.available if row.unit_price == 0 else min(row.available, int(left // row.unit_price))
            if n:
                take[i] = n
                left -= n * row.unit_price
        # Exact arithmetic can sit a paisa under the budget that the rounded bill is over.
        def billed() -> Decimal:
            return sum((order[i].price(n) for i, n in take.items()), Decimal(0))
        while take and billed() > ask.budget:
            i = max(take, key=lambda j: (order[j].unit_price, j))
            take[i] -= 1
            if not take[i]:
                del take[i]
        everything = all(take.get(i, 0) == r.available for i, r in enumerate(order))
        limited_by = INVENTORY if everything else BUDGET
    else:
        need = ask.target_plays
        for i, row in enumerate(order):
            if need <= 0:
                break
            n = min(row.available, -(-need // row.plays_per_screen))
            take[i] = n
            need -= n * row.plays_per_screen
        limited_by = TARGET if need <= 0 else INVENTORY

    lines = tuple(Line(order[i].city, order[i].shop_type, n, order[i].available,
                       n * order[i].plays_per_screen, order[i].price(n))
                  for i, n in sorted(take.items(), key=lambda kv: (order[kv[0]].city, order[kv[0]].shop_type)))
    return Plan(lines, ask.budget, ask.target_plays, limited_by, optimal, smallest, tuple(excluded))


def alternatives(inputs: Inputs, ask: Ask, base: Plan, most: int = 2) -> list[tuple[str, Plan]]:
    """The same ask without the plan's biggest city, then without its second: what the
    budget buys if the user would rather not be there."""
    if len(base.cities) < 2:
        return []
    weight: dict[str, int] = {}
    for line in base.lines:
        weight[line.city] = weight.get(line.city, 0) + line.screens
    allowed = frozenset(ask.cities) if ask.cities is not None else frozenset(g.city for g in inputs.groups)
    out = []
    for city in sorted(weight, key=lambda c: (-weight[c], c))[:most]:
        out.append((city, plan(inputs, replace(ask, cities=allowed - {city}))))
    return out


def prefill(p: Plan, ask: Ask) -> dict:
    """The plan as the launch wizard's fields (the create-campaign body's shape, guide §7.6).
    Genie hands this over; the user submits."""
    return {
        "deliveryMode": "BROADCAST",
        "deviceTier": "NON_PREMIUM",
        "startDate": ask.start.isoformat(),
        "endDate": ask.end.isoformat(),
        "timeSlots": list(ask.time_slots),
        "imageSlotsRequested": ask.image_slots,
        "videoSlotsRequested": ask.video_slots,
        "selections": [{"city": line.city, "shopType": line.shop_type, "deviceCount": line.screens}
                       for line in p.lines],
    }
