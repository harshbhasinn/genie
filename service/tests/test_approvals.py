"""Approvals, live — "what needs approving?", approve, send back, withdraw, resubmit.

The queue is called exactly as the QA dashboard's approvals page calls it
(view=AWAITING_ME, status=PENDING, rows I already decided dropped). Every answer
here is code or the catalog's authored OTP refusal; a model never rewords it.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from genie.chat.responder import Responder
from genie.ontology import load
from .test_campaigns import client_for, envelope, run, session

REPO = Path(__file__).resolve().parents[2]


def req(ref, op, subject, maker, status="PENDING", verdict=None):
    return {"requestId": ref.lower(), "referenceCode": ref, "operationCode": op, "status": status,
            "clientId": "c-infinity", "makerName": maker, "scopeLabel": subject,
            "createdOnEpochMs": 1792600000000, "actionable": True, "myVerdict": verdict,
            "quantityValues": {"amount": 999999}}


def page(*rows):
    return envelope({"requests": list(rows), "page": 0, "size": 100, "totalElements": len(rows),
                     "totalPages": 1})


class FakeApprovals:
    def __init__(self, queue=(), mine=(), decided=(), details=None, previews=None):
        self.calls: list[httpx.Request] = []
        self.queue, self.mine, self.decided = queue, mine, decided
        self.details, self.previews = details or {}, previews or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path, q = request.url.path, request.url.params
        op, st = q.get("operationCode"), q.get("status")
        keep = lambda rows: [r for r in rows if (not op or r["operationCode"] == op)  # noqa: E731
                             and (not st or r["status"] == st)]
        if path == "/api/v1/approvals/queue":
            if q.get("view") == "DECIDED_BY_ME":
                return httpx.Response(200, json=page(*keep(self.decided)))
            return httpx.Response(200, json=page(*keep(self.queue)))
        if path == "/api/v1/approvals/mine":
            return httpx.Response(200, json=page(*keep(self.mine)))
        ref = path.removeprefix("/api/v1/approvals/").removesuffix("/preview-today")
        if path.endswith("/preview-today") and ref in self.previews:
            return httpx.Response(200, json=envelope(self.previews[ref]))
        if ref in self.details:
            return httpx.Response(200, json=envelope(self.details[ref]))
        return httpx.Response(404, json=envelope(None, False))


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


QUEUE = (req("APR-1", "CAMPAIGN_CONFIRM", "Diwali Blast", "Akshay"),
         req("APR-2", "CAMPAIGN_CONFIRM", "Monsoon Sale", "Neha"),
         req("APR-3", "WALLET_TOPUP", None, "Ravi"),
         req("APR-4", "CAMPAIGN_CONFIRM", "Already Done", "Neha", verdict="APPROVED"))


def nav(blocks):
    return next((b for b in blocks if b.type == "navigate"), None)


def test_what_needs_approving_names_the_requests_as_the_screen_counts_them(ontology):
    fake = FakeApprovals(queue=QUEUE)
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(), "what needs approving")
    assert blocks[0].content == "navigate.approvals"
    q = fake.calls[0].url.params
    assert q["view"] == "AWAITING_ME" and q["status"] == "PENDING"
    assert blocks[-1].content == ("3 requests are waiting for your approval: Diwali Blast from "
                                  "Akshay, Monsoon Sale from Neha, Wallet top-up from Ravi. "
                                  "Opening the approvals queue.")
    assert nav(blocks).content == "/notifications"


def test_approve_lists_the_campaigns_then_the_authored_otp_refusal(ontology):
    fake = FakeApprovals(queue=QUEUE)
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(),
                 "approve the pending campaign")
    assert blocks[0].content == "campaign.approve"
    assert fake.calls[0].url.params["operationCode"] == "CAMPAIGN_CONFIRM"
    text = blocks[-1].content
    assert text.startswith("2 campaign requests are waiting for your approval: "
                           "Diwali Blast from Akshay, Monsoon Sale from Neha.")
    assert "OTP" in text and "Already Done" not in text           # decided by me: dropped
    assert "999999" not in text and "9,99,999" not in text        # quantityValues never read


def test_nothing_waiting_says_so_without_a_refusal(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeApprovals())), session(),
                 "approve the pending campaign")
    assert blocks[-1].content == "No campaign requests are waiting for your approval right now."
    assert nav(blocks) is None


def test_withdraw_reads_my_open_requests(ontology):
    fake = FakeApprovals(mine=(req("APR-9", "CAMPAIGN_CONFIRM", "Winter Launch", "Me"),))
    blocks = run(Responder(ontology, None, bff=client_for(fake)),
                 session(role="CAMPAIGN_MANAGER"), "withdraw the campaign I submitted")
    assert blocks[0].content == "campaign.withdraw_request"
    assert fake.calls[0].url.path == "/api/v1/approvals/mine"
    assert fake.calls[0].url.params["status"] == "PENDING"
    assert blocks[-1].content.startswith("1 of your campaign requests is awaiting approval: "
                                         "Winter Launch from Me.")


def test_resubmit_reads_my_sent_back_requests(ontology):
    fake = FakeApprovals(mine=(req("APR-7", "CAMPAIGN_CONFIRM", "Spring Push", "Me", status="SENT_BACK"),))
    blocks = run(Responder(ontology, None, bff=client_for(fake)),
                 session(role="CAMPAIGN_MANAGER"), "resubmit this campaign")
    assert fake.calls[0].url.params["status"] == "SENT_BACK"
    assert blocks[-1].content.startswith("1 of your campaign requests was sent back for changes: "
                                         "Spring Push from Me.")


def test_a_campaign_manager_cannot_open_the_approvals_queue(ontology):
    fake = FakeApprovals(queue=QUEUE)
    blocks = run(Responder(ontology, None, bff=client_for(fake)),
                 session(role="CAMPAIGN_MANAGER"), "what needs approving")
    assert blocks[0].meta["denied"] and not fake.calls             # refused before any call


# ── one request, history, what changed (shaped like REQ-1136 on live QA) ──

import time as _time                                       # noqa: E402
from datetime import date as _date, timedelta as _td      # noqa: E402

DAY_MS = 86_400_000
NOW_MS = int(_time.time() * 1000)


def detail(ref, subject, status, *, trail=(), stage=(1, 1), deadline_days=7, failure=None, op="CAMPAIGN_CONFIRM"):
    return {"referenceCode": ref, "operationCode": op, "status": status, "makerName": "Campaign Manager",
            "scopeLabel": subject, "scopeRef": "cmp-" + ref, "createdOnEpochMs": NOW_MS - DAY_MS,
            "currentStage": stage[0], "totalStages": stage[1],
            "finalDeadlineAtEpochMs": NOW_MS + deadline_days * DAY_MS, "lastFailureCode": failure,
            "stages": [{"stageNo": 1, "status": status}],
            "trail": [{"seq": i, "event": e, "actorName": who, "actorRole": "AD_PARTNER_ADMIN",
                       "reasonCode": rc, "note": note, "occurredAtEpochMs": NOW_MS}
                      for i, (e, who, rc, note) in enumerate(trail)],
            "quantityValues": {"amount": 999999}}


PENDING_1136 = req("REQ-1136", "CAMPAIGN_CONFIRM", "GENIE TEST - approval flow", "Campaign Manager")


def test_a_named_request_says_where_it_stands(ontology):
    fake = FakeApprovals(mine=(PENDING_1136,), details={"REQ-1136": detail(
        "REQ-1136", "GENIE TEST - approval flow", "PENDING", stage=(1, 2), deadline_days=2)})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(role="CAMPAIGN_MANAGER"),
                 "where is my request REQ-1136")
    assert blocks[0].content == "approvals.request_status"
    text = blocks[-1].content
    assert text.startswith("REQ-1136, GENIE TEST - approval flow raised by Campaign Manager on ")
    assert "is pending at stage 1 of 2." in text
    assert f"It expires on {(_date.today() + _td(days=2)).day}" in text or "It expires" in text
    assert "999999" not in text


def test_sent_back_gives_the_reason_and_the_note(ontology):
    sent = req("REQ-7", "CAMPAIGN_CONFIRM", "Spring Push", "Me", status="SENT_BACK")
    fake = FakeApprovals(mine=(sent,), details={"REQ-7": detail("REQ-7", "Spring Push", "SENT_BACK", trail=(
        ("RAISED", "Me", None, None), ("SENT_BACK", "Sahgal Yadav", "CREATIVE_ISSUE", "Logo is cropped")))})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(role="CAMPAIGN_MANAGER"),
                 "why was my campaign sent back")
    text = blocks[-1].content
    assert "was sent back by Sahgal Yadav" in text
    assert "Reason: creative issue." in text and 'Note: "Logo is cropped".' in text


def test_approved_but_failed_says_why_and_offers_the_retry(ontology):
    failed = req("REQ-9", "MEMBER_FUND_ALLOCATE", None, "Neha", status="EXECUTION_FAILED")
    fake = FakeApprovals(mine=(failed,), details={"REQ-9": detail(
        "REQ-9", None, "EXECUTION_FAILED", failure="INSUFFICIENT_BALANCE", op="MEMBER_FUND_ALLOCATE")})
    text = run(Responder(ontology, None, bff=client_for(fake)), session(), "why did my request fail")[-1].content
    assert "was approved but didn't go through: insufficient balance." in text
    assert "offers a retry" in text


def test_several_open_requests_ask_which_then_answer(ontology):
    a = req("REQ-1", "CAMPAIGN_CONFIRM", "Diwali Blast", "Me")
    b = req("REQ-2", "CAMPAIGN_CONFIRM", "Monsoon Sale", "Me")
    fake = FakeApprovals(mine=(a, b), details={"REQ-2": detail("REQ-2", "Monsoon Sale", "PENDING")})
    responder = Responder(ontology, None, bff=client_for(fake))
    s = session(role="CAMPAIGN_MANAGER")
    first = run(responder, s, "what's the status of my request")
    ask = next(x for x in first if x.type == "follow_up")
    assert ask.content == "Which request?" and [o["label"] for o in ask.meta["options"]] == ["Diwali Blast", "Monsoon Sale"]
    second = run(responder, s, "Monsoon Sale")
    assert second[-1].content.startswith("REQ-2, Monsoon Sale")


def test_a_campaign_named_in_the_question_finds_its_request(ontology):
    a = req("REQ-1", "CAMPAIGN_CONFIRM", "Diwali Blast", "Me")
    b = req("REQ-2", "CAMPAIGN_CONFIRM", "Monsoon Sale", "Me")
    fake = FakeApprovals(mine=(a, b), details={"REQ-1": detail("REQ-1", "Diwali Blast", "PENDING")})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), session(role="CAMPAIGN_MANAGER"),
                 "is my campaign approved diwali blast")
    assert not any(x.type == "follow_up" for x in blocks)
    assert blocks[-1].content.startswith("REQ-1, Diwali Blast")


def test_what_changed_compares_requested_with_today(ontology):
    fake = FakeApprovals(queue=(PENDING_1136,), previews={"REQ-1136": {
        # shaped like live QA's preview-today: money as strings under quantity keys
        "referenceCode": "REQ-1136", "requested": {"amount": "0.00", "campaignValue": "1200.00", "devices": 1},
        "today": {"amount": "0.00", "campaignValue": "1450.00", "devices": 2},
        "delta": {"amount": "0.00", "campaignValue": "250.00"}}})
    text = run(Responder(ontology, None, bff=client_for(fake)), session(),
               "has anything changed since it was requested")[-1].content
    assert text.startswith("Changed since REQ-1136 was raised")
    assert "campaign value was ₹1,200 and is now ₹1,450" in text
    assert "devices was 1 and is now 2" in text                  # not a money key: shown as returned


def test_nothing_changed_says_so(ontology):
    fake = FakeApprovals(queue=(PENDING_1136,), previews={"REQ-1136": {
        "referenceCode": "REQ-1136", "requested": {"campaignValue": "1200.00"},
        "today": {"campaignValue": "1200.00"}, "delta": {}}})
    text = run(Responder(ontology, None, bff=client_for(fake)), session(),
               "what changed in this request")[-1].content
    assert text.startswith("Nothing has changed since REQ-1136 was raised")


def test_history_for_an_approver_and_for_a_maker(ontology):
    decided = (req("REQ-3", "CAMPAIGN_CONFIRM", "Winter Launch", "Neha", status="EXECUTED"),
               req("REQ-4", "CAMPAIGN_CONFIRM", "Spring Push", "Akshay", status="SENT_BACK"),
               req("REQ-5", "WALLET_TOPUP", None, "Ravi", status="EXECUTED"))
    fake = FakeApprovals(decided=decided, mine=(PENDING_1136,))
    admin = run(Responder(ontology, None, bff=client_for(fake)), session(), "what did I approve")[-1].content
    assert admin.startswith("You've decided 3 approval requests: 2 approved and carried out, 1 sent back.")
    maker = run(Responder(ontology, None, bff=client_for(fake)), session(role="CAMPAIGN_MANAGER"),
                "what happened to my requests")[-1].content
    assert maker.startswith("You've raised 1 approval request: 1 pending.")


def test_approve_names_the_kind_of_request_asked_about(ontology):
    fake = FakeApprovals(queue=QUEUE)          # one top-up among campaign confirmations
    text = run(Responder(ontology, None, bff=client_for(fake)), session(),
               "approve the top-up request")[-1].content
    assert fake.calls[0].url.params["operationCode"] == "WALLET_TOPUP"
    assert text.startswith("1 top-up request is waiting for your approval")


def test_what_needs_approving_includes_approved_but_failed(ontology):
    failed = req("APR-8", "MEMBER_FUND_ALLOCATE", None, "Neha", status="EXECUTION_FAILED")
    fake = FakeApprovals(queue=QUEUE + (failed,))
    text = run(Responder(ontology, None, bff=client_for(fake)), session(), "what needs approving")[-1].content
    assert "1 approved request didn't go through: Member funding from Neha" in text


def test_a_draft_awaiting_approval_says_so(ontology):
    from .test_campaigns import FakeBff as CampaignBff, row
    draft = row("cmp-REQ-1136", "GENIE TEST - approval flow", "DRAFT", "2026-10-25", "2026-10-25")
    camp = CampaignBff(campaigns=(draft,))
    appr = FakeApprovals(mine=(PENDING_1136 | {"scopeRef": "cmp-REQ-1136"},))

    def both(request):
        return appr(request) if "/approvals/" in request.url.path else camp(request)
    responder = Responder(ontology, None, bff=client_for(both))
    s = session(role="CAMPAIGN_MANAGER")
    run(responder, s, "list all campaigns")                 # asks "Which campaigns?"
    listed = run(responder, s, "All of them")[-1].content
    assert "GENIE TEST - approval flow (awaiting approval, REQ-1136)" in listed
    doing = run(responder, session(role="CAMPAIGN_MANAGER"), "how is my campaign doing")[-1].content
    assert doing == ("GENIE TEST - approval flow is a draft awaiting approval (REQ-1136), "
                     "so nothing has played yet.")


# ── the real trail and closure reasons (ag-common-util on QA) ───────────

def test_auto_approved_and_closed_requests_say_why(ontology):
    auto = req("REQ-11", "CAMPAIGN_CONFIRM", "Auto Push", "Me", status="EXECUTED")
    closed = req("REQ-12", "CAMPAIGN_CONFIRM", "Gone Away", "Me", status="INVALIDATED")
    fake = FakeApprovals(mine=(auto, closed), details={
        "REQ-11": detail("REQ-11", "Auto Push", "EXECUTED", trail=(
            ("RAISED", "Me", None, None), ("AUTO_APPROVED", None, None, None), ("EXECUTED", None, None, None))),
        "REQ-12": detail("REQ-12", "Gone Away", "INVALIDATED") | {"terminalReason": "INSUFFICIENT_DEVICES"}})
    responder = Responder(ontology, None, bff=client_for(fake))
    a = run(responder, session(role="CAMPAIGN_MANAGER"), "what's the status of REQ-11")
    assert a[0].content == "approvals.request_status"
    assert a[-1].content.endswith("was approved automatically when no one decided in time, and carried out.")
    b = run(responder, session(role="CAMPAIGN_MANAGER"), "where is REQ-12")
    assert b[0].content == "approvals.request_status"                  # routed by the reference alone
    assert b[-1].content.endswith("was closed: the screens it needed are no longer available.")


def test_an_action_on_a_reference_is_still_the_action(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeApprovals(queue=QUEUE))), session(),
                 "approve REQ-1")
    assert blocks[0].content != "approvals.request_status"


# ── the rules ───────────────────────────────────────────────────────────

AUTOMATION = [{"operationCode": "CAMPAIGN_CONFIRM", "displayName": "Campaign confirmation",
               "effectiveSlaHours": 48, "effectiveOnTimeout": "EXPIRE", "autoApprovePermitted": False,
               "inheritedFromDefault": True},
              {"operationCode": "MEMBER_FUND_ALLOCATE", "displayName": "Member funding",
               "effectiveSlaHours": 12, "effectiveOnTimeout": "AUTO_APPROVE", "autoApprovePermitted": True,
               "inheritedFromDefault": False}]
CONFIGS = [{"operationCode": "CAMPAIGN_CONFIRM", "requiresApproval": True, "enabled": True,
            "stages": [{"stageNo": 1, "roles": ["AD_PARTNER_ADMIN"]}]}]


class FakeRules:
    def __init__(self, configs_status=200):
        self.calls, self.configs_status = [], configs_status

    def __call__(self, request):
        self.calls.append(request)
        if request.url.path == "/api/v1/clients/c-infinity/approval-automation":
            return httpx.Response(200, json=envelope(AUTOMATION))
        if request.url.path == "/api/v1/approval-configs":
            return httpx.Response(self.configs_status, json=envelope(CONFIGS if self.configs_status == 200 else None,
                                                                     self.configs_status == 200))
        return httpx.Response(404, json=envelope(None, False))


def test_the_rules_say_who_approves_how_long_and_what_happens_after(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeRules())), session(),
                 "who approves my campaigns")
    assert blocks[0].content == "approvals.explain_rules"
    assert blocks[-1].content == ("Campaign confirmation: approved by a Media Partner Admin; approvers have "
                                  "2 days, and if no one decides, it expires. These are the platform defaults; "
                                  "they haven't been changed for your account.")


def test_without_the_configs_the_rules_leave_approvers_out(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeRules(configs_status=403))), session(),
                 "what happens if nobody approves")
    assert blocks[-1].content == ("Campaign confirmation: approvers have 2 days, and if no one decides, it "
                                  "expires. Member funding: approvers have 12 hours, and if no one decides, it "
                                  "is approved automatically. I can't see who approves them with your access.")
