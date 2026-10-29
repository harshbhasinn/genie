"""The solver's numbers, as the user sees them: rupees, names, notices, and the campaign
the plan is - ready to drop into the wizard.

Money leaves as decimal strings ("17920.00"), as the BFF sends it. A plan's `cost` is the
quote from simulate-cost, the same figure the wizard will show; each line's `listCost` is
that line at the rate card's list price (surcharge days and billing's rounding are in the
quote, not the lines). Nothing here is estimated or worded by a model.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..data.money import count, inr
from . import model
from .contract import DEVICE_TIER, PlanQuery
from .engine import Prepared

PAISA = Decimal("0.01")
# How the campaign wizard names a city code (launch-campaign.yaml, R11: GGN is Gurugram).
CITY_NAMES = {"DEL": "Delhi", "GGN": "Gurugram", "NOI": "Noida", "MUM": "Mumbai",
              "BLR": "Bengaluru", "HYD": "Hyderabad", "CHN": "Chennai", "PUN": "Pune",
              "FBD": "Faridabad", "GBD": "Ghaziabad", "GND": "Greater Noida",
              "OTHERS": "Other cities"}


def rupees(cost30: int) -> Decimal:
    """Paise x 30 -> rupees, to the paisa."""
    return (Decimal(cost30) / 3000).quantize(PAISA, rounding=ROUND_HALF_UP)


def city_name(code: str) -> str:
    return CITY_NAMES.get(code, code)


def shop_name(code: str) -> str:
    return code.replace("_", " ").capitalize()


def _signed(d: Decimal) -> str:
    return f"+{d}" if d > 0 else str(d)


def selections(plan: dict, prep: Prepared) -> list[dict]:
    """The plan as the wizard books it: `selections` of POST /campaigns (§7.6)."""
    return [{"city": prep.cells[c][0], "shopType": prep.cells[c][1], "deviceCount": n}
            for c, n in sorted(plan["n"].items())]


def plan_json(rank: int, plan: dict, prep: Prepared, q: PlanQuery, *, budget: Decimal,
              quote: Decimal, status: str, target: int | None, brand_id: str,
              first: dict | None = None, first_quote: Decimal | None = None) -> dict:
    lines = []
    for c, n in sorted(plan["n"].items()):
        city, shop = prep.cells[c]
        cell = prep.problem.cells[c]
        lines.append({"city": city, "cityName": city_name(city), "shopType": shop,
                      "shopTypeName": shop_name(shop), "screens": n, "reach": cell.reach * n,
                      "listCost": str(rupees(cell.price30 * n))})
    out = {"rank": rank, "cost": str(quote), "budgetLeft": str((budget - quote).quantize(PAISA)),
           "reach": plan["reach"], "screens": sum(plan["n"].values()),
           "cities": [city_name(g) for g in plan["groups"]], "lines": lines,
           "campaign": {"startDate": q.start.isoformat(), "endDate": q.end.isoformat(),
                        "timeSlots": list(q.dayparts), "imageSlotsRequested": q.image_slots,
                        "videoSlotsRequested": q.video_slots, "deviceTier": DEVICE_TIER,
                        "advertisedBrandId": brand_id, "selections": selections(plan, prep)}}
    surcharge = quote - rupees(plan["cost30"])
    if surcharge >= 1:
        out["note"] = f"The price includes {inr(surcharge)} for surcharge days."
    if first is None:
        out["label"] = (f"Cheapest plan that reaches {count(target)}" if status == model.TARGET_MET
                        else f"Most reach for {inr(budget)}")
    else:
        dropped = [city_name(g) for g in first["groups"] if g not in plan["groups"]]
        out["label"] = f"Without {', '.join(dropped)}" if dropped else "A different mix of cities"
        out["vsPlan1"] = {"cost": _signed(quote - first_quote), "reach": plan["reach"] - first["reach"]}
    return out


def notices(out: dict, prep: Prepared, q: PlanQuery, *, budget: Decimal,
            target: int | None) -> list[dict]:
    """What the user must know besides the plan, in plain words."""
    found = []
    for n, code, why in ((prep.skipped_no_rate, "LINES_SKIPPED_NO_RATE", "no price set for this brand"),
                         (prep.skipped_no_reach, "LINES_SKIPPED_NO_REACH", "no reach data")):
        if n:
            found.append({"code": code, "text": f"{count(n)} city and shop type"
                                                f"{' pairs' if n != 1 else ' pair'} left out: {why}."})
    short = [d for d in q.dayparts if prep.plays[d] < q.days]
    if short:
        found.append({"code": "START_DAY_SHORT",
                      "text": f"{' and '.join(d.capitalize() for d in short)} "
                              f"{'has' if len(short) == 1 else 'have'} already ended today, so the "
                              f"first day plays without {'it' if len(short) == 1 else 'them'}."})
    status = out["status"]
    if status == model.NO_INVENTORY:
        found.append({"code": "NO_INVENTORY",
                      "text": "No screens are free for the whole window in these dayparts. Try "
                              "fewer dayparts, fewer slots or other dates."})
    elif status == model.TARGET_OUT_OF_REACH:
        best = out["plans"][0]["reach"] if out["plans"] else 0
        if out.get("target_needs30") is not None:
            text = (f"{inr(budget)} reaches at most {count(best)}. Reaching {count(target)} needs "
                    f"about {inr(rupees(out['target_needs30']))}.")
        elif out.get("target_possible") is False:
            most = sum(c.reach * c.free for c in prep.problem.cells)
            text = (f"No plan in this window reaches {count(target)} – every free screen together "
                    f"reaches {count(most)}.")
        else:
            text = f"{inr(budget)} reaches at most {count(best)}, short of {count(target)}."
        found.append({"code": "TARGET_OUT_OF_REACH", "text": text})
    elif status == model.BUDGET_TOO_LOW:
        text = f"{inr(budget)} doesn't buy one screen for the whole campaign."
        if out.get("min_budget30") is not None:
            text += f" The cheapest screen costs about {inr(rupees(out['min_budget30']))}."
        found.append({"code": "BUDGET_TOO_LOW", "text": text})
    elif status == model.NOT_PROVEN:
        found.append({"code": "NOT_PROVEN",
                      "text": "Genie couldn't finish this plan in time. Try again, or narrow the "
                              "cities or shop types."})
    return found
