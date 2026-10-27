"""Where the money went and what is owed - live, as the agency admin.

No network: the dashboard API is a fake answering with payloads shaped like the
live QA spec (SpendingAnalysisResponse, WalletMonthlyRollupResponse,
WalletStatementResponse, ClientSettlementSummaryResponseDto, ClientSettlementsResponseDto).
The promises:

  · the question picks the source: transactions -> the ledger, brands -> the brand
    split, a month -> its rollup
  · a period the user names is sent; otherwise none is, and the backend's own window
    is echoed
  · "Over what period?" is answered without losing the question it was asked about
  · invoices are totals and charge types only - never per-contract charge lines
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from genie.chat.responder import Responder
from genie.ontology import load
from genie.data.fields import today as _today
from genie.router.live_spending import spending_kind
from genie.router.periods import period_from, without_period
from .test_campaigns import client_for, envelope, run, session

REPO = Path(__file__).resolve().parents[2]
BASE = "/api/v1/clients/c-infinity/wallet"


class FakeWallet:
    def __init__(self, payloads: dict, refused: dict | None = None):
        self.calls: list[httpx.Request] = []
        self.payloads, self.refused = payloads, refused or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path in self.refused:
            return httpx.Response(self.refused[path], json=envelope(None, False))
        if path in self.payloads:
            return httpx.Response(200, json=envelope(self.payloads[path]))
        return httpx.Response(404, json=envelope(None, False))

    def call(self, suffix: str) -> httpx.Request:
        return next(c for c in self.calls if c.url.path == BASE + suffix)


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


def wallet_session(screen="wallet_management", role="AD_PARTNER_ADMIN"):
    return session(screen=screen, role=role)


BRANDS = {"brands": [
    {"brandId": "b-2", "brandName": "Monsoon Malabar", "creditsConsumed": "45000.00",
     "percentageOfTotal": "30.00", "campaignsContributing": 2, "totalCampaigns": 3},
    {"brandId": "b-1", "brandName": "Infinity Foods", "creditsConsumed": "105000.00",
     "percentageOfTotal": "70.00", "campaignsContributing": 4, "totalCampaigns": 4}],
    "totalCreditsConsumed": "150000.00", "totalCampaignsContributing": 6, "activeBrands": 2,
    "currency": "INR", "from": "2026-10-01", "to": "2026-10-24", "totalElements": 2}


# ── reading the question ────────────────────────────────────────────────

def test_the_period_is_read_from_the_words():
    today = date(2026, 10, 24)
    assert period_from("what did we spend this month", today=today).start == date(2026, 10, 1)
    last = period_from("show last month", today=today)
    assert (last.start, last.end, last.label) == (date(2026, 9, 1), date(2026, 9, 30), "September 2026")
    assert period_from("spend in September", today=today).label == "September 2026"
    assert period_from("spend in december", today=today).label == "December 2025"   # not yet this year
    assert period_from("for May", today=today).label == "May 2026"
    assert period_from("may I see my spending", today=today) is None              # "may" the verb
    assert period_from("anything", "last_30_days", today=today).start == date(2026, 9, 25)


def test_the_question_picks_the_source():
    assert spending_kind("show me recent transactions") == "ledger"
    assert spending_kind("which brand is spending the most") == "brands"
    assert spending_kind("what did we spend in September") == "month"


# ── where the money went ────────────────────────────────────────────────

def test_brand_split_on_the_wallet_screen_echoes_the_backends_window(ontology):
    fake = FakeWallet({BASE + "/spending-analysis": BRANDS})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), wallet_session(),
                 "which brand is spending the most")
    assert blocks[0].content == "wallet.explain_spending"
    q = fake.call("/spending-analysis").url.params
    assert "from" not in q and "to" not in q                     # nothing named, nothing sent
    assert blocks[-1].content == ("Campaigns spent ₹1,50,000 between 1 Oct 2026 and 24 Oct 2026 across "
                                  "2 brands. Most first: Infinity Foods ₹1,05,000 (70%), "
                                  "Monsoon Malabar ₹45,000 (30%).")


def test_over_what_period_keeps_the_question_it_was_asked_about(ontology):
    fake = FakeWallet({BASE + "/spending-analysis": BRANDS})
    responder, s = Responder(ontology, None, bff=client_for(fake)), wallet_session("command_centre")
    first = run(responder, s, "which brand is spending the most")
    assert any(b.type == "follow_up" and b.content == "Over what period?" for b in first)
    blocks = run(responder, s, "Last month")
    prev = _today().replace(day=1) - timedelta(days=1)
    q = fake.call("/spending-analysis").url.params
    assert q["from"] == prev.replace(day=1).isoformat() and q["to"] == prev.isoformat()
    assert blocks[-1].content.startswith(f"Campaigns spent ₹1,50,000 in {prev:%B %Y} across 2 brands.")


def test_recent_transactions_read_the_ledger_without_asking_a_period(ontology):
    fake = FakeWallet({BASE + "/statement": {"lines": [
        {"entryId": "e2", "entryType": "DEBIT", "side": "DEBIT", "amount": "11800.00",
         "balanceAfter": "488200.00", "postedAt": "2026-10-22T10:00:00Z", "description": "Campaign debit",
         "campaign": {"campaignId": "c1", "name": "Diwali Blast"}},
        {"entryId": "e1", "entryType": "TOPUP", "side": "CREDIT", "amount": "500000.00",
         "balanceAfter": "500000.00", "postedAt": "2026-10-20T10:00:00Z", "description": "Top-up"}],
        "totalElements": 2}})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), wallet_session("command_centre"),
                 "show me recent transactions")
    assert not any(b.type == "follow_up" for b in blocks)
    assert blocks[-1].content == ("The latest 2 wallet transactions: 22 Oct 2026: Campaign spend -₹11,800 "
                                  "(Diwali Blast); 20 Oct 2026: Top-up +₹5,00,000. "
                                  "The balance after the latest was ₹4,88,200.")


def test_a_named_month_reads_that_months_rollup(ontology):
    month = period_from("in September").start
    fake = FakeWallet({BASE + "/consumption/monthly": {"rows": [
        {"month": f"{month:%Y-%m}", "added": "200000.00", "consumed": "87500.00", "net": "112500.00",
         "campaignCount": "3", "settlementStatus": "PAID"}]}})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), wallet_session(),
                 "what did we spend in September")
    q = fake.call("/consumption/monthly").url.params
    assert q["fromMonth"] == q["toMonth"] == f"{month:%Y-%m}"
    assert blocks[-1].content == (f"You spent ₹87,500 in {month:%B %Y} across 3 campaigns. "
                                  f"₹2,00,000 was added to the wallet that month.")


def test_a_refused_read_is_said_not_zeroed(ontology):
    fake = FakeWallet({}, refused={BASE + "/spending-analysis": 403})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), wallet_session(),
                 "which brand is spending the most")
    assert blocks[-1].content == ("I couldn't read spending by brand: your account does not "
                                  "have access to that.")


# ── what is owed ────────────────────────────────────────────────────────

SUMMARY = {"currency": "INR", "totalOutstanding": "59000.00",
           "outstandingMonths": [{"settlementMonth": "2026-09", "unpaidAmount": "35400.00"},
                                 {"settlementMonth": "2026-08", "unpaidAmount": "23600.00"}],
           "currentInvoice": {"settlementMonth": "2026-10", "amount": "41300.00", "status": "UNPAID"},
           "paidThisTerm": {"from": "2026-04-01", "to": "2026-10-24", "amount": "120000.00"}}


def test_what_do_i_owe_reads_the_summary(ontology):
    fake = FakeWallet({BASE + "/settlements/summary": SUMMARY})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), wallet_session("command_centre"),
                 "what do I owe")
    assert blocks[0].content == "wallet.explain_invoices"
    assert blocks[-1].content == ("You owe AdGrid ₹59,000 for August 2026 (₹23,600), September 2026 (₹35,400). "
                                  "The current invoice, for October 2026, is ₹41,300 and is unpaid. "
                                  "You've paid ₹1,20,000 this term (1 Apr 2026 to 24 Oct 2026).")


def bill(month, status="UNPAID"):
    return {"settlementMonth": f"{month}-01", "paymentStatus": status,
            "totals": {"base": "30000.00", "gst": "5400.00", "total": "35400.00",
                       "paid": "0.00", "unpaid": "35400.00"},
            # an object keyed by charge type, as live QA returns it (the spec says a list)
            "byChargeType": {"revenueSharing": "23600.00", "minGuarantee": "0.00", "rental": "11800.00"},
            "contracts": [{"contractId": "k1", "paymentStatus": status, "dueDate": "2026-10-15",
                           "charges": [{"amount": "777777.00"}]}]}


def test_one_months_invoice_is_totals_and_charge_types_only(ontology):
    prev = _today().replace(day=1) - timedelta(days=1)
    fake = FakeWallet({BASE + "/settlements": {"grandTotals": {"unpaid": "35400.00"},
                                               "months": [bill(f"{prev:%Y-%m}")]}})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), wallet_session(),
                 "was last month's invoice paid")
    assert fake.call("/settlements").url.params.get_list("months") == [f"{prev:%Y-%m}-01"]
    text = blocks[-1].content
    assert text == (f"The {prev:%B %Y} invoice is ₹35,400, including ₹5,400 GST, and it is unpaid with "
                    f"₹35,400 still to pay (due 15 Oct 2026). It is made up of revenue share ₹23,600, "
                    f"screen rental ₹11,800.")
    assert "7,77,777" not in str([b.meta for b in blocks])        # contract charge lines never read


def test_with_the_summary_switched_off_the_last_months_answer(ontology):
    months = [(_today().replace(day=1) - timedelta(days=d)).replace(day=1) for d in (0, 1, 40)]
    fake = FakeWallet({BASE + "/settlements": {"grandTotals": {"unpaid": "35400.00"},
                                               "months": [bill(f"{months[1]:%Y-%m}")]}},
                      refused={BASE + "/settlements/summary": 404})
    blocks = run(Responder(ontology, None, bff=client_for(fake)), wallet_session(), "what do I owe")
    assert len(fake.call("/settlements").url.params.get_list("months")) == 3
    assert blocks[-1].content == (f"₹35,400 is outstanding on your invoices for the last 3 months: "
                                  f"{months[1]:%B %Y} ₹35,400.")


def test_a_campaign_manager_is_not_shown_the_agency_invoice(ontology):
    blocks = run(Responder(ontology, None, bff=client_for(FakeWallet({}))),
                 wallet_session(role="CAMPAIGN_MANAGER"), "do I have any unpaid invoices")
    assert blocks[0].meta["denied"] is True


def test_the_period_plays_no_part_in_routing():
    assert without_period("how is my brand doing all time") == "how is my brand doing"
    assert without_period("what did we spend last month") == "what did we spend"
    assert without_period("spend in september 2026 by brand") == "spend by brand"
    assert without_period("brand performance over the last 7 days") == "brand performance"
    assert without_period("may i see my spending") == "may i see my spending"      # "may" the verb
