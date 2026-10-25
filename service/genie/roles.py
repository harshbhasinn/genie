"""Platform roles as Genie uses them: what each is called, and who gets the same access.

Names follow PlatformRole's display names (ag-common-util; ADGRID_KNOWLEDGE_BASE.md §3.3).
The stored codes are never shown to a user, and neither are "agency", "Ad Partner" or "AP".
"""
from __future__ import annotations

LABEL = {"AD_PARTNER_ADMIN": "Media Partner Admin", "RESELLER_ADMIN": "Advertising Partner Admin",
         "CAMPAIGN_MANAGER": "Campaign Manager", "RESELLER_CAMPAIGN_MANAGER": "Campaign Manager",
         "NETWORK_PARTNER_ADMIN": "Network Partner Admin", "BRAND_MANAGER": "Brand Manager",
         "MARKETING_AGENCY_ADMIN": "Marketing Agency Admin", "ADOPS_ADMIN": "Adops Admin"}

# An Advertising Partner's seats get exactly a Media Partner's access (ROLE_PERMISSIONS in
# the agency console, GENIE1.md §2), so the catalog and ontology name only the latter.
SAME_ACCESS = {"RESELLER_ADMIN": "AD_PARTNER_ADMIN", "RESELLER_CAMPAIGN_MANAGER": "CAMPAIGN_MANAGER"}

# Whose admin a campaign manager asks: their own partner's.
ADMIN_OF = {"CAMPAIGN_MANAGER": "AD_PARTNER_ADMIN", "RESELLER_CAMPAIGN_MANAGER": "RESELLER_ADMIN"}


def access(role: str | None) -> str | None:
    """The role whose access this one has."""
    return SAME_ACCESS.get(role, role) if role else role


def label(role: str | None, default: str = "your role") -> str:
    return LABEL.get(role or "", default)


def a_label(role: str | None) -> str:
    """ "a Campaign Manager", "an Advertising Partner Admin" - and AdGrid's own ops, "AdGrid"."""
    if role == "ADOPS_ADMIN":
        return "AdGrid"
    name = label(role, (role or "").replace("_", " ").title())
    return ("an " if name[:1] in "AEIOU" else "a ") + name


def is_campaign_manager(role: str | None) -> bool:
    return access(role) == "CAMPAIGN_MANAGER"
