"""The brands a user works with, read live.

  the brands          brands.of_partner  brands[], totalElements
  one brand's figures brands.analytics   the Brand Summary
  who owns a brand    clients.detail     clientInfo

Every role reads the Brand Management list, as the console does; the BFF decides what each
role sees. (members/{userId}/brands is the admin's member-management read - a campaign
manager gets 403 there, live QA 2026-10-27.)

A brand is itself a client (BRAND_UNDER_AD_PARTNER), so its id is a clientId. Field
allowlist, as for the wallet: clientInfo also carries GST/PAN/TAN/CIN numbers and the
other sections carry rates and credit. Only CLIENT_INFO is requested - but the BFF returns
every section anyway (live QA 2026-10-27; asks/BACKEND-CHECKS.md section 9), so the
allowlist below is what keeps the rest out.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

from ..bff import BffClient, BffError
from ..bff.endpoints import endpoint
from .fields import Window, to_int
from .money import to_decimal

BRANDS = endpoint("brands.of_partner")
ANALYTICS = endpoint("brands.analytics")
CLIENT = endpoint("clients.detail")
PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class Brand:
    id: str
    name: str
    campaigns: int | None = None      # campaignsCount
    budget: Decimal | None = None     # totalBudget: "total budget allocated to this brand"


@dataclass(slots=True)
class BrandList:
    brands: list[Brand] = field(default_factory=list)
    total: int | None = None          # brands in all, not one page
    problem: str | None = None

    def find(self, brand_id: str) -> Brand | None:
        return next((b for b in self.brands if b.id == brand_id), None)


async def list_brands(bff: BffClient, token: str, client_id: str) -> BrandList:
    try:
        data = (await bff.get(BRANDS, token=token, path_params={"apId": client_id},
                              params={"page": 0, "size": PAGE_SIZE})).data
    except BffError as err:
        return BrandList(problem=err.kind)
    data = data or {}
    rows = [r for r in data.get("brands") or [] if isinstance(r, dict) and r.get("brandId")]
    return BrandList(
        brands=[Brand(str(r["brandId"]), str(r.get("brandName") or "Unnamed brand"),
                      to_int(r.get("campaignsCount")), to_decimal(r.get("totalBudget")))
                for r in rows],
        total=to_int(data.get("totalElements")))


@dataclass(slots=True)
class BrandStats:
    """The Brand Summary. Booked value is ESTIMATED (booked, not spent); reach is per day."""
    screens_used: int | None = None
    live_campaigns: int | None = None
    booked_value: Decimal | None = None          # totalEstimatedRevenue
    impressions_per_day: int | None = None       # totalDailyImpressions
    reach_per_day: int | None = None             # totalAudienceReach
    by_city: list[tuple[str, int, Decimal | None]] = field(default_factory=list)  # city, screens, %
    problem: str | None = None


async def brand_stats(bff: BffClient, token: str, brand_id: str, window: Window | None) -> BrandStats:
    """One brand's figures; no window = the whole history."""
    try:
        data = (await bff.get(ANALYTICS, token=token, path_params={"brandId": brand_id},
                              params=window.params() if window else None)).data or {}
    except BffError as err:
        return BrandStats(problem=err.kind)
    return BrandStats(
        screens_used=to_int(data.get("screensUsed")), live_campaigns=to_int(data.get("activeCampaigns")),
        booked_value=to_decimal(data.get("totalEstimatedRevenue")),
        impressions_per_day=to_int(data.get("totalDailyImpressions")),
        reach_per_day=to_int(data.get("totalAudienceReach")),
        by_city=[(str(c.get("city") or "Unknown"), to_int(c.get("screenCount")) or 0,
                  to_decimal(c.get("percentage")))
                 for c in data.get("screenDistribution") or [] if isinstance(c, dict)])


async def many_brand_stats(bff: BffClient, token: str, brands: list[Brand],
                           window: Window | None) -> list[BrandStats]:
    return list(await asyncio.gather(*(brand_stats(bff, token, b.id, window) for b in brands)))


@dataclass(slots=True)
class BrandContacts:
    contact: str | None = None          # the brand's own person
    designation: str | None = None
    email: str | None = None
    phone: str | None = None
    managed_by: str | None = None       # assignedUserName: the partner's campaign manager for it
    problem: str | None = None


async def brand_contacts(bff: BffClient, token: str, brand_id: str) -> BrandContacts:
    try:
        data = (await bff.get(CLIENT, token=token, path_params={"clientId": brand_id},
                              params={"sections": ["CLIENT_INFO"]})).data or {}
    except BffError as err:
        return BrandContacts(problem=err.kind)
    info = data.get("clientInfo") or {}
    pick = lambda k: (str(info.get(k)).strip() or None) if info.get(k) else None  # noqa: E731
    return BrandContacts(contact=pick("contactPersonName"), designation=pick("designation"),
                         email=pick("email"), phone=pick("phoneNumber"),
                         managed_by=pick("assignedUserName"))
