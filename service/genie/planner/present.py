"""The solver's numbers, as the user sees them: rupees, codes, schedules, notices.

Money leaves as decimal strings ("17920.00"), as the BFF sends it. Every figure here is
computed by code from the solver's integers - nothing is estimated or worded by a model.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..data.money import count, inr
from . import model
from .contract import Availability
from .engine import Prepared

PAISA = Decimal("0.01")


def rupees(cost30: int) -> Decimal:
    """Paise x 30 -> rupees, to the paisa."""
    return (Decimal(cost30) / 3000).quantize(PAISA, rounding=ROUND_HALF_UP)


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _span(start: date, a: int, b: int) -> str:
    da, db = start + timedelta(days=a), start + timedelta(days=b)
    if a == b:
        return _day(da)
    return f"{_day(da)}–{db.day}" if da.month == db.month else f"{_day(da)}–{_day(db)}"


def runs(values, start: date, *, skip_zero: bool = True) -> list[tuple[int, str]]:
    """Consecutive dates with the same value: [(value, "Nov 1–10"), ...]."""
    out, i = [], 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[j + 1] == values[i]:
            j += 1
        if not (skip_zero and values[i] == 0):
            out.append((values[i], _span(start, i, j)))
        i = j + 1
    return out


def schedule_text(screens: list[int], start: date) -> str:
    """"4 a day Nov 1–10" / "1 a day Nov 1–7; 1 a day Nov 11–15"."""
    return "; ".join(f"{v} a day {span}" for v, span in runs(screens, start))


def plan_json(rank: int, plan: dict, prep: Prepared, avail: Availability, *, budget: Decimal,
              status: str, target: int | None, first: dict | None = None) -> dict:
    lines, screen_days = [], 0
    for r, xs in plan["x"].items():
        row, line = prep.rows[r], prep.problem.lines[r]
        y = sum(xs)
        screen_days += y
        lines.append({"contractId": row.contract_id, "contractCode": row.contract_code,
                      "city": row.city, "cityName": row.city_name, "category": row.category,
                      "daypart": row.daypart, "screens": list(xs),
                      "schedule": schedule_text(list(xs), avail.start), "screenDays": y,
                      "cost": str(rupees(line.price30 * y)), "reach": line.reach * y})
    code_of = {row.contract_id: row.contract_code for row in prep.rows}
    cost = rupees(plan["cost30"])
    out = {"rank": rank, "cost": str(cost), "budgetLeft": str((budget - cost).quantize(PAISA)),
           "reach": plan["reach"], "screenDays": screen_days,
           "daysPlayed": avail.days - len(prep.dark_dates), "windowDays": avail.days,
           "contracts": [code_of[c] for c in plan["contracts"]], "lines": lines}
    if first is None:
        out["label"] = (f"Cheapest plan that reaches {count(target)}" if status == model.TARGET_MET
                        else f"Most reach for {inr(budget)}")
    else:
        dropped = [code_of[c] for c in first["contracts"] if c not in plan["contracts"]]
        out["label"] = f"Without {', '.join(dropped)}" if dropped else "A different mix of contracts"
        out["vsPlan1"] = {"cost": _signed(cost - rupees(first["cost30"])),
                          "reach": plan["reach"] - first["reach"]}
    return out


def _signed(d: Decimal) -> str:
    return f"+{d}" if d > 0 else str(d)


def notices(out: dict, prep: Prepared, avail: Availability, *, budget: Decimal,
            target: int | None) -> list[dict]:
    """What the user must know besides the plan, in plain words."""
    found = []
    status = out["status"]
    if prep.skipped_no_rate:
        found.append({"code": "LINES_SKIPPED_NO_RATE",
                      "text": f"{count(prep.skipped_no_rate)} line"
                              f"{'s' if prep.skipped_no_rate != 1 else ''} skipped: no rate set."})
    if prep.skipped_no_reach:
        found.append({"code": "LINES_SKIPPED_NO_REACH",
                      "text": f"{count(prep.skipped_no_reach)} line"
                              f"{'s' if prep.skipped_no_reach != 1 else ''} skipped: no reach data."})
    if prep.dark_dates and len(prep.dark_dates) < avail.days:
        flags = [0 if t in prep.dark_dates else 1 for t in range(avail.days)]
        spans = ", ".join(span for v, span in runs(flags, avail.start, skip_zero=False) if v == 0)
        found.append({"code": "NO_SCREENS_ON_DATES",
                      "text": f"No free screens on {spans} – the campaign will not play those days."})
    if status == model.NO_INVENTORY:
        found.append({"code": "NO_INVENTORY",
                      "text": "No free screens with a rate and reach in this window for these choices."})
    elif status == model.TARGET_OUT_OF_REACH:
        best = out["plans"][0]["reach"]
        if out.get("target_needs30") is not None:
            text = (f"{inr(budget)} reaches at most {count(best)}. Reaching {count(target)} needs "
                    f"{inr(rupees(out['target_needs30']))}.")
        elif out.get("target_possible") is False:
            most = sum(ln.reach * sum(ln.free) for ln in prep.problem.lines)
            text = (f"No plan in this window reaches {count(target)} – every free screen together "
                    f"reaches {count(most)}.")
        else:
            text = f"{inr(budget)} reaches at most {count(best)}, short of {count(target)}."
        found.append({"code": "TARGET_OUT_OF_REACH", "text": text})
    elif status == model.BUDGET_TOO_LOW:
        text = f"{inr(budget)} can't play a screen on every date of the window."
        if out.get("min_budget30") is not None:
            text += f" The smallest budget that does is {inr(rupees(out['min_budget30']))}."
        found.append({"code": "BUDGET_TOO_LOW", "text": text})
    elif status == model.NOT_PROVEN:
        found.append({"code": "NOT_PROVEN",
                      "text": "Genie couldn't finish this plan in time. Try again, or narrow the "
                              "cities or dates."})
    return found
