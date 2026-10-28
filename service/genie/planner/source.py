"""Where the calculator's data comes from: one interface, two answers.

  FixtureSource   demo data from a JSON file, served in EXACTLY the shape the backend
                  will send (design/calculator/availability-api-spec.md). Local only:
                  config refuses it in qa/prod, so demo prices can never reach a user.
  (BFF source)    the real endpoints A and B, read with the user's own token. Added when
                  the backend shares its contract - their paths go in
                  ontology/endpoints.yaml, which only accepts paths that exist in the
                  BFF's published spec.

A source returns the raw `data` object; contract.py checks it. Both sources go through
the same checks, so the fixture also proves the parser.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .contract import AvailabilityQuery

FIXTURE = Path(__file__).with_name("fixtures") / "demo.json"


class PlanningSource(Protocol):
    name: str

    async def availability(self, token: str | None, client_id: str,
                           q: AvailabilityQuery) -> dict: ...

    async def rates_and_reach(self, token: str | None, client_id: str) -> dict: ...


class FixtureSource:
    """Each fixture row has a `pattern` of free screens, repeated across the window from
    its first date, so the four-row worked example plays out exactly over 15 days."""
    name = "fixture"

    def __init__(self, path: Path = FIXTURE):
        self.doc = json.loads(Path(path).read_text(encoding="utf-8"))

    async def availability(self, token, client_id, q):
        rows = []
        for r in self.doc["rows"]:
            if (q.cities and r["city"] not in q.cities) \
                    or (q.categories and r["category"] not in q.categories) \
                    or (q.dayparts and r["daypart"] not in q.dayparts) \
                    or (q.contract_ids and r["contractId"] not in q.contract_ids):
                continue
            pattern = r["pattern"]
            free = [pattern[i % len(pattern)] for i in range(q.days)]
            if not any(free):
                continue                                    # all-zero rows are left out
            rows.append({k: r[k] for k in ("contractId", "contractCode", "city", "cityName",
                                           "category", "daypart", "totalDevices")}
                        | {"freeDevices": free})
        rows.sort(key=lambda r: (r["contractCode"], r["city"], r["category"], r["daypart"]))
        return {"clientId": client_id,
                "window": {"from": q.start.isoformat(), "to": q.end.isoformat(), "days": q.days},
                "imageSlots": q.image_slots, "videoSlots": q.video_slots,
                "ratesVersion": self.doc["ratesVersion"], "reachVersion": self.doc["reachVersion"],
                "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "rows": rows}

    async def rates_and_reach(self, token, client_id):
        return {"clientId": client_id, "currency": "INR",
                "ratesVersion": self.doc["ratesVersion"], "reachVersion": self.doc["reachVersion"],
                "rates": [{k: r[k] for k in ("contractId", "contractCode", "city", "category",
                                             "daypart", "imageSlotRatePerMonth",
                                             "videoSlotRatePerMonth")} for r in self.doc["rows"]],
                "reach": self.doc["reach"]}


def build(kind: str) -> PlanningSource | None:
    """None = the calculator is not connected (it answers 503 and says so)."""
    if kind == "fixture":
        return FixtureSource()
    return None
