"""Brands, live - the list, how one is doing, which is doing best, who owns it - and what
is blocking a draft campaign's launch.

No network: a fake BFF answers with payloads shaped like the live spec (BrandListResponse,
BrandAnalyticsResponse, ClientDetailCompositeResponse, CampaignFullDetailResponse). The
promises: figures are said as the ontology defines them (booked value is an estimate, reach
is per day), only allowlisted fields are read, and the period is asked at most once.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from genie.chat.responder import Responder
from genie.data.fields import today
from genie.ontology import load
from .test_campaigns import (CLIENT, DIWALI, FakeBff, WINTER, client_for, envelope, row, run)
from .test_campaigns import session as known_session

REPO = Path(__file__).resolve().parents[2]
COKE, PEPSI = "b-coke", "b-pepsi"
LIST = envelope({"brands": [
    {"brandId": COKE, "brandName": "Coca-Cola", "campaignsCount": 12, "totalBudget": "450000.00"},
    {"brandId": PEPSI, "brandName": "Pepsi", "campaignsCount": 1, "totalBudget": "20000.00"}],
    "totalElements": 2})


def stats(impressions, live=2, screens=40, reach=9000, booked="250000.00"):
    return envelope({"screensUsed": screens, "activeCampaigns": live, "totalEstimatedRevenue": booked,
                     "totalDailyImpressions": impressions, "totalAudienceReach": reach,
                     "screenDistribution": [{"city": "DEL", "screenCount": 30, "percentage": 75.0},
                                            {"city": "NOI", "screenCount": 10, "percentage": 25.0}]})


def brands_fake(**overrides):
    fake = FakeBff()
    fake.payloads.update({
        f"/api/v1/clients/{CLIENT}/brands": LIST,
        f"/api/v1/clients/{COKE}/analytics": stats(120000),
        f"/api/v1/clients/{PEPSI}/analytics": stats(30000, live=1, screens=8, booked="20000.00"),
        f"/api/v1/clients/{COKE}": envelope({"clientInfo": {
            "displayName": "Coca-Cola", "contactPersonName": "Rahul Sharma", "designation": "Brand Manager",
            "email": "rahul@coke.example", "phoneNumber": "+91 98100 00000", "assignedUserName": "Neha",
            "gstNumber": "07AAACC1234F1Z5", "panNumber": "AAACC1234F"}}),
    })
    fake.payloads.update(overrides)
    return fake


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


def session(**kw):
    """A session at its first message: the user's names are read before routing."""
    s = known_session(**kw)
    s.names_read_for = None
    return s


def calls(fake, suffix):
    return [c for c in fake.calls if c.url.path.endswith(suffix)]


# ── the list ────────────────────────────────────────────────────────────

def test_the_list_names_brands_with_their_campaigns(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(brands_fake())), session(), "list my brands")
    assert blocks[0].content == "brand.list"
    assert blocks[-1].content == "You have 2 brands: Coca-Cola (12 campaigns), Pepsi (1 campaign)."


def test_a_campaign_manager_reads_the_same_list(ontology):
    fake = brands_fake()
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(role="CAMPAIGN_MANAGER"),
                 "list my brands")
    assert calls(fake, f"/clients/{CLIENT}/brands") and not calls(fake, "/members/u-1/brands")
    assert blocks[-1].content == "You have 2 brands: Coca-Cola (12 campaigns), Pepsi (1 campaign)."


def test_a_brand_with_nothing_in_the_window_says_so(ontology):
    quiet = envelope({"screensUsed": 0, "activeCampaigns": 0, "totalEstimatedRevenue": "0.00",
                      "totalDailyImpressions": 0, "totalAudienceReach": 0, "screenDistribution": []})
    fake = brands_fake(**{f"/api/v1/clients/{COKE}/analytics": quiet})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(),
                 "how is Coca-Cola doing this month")
    assert blocks[-1].content == ("Coca-Cola had nothing running this month: no screens, impressions or "
                                  "reach, and nothing booked.")


def test_best_brand_with_one_brand_is_that_brands_figures(ontology):
    one = envelope({"brands": [{"brandId": COKE, "brandName": "Coca-Cola"}], "totalElements": 1})
    fake = brands_fake(**{f"/api/v1/clients/{CLIENT}/brands": one})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(),
                 "which brand is performing best this month")
    assert len(calls(fake, "/analytics")) == 1
    assert blocks[-1].content.startswith("Coca-Cola, this month: 2 live campaigns on 40 screens")


# ── how a brand is doing ────────────────────────────────────────────────

def test_a_named_brand_with_a_period_needs_no_question(ontology):
    fake = brands_fake()
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(),
                 "how is Coca-Cola doing this month")
    assert blocks[0].content == "brand.explain_performance"
    q = calls(fake, f"/{COKE}/analytics")[0].url.params
    assert q["from"] == today().replace(day=1).isoformat() and q["to"] == today().isoformat()
    text = blocks[-1].content
    assert text.startswith("Coca-Cola, this month: 2 live campaigns on 40 screens, about 1,20,000 "
                           "impressions a day and 9,000 people a day in front of its screens.")
    assert "worth an estimated ₹2,50,000 - booked, not yet spent." in text
    assert "Its screens: DEL 75% (30), NOI 25% (10)." in text


def test_no_period_is_asked_once_then_answered(ontology):
    fake = brands_fake()
    responder, s = Responder(ontology, None, bff=client_for(fake)), session()
    first = run(responder, s, "how is Coca-Cola doing")
    ask = next(b for b in first if b.type == "follow_up")
    assert ask.content == "Over what period?"
    second = run(responder, s, "Last 7 days")
    q = calls(fake, f"/{COKE}/analytics")[0].url.params
    assert q["to"] == today().isoformat()
    assert second[-1].content.startswith("Coca-Cola, in the last 7 days:")


def test_all_time_sends_no_dates(ontology):
    fake = brands_fake()
    run(Responder(ontology, None, bff=client_for(fake)), session(), "how is Coca-Cola doing all time")
    assert "from" not in calls(fake, f"/{COKE}/analytics")[0].url.params


def test_which_brand_is_best_compares_them_by_impressions(ontology):
    fake = brands_fake()
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(),
                 "which brand is performing best this month")
    assert len(calls(fake, "/analytics")) == 2         # one each - after the names read at session start
    assert blocks[-1].content == ("By impressions a day this month, your leading brands are Coca-Cola "
                                  "(1,20,000 a day, 2 live campaigns), Pepsi (30,000 a day, 1 live campaign).")


# ── who owns a brand ────────────────────────────────────────────────────

def test_contacts_are_the_brands_person_and_your_manager(ontology):
    fake = brands_fake()
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "who manages Coca-Cola")
    assert blocks[0].content == "brand.explain_contacts"
    assert calls(fake, f"/clients/{COKE}")[0].url.params.get_list("sections") == ["CLIENT_INFO"]
    assert blocks[-1].content == ("Coca-Cola's contact is Rahul Sharma (Brand Manager): rahul@coke.example "
                                  "- +91 98100 00000. Neha manages it on your side.")
    assert "AAACC1234F" not in str([b.meta for b in blocks]) + blocks[-1].content   # PAN never read


# ── what is blocking a draft's launch ───────────────────────────────────

SCREENS = {"deviceSelections": [{"city": "DEL", "shopType": "GROCERY", "deviceCount": 5}]}


def draft_fake(creatives, image=2, video=1, screens=SCREENS):
    draft = row("55555555-5555-5555-5555-555555555555", "Holi Teaser", "DRAFT", "2026-11-10", "2026-11-20")
    detail = draft | {"imageSlotsRequested": image, "videoSlotsRequested": video, **screens,
                      "creatives": creatives, "adgridCost": "99999.00"}
    return FakeBff(campaigns=(DIWALI, WINTER, draft),
                   overrides={f"/api/v1/campaigns/{draft['campaignId']}": (200, envelope(detail))})


def creative(zone, review, reason=None):
    return {"fileName": f"{zone.lower()}.png", "zone": zone, "reviewStatus": review,
            "reviewDecisionReason": reason}


def test_launch_names_what_is_missing(ontology):
    fake = draft_fake([creative("HERO", "APPROVED"), creative("SHOWCASE", "REJECTED", "logo cropped")])
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "what's blocking my launch")
    assert blocks[0].content == "creative.explain_launch_blocker"
    assert blocks[-1].content == ("Holi Teaser can't launch yet: 1 of 2 image slots has no creative and "
                                  "1 creative was rejected (logo cropped). It can be confirmed once every "
                                  "slot has an approved creative.")
    assert "99,999" not in str([b.meta for b in blocks])                # the cost leg is never read


def test_launch_ready_says_so(ontology):
    fake = draft_fake([creative("HERO", "APPROVED"), creative("HERO", "APPROVED"),
                       creative("SHOWCASE", "APPROVED")])
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "what's blocking my launch")
    assert blocks[-1].content == ("Nothing is missing from Holi Teaser: every slot has an approved creative, "
                                  "so it is ready to confirm - confirming needs your OTP.")


def test_launch_with_no_draft_says_nothing_is_waiting(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff(campaigns=(DIWALI, WINTER)))), session(),
                 "what's blocking my launch")
    assert blocks[-1].content == "None of your campaigns is a draft, so nothing is waiting to launch."


@pytest.mark.parametrize("screens, chosen", [
    ({}, False),
    ({"storeSelections": [{"storeId": "s-1", "deviceCount": 2}]}, True),     # picked store by store
    ({"targets": [{"city": "DEL", "shopType": "GROCERY"}]}, True),           # a targeted campaign
])
def test_screens_count_however_they_were_chosen(ontology, screens, chosen):
    fake = draft_fake([creative("HERO", "APPROVED"), creative("HERO", "APPROVED"),
                       creative("SHOWCASE", "APPROVED")], screens=screens)
    text = run(Responder(ontology, None, bff=client_for(fake)), session(), "what's blocking my launch")[-1].content
    assert ("no screens are chosen yet" in text) is not chosen
