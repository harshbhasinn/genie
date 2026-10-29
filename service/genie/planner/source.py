"""Where the calculator's data comes from.

  BffPlanningSource   the dashboard BFF's own reads, AS THE USER (their token, so the BFF's
                      gates decide what they may see): availability-search, client-rate-cards,
                      simulate-cost. These exist today (CAMPAIGN_CREATION_GUIDE.md §7.2, §7.5).
  FixtureSource       demo data in exactly those shapes. LOCAL ONLY: config refuses it in
                      qa/prod, so demo prices can never reach a user.
  FixtureReach        demo reach. There is no reach API yet - the one input the calculator
                      still needs from another team. Local only, like the fixture.

A source returns the raw `data` object; contract.py checks it. Every source goes through the
same checks, so the fixture also proves the parser.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..bff import BffClient
from ..bff.endpoints import endpoint
from .contract import DAYPARTS, PlanQuery

FIXTURE = Path(__file__).with_name("fixtures") / "demo.json"
QUOTE_FIELDS = {"clientId", "advertisedBrandId", "startDate", "endDate", "timeSlots",
                "imageSlotsRequested", "videoSlotsRequested", "deviceTier", "selections"}


class PlanningSource(Protocol):
    name: str

    async def availability(self, token: str | None, client_id: str, q: PlanQuery) -> dict: ...

    async def rate_cards(self, token: str | None, client_id: str) -> dict: ...

    async def quote(self, token: str | None, client_id: str, brand_id: str, q: PlanQuery,
                    selections: list[dict]) -> dict: ...


class ReachSource(Protocol):
    name: str

    async def reach(self, token: str | None, client_id: str) -> dict: ...


class BffPlanningSource:
    name = "bff"

    def __init__(self, bff: BffClient):
        self.bff = bff

    async def availability(self, token, client_id, q):
        return (await self.bff.post_read(endpoint("campaigns.availability_search"), token=token,
                                         body=q.search_body(client_id))).data or {}

    async def rate_cards(self, token, client_id):
        # Every BROADCAST row of the partner: its partner-wide prices and each brand's own.
        # contract.parse_rate_cards keeps this brand's, falling back to partner-wide (§12.2).
        return (await self.bff.get(endpoint("rate_cards.of_partner"), token=token,
                                   params={"parentClientId": client_id,
                                           "rateCardType": "BROADCAST"})).data or {}

    async def quote(self, token, client_id, brand_id, q, selections):
        body = q.quote_body(client_id, brand_id, selections)
        if set(body) - QUOTE_FIELDS or "campaignId" in body:   # a campaignId would SAVE a price
            raise ValueError("a price preview carries no campaign")
        return (await self.bff.post_read(endpoint("campaigns.simulate_cost"), token=token,
                                         body=body)).data or {}


class FixtureSource:
    """Each fixture cell has free screens per daypart; a screen counts only if it is free in
    every chosen daypart (as availability-search counts), so a cell offers the fewest of
    its chosen dayparts. A quote is the list price: the demo has no surcharge days."""
    name = "fixture"

    def __init__(self, path: Path = FIXTURE):
        self.doc = json.loads(Path(path).read_text(encoding="utf-8"))

    def _cells(self, q: PlanQuery):
        for c in self.doc["cells"]:
            if (q.cities and c["city"] not in q.cities) or \
                    (q.shop_types and c["shopType"] not in q.shop_types):
                continue
            yield c

    async def availability(self, token, client_id, q):
        cities: dict[str, list] = {}
        for c in self._cells(q):
            count = min(c["free"].get(d, 0) for d in q.dayparts)
            if count:
                cities.setdefault(c["city"], []).append({"shopType": c["shopType"], "count": count})
        return {"totalCount": sum(s["count"] for v in cities.values() for s in v),
                "cities": [{"city": city, "totalDevices": sum(s["count"] for s in shops),
                            "shopTypes": shops} for city, shops in sorted(cities.items())],
                "startDateUnavailableSlots": [], "startDateFullyClosed": False,
                "earliestStartDate": None}

    async def rate_cards(self, token, client_id):
        rows = []
        for c in self.doc["cells"]:
            for d, (image, video) in c["rates"].items():
                rows.append({"parentClientId": client_id, "clientId": None, "city": c["city"],
                             "shopType": c["shopType"], "timeSlot": d, "imageSlotRate": image,
                             "videoSlotRate": video, "rateCardType": "BROADCAST"})
        return {"rateCards": rows}

    async def quote(self, token, client_id, brand_id, q, selections):
        rates = {(c["city"], c["shopType"]): c["rates"] for c in self.doc["cells"]}
        paise = 0
        for s in selections:
            for d in q.dayparts:
                image, video = rates[(s["city"], s["shopType"])][d]
                monthly = q.image_slots * int(round(float(image) * 100)) \
                    + q.video_slots * int(round(float(video) * 100))
                paise += monthly * s["deviceCount"] * q.days            # / 30, below
        total = (paise + 15) // 30                                       # to the paisa, half up
        return {"totalCost": f"{total // 100}.{total % 100:02d}"}


class FixtureReach:
    name = "fixture"

    def __init__(self, path: Path = FIXTURE):
        self.doc = json.loads(Path(path).read_text(encoding="utf-8"))

    async def reach(self, token, client_id):
        return {"reachVersion": self.doc["reachVersion"],
                "reach": [{"city": c["city"], "shopType": c["shopType"], "daypart": d,
                           "dailyReachPerDevice": r}
                          for c in self.doc["cells"] for d, r in c["reach"].items() if d in DAYPARTS]}


def build(kind: str, bff: BffClient | None) -> PlanningSource | None:
    """None = the calculator is not connected (it answers 503 and says so)."""
    if kind == "fixture":
        return FixtureSource()
    if kind == "bff" and bff is not None:
        return BffPlanningSource(bff)
    return None


def build_reach(kind: str) -> ReachSource | None:
    return FixtureReach() if kind == "fixture" else None
