"""The general rules behind the live answers, checked across every case they cover.

Each test states a rule and runs it over the whole catalog or every combination of
inputs - not over the one conversation where it was first seen to break:

  · without live access, every handoff still refuses and every navigation still
    navigates, exactly as with no live path at all
  · a request reference names which request; the words say what is asked about it
  · a name the user has been shown is routed as a name, not as its words
  · only notes written for users ever reach a model
  · the approval-rules sentence reads correctly for any mix of present and missing parts
  · a model may repeat any number it was given, and only those
"""
from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest

from genie.chat.responder import LIVE_CAPS, Responder
from genie.chat.session import Session, UIContext
from genie.data.approvals import Rule, Rules
from genie.ontology import load
from genie.ontology.models import Mode
from genie.router import answers as build
from genie.router.intent import IntentRouter
from genie.router.live_approvals import rules_answer
from .test_approvals import FakeApprovals, req
from .test_campaigns import client_for, run, session
from .test_wallet import InventingModel

REPO = Path(__file__).resolve().parents[2]
NOT_SIGNED_IN = "not signed in"


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


def _blocks(responder, cap, role):
    s = Session(id="x")
    s.context = UIContext(screen=cap.target_screen or "command_centre", role=role, client_id="c1")
    async def go():
        return [b async for b in responder.respond(s, cap.utterances[0])]
    return asyncio.run(go())


def test_without_live_access_actions_and_links_behave_as_without_the_live_path(ontology):
    responder = Responder(ontology, None)                          # no BFF, no token
    checked = 0
    for cap in ontology.capabilities.values():
        if cap.id not in LIVE_CAPS or cap.mode not in (Mode.HANDOFF, Mode.NAVIGATE):
            continue
        role = next(iter(cap.authorization.roles), "AD_PARTNER_ADMIN") if cap.authorization.roles \
            else "AD_PARTNER_ADMIN"
        blocks = _blocks(responder, cap, role)
        if blocks[0].content != cap.id:
            continue                                               # routing is not this test's subject
        text = " ".join(b.content for b in blocks if b.type in ("text", "unavailable"))
        assert NOT_SIGNED_IN not in text, cap.id
        if cap.mode is Mode.HANDOFF and cap.refusal and not blocks[0].meta.get("denied"):
            assert " ".join(cap.refusal.split()) in text, cap.id
        checked += 1
    assert checked >= 10


# ── references and names ────────────────────────────────────────────────

@pytest.mark.parametrize("ref", ["REQ-1136", "req-77", "REQ9"])
def test_a_reference_says_which_request_and_the_words_say_what(ontology, ref):
    router = IntentRouter(ontology, None)
    route = lambda t: asyncio.run(router.route(t, screen="command_centre",            # noqa: E731
                                               roles=frozenset({"AD_PARTNER_ADMIN"}))).capability
    for template, want in (("{r}", "approvals.request_status"),
                           ("where is {r}", "approvals.request_status"),
                           ("what changed in {r}", "approvals.what_changed"),
                           ("is {r} still up to date", "approvals.what_changed")):
        got = route(template.format(r=ref))
        assert got is not None and got.id == want, (template, ref)


def test_a_name_the_user_was_shown_is_routed_as_a_name(ontology):
    """A campaign called "... approval flow" is a campaign, once the session has seen it."""
    draft = req("REQ-1136", "CAMPAIGN_CONFIRM", "GENIE TEST - approval flow", "Campaign Manager")
    responder = Responder(ontology, None, bff=client_for(FakeApprovals(mine=(draft,))))
    s = session(role="CAMPAIGN_MANAGER")
    run(responder, s, "what happened to my requests")             # the answer names the campaign
    blocks = run(responder, s, "how is GENIE TEST - approval flow doing")
    assert blocks[0].content == "campaign.explain_performance"


# ── what a model is given ───────────────────────────────────────────────

def test_only_notes_written_for_users_reach_a_model(ontology):
    for metric in ontology.metrics.values():
        caveats = [b["content"] for b in build.metric_facts(metric).blocks if b["type"] == "caveat"]
        users = {" ".join(n.split()) for n in metric.user_notes}
        assert set(caveats) <= users, metric.id
        for g in metric.gotchas:
            assert " ".join(g.split()) not in caveats, metric.id


def test_a_model_may_repeat_a_number_from_any_fact_it_was_given(ontology):
    """A caveat's number ("18% GST") is a fact the model was given; a new one is invented."""
    from .test_spending import BASE, FakeWallet
    from genie.router.live_spending import period_from
    month = period_from("in September").start
    rollup = {BASE + "/consumption/monthly": {"rows": [
        {"month": f"{month:%Y-%m}", "consumed": "87500.00", "campaignCount": "3"}]}}
    def final(model_text):
        r = Responder(ontology, InventingModel(model_text), bff=client_for(FakeWallet(rollup)),
                      live_wording="model")
        return run(r, session("wallet_management"), "what did we spend in September")[-1]
    kept = final(f"You spent ₹87,500 in {month:%B %Y} on 3 campaigns; debits carry 18% GST.")
    assert kept.meta["narrated_by"] == "model"
    dropped = final(f"You spent ₹87,500 in {month:%B %Y} on 4 campaigns.")
    assert dropped.meta["narrated_by"] == "template" and dropped.meta["rejected_numbers"] == ["4"]


# ── the rules sentence ──────────────────────────────────────────────────

@pytest.mark.parametrize("approvers,hours,timeout,readable", list(itertools.product(
    ([], [["AD_PARTNER_ADMIN"]], [["AD_PARTNER_ADMIN"], ["ADOPS_ADMIN"]]),
    (None, 12, 48),
    (None, "MANUAL", "EXPIRE", "AUTO_APPROVE", "AUTO_REJECT"),
    (True, False))))
def test_the_rules_read_correctly_for_any_mix_of_parts(approvers, hours, timeout, readable):
    rules = Rules(rules=[Rule("CAMPAIGN_CONFIRM", "Confirm campaign", hours, timeout, False,
                              approvers if readable else []),
                         Rule("WALLET_TOPUP", "Wallet top-up", hours, timeout, False,
                              approvers if readable else [])],
                  approvers_known=readable)
    text = rules_answer(rules, None).template
    # identical rules share one sentence, which names both types and then says the rule
    assert text.startswith("Confirm campaign and Wallet top-up: ")
    assert "None" not in text and "  " not in text and ".." not in text and ";." not in text
    assert text.endswith(".")
    if not readable:
        assert "I can't see who approves them with your access." in text
    if hours is None and timeout in (None, "MANUAL"):
        assert "no deadline" in text


def test_only_user_written_reasons_say_why_a_figure_is_missing(ontology):
    from genie.ontology.models import Status
    for metric in ontology.metrics.values():
        if metric.status is not Status.BLOCKED:
            continue
        said = [b["content"] for b in build.metric_facts(metric).blocks if b["type"] == "unavailable"]
        want = " ".join((metric.unavailable_note or "").split()) or "This figure isn't available in the dashboard yet."
        assert said == [want], metric.id


def test_links_written_in_code_match_the_ontology(ontology):
    """Two links are built without the ontology at hand; they must still be its routes."""
    from genie.data.campaigns import CAMPAIGN_ROUTE
    from genie.router.live_approvals import APPROVALS_SCREEN
    assert CAMPAIGN_ROUTE == ontology.screen("campaign_detail").route
    screen_id, route, title = APPROVALS_SCREEN
    assert (route, title) == (ontology.screen(screen_id).route, ontology.screen(screen_id).title)


# ── roles ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reseller,partner", [("RESELLER_ADMIN", "AD_PARTNER_ADMIN"),
                                              ("RESELLER_CAMPAIGN_MANAGER", "CAMPAIGN_MANAGER")])
def test_an_advertising_partner_seat_has_the_media_partner_seats_access(ontology, reseller, partner):
    for cap in ontology.capabilities.values():
        assert cap.role_allowed(frozenset({reseller})) == cap.role_allowed(frozenset({partner})), cap.id
    for screen in ontology.screens.values():
        assert screen.visible_to(reseller) == screen.visible_to(partner), screen.id


def test_roles_are_named_never_shown_as_codes(ontology):
    from genie.chat.responder import denied_text
    cap = ontology.capability("wallet.explain_invoices")
    assert denied_text(cap, "CAMPAIGN_MANAGER") == ("Invoices and what is owed isn't available to a "
                                                     "Campaign Manager. Your Media Partner Admin can help.")
    assert denied_text(cap, "RESELLER_CAMPAIGN_MANAGER").endswith("Your Advertising Partner Admin can help.")
    denied = build.denied_facts(cap, "RESELLER_CAMPAIGN_MANAGER").blocks[0]["content"]
    assert "RESELLER" not in denied and "a Campaign Manager" in denied


def test_a_resellers_campaign_manager_reads_their_own_subwallet(ontology):
    from genie.auth.identity import Grant, Identity
    from .test_wallet import CLIENT, FakeBff as WalletBff
    s = Session(id="rcm")
    s.context = UIContext(screen="command_centre", role="RESELLER_CAMPAIGN_MANAGER", client_id=CLIENT)
    s.identity = Identity("u-cm", "CM", "RESELLER_CAMPAIGN_MANAGER", CLIENT,
                          (Grant(CLIENT, "Infinity", "RESELLER_CAMPAIGN_MANAGER", frozenset()),))
    fake = WalletBff()
    run(Responder(ontology, None, bff=client_for(fake)), s, "what's my wallet balance")
    paths = [c.url.path for c in fake.calls]
    assert any(p.endswith("/wallet/summary") for p in paths)
    assert not any(p.endswith("/wallet/balance") for p in paths)
