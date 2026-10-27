"""Reading the BFF's fields the same way everywhere: dates, counts and enum codes.

Business dates are IST (ADGRID_KNOWLEDGE_BASE.md §2): "today" and an epoch timestamp's
day are both taken in Asia/Kolkata, never in the container's UTC.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .money import to_decimal

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True, slots=True)
class Window:
    """The days a question is about, as the BFF takes them."""
    start: date
    end: date
    label: str                      # "this month", "September 2026", "the last 30 days"
    named: bool = False             # a calendar month the user named ("September", "last month")
    rolling: bool = False           # a span of days, not a calendar month ("the last 30 days")

    def params(self) -> dict:
        return {"from": self.start.isoformat(), "to": self.end.isoformat()}


def today() -> date:
    return datetime.now(IST).date()


def parse_date(raw) -> date | None:
    """ "2026-10-16" or "2026-10-15 17:17:07" -> 16 Oct 2026; None for anything else."""
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def parse_month(raw) -> date | None:
    """ "2026-09" or "2026-09-01" -> 1 Sep 2026."""
    try:
        return date.fromisoformat(str(raw)[:7] + "-01") if raw else None
    except ValueError:
        return None


def from_epoch_ms(ms) -> date | None:
    return (datetime.fromtimestamp(ms / 1000, tz=IST).date()
            if isinstance(ms, (int, float)) and ms > 0 else None)


def to_int(raw) -> int | None:
    d = to_decimal(raw)
    return int(d) if d is not None else None


def humanize(code: str | None) -> str | None:
    """SCOPE_CHANGED -> "scope changed". For enum-like codes with no label of their own."""
    return code.replace("_", " ").lower() if code else None


def window_of(data: dict) -> tuple[date, date] | None:
    """The days a response covers, from its own from/to."""
    lo, hi = parse_date(data.get("from")), parse_date(data.get("to"))
    return (lo, hi) if lo and hi else None


def day(d: date) -> str:
    """3 Oct 2026."""
    return f"{d.day} {d:%b %Y}"
