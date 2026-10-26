"""Navigation: the route Genie hands back has to be a route that exists.

Nine of the twelve screen routes in this ontology were invented from Figma
frames and survived to a merge. Nothing caught it, because the golden set
scores capability, mode and follow-up and never once looked at the route. Then
the routes were corrected and a second defect appeared behind the first:
navigate_facts handed back `screen.route` verbatim, so a user asking for the
creative library was told to visit "/brand-management/library/{brandId}".

Both failures are the same shape - a link nobody checked - so these tests check
the link. They run against the stub: no key, no network, no token.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from genie.chat.responder import Responder
from genie.chat.session import Session, UIContext
from genie.ontology import load
from genie.ontology.models import Mode
from genie.router.answers import fill_route, navigate_facts, route_params

REPO = Path(__file__).resolve().parents[2]
ROLES = ("AD_PARTNER_ADMIN", "CAMPAIGN_MANAGER", "BRAND_MANAGER")


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


@pytest.fixture(scope="module")
def registry() -> dict:
    with (REPO / "ontology" / "routes.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def shape(route: str) -> str:
    """A route with its parameters collapsed, so a rename is not a difference."""
    return "/".join("*" if (s.startswith("{") and s.endswith("}")) or s.startswith(":") else s
                    for s in route.split("/"))


def navigate_caps(ontology):
    return [c for c in ontology.capabilities.values() if c.mode is Mode.NAVIGATE]


# ── the registry ────────────────────────────────────────────────────────

def test_every_screen_route_is_one_the_console_serves(ontology, registry):
    known = {shape(r) for r in registry["routes"]} | {registry["overlay"]}
    for screen in ontology.screens.values():
        for route in (screen.route, screen.entity_route):
            if route:
                assert shape(route) in known, (
                    f"{screen.id}: '{route}' is not in ontology/routes.yaml")


def test_no_two_screens_claim_one_route(ontology, registry):
    seen: dict[str, str] = {}
    for screen in ontology.screens.values():
        if screen.route == registry["overlay"]:
            continue
        key = shape(screen.route)
        assert key not in seen, (
            f"{screen.id} and {seen[key]} both own '{screen.route}' — NAVIGATE "
            f"would have two answers for one page")
        seen[key] = screen.id


def test_the_basename_is_recorded(registry):
    """Genie emits "/wallet-management"; the browser is at "/agency/wallet-management"."""
    assert registry["basename"] == "/agency"


# ── no placeholder ever escapes ─────────────────────────────────────────

@pytest.mark.parametrize("brand_id", [None, "b-42"])
@pytest.mark.parametrize("role", ROLES)
def test_a_navigate_answer_is_never_a_template(ontology, role, brand_id):
    """The defect this file exists for: '{brandId}' reaching a user."""
    for cap in navigate_caps(ontology):
        facts = navigate_facts(cap, ontology, role, brand_id=brand_id,
                               campaign_id=None)
        for block in facts.blocks:
            if block["type"] == "navigate":
                assert "{" not in block["content"], f"{cap.id} → {block['content']}"
                assert "}" not in block["content"], f"{cap.id} → {block['content']}"


def test_an_unfillable_route_is_refused_rather_than_handed_over(ontology):
    cap = ontology.capabilities["navigate.creatives"]
    facts = navigate_facts(cap, ontology, "AD_PARTNER_ADMIN")
    kinds = {b["type"] for b in facts.blocks}
    assert "navigate" not in kinds          # no link at all beats a broken one
    assert "navigate_needs" in kinds
    said = next(b["content"] for b in facts.blocks if b["type"] == "navigate_needs")
    assert "brand" in said and "{" not in said


def test_fill_route_refuses_a_partial_substitution():
    assert fill_route("/campaign/{campaignId}", {"campaignId": "c1"}) == "/campaign/c1"
    assert fill_route("/campaign/{campaignId}", {}) is None
    assert fill_route("/wallet-management", {}) == "/wallet-management"
    assert route_params("/brand-management/library/{brandId}") == ("brandId",)


# ── the list page and the entity page are different pages ───────────────

def test_brand_management_opens_its_list_without_naming_a_brand(ontology):
    """"open brand management" is answerable: /brand-management is the list."""
    facts = navigate_facts(ontology.capabilities["navigate.brands"], ontology,
                           "AD_PARTNER_ADMIN")
    link = next(b for b in facts.blocks if b["type"] == "navigate")
    assert link["content"] == "/brand-management"
    assert link["meta"]["entity"] is False


def test_brand_management_opens_one_brand_when_the_panel_names_one(ontology):
    facts = navigate_facts(ontology.capabilities["navigate.brands"], ontology,
                           "AD_PARTNER_ADMIN", brand_id="b-42")
    link = next(b for b in facts.blocks if b["type"] == "navigate")
    assert link["content"] == "/brand-management/b-42"
    assert link["meta"]["entity"] is True


# ── through the whole turn ──────────────────────────────────────────────

def run(responder, session, text):
    import asyncio

    async def go():
        return [b async for b in responder.respond(session, text)]
    return asyncio.run(go())


def turn(ontology, text, *, role="AD_PARTNER_ADMIN", screen=None, brand_id=None):
    session = Session(id="t")
    session.context = UIContext(screen=screen, role=role, brand_id=brand_id)
    return run(Responder(ontology, None), session, text)


def test_opening_brand_management_does_not_interrogate(ontology):
    """"Which brand?" for a page that lists every brand is a question nobody posed."""
    blocks = turn(ontology, "open brand management")
    kinds = {b.type for b in blocks[1:]}
    assert "follow_up" not in kinds
    assert next(b.content for b in blocks if b.type == "navigate") == "/brand-management"


def test_a_campaign_manager_reaches_wallet_management(ontology):
    """Ungated in the nav bar (GENIE1 section 3); what VIEW_WALLET gates is the
    account-level figures ON the screen, not the door."""
    blocks = turn(ontology, "open wallet management", role="CAMPAIGN_MANAGER")
    assert blocks[0].meta["denied"] is False
    assert next(b.content for b in blocks if b.type == "navigate") == "/wallet-management"


def test_a_campaign_manager_is_still_refused_team_management(ontology):
    """MANAGE_ASSIGNEES is genuinely not theirs - the gate that should hold."""
    blocks = turn(ontology, "go to team management", role="CAMPAIGN_MANAGER")
    assert blocks[0].meta["denied"] is True


@pytest.mark.parametrize("role", ROLES)
def test_no_turn_ever_streams_a_placeholder(ontology, role):
    for text in ("take me home", "open brand management", "take me to inventory",
                 "open wallet management", "go to team management",
                 "what needs approving", "open the creative library"):
        for brand in (None, "b-42"):
            for block in turn(ontology, text, role=role, brand_id=brand):
                assert "{brandId}" not in (block.content or "")
                assert "{campaignId}" not in (block.content or "")
