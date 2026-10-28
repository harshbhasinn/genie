"""One `calculator-inputs` snapshot, read into types the engine can trust.

The response is checked here, once, so the engine never meets a ragged array or an unknown
daypart: every per-day array must be as long as `days`, every count a whole number >= 0, every
rate a finite amount. Anything else is an InputsError - a plan built on a misread snapshot
would be a confident wrong answer.

The rates half is split out (Rates) because it is the part Genie caches: it does not depend on
the dates or the filters, only on the client, the brand and `versions.rates`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..data.fields import parse_date, to_int
from ..data.money import to_decimal

TIME_SLOTS = ("MORNING", "AFTERNOON", "EVENING", "NIGHT")


class InputsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Group:
    """`devices` screens with exactly the same free room, cell by cell."""
    id: str
    city: str
    shop_type: str
    loop_seconds: int
    devices: int
    contract_id: str | None
    image: dict[str, tuple[int, ...]]     # time slot -> free image slots per day
    video: dict[str, tuple[int, ...]]     # time slot -> free video slots per day


@dataclass(frozen=True, slots=True)
class RateCell:
    image_monthly: Decimal
    video_monthly: Decimal
    source: str                           # BRAND | PARTNER_WIDE


Cell = tuple[str, str, str]               # (city, shop type, time slot)


@dataclass(frozen=True, slots=True)
class Rates:
    """The brand's full effective rate matrix. Cached under its version."""
    version: str
    plays_per_month: int
    cells: dict[Cell, RateCell]
    missing: frozenset[Cell]

    def to_json(self) -> str:
        return json.dumps({
            "version": self.version, "playsPerMonth": self.plays_per_month,
            "cells": [{"city": c, "shopType": s, "timeSlot": t,
                       "imageMonthly": str(r.image_monthly), "videoMonthly": str(r.video_monthly),
                       "source": r.source} for (c, s, t), r in sorted(self.cells.items())],
            "missing": [{"city": c, "shopType": s, "timeSlot": t} for c, s, t in sorted(self.missing)],
        })

    @classmethod
    def from_json(cls, raw: str) -> "Rates":
        doc = json.loads(raw)
        return rates_from(doc, str(doc.get("version") or ""))


@dataclass(frozen=True, slots=True)
class Inputs:
    snapshot_at: str | None
    days: tuple[date, ...]
    groups: tuple[Group, ...]
    closed: frozenset[tuple[date, str]]   # cells already over today: not bookable, not checked
    surcharges: dict[date, Decimal]       # days not listed are 1.0
    rates: Rates

    def multiplier(self, d: date) -> Decimal:
        return self.surcharges.get(d, Decimal(1))


def _slot(raw) -> str:
    if raw not in TIME_SLOTS:
        raise InputsError(f"unknown time slot {raw!r}")
    return raw


def _cell(item: dict) -> Cell:
    city, shop = item.get("city"), item.get("shopType")
    if not city or not shop:
        raise InputsError(f"a rate cell without city or shop type: {item}")
    return str(city), str(shop), _slot(item.get("timeSlot"))


def _amount(raw, what: str) -> Decimal:
    d = to_decimal(raw)
    if d is None or d < 0:
        raise InputsError(f"{what} is not an amount: {raw!r}")
    return d


def rates_from(block: dict, version: str) -> Rates:
    if not version:
        raise InputsError("rates without a version cannot be cached or trusted")
    divisor = to_int(block.get("playsPerMonth"))
    if not divisor or divisor <= 0:
        raise InputsError(f"playsPerMonth is not a positive count: {block.get('playsPerMonth')!r}")
    cells: dict[Cell, RateCell] = {}
    for item in block.get("cells") or []:
        key = _cell(item)
        if key in cells:
            raise InputsError(f"rate cell {key} is listed twice")
        cells[key] = RateCell(_amount(item.get("imageMonthly"), f"{key} imageMonthly"),
                              _amount(item.get("videoMonthly"), f"{key} videoMonthly"),
                              str(item.get("source") or ""))
    missing = frozenset(_cell(item) for item in block.get("missing") or [])
    if missing & cells.keys():
        raise InputsError(f"cells both priced and missing: {sorted(missing & cells.keys())}")
    return Rates(version, divisor, cells, missing)


def _counts(raw, n_days: int, what: str) -> tuple[int, ...]:
    if not isinstance(raw, list) or len(raw) != n_days:
        raise InputsError(f"{what} must have one value per day ({n_days})")
    out = []
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise InputsError(f"{what} has a value that is not a count: {v!r}")
        out.append(v)
    return tuple(out)


def _group(item: dict, n_days: int) -> Group:
    gid = str(item.get("id") or "")
    devices, loop = item.get("devices"), item.get("loopSeconds")
    if not isinstance(devices, int) or isinstance(devices, bool) or devices < 0:
        raise InputsError(f"group {gid}: devices is not a count")
    if not isinstance(loop, int) or isinstance(loop, bool) or loop <= 0:
        raise InputsError(f"group {gid}: loopSeconds is not a positive count")
    if not item.get("city") or not item.get("shopType"):
        raise InputsError(f"group {gid}: no city or shop type")
    image, video = {}, {}
    for slot, legs in (item.get("free") or {}).items():
        slot = _slot(slot)
        image[slot] = _counts((legs or {}).get("image"), n_days, f"group {gid} {slot} image")
        video[slot] = _counts((legs or {}).get("video"), n_days, f"group {gid} {slot} video")
    return Group(gid, str(item["city"]), str(item["shopType"]), loop, devices,
                 item.get("contractId"), image, video)


def parse(data: dict, cached: Rates | None = None) -> Inputs:
    """The `data` of a calculator-inputs answer. `cached` is the copy sent as knownRatesVersion;
    it is used only when the server says it is still current."""
    days = tuple(parse_date(d) for d in data.get("days") or [])
    if not days or None in days:
        raise InputsError("days is missing or holds something that is not a date")
    if list(days) != sorted(set(days)) or any((b - a).days != 1 for a, b in zip(days, days[1:])):
        raise InputsError("days must be consecutive, in order, each once")

    version = str((data.get("versions") or {}).get("rates") or "")
    if data.get("ratesUnchanged"):
        if cached is None or cached.version != version:
            raise InputsError("the server kept the rates back, but the copy held is not that version")
        rates = cached
    else:
        rates = rates_from(data.get("rates") or {}, version)

    availability = data.get("availability") or {}
    groups = tuple(_group(g, len(days)) for g in availability.get("groups") or [])
    closed = frozenset((parse_date(c.get("date")), _slot(c.get("timeSlot")))
                       for c in availability.get("closedCells") or [])
    surcharges = {}
    for s in data.get("surcharges") or []:
        d, m = parse_date(s.get("date")), to_decimal(s.get("multiplier"))
        if d is None or m is None or m <= 0:
            raise InputsError(f"a surcharge that is not a date and a positive multiplier: {s}")
        surcharges[d] = m
    return Inputs(data.get("snapshotAt"), days, groups, closed, surcharges, rates)
