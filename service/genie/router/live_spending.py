"""Answers about where the money went and what is owed, built from live figures.

Same contract as the wallet (live_answers.py): the card and the sentence are code;
a model - only when GENIE_LIVE_WORDING=model - may rephrase, number-checked.

Which source answers depends on the question (catalog gotcha on wallet.explain_spending):
  "show me recent transactions"        the ledger
  "which brand is spending the most"   spend by brand
  "what did we spend in September"     the month's rollup
A period the user names is sent; otherwise the backend's default window is echoed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..data.fields import Window, day, humanize
from ..data.money import count_of, inr, inr_short, pct
from ..data.spending import Bills, Ledger, Months, Owed, SpendByBrand
from .answers import Facts
from .lexical import tokens
from .live_answers import LIVE_PROSE, MAX_LISTED, LiveAnswer, between, couldnt, text_answer

SPENDING_CAP = "wallet.explain_spending"
INVOICE_CAP = "wallet.explain_invoices"
FALLBACK_MONTHS = 3             # "what do I owe" from the bills when the summary is off

LEDGER_WORDS = frozenset(tokens("transaction transactions entries entry ledger statement recent "
                                "history debits credits debited credited"))
BRAND_WORDS = frozenset(tokens("brand brands which most where going breakdown break split"))
STATUS_LABEL = {"PAID": "paid", "UNPAID": "unpaid", "PARTIALLY_PAID": "partly paid",
                "PARTIAL": "partly paid", "OVERDUE": "overdue"}


def spending_kind(text: str) -> str:
    said = set(tokens(text))
    if said & LEDGER_WORDS:
        return "ledger"
    if said & BRAND_WORDS:
        return "brands"
    return "month"


LEDGER_FRAME = "wallet transactions"        # what the ledger answer already says about every line


def _restates(description: str, kind: str) -> bool:
    """Does the description add nothing to the entry type and the answer's own words?
    "Wallet top-up" on a Top-up does not; "Refund for a cancelled campaign" does."""
    return set(tokens(description)) <= set(tokens(kind)) | set(tokens(LEDGER_FRAME))


def _between(echoed: tuple[date, date] | None, asked: Window | None) -> str:
    """ "in September 2026" for a named month; otherwise the dates - the response's own,
    else the ones asked for - or "so far" when there are none."""
    if asked and asked.named:
        return f"in {asked.label}"
    return between(echoed or ((asked.start, asked.end) if asked else None))


def _problem(what: str, kind: str) -> LiveAnswer:
    text = couldnt(f"read {what}", kind)
    facts = Facts()
    facts.add("unavailable", text)
    return text_answer("Wallet", text, facts=facts)


def _answer(title: str, items: list[dict], sentences: list[str], extra_facts: list[str],
            caveat: str | None = None) -> LiveAnswer:
    facts = Facts()
    for i in items:
        facts.add("live_value", f"{i['label']}: {i['display']}", metric_id=i.get("metric_id"))
    for line in extra_facts:
        facts.add("live_value", line)
    if caveat:
        facts.add("caveat", caveat)
    facts.prose_hint = LIVE_PROSE
    allowed = [i["display"] for i in items] + [i.get("short") or "" for i in items] + extra_facts
    return LiveAnswer(facts=facts, card={"title": title, "items": items},
                      template=" ".join(sentences), allowed=allowed)


def _money_item(metric_id: str, label: str, amount: Decimal) -> dict:
    return {"metric_id": metric_id, "label": label, "display": inr(amount), "short": inr_short(amount)}


# ── where the money went ────────────────────────────────────────────────

def brands_answer(got: SpendByBrand, window: Window | None) -> LiveAnswer:
    if got.problem:
        return _problem("spending by brand", got.problem)
    when = _between(got.window, window)
    spent = [b for b in got.brands if b.amount]
    if not spent:
        return _answer("Spending by brand", [], [f"No brand spent anything {when}."], [])
    items = ([_money_item("spend_total", "Spent", got.total)] if got.total is not None else [])
    for b in spent[:10]:
        items.append({"metric_id": "spend_by_brand", "label": b.name, "display": inr(b.amount),
                      "short": inr_short(b.amount),
                      "share": pct(b.share) if b.share is not None else None})
    n = got.count if got.count is not None else len(spent)
    lead = (f"Campaigns spent {inr(got.total)} {when}" if got.total is not None
            else f"Spending {when}")
    lead += f" across {count_of(n, 'brand')}."
    top = ", ".join(f"{b.name} {inr(b.amount)}" + (f" ({pct(b.share)})" if b.share is not None else "")
                    for b in spent[:MAX_LISTED])
    more = len(spent) - MAX_LISTED
    sentences = [lead, f"Most first: {top}" + (f" and {more} more." if more > 0 else ".")]
    lines = [f"{b.name}: {inr(b.amount)}"
             + (f", {pct(b.share)} of the total" if b.share is not None else "")
             + (f", {b.campaigns} campaigns" if b.campaigns is not None else "") for b in spent[:10]]
    if got.window:
        lines.append(f"Window: {day(got.window[0])} to {day(got.window[1])}")
    return _answer("Spending by brand", items, sentences, lines)


def month_answer(got: Months, window: Window | None) -> LiveAnswer:
    if got.problem:
        return _problem("the monthly spending", got.problem)
    row = got.get(window.start.replace(day=1)) if window else (got.rows[0] if got.rows else None)
    label = window.label if window else (f"{row.month:%B %Y}" if row else "this month")
    if row is None or row.consumed is None:
        return _answer("Spending", [], [f"The dashboard has no spending recorded for {label}."], [])
    items = [_money_item("month_consumption", f"Spent, {row.month:%B %Y}", row.consumed)]
    when = "this month" if label == "this month" else f"in {label}"
    across = f" across {count_of(row.campaigns, 'campaign')}" if row.campaigns is not None else ""
    sentences = [f"You spent {inr(row.consumed)} {when}{across}."]
    if row.added:
        items.append(_money_item("month_added", f"Added, {row.month:%B %Y}", row.added))
        sentences.append(f"{inr(row.added)} was added to the wallet that month.")
    lines = [f"Month: {row.month:%B %Y}"]
    if row.campaigns is not None:
        lines.append(f"Campaigns: {row.campaigns}")
    return _answer("Spending", items, sentences, lines,
                   caveat="Campaign spend is debited with 18% GST, so it can differ from a "
                          "campaign's quoted cost.")


def ledger_answer(got: Ledger, window: Window | None) -> LiveAnswer:
    if got.problem:
        return _problem("the wallet's transactions", got.problem)
    when = f" {_between(None, window)}" if window else ""
    if not got.entries:
        return _answer("Transactions", [], [f"There are no wallet transactions{when}."], [])

    def line(e) -> str:
        sign = "-" if e.debit else "+" if e.debit is False else ""
        # The campaign says what the money was for; a description only when it adds
        # to the entry type ("Wallet top-up" on a Top-up says nothing new).
        what = e.campaign or (e.description if e.description and not _restates(e.description, e.kind)
                              else None)
        amount = inr(e.amount) if e.amount is not None else ""
        return (f"{day(e.on) + ': ' if e.on else ''}{e.kind} {sign}{amount}"
                + (f" ({what})" if what else "")).strip()

    shown = got.entries[:MAX_LISTED]
    sentences = [f"The latest {count_of(len(shown), 'wallet transaction')}{when}: "
                 + "; ".join(line(e) for e in shown) + "."]
    latest = got.entries[0]
    if latest.balance_after is not None:
        sentences.append(f"The balance after the latest was {inr(latest.balance_after)}.")
    items = [{"metric_id": "wallet_entry", "label": f"{day(e.on) if e.on else ''} {e.kind}".strip(),
              "display": inr(e.amount) if e.amount is not None else "-",
              "short": None, "debit": e.debit} for e in got.entries[:10]]
    lines = [line(e) for e in got.entries[:10]]
    if latest.balance_after is not None:
        lines.append(f"Balance after the latest: {inr(latest.balance_after)}")
    if got.total is not None:
        lines.append(f"Transactions in all: {got.total}")
    return _answer("Transactions", items, sentences, lines)


# ── what is owed ────────────────────────────────────────────────────────

def _status(code: str | None) -> str:
    return STATUS_LABEL.get((code or "").upper()) or humanize(code) or "status unknown"


def bill_answer(got: Bills, window: Window) -> LiveAnswer:
    """One month's invoice: total, GST, paid or not, when it is due, what it is made of."""
    if got.problem:
        return _problem(f"the invoice for {window.label}", got.problem)
    bill = next((b for b in got.months if b.month == window.start.replace(day=1)), None)
    if bill is None or bill.total is None:
        return _answer("Invoice", [], [f"There is no invoice for {window.label} yet."], [])
    label = f"{bill.month:%B %Y}"
    items = [_money_item("invoice_total", f"Invoice, {label}", bill.total)]
    s = f"The {label} invoice is {inr(bill.total)}"
    s += f", including {inr(bill.gst)} GST" if bill.gst else ""
    s += f", and it is {_status(bill.status)}"
    if bill.unpaid and bill.status != "PAID":
        s += f" with {inr(bill.unpaid)} still to pay"
        items.append(_money_item("invoice_unpaid", "Still to pay", bill.unpaid))
    s += f" (due {day(bill.due)})." if bill.due and bill.status != "PAID" else "."
    sentences = [s]
    parts = [(k, v) for k, v in bill.by_charge if v]          # a ₹0 charge is not part of the bill
    if parts:
        made_of = ", ".join(f"{k.lower()} {inr(v)}" for k, v in parts)
        sentences.append(f"It is made up of {made_of}.")
    lines = [f"{k}: {inr(v)}" for k, v in bill.by_charge]
    if bill.due:
        lines.append(f"Due: {day(bill.due)}")
    return _answer("Invoice", items, sentences, lines)


def owed_answer(got: Owed, fallback: Bills | None = None) -> LiveAnswer:
    """What is owed to AdGrid now. The summary first; the last months' bills if it is off."""
    if got.problem:
        if fallback is None or fallback.problem or fallback.unpaid is None:
            return _problem("your invoices", got.problem)
        unpaid = [b for b in fallback.months if b.unpaid]
        items = [_money_item("invoice_outstanding", "Outstanding", fallback.unpaid)]
        if not fallback.unpaid:
            s = f"Nothing is outstanding on your invoices for the last {FALLBACK_MONTHS} months."
        else:
            s = (f"{inr(fallback.unpaid)} is outstanding on your invoices for the last "
                 f"{FALLBACK_MONTHS} months: "
                 + ", ".join(f"{b.month:%B %Y} {inr(b.unpaid)}" for b in unpaid) + ".")
        return _answer("Invoices", items, [s],
                       [f"{b.month:%B %Y}: {inr(b.unpaid)} unpaid" for b in unpaid])

    items, sentences, lines = [], [], []
    if got.outstanding is not None:
        items.append(_money_item("invoice_outstanding", "Outstanding", got.outstanding))
        unpaid = [(m, a) for m, a in got.unpaid_months if a]
        if not got.outstanding:
            sentences.append("Nothing is outstanding on your invoices.")
        else:
            s = f"You owe AdGrid {inr(got.outstanding)}"
            if len(unpaid) == 1:                       # its amount is the total just given
                s += f", for {unpaid[0][0]:%B %Y}"
            elif unpaid:
                s += " for " + ", ".join(f"{m:%B %Y} ({inr(a)})" for m, a in unpaid)
            sentences.append(s + ".")
        lines += [f"{m:%B %Y}: {inr(a)} unpaid" for m, a in unpaid]
    if got.current and got.current[1] is not None:
        month, amount, status = got.current
        items.append(_money_item("invoice_current", "Current invoice", amount))
        sentences.append("The current invoice" + (f", for {month:%B %Y}," if month else "")
                         + f" is {inr(amount)}"
                         + (f" and is {_status(status)}" if status else "") + ".")
    if got.paid_term and got.paid_term[2]:
        lo, hi, amount = got.paid_term
        items.append(_money_item("invoice_paid_term", "Paid this term", amount))
        sentences.append(f"You've paid {inr(amount)} this term"
                         + (f" ({day(lo)} to {day(hi)})" if lo and hi else "") + ".")
    if not sentences:
        sentences = ["The dashboard returned no invoice figures."]
    return _answer("Invoices", items, sentences, lines,
                   caveat="Invoices are what AdGrid bills you: revenue share and screen rental, "
                          "with GST.")

