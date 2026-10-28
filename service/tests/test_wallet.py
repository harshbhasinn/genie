"""Wallet position — the first live-data answer.

No network anywhere: the dashboard API is a fake transport answering from canned
payloads shaped like the QA spec. Covers the four promises the feature makes:

  · QA only — config and the client both refuse any other host
  · as the user — their token and the portal header go on every call, and /me,
    not the browser, decides role and client
  · nothing invented — figures are formatted by code, and a model's words are
    dropped for the template if they contain a number the card does not
  · nothing leaked — fields outside the allowlist never reach a fact
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from genie.auth.identity import Grant, Identity
from genie.auth.tokens import RefreshingFileToken
from genie.bff import BffClient, BffError
from genie.chat.responder import Responder
from genie.chat.session import MemorySessionStore, Session, UIContext
from genie.config import Settings
from genie.data.money import inr, inr_short, unexplained_numbers
from genie.data.wallet import agency_wallet, my_subwallet
from genie.ontology import load
from genie.router.followups import decide
from genie.router.live_answers import wallet_answer

REPO = Path(__file__).resolve().parents[2]
QA = "https://ag-dashboard-bff.qa.adgrid.ai"
CLIENT = "c-infinity"


def jwt(**claims) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


TOKEN = jwt(sub="u-1", azp="adg-dashboard", exp=int(time.time()) + 300)


def envelope(data, success=True):
    return {"success": success, "code": "OK", "message": "", "data": data}


PAYLOADS = {
    f"/api/v1/clients/{CLIENT}/wallet/balance":
        envelope({"clientId": CLIENT, "balance": "1575000.00", "currency": "INR",
                  "team": {"totalAllocated": "4000000", "totalSpent": "1070000", "balance": "2930000"}}),
    f"/api/v1/clients/{CLIENT}/wallet/kpis":
        envelope({"adgridRevenue": "9999999", "apRevenueTotal": "8888888",   # must never surface
                  "consumedThisMonth": "260000.00", "runwayMonths": "3.24", "lowBalance": False,
                  "lowBalanceThreshold": "500000", "unpaid": "0", "currency": "INR"}),
    f"/api/v1/clients/{CLIENT}/members/wallet-overview":
        envelope({"members": [
            {"name": "Akshay", "currentBalance": "120000", "totalAllocated": "500000", "totalSpent": "380000"},
            {"name": "Neha", "currentBalance": "1810000", "totalAllocated": "2500000", "totalSpent": "690000"},
            {"name": "Ravi", "currentBalance": "1000000", "totalAllocated": "1000000", "totalSpent": "0"}],
            "subWalletCount": 3, "totalSubWalletBalance": "2930000", "currency": "INR"}),
    f"/api/v1/clients/{CLIENT}/members/u-cm/wallet/summary":
        envelope({"currentBalance": "45000", "totalAllocated": "200000", "totalSpent": "155000",
                  "totalRefunded": "0", "currency": "INR"}),
    "/api/v1/me": envelope({"userId": "u-1", "clientId": CLIENT, "primaryRole": "AD_PARTNER_ADMIN",
                            "grants": [{"clientId": CLIENT, "clientName": "Infinity",
                                        "role": "AD_PARTNER_ADMIN", "permissions": ["VIEW_WALLET"]}]}),
}


class FakeBff:
    """Answers like the QA BFF and records what it was sent."""

    def __init__(self, overrides: dict | None = None):
        self.calls: list[httpx.Request] = []
        self.overrides = overrides or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path in self.overrides:
            status, body = self.overrides[path]
            return httpx.Response(status, json=body)
        if not request.headers.get("x-portal-id"):
            return httpx.Response(400, json=envelope(None, False) | {"code": "PORTAL_ID_REQUIRED"})
        if path in PAYLOADS:
            return httpx.Response(200, json=PAYLOADS[path])
        return httpx.Response(404, json=envelope(None, False))


def client_for(fake: FakeBff) -> BffClient:
    return BffClient(QA, portal_id="AGENCY_PORTAL", transport=httpx.MockTransport(fake))


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


# ── QA only ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("base", ["https://ag-dashboard-bff.adgrid.ai", "https://evil.example.com",
                                  "http://ag-dashboard-bff.qa.adgrid.ai"])
def test_config_refuses_anything_but_qa(base):
    with pytest.raises(ValueError, match="QA only"):
        Settings(_env_file=None, genie_env="local", genie_bff_base=base)


def test_config_accepts_qa():
    assert Settings(_env_file=None, genie_env="local", genie_bff_base=QA + "/").genie_bff_base == QA


def test_client_refuses_anything_but_qa():
    with pytest.raises(ValueError):
        BffClient("https://ag-dashboard-bff.adgrid.ai")


# ── the client ───────────────────────────────────────────────────────────

def test_token_and_portal_go_on_every_call():
    fake = FakeBff()
    data = asyncio.run(client_for(fake).get("/api/v1/me", token=TOKEN)).data
    assert data["userId"] == "u-1"
    sent = fake.calls[0].headers
    assert sent["authorization"] == f"Bearer {TOKEN}" and sent["x-portal-id"] == "AGENCY_PORTAL"


@pytest.mark.parametrize("status,kind", [(401, "signed_out"), (403, "forbidden"), (400, "rejected"),
                                         (404, "not_found"), (503, "unavailable")])
def test_errors_keep_their_meaning_and_never_carry_the_token(status, kind):
    fake = FakeBff({"/api/v1/me": (status, envelope(None, False))})
    with pytest.raises(BffError) as caught:
        asyncio.run(client_for(fake).get("/api/v1/me", token=TOKEN))
    assert caught.value.kind == kind
    assert TOKEN not in str(caught.value) and TOKEN not in repr(caught.value)


def test_success_false_is_a_failure_not_an_empty_answer():
    fake = FakeBff({"/api/v1/me": (200, envelope(None, False))})
    with pytest.raises(BffError, match="request failed|^$"):
        asyncio.run(client_for(fake).get("/api/v1/me", token=TOKEN))


def test_no_token_never_calls_out():
    fake = FakeBff()
    with pytest.raises(BffError):
        asyncio.run(client_for(fake).get("/api/v1/me", token=None))
    assert fake.calls == []


# ── money ────────────────────────────────────────────────────────────────

def test_indian_formatting():
    assert inr(Decimal("1575000.00")) == "₹15,75,000"
    assert inr(Decimal("190000000")) == "₹19,00,00,000"
    assert inr_short(Decimal("1575000")) == "₹15.75 L"
    assert inr_short(Decimal("19000000")) == "₹1.90 Cr"
    assert inr(Decimal("48000.5")) == "₹48,000.50"


def test_number_check_catches_an_invented_figure():
    allowed = ["₹15,75,000", "₹15.75 L", "3.2 months"]
    assert unexplained_numbers("You have ₹15.75 L, about 3.2 months.", allowed) == []
    assert unexplained_numbers("You have ₹15.75 L, roughly ₹16 L.", allowed) == ["16"]


# ── reading the wallet ───────────────────────────────────────────────────

def test_agency_wallet_reads_the_ontology_sources(ontology):
    view = asyncio.run(agency_wallet(client_for(FakeBff()), TOKEN, CLIENT))
    got = {f.metric_id: f.display for f in view.facts}
    assert got["main_wallet_balance"] == "₹15,75,000"
    assert got["sub_wallet_total"] == "₹29,30,000"
    assert got["total_credits_owned"] == "₹45,05,000"          # computed in code, never by a model
    assert got["month_consumption"] == "₹2,60,000"
    assert got["wallet_runway_days"] == "3.2 months"            # months, per ruling R32
    assert [m.name for m in view.managers] == ["Akshay", "Ravi", "Neha"]   # lowest balance first
    assert not view.problems


def test_cost_leg_fields_never_become_facts():
    view = asyncio.run(agency_wallet(client_for(FakeBff()), TOKEN, CLIENT))
    everything = json.dumps([f.as_meta() for f in view.facts])
    assert "9999999" not in everything and "8888888" not in everything


def test_a_failed_endpoint_is_reported_not_zeroed(ontology):
    fake = FakeBff({f"/api/v1/clients/{CLIENT}/wallet/kpis": (403, envelope(None, False))})
    view = asyncio.run(agency_wallet(client_for(fake), TOKEN, CLIENT))
    assert view.problems == {"/api/v1/clients/{clientId}/wallet/kpis": "forbidden"}
    assert view.fact("month_consumption") is None and view.fact("main_wallet_balance") is not None
    answer = wallet_answer("wallet.explain_balance", "explain my wallet position", view, ontology)
    assert "access" in answer.template and "₹0" not in answer.template


def test_campaign_manager_sees_their_own_subwallet(ontology):
    view = asyncio.run(my_subwallet(client_for(FakeBff()), TOKEN, CLIENT, "u-cm"))
    answer = wallet_answer("wallet.explain_balance", "how much money do I have", view, ontology,
                           own_subwallet=True)
    assert "₹45,000" in answer.template and "₹2,00,000" in answer.template


# ── which figures each question gets ─────────────────────────────────────

def test_position_gets_the_whole_picture(ontology):
    view = asyncio.run(agency_wallet(client_for(FakeBff()), TOKEN, CLIENT))
    answer = wallet_answer("wallet.explain_balance", "explain my wallet position", view, ontology)
    shown = {i["metric_id"] for i in answer.card["items"]}
    assert shown == {"main_wallet_balance", "sub_wallet_total", "total_credits_owned",
                     "month_consumption", "wallet_runway_days", "low_balance"}
    for figure in ("₹15,75,000", "₹29,30,000", "₹45,05,000", "₹2,60,000", "3.2 months"):
        assert figure in answer.template


def test_a_chosen_balance_shows_only_that_balance(ontology):
    view = asyncio.run(agency_wallet(client_for(FakeBff()), TOKEN, CLIENT))
    answer = wallet_answer("wallet.explain_balance", "main wallet", view, ontology,
                           chosen="main_wallet_balance")
    assert [i["metric_id"] for i in answer.card["items"]] == ["main_wallet_balance"]


def test_an_overview_word_answers_which_balance(ontology):
    cap = ontology.capability("wallet.explain_balance")
    assert decide(cap, screen="command_centre", query="explain my wallet position") is None
    assert decide(cap, screen="command_centre", query="what's my wallet balance") is not None


def test_the_chip_routes_exactly(ontology):
    assert ontology.exact_utterance("Explain my wallet position").id == "wallet.explain_balance"


# ── the responder, end to end ────────────────────────────────────────────

def admin_session(screen="command_centre") -> Session:
    s = Session(id="s1")
    s.context = UIContext(screen=screen, role="AD_PARTNER_ADMIN", client_id=CLIENT)
    s.identity = Identity("u-1", "Admin", "AD_PARTNER_ADMIN", CLIENT,
                          (Grant(CLIENT, "Infinity", "AD_PARTNER_ADMIN", frozenset({"VIEW_WALLET"})),))
    return s


def run(responder, session, text, token=TOKEN):
    async def go():
        return [b async for b in responder.respond(session, text, token=token)]
    return asyncio.run(go())


def test_position_end_to_end_without_a_model(ontology):
    responder = Responder(ontology, None, bff=client_for(FakeBff()))
    blocks = run(responder, admin_session(), "explain my wallet position")
    kinds = [b.type for b in blocks]
    assert kinds[0] == "capability" and blocks[0].content == "wallet.explain_balance"
    assert "metrics" in kinds and "follow_up" not in kinds
    final = blocks[-1]
    assert final.meta["narrated_by"] == "template" and "₹15,75,000" in final.content


def test_which_balance_then_the_answer(ontology):
    responder = Responder(ontology, None, bff=client_for(FakeBff()))
    session = admin_session()
    first = run(responder, session, "what's my wallet balance")
    assert any(b.type == "follow_up" for b in first)
    second = run(responder, session, "Main wallet")
    card = next(b for b in second if b.type == "metrics")
    assert [i["metric_id"] for i in card.meta["items"]] == ["main_wallet_balance"]


def test_signed_out_says_so_and_still_explains(ontology):
    responder = Responder(ontology, None, bff=client_for(FakeBff()))
    session = admin_session()
    session.identity = None
    blocks = run(responder, session, "explain my wallet position", token=None)
    assert blocks[1].type == "unavailable"
    assert not any(b.type == "metrics" for b in blocks)


class InventingModel:
    """A model that rounds a figure — the classic plausible-wrong-number."""
    name = "fake"

    def __init__(self, text):
        self.text = text

    async def stream(self, messages, *, system=None, temperature=0.4, max_output_tokens=2048):
        yield self.text


def test_a_model_that_invents_a_number_is_replaced_by_the_template(ontology):
    responder = Responder(ontology, InventingModel("You have roughly ₹16 L in the main wallet."),
                          bff=client_for(FakeBff()), live_wording="model")
    final = run(responder, admin_session("wallet_management"), "explain my wallet position")[-1]
    assert final.meta["narrated_by"] == "template" and final.meta["rejected_numbers"] == ["16"]


def test_a_model_that_uses_the_card_is_kept(ontology):
    responder = Responder(ontology, InventingModel("The main wallet has ₹15.75 L to allocate."),
                          bff=client_for(FakeBff()), live_wording="model")
    final = run(responder, admin_session("wallet_management"), "explain my wallet position")[-1]
    assert final.meta["narrated_by"] == "model"


def test_by_default_the_template_words_live_figures_even_with_a_model(ontology):
    responder = Responder(ontology, InventingModel("The main wallet has ₹15.75 L to allocate."),
                          bff=client_for(FakeBff()))
    final = run(responder, admin_session("wallet_management"), "explain my wallet position")[-1]
    assert final.meta["narrated_by"] == "template" and "₹15,75,000" in final.content


# ── the HTTP endpoint: /me is the authority, not the browser ─────────────

def test_endpoint_takes_role_and_client_from_me_not_the_browser(ontology):
    from genie.auth.tokens import NoToken
    from genie.main import app
    fake = FakeBff()
    with TestClient(app) as c:
        app.state.ontology, app.state.provider = ontology, None
        app.state.tokens, app.state.bff = NoToken(), client_for(fake)
        app.state.sessions = MemorySessionStore()
        app.state.responder = Responder(ontology, None, bff=app.state.bff)
        r = c.post("/v1/genie/chat", headers={"Authorization": f"Bearer {TOKEN}"}, json={
            "message": "explain my wallet position",
            # A browser claiming another client: ignored in favour of the user's own grant.
            "uiContext": {"screen": "command_centre", "role": "CAMPAIGN_MANAGER",
                           "clientId": "someone-else"}})
    assert r.status_code == 200 and "₹15,75,000" in r.text
    paths = [call.url.path for call in fake.calls]
    assert paths[0] == "/api/v1/me" and all("someone-else" not in p for p in paths)


# ── dev token refresh ────────────────────────────────────────────────────

def test_an_expiring_token_is_renewed_from_the_refresh_token(tmp_path):
    access, refresh = tmp_path / "bff.jwt", tmp_path / "bff-refresh.token"
    access.write_text(jwt(sub="u-1", azp="adg-dashboard", exp=int(time.time()) + 5))
    refresh.write_text("refresh-1")
    renewed = jwt(sub="u-1", azp="adg-dashboard", exp=int(time.time()) + 300)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=envelope({"accessToken": renewed, "refreshToken": "refresh-2"}))

    source = RefreshingFileToken(access, refresh, QA, portal_id="AGENCY_PORTAL",
                                 transport=httpx.MockTransport(handler))
    assert asyncio.run(source.fresh()) == renewed
    assert refresh.read_text().strip() == "refresh-2"
    req = seen[0]
    assert req.url.host == "ag-dashboard-bff.qa.adgrid.ai" and req.url.path == "/v1/auth/refresh-token"
    # client_id is a HEADER on the auth endpoints (spec), exactly as the QA dashboard frontend sends it
    assert req.headers["client_id"] == "adg-dashboard" and "client_id" not in req.url.params
    assert req.headers["x-portal-id"] == "AGENCY_PORTAL"
    assert not list(tmp_path.glob("*.tmp*"))     # the temp file was renamed into place


# ── /me as QA actually returns it ────────────────────────────────────────

def test_admin_with_no_grants_uses_their_home_agency():
    """Measured on QA: an AD_PARTNER_ADMIN's /me has "grants": [] and only clientId."""
    me = Identity("u-1", "Kunal", "AD_PARTNER_ADMIN", CLIENT, ())
    grant = me.grant_for(None)
    assert grant.client_id == CLIENT and grant.role == "AD_PARTNER_ADMIN"
    assert me.grant_for(CLIENT).client_id == CLIENT
    assert me.grant_for("someone-else") is None        # a hinted foreign agency still gets nothing


def test_runway_is_left_out_when_the_backend_gives_none(ontology):
    """Live QA, empty wallet: runwayMonths absent. The card omits it — it never invents one."""
    empty = {**PAYLOADS[f"/api/v1/clients/{CLIENT}/wallet/kpis"]}
    empty["data"] = {**empty["data"], "runwayMonths": None, "lowBalance": True}
    fake = FakeBff({f"/api/v1/clients/{CLIENT}/wallet/kpis": (200, empty)})
    view = asyncio.run(agency_wallet(client_for(fake), TOKEN, CLIENT))
    assert view.fact("wallet_runway_days") is None
    answer = wallet_answer("wallet.explain_balance", "explain my wallet position", view, ontology)
    assert "runway" not in answer.template.lower()
    assert "below its low-balance threshold" in answer.template


# ── dev-only paths stay dev-only ─────────────────────────────────────────

@pytest.mark.parametrize("env", ["qa", "prod"])
def test_outside_local_no_token_is_ever_read_from_disk(env, tmp_path):
    from genie.auth.tokens import NoToken, build
    token_file = tmp_path / "bff.jwt"
    token_file.write_text(TOKEN)                       # a developer's token left on the box
    cfg = Settings(_env_file=None, genie_env=env, genie_bff_token_file=token_file)
    source = build(cfg)
    assert isinstance(source, NoToken) and source.token() is None


def test_local_still_reads_the_dev_token(tmp_path):
    from genie.auth.tokens import build
    token_file = tmp_path / "bff.jwt"
    token_file.write_text(TOKEN)
    cfg = Settings(_env_file=None, genie_env="local", genie_bff_token_file=token_file)
    assert build(cfg).token() == TOKEN


def test_unknown_environment_is_refused():
    with pytest.raises(ValueError):
        Settings(_env_file=None, genie_env="staging-ish")


def test_the_narrator_is_told_about_each_manager(ontology):
    """Live bug: the card listed managers, the model only saw the total and said it had no list."""
    view = asyncio.run(agency_wallet(client_for(FakeBff()), TOKEN, CLIENT))
    answer = wallet_answer("wallet.explain_manager_wallets", "who has budget left", view, ontology)
    told = " ".join(b["content"] for b in answer.facts.blocks if b["type"] == "live_value")
    for name, balance in (("Akshay", "₹1,20,000"), ("Ravi", "₹10,00,000"), ("Neha", "₹18,10,000")):
        assert name in told and balance in told
    assert "₹5,00,000" in " ".join(answer.allowed)      # allocated figures may be quoted too


# ── campaign manager, as the live API behaves (QA, 2026-10-23) ───────────
# CM: 200 on members/{me}/wallet/summary, 403 on wallet/balance, kpis, wallet-overview.

def cm_session() -> Session:
    s = Session(id="cm")
    s.context = UIContext(screen="command_centre", role="CAMPAIGN_MANAGER", client_id=CLIENT)
    s.identity = Identity("u-cm", "CM", "CAMPAIGN_MANAGER", CLIENT,
                          (Grant(CLIENT, "Infinity", "CAMPAIGN_MANAGER", frozenset({"VIEW_MEMBER_WALLET"})),))
    s.names_read_for = CLIENT         # past session start: only this test's reads
    return s


def test_campaign_manager_gets_their_own_subwallet_without_a_which_balance_question(ontology):
    fake = FakeBff()
    responder = Responder(ontology, None, bff=client_for(fake))
    blocks = run(responder, cm_session(), "what's my wallet balance")
    assert not any(b.type == "follow_up" for b in blocks)          # the agency pots aren't theirs
    card = next(b for b in blocks if b.type == "metrics")
    assert [i["metric_id"] for i in card.meta["items"]] == [
        "user_subwallet_balance", "manager_allocated", "manager_spent"]
    paths = [c.url.path for c in fake.calls]
    assert paths == [f"/api/v1/clients/{CLIENT}/members/u-cm/wallet/summary"]   # never the agency wallet


def test_campaign_manager_is_allowed_the_balance_capability_but_not_the_agency_ones(ontology):
    from genie.router.intent import IntentRouter
    router = IntentRouter(ontology, None)
    roles, perms = frozenset({"CAMPAIGN_MANAGER"}), frozenset({"VIEW_CAMPAIGN", "VIEW_MEMBER_WALLET"})
    balance = asyncio.run(router.route("how much money do I have", screen="command_centre",
                                       roles=roles, permissions=perms))
    runway = asyncio.run(router.route("how long will my budget last", screen="command_centre",
                                      roles=roles, permissions=perms))
    assert balance.capability.id == "wallet.explain_balance" and not balance.denied
    assert runway.capability.id == "wallet.explain_runway" and runway.denied


def test_overview_words_only_skip_the_wallet_balance_question(ontology):
    """'Summary' skips "which balance?" because the wallet card shows all three. Anywhere
    else it must change nothing - "campaign summary" still has to ask which campaigns."""
    from genie.router.followups import OVERVIEW_ANSWERS, _is_relevant
    checked = 0
    for cap in ontology.capabilities.values():
        if cap.id in OVERVIEW_ANSWERS:
            continue
        for f in cap.follow_ups:
            if f.kind != "disambiguation":
                continue
            for q in cap.utterances:
                assert _is_relevant(f, cap, q) == _is_relevant(f, cap, q + " summary overview"), (cap.id, q)
                checked += 1
    assert checked > 0


def test_runway_says_why_it_is_missing_when_nothing_was_spent(ontology):
    kpis = {**PAYLOADS[f"/api/v1/clients/{CLIENT}/wallet/kpis"]}
    kpis["data"] = {**kpis["data"], "runwayMonths": None, "consumedThisMonth": "0"}
    fake = FakeBff({f"/api/v1/clients/{CLIENT}/wallet/kpis": (200, kpis)})
    view = asyncio.run(agency_wallet(client_for(fake), TOKEN, CLIENT, with_managers=False))
    answer = wallet_answer("wallet.explain_runway", "how long will my budget last", view, ontology)
    assert "Runway isn't calculated: nothing has been spent this month" in answer.template



def test_one_manager_is_one_manager(ontology):
    """ "across 1 managers" - live QA, a partner with a single campaign manager."""
    from genie.data.wallet import LiveFact, Manager, WalletView
    from genie.router.live_answers import wallet_answer
    view = WalletView(facts=[LiveFact("sub_wallet_total", "Team sub-wallets", 0, "₹0", None, "test")],
                      managers=[Manager("Neha", None, None, None)], manager_count=1)
    text = wallet_answer("wallet.explain_balance", "wallet overview", view, ontology).template
    assert "Team sub-wallets hold ₹0 across 1 manager —" in text
