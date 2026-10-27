"""Campaigns, live — "how is my campaign doing?" and "what campaigns are running?".

No network: the dashboard API is a fake answering with payloads shaped like the
live QA spec (CampaignListResponse, Campaign{Impressions,Reach,Spend}Response).
The promises:

  · which campaign is decided by code — chosen, on screen, named, or the only
    running one — and otherwise Genie asks, with the user's own campaigns
  · nothing played, nothing read: drafts and scheduled campaigns make no
    delivery calls, and a failed delivery call is reported, never zeroed
  · the window is echoed, counts come from totalElements, the cost leg never leaks
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from genie.auth.identity import Grant, Identity
from genie.bff import BffClient
from genie.chat.responder import Responder
from genie.chat.session import Session, UIContext
from genie.data.campaigns import Campaign, campaign_view, list_campaigns, time_elapsed
from genie.data.money import count, count_short
from genie.ontology import load
from genie.router.live_campaigns import list_answer, performance_answer

REPO = Path(__file__).resolve().parents[2]
QA = "https://ag-dashboard-bff.qa.adgrid.ai"
CLIENT = "c-infinity"
TODAY = date.today()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()  # noqa: E731


def jwt(**claims) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


TOKEN = jwt(sub="u-1", azp="adg-dashboard", exp=int(time.time()) + 300)


def envelope(data, success=True):
    return {"success": success, "code": "OK", "message": "", "data": data}


def row(cid, name, state, start, end, cost="300000.00", brand="b-1"):
    return {"campaignId": cid, "campaignCode": cid.upper(), "name": name, "clientId": CLIENT,
            "advertisedBrandId": brand, "startDate": start, "endDate": end, "status": state,
            "effectiveStatus": state, "notPlayingReason": None, "totalCost": cost,
            "paused": state == "PAUSED", "scope": {"devices": 240, "store": 80, "cities": 6}}


DIWALI = row("11111111-1111-1111-1111-111111111111", "Diwali Blast", "LIVE", D(-11), D(18))
MONSOON = row("22222222-2222-2222-2222-222222222222", "Monsoon Sale", "LIVE", D(-3), D(26))
WINTER = row("33333333-3333-3333-3333-333333333333", "Winter Launch", "SCHEDULED", D(10), D(40))
SUMMER = row("44444444-4444-4444-4444-444444444444", "Summer Splash", "COMPLETED", D(-90), D(-60))


def delivery(cid):
    window = {"campaignId": cid, "status": "LIVE", "startDate": D(-11), "endDate": D(18),
              "from": D(-11), "to": D(0)}
    return {
        f"/api/v1/campaign-analytics/{cid}/impressions": envelope(window | {
            "totalImpressions": 1234567, "dailyImpressions": []}),
        f"/api/v1/campaign-analytics/{cid}/reach": envelope(window | {
            "reachForRange": 345678, "dailyReach": 28806, "totalDeliverableReach": 900000,
            "dailyReachInRange": []}),
        f"/api/v1/campaign-analytics/{cid}/spend": envelope(window | {
            "spendForRange": 120000, "totalCost": 300000, "dailySpend": 10000,
            "dailySpendInRange": []}),
    }


def listing(*rows, total=None):
    return envelope({"campaigns": list(rows), "currentPage": 0, "pageSize": 100,
                     "totalElements": total if total is not None else len(rows),
                     "totalPages": 1, "hasNext": False, "hasPrevious": False})


class FakeBff:
    def __init__(self, campaigns=(DIWALI, MONSOON, WINTER, SUMMER), total=None, overrides=None):
        self.calls: list[httpx.Request] = []
        self.payloads = {"/api/v1/campaigns": listing(*campaigns, total=total)}
        for c in campaigns:
            self.payloads.update(delivery(c["campaignId"]))
            self.payloads[f"/api/v1/campaigns/{c['campaignId']}"] = envelope(
                c | {"adgridCost": "254237.29", "costBreakdown": [{"leg": "ADGRID", "amount": "1"}]})
        self.overrides = overrides or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path in self.overrides:
            status, body = self.overrides[path]
            return httpx.Response(status, json=body)
        if path in self.payloads:
            return httpx.Response(200, json=self.payloads[path])
        return httpx.Response(404, json=envelope(None, False))

    @property
    def paths(self) -> list[str]:
        return [c.url.path for c in self.calls]


def client_for(fake: FakeBff) -> BffClient:
    return BffClient(QA, portal_id="ADOPS_PORTAL", transport=httpx.MockTransport(fake))


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


def session(screen="command_centre", selected=None, role="AD_PARTNER_ADMIN") -> Session:
    s = Session(id="s1")
    s.context = UIContext(screen=screen, role=role, client_id=CLIENT, selected_entity=selected)
    s.identity = Identity("u-1", "Admin", role, CLIENT,
                          (Grant(CLIENT, "Infinity", role, frozenset({"VIEW_CAMPAIGN"})),))
    s.names_read_for = CLIENT         # past session start (Responder._learn_names): only this test's reads
    return s


def run(responder, s, text, token=TOKEN):
    async def go():
        return [b async for b in responder.respond(s, text, token=token)]
    return asyncio.run(go())


def card(blocks):
    return next(b for b in blocks if b.type == "metrics")


# ── formatting and arithmetic ───────────────────────────────────────────

def test_counts_are_grouped_the_indian_way():
    assert count(1234567) == "12,34,567" and count(950) == "950"
    assert count_short(1234567) == "12.35 L" and count_short(950) is None


def test_time_elapsed_is_the_share_of_the_flight():
    c = Campaign.from_payload(row("x", "X", "LIVE", D(-9), D(20)))
    assert time_elapsed(c, TODAY).display == "Day 10 of 30 (33%)"
    ended = Campaign.from_payload(row("x", "X", "COMPLETED", D(-40), D(-11)))
    assert time_elapsed(ended, TODAY).display == "Day 30 of 30 (100%)"


# ── reading ─────────────────────────────────────────────────────────────

def test_the_list_counts_from_total_elements_and_sorts_newest_first():
    fake = FakeBff(total=137)
    got = asyncio.run(list_campaigns(client_for(fake), TOKEN, CLIENT))
    assert got.total == 137 and got.partial
    assert [c.name for c in got.campaigns][0] == "Winter Launch"
    req = fake.calls[0]
    assert req.url.params["clientId"] == CLIENT and req.headers["authorization"] == f"Bearer {TOKEN}"


def test_delivery_is_read_and_its_window_echoed():
    fake = FakeBff()
    c = Campaign.from_payload(DIWALI)
    view = asyncio.run(campaign_view(client_for(fake), TOKEN, c))
    assert view.fact("campaign_impressions").display == "12,34,567"
    assert view.fact("campaign_reach").display == "9,00,000"       # the plan: totalDeliverableReach
    assert view.fact("campaign_spend").display == "₹1,20,000 of ₹3,00,000"
    answer = performance_answer(view)
    lo = date.fromisoformat(D(-11))
    assert f"{lo.day} {lo:%b %Y}" in answer.template            # the window, said back
    assert "recorded 12,34,567 impressions" in answer.template
    assert "booked to reach about 9,00,000 people" in answer.template
    assert "₹1,20,000 of ₹3,00,000 of its cost is scheduled to date" in answer.template
    assert "reached" not in answer.template and "spent" not in answer.template


def test_nothing_played_means_nothing_is_read():
    fake = FakeBff()
    view = asyncio.run(campaign_view(client_for(fake), TOKEN, Campaign.from_payload(WINTER)))
    assert not any("campaign-analytics" in p for p in fake.paths)
    assert "scheduled to start on" in performance_answer(view).template


def test_a_failed_delivery_call_is_reported_not_zeroed():
    cid = DIWALI["campaignId"]
    fake = FakeBff(overrides={f"/api/v1/campaign-analytics/{cid}/reach": (403, envelope(None, False))})
    view = asyncio.run(campaign_view(client_for(fake), TOKEN, Campaign.from_payload(DIWALI)))
    assert view.fact("campaign_reach") is None and view.fact("campaign_impressions") is not None
    text = performance_answer(view).template
    assert "missing because your account does not have access" in text and " 0 people" not in text


def test_the_cost_leg_never_reaches_an_answer(ontology):
    fake = FakeBff()
    blocks = run(Responder(ontology, None, bff=client_for(fake)),
                 session("campaign_detail", selected=DIWALI["campaignId"]), "how is this performing")
    dumped = json.dumps([b.meta for b in blocks if b.meta]) + " ".join(b.content for b in blocks)
    assert "254237" not in dumped and "2,54,237" not in dumped and "ADGRID" not in dumped


# ── which campaign ──────────────────────────────────────────────────────

def test_several_running_means_asking_with_their_names_then_answering(ontology):
    fake = FakeBff()
    responder = Responder(ontology, None, bff=client_for(fake))
    s = session()
    first = run(responder, s, "how is my campaign doing")
    ask = next(b for b in first if b.type == "follow_up")
    assert ask.content == "Which campaign?"
    assert [o["label"] for o in ask.meta["options"]] == ["Monsoon Sale", "Diwali Blast"]
    assert not any("campaign-analytics" in p for p in fake.paths)    # nothing read before choosing
    second = run(responder, s, "Diwali Blast")
    assert card(second).meta["title"] == "Diwali Blast"
    assert "Diwali Blast is live" in second[-1].content


def test_a_named_campaign_needs_no_question(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff())), session(),
                 "how is the monsoon sale campaign doing")
    assert not any(b.type == "follow_up" for b in blocks)
    assert card(blocks).meta["title"] == "Monsoon Sale"


def test_the_campaign_on_screen_is_the_one_meant(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff())),
                 session("campaign_detail", selected=MONSOON["campaignId"]), "how is this performing")
    assert not any(b.type == "follow_up" for b in blocks)
    assert card(blocks).meta["title"] == "Monsoon Sale"
    assert card(blocks).meta["campaign"]["route"] == f"/campaign/{MONSOON['campaignId']}"


def test_one_running_campaign_is_answered_straight_away(ontology):
    fake = FakeBff(campaigns=(DIWALI, WINTER, SUMMER))
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "how is my campaign doing")
    assert not any(b.type == "follow_up" for b in blocks)
    assert card(blocks).meta["title"] == "Diwali Blast"
    assert blocks[-1].meta["narrated_by"] == "template"


def test_no_campaigns_says_so(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff(campaigns=()))), session(),
                 "how is my campaign doing")
    assert blocks[-1].content == "You don't have any campaigns yet."


def test_period_is_not_asked_before_the_campaign(ontology):
    """The window defaults to campaign-to-date and is echoed; one question at most."""
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff(campaigns=(DIWALI,)))),
                 session("brand_management"), "how is my campaign doing")
    assert not any(b.type == "follow_up" for b in blocks)


# ── the list ────────────────────────────────────────────────────────────

def test_the_list_says_how_many_and_names_the_running_ones():
    fake = FakeBff()
    got = asyncio.run(list_campaigns(client_for(fake), TOKEN, CLIENT))
    answer = list_answer(got, "which campaigns are live")
    assert answer.template.startswith("You have 4 campaigns: 2 live, 1 scheduled, 1 completed.")
    assert "Running: Monsoon Sale, Diwali Blast." in answer.template


def test_a_partial_page_is_admitted():
    got = asyncio.run(list_campaigns(client_for(FakeBff(total=137)), TOKEN, CLIENT))
    text = list_answer(got, "list all campaigns").template
    assert text.startswith("You have 137 campaigns") and "covers the newest 4" in text


def test_list_end_to_end_after_which_campaigns(ontology):
    responder = Responder(ontology, None, bff=client_for(FakeBff()))
    s = session()
    first = run(responder, s, "show me my campaigns")
    if any(b.type == "follow_up" for b in first):        # catalog: which_state may ask
        first = run(responder, s, "Scheduled")
    assert "Scheduled: Winter Launch." in first[-1].content


def test_naming_a_state_answers_which_campaigns(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff())), session(),
                 "what campaigns are running")
    assert not any(b.type == "follow_up" for b in blocks)
    assert "Running: Monsoon Sale, Diwali Blast." in blocks[-1].content


# ── a response shaped exactly as QA returns it (2026-10-23) ──────────────
# The VALUES are QA test data and need not agree with each other; what matters is
# that Genie reads the contract's fields and reports them as returned.

QA_CID = "19c539af-24d8-47ec-ae33-779de7f9e925"
QA_ROW = row(QA_CID, "Monsoon Malabar Launch", "COMPLETED", "2026-10-16", "2026-10-20", cost="87.12")
QA_ROW["scope"] = {"devices": 1, "store": 0, "cities": 1}
QA_WINDOW = {"campaignId": QA_CID, "status": "COMPLETED", "startDate": "2026-10-16",
             "endDate": "2026-10-20", "cancelledOn": None, "from": "2026-10-16", "to": "2026-10-20"}
QA_DAYS = ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"]
QA_PAYLOADS = {
    f"/api/v1/campaign-analytics/{QA_CID}/impressions": envelope(QA_WINDOW | {
        "totalImpressions": 0, "dailyImpressions": [{"date": d, "impressions": 0} for d in QA_DAYS]}),
    f"/api/v1/campaign-analytics/{QA_CID}/reach": envelope(QA_WINDOW | {
        "timeSlots": ["AFTERNOON", "EVENING"], "dailyReach": 633, "totalDeliverableReach": 3165,
        "reachForRange": 3165, "dailyReachInRange": [{"date": d, "reach": 633} for d in QA_DAYS]}),
    f"/api/v1/campaign-analytics/{QA_CID}/spend": envelope(QA_WINDOW | {
        "totalCost": 87.12, "dailySpend": 17.42, "spendForRange": 87.12,
        "dailySpendInRange": [{"date": d, "amount": 17.42 if d != QA_DAYS[-1] else 17.44}
                              for d in QA_DAYS]}),
}


def test_a_completed_campaign_as_qa_returns_it(ontology):
    fake = FakeBff(campaigns=(QA_ROW,))
    fake.payloads.update(QA_PAYLOADS)
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "how is my campaign doing")
    assert not any(b.type == "follow_up" for b in blocks)        # one campaign: answer, don't ask
    text = blocks[-1].content
    assert text == ("Monsoon Malabar Launch finished on 20 Oct 2026. It recorded 0 impressions "
                    "between 16 Oct 2026 and 20 Oct 2026. It cost ₹87.12 and it was booked to reach "
                    "about 3,165 people. It ran on 1 screen in 0 stores across 1 city.")


def test_drafts_are_not_offered_when_a_real_campaign_exists(ontology):
    draft = row("55555555-5555-5555-5555-555555555555", "hjuh", "DRAFT", D(5), D(5))
    fake = FakeBff(campaigns=(draft, QA_ROW))
    fake.payloads.update(QA_PAYLOADS)
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "how is my campaign doing")
    assert not any(b.type == "follow_up" for b in blocks)
    assert card(blocks).meta["title"] == "Monsoon Malabar Launch"


def test_no_campaigns_sends_no_empty_card(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff(campaigns=()))), session(),
                 "how is my campaign doing")
    assert not any(b.type == "metrics" for b in blocks)


def test_a_campaign_the_user_cannot_see_is_refused_by_name_not_shown(ontology):
    """Live QA: another agency's campaign id on screen -> the BFF says 403, Genie shows nothing of it."""
    other = "99999999-9999-9999-9999-999999999999"
    fake = FakeBff(overrides={f"/api/v1/campaigns/{other}": (403, envelope(None, False))})
    blocks = run(Responder(ontology, None, bff=client_for(fake)),
                 session("campaign_detail", selected=other), "how is this performing")
    assert blocks[-1].content == "I couldn't open that campaign: your account does not have access to that."
    assert not any(b.type == "metrics" for b in blocks)
    assert not any("campaign-analytics" in p for p in fake.paths)


# ── one campaign, for the OTP-gated actions and open ─────────────────────

def nav(blocks):
    return next((b for b in blocks if b.type == "navigate"), None)


def test_pause_asks_which_live_campaign_then_links_to_it(ontology):
    responder = Responder(ontology, None, bff=client_for(FakeBff()))
    s = session()
    first = run(responder, s, "pause my campaign")
    ask = next(b for b in first if b.type == "follow_up")
    assert [o["label"] for o in ask.meta["options"]] == ["Monsoon Sale", "Diwali Blast"]   # live only
    second = run(responder, s, "Diwali Blast")
    assert nav(second).content == f"/campaign/{DIWALI['campaignId']}"
    assert second[-1].content.startswith("Pausing needs an OTP")      # the catalog's own words
    assert second[-1].content.endswith("Here is Diwali Blast.")


def test_an_action_on_a_campaign_it_cannot_apply_to_says_why(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff())), session(),
                 "pause the summer splash campaign")
    assert blocks[0].content == "campaign.pause"
    assert blocks[-1].content == "Summer Splash is completed, so it can't be paused."


def test_nothing_to_resume_says_so(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff())), session(), "resume my campaign")
    assert blocks[-1].content == "You have no paused campaign to resume right now."
    assert nav(blocks) is None


@pytest.mark.parametrize("names_known", [False, True])
def test_open_a_named_campaign_goes_to_its_page(ontology, names_known):
    s = session()
    s.names_read_for = None if not names_known else s.names_read_for   # fresh: names read first
    blocks = run(Responder(ontology, None, bff=client_for(FakeBff())), s,
                 "open the monsoon sale campaign")
    assert blocks[0].content == "campaign.open"
    assert not any(b.type == "follow_up" for b in blocks)
    assert nav(blocks).content == f"/campaign/{MONSOON['campaignId']}"
    assert blocks[-1].content == "Opening Monsoon Sale."


def test_a_model_never_rewords_an_otp_refusal(ontology):
    class Chatty:
        name = "fake"
        async def stream(self, messages, **kw):
            yield "Sure, I've paused it for you!"
    fake = FakeBff(campaigns=(DIWALI, SUMMER))
    blocks = run(Responder(ontology, Chatty(), bff=client_for(fake), live_wording="model"),
                 session(), "pause my campaign")
    assert blocks[-1].meta["narrated_by"] == "template"
    assert blocks[-1].content.startswith("Pausing needs an OTP")


# ── pacing ──────────────────────────────────────────────────────────────

def test_pacing_says_what_it_knows_and_what_is_not_served(ontology):
    fake = FakeBff(campaigns=(DIWALI, SUMMER))
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "is my campaign on track")
    assert blocks[0].content == "campaign.explain_pacing"
    text = blocks[-1].content
    assert text.startswith("Diwali Blast is live, on day 12 of its 30-day flight (40%).")
    assert "recorded 12,34,567 impressions" in text
    assert "doesn't report planned impressions" in text


# ── audience ────────────────────────────────────────────────────────────

def test_audience_is_the_gender_split_of_impressions(ontology):
    cid = DIWALI["campaignId"]
    fake = FakeBff(campaigns=(DIWALI, SUMMER))
    fake.payloads[f"/api/v1/campaign-analytics/{cid}/impressions/gender"] = envelope({
        "campaignId": cid, "status": "LIVE", "from": D(-11), "to": D(0), "totalImpressions": 1234567,
        "genders": [{"gender": "FEMALE", "label": "Female", "impressions": 518518, "sharePct": 42.0},
                    {"gender": "MALE", "label": "Male", "impressions": 716049, "sharePct": 58.0}]})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "who saw my ad")
    assert blocks[0].content == "campaign.explain_audience"
    lo, hi = date.fromisoformat(D(-11)), TODAY
    assert blocks[-1].content == (f"Diwali Blast's 12,34,567 impressions between {lo.day} {lo:%b %Y} "
                                  f"and {hi.day} {hi:%b %Y} split by gender: Male 58%, Female 42%. "
                                  "Age isn't reported per campaign.")
    assert [i["label"] for i in card(blocks).meta["items"]] == ["Male", "Female"]


def test_audience_failure_is_reported_not_zeroed(ontology):
    cid = DIWALI["campaignId"]
    fake = FakeBff(campaigns=(DIWALI,), overrides={
        f"/api/v1/campaign-analytics/{cid}/impressions/gender": (500, envelope(None, False))})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "who saw my ad")
    assert blocks[-1].content == "I couldn't read the audience for Diwali Blast: the dashboard did not answer just now."


# ── links under a brand ─────────────────────────────────────────────────

BRANDS = envelope({"brands": [{"brandId": "b-tanishq", "brandName": "Tanishq"},
                              {"brandId": "b-titan", "brandName": "Titan Eye"}],
                   "currentPage": 0, "pageSize": 100, "totalElements": 2})


def brand_fake(brands=BRANDS):
    fake = FakeBff()
    fake.payloads[f"/api/v1/clients/{CLIENT}/brands"] = brands
    return fake


def test_a_brand_link_never_carries_the_placeholder(ontology):
    responder = Responder(ontology, None, bff=client_for(brand_fake()))
    s = session()
    first = run(responder, s, "launch a new campaign")
    ask = next(b for b in first if b.type == "follow_up")
    assert ask.content == "Which brand?" and [o["label"] for o in ask.meta["options"]] == ["Tanishq", "Titan Eye"]
    second = run(responder, s, "Titan Eye")
    assert nav(second).content == "/campaign/create-campaign/b-titan"
    assert second[-1].content == "Opening Create Campaign for Titan Eye."


def test_a_named_brand_or_the_one_on_screen_needs_no_question(ontology):
    responder = Responder(ontology, None, bff=client_for(brand_fake()))
    named = run(responder, session(), "go to creative library for tanishq")
    assert nav(named).content == "/brand-management/library/b-tanishq"
    s = session("brand_management")
    s.context.brand_id = "b-titan"
    on_screen = run(responder, s, "launch a new campaign")
    assert nav(on_screen).content == "/campaign/create-campaign/b-titan"


def test_a_campaign_manager_reads_the_brand_management_list(ontology):
    one = envelope({"brands": [{"brandId": "b-tanishq", "brandName": "Tanishq"}], "totalElements": 1})
    fake = brand_fake(one)
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(role="CAMPAIGN_MANAGER"),
                 "launch a new campaign")
    assert f"/api/v1/clients/{CLIENT}/brands" in fake.paths
    assert not any("/members/" in p for p in fake.paths)    # the admin's member read refuses them
    assert nav(blocks).content == "/campaign/create-campaign/b-tanishq"



# ── where and when it played ────────────────────────────────────────────

def breakdown_fake(group_rows, daypart=None):
    cid = DIWALI["campaignId"]
    window = {"campaignId": cid, "from": D(-11), "to": D(0)}
    return FakeBff(campaigns=(DIWALI, WINTER), overrides={
        f"/api/v1/campaign-analytics/{cid}/performance-breakdown": (200, envelope(window | {
            "totalImpressions": 100000, "spendAvailable": True, "totalSpend": 98765.43,
            "rows": group_rows})),
        f"/api/v1/campaign-analytics/{cid}/impressions/daypart": (200, envelope(window | (daypart or {}))),
    })


CITY_ROWS = [{"key": "MUM", "label": "Mumbai", "screens": 12, "impressions": 35000, "sharePct": 35.0,
              "spend": 98765.43},
             {"key": "DEL", "label": "Delhi", "screens": 20, "impressions": 65000, "sharePct": 65.0,
              "spend": 1.0}]


def test_impressions_by_city_most_first_and_never_the_planned_spend(ontology):
    fake = breakdown_fake(CITY_ROWS)
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(),
                 "which city is my campaign doing best in")
    assert blocks[0].content == "campaign.explain_breakdown"
    call = next(c for c in fake.calls if c.url.path.endswith("/performance-breakdown"))
    assert call.url.params["groupBy"] == "CITY"
    assert blocks[-1].content == (f"Diwali Blast's 1,00,000 impressions between {day_(D(-11))} and "
                                  f"{day_(D(0))} by city, most first: Delhi 65,000 (65%), Mumbai 35,000 (35%).")
    assert "98,765" not in str([b.meta for b in blocks])


def test_store_types_are_asked_for_by_name(ontology):
    fake = breakdown_fake(CITY_ROWS)
    run(Responder(ontology, None, bff=client_for(fake)), session(), "which store type performs best")
    call = next(c for c in fake.calls if c.url.path.endswith("/performance-breakdown"))
    assert call.url.params["groupBy"] == "CATEGORY"


def test_time_of_day_names_the_busiest_daypart_and_hour(ontology):
    fake = breakdown_fake([], daypart={"totalImpressions": 100000, "outsideDaypartImpressions": 0, "dayparts": [
        {"timeSlot": "EVENING", "impressions": 30000, "sharePct": 30.0, "hours": []},
        {"timeSlot": "MORNING", "impressions": 70000, "sharePct": 70.0, "hours": [
            {"hour": 9, "impressions": 40000, "sharePct": 40.0}, {"hour": 10, "impressions": 30000}]}]})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(),
                 "what time of day does my ad play most")
    assert blocks[0].content == "campaign.explain_daypart"
    assert blocks[-1].content == (f"Most of Diwali Blast's impressions between {day_(D(-11))} and {day_(D(0))} "
                                  f"came in the morning: 70,000 (70%), busiest from 9 am to 10 am. "
                                  f"Then evening 30,000 (30%).")


def day_(iso):
    from genie.data.campaigns import day
    return day(date.fromisoformat(iso))
