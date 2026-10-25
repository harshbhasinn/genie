"""The brands a user may work with, read live - so a link like /brands/{brandId}/creatives
is filled with a real brand instead of the literal placeholder.

  agency admin       GET /api/v1/clients/{apId}/brands                     brands[].brandId, brandName
  campaign manager   GET /api/v1/clients/{clientId}/members/{userId}/brands brands[].brandId, brandName
                     (the brands assigned to them)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..bff import BffClient, BffError
from ..roles import is_campaign_manager

AGENCY_BRANDS = "/api/v1/clients/{apId}/brands"
MY_BRANDS = "/api/v1/clients/{clientId}/members/{userId}/brands"


@dataclass(frozen=True, slots=True)
class Brand:
    id: str
    name: str


@dataclass(slots=True)
class BrandList:
    brands: list[Brand] = field(default_factory=list)
    problem: str | None = None

    def find(self, brand_id: str) -> Brand | None:
        return next((b for b in self.brands if b.id == brand_id), None)


async def list_brands(bff: BffClient, token: str, client_id: str, user_id: str,
                      role: str | None) -> BrandList:
    """Admins get all their brands; a campaign manager gets the ones assigned to them."""
    try:
        if is_campaign_manager(role):
            data = (await bff.get(MY_BRANDS, token=token,
                                  path_params={"clientId": client_id, "userId": user_id})).data
        else:
            data = (await bff.get(AGENCY_BRANDS, token=token, path_params={"apId": client_id},
                                  params={"page": 0, "size": 100})).data
    except BffError as err:
        return BrandList(problem=err.kind)
    rows = (data or {}).get("brands") or []
    return BrandList(brands=[Brand(str(r.get("brandId")), str(r.get("brandName") or "Unnamed brand"))
                             for r in rows if isinstance(r, dict) and r.get("brandId")])
