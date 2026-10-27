"""Answers about approval requests, built from the live queue.

Every capability here either reads requests or hands off an OTP-gated decision,
so the words are code and authored text only - a model never rewords them (an
OTP refusal must read the same every time). What the live data adds is WHICH
requests: Genie names them, says where each one stands, and why.
"""
from __future__ import annotations

import re
from datetime import date

from ..data.approvals import (MONEY_QUANTITIES, REFERENCE, OPEN, Drift, Request, RequestDetail,
                              RequestList, Rules)
from ..data.fields import day, humanize, today
from ..data.money import count, count_of, inr, to_decimal
from ..roles import a_label
from .answers import Facts
from .lexical import normalise, tokens
from .live_answers import (MAX_LISTED, LiveAnswer, and_list, couldnt, entity_choice, names,
                           text_answer)

# capability -> (which list, operation filter, status for "mine")
APPROVAL_CAPS: dict[str, tuple[str, str | None, str | None]] = {
    "navigate.approvals": ("queue", None, None),
    "campaign.approve": ("queue", "CAMPAIGN_CONFIRM", None),
    "campaign.send_back": ("queue", "CAMPAIGN_CONFIRM", None),
    "approvals.approve_request": ("queue", None, None),     # the operation comes from the words
    "campaign.withdraw_request": ("mine", "CAMPAIGN_CONFIRM", "PENDING"),
    "campaign.resubmit_request": ("mine", "CAMPAIGN_CONFIRM", "SENT_BACK"),
}
# Questions about ONE request, or about past decisions.
REQUEST_CAPS = ("approvals.request_status", "approvals.what_changed")
HISTORY_CAP = "approvals.history"
RULES_CAP = "approvals.explain_rules"
# The approvals inbox - the ontology's notifications_approvals screen (a test holds them equal).
APPROVALS_SCREEN = ("notifications_approvals", "/notifications", "Campaign Approvals")
SOON_DAYS = 3                                      # a deadline this close is worth saying

_OPERATION_WORDS = (("WALLET_TOPUP", {"topup", "top", "recharge"}),
                    ("MEMBER_FUND_ALLOCATE", {"fund", "funds", "funding", "allocate", "allocation"}),
                    ("MARK_INVOICE_PAID", {"invoice", "invoices"}),
                    ("CREATIVE_REVIEW", {"creative", "creatives", "content"}),
                    ("CAMPAIGN_CONFIRM", {"campaign", "campaigns"}))


def operation_named(text: str) -> str | None:
    """The request type the words name, if any - "approve the top-up request"."""
    said = set(tokens(text)) | set(normalise(text).replace("-", " ").split())
    return next((op for op, words in _OPERATION_WORDS if said & words), None)


def _when(d: date) -> str:
    gap = (d - today()).days
    return "today" if gap == 0 else "tomorrow" if gap == 1 else f"on {day(d)}"


def _describe(r: Request) -> str:
    """"Diwali Blast from Akshay"; the type only when it says something new; a close deadline."""
    by = f" from {r.maker}" if r.maker else ""
    if not r.subject:
        text = f"{r.operation_label}{by}"
    else:
        kind = "" if r.operation == "CAMPAIGN_CONFIRM" else f" ({r.operation_label.lower()})"
        text = f"{r.subject}{kind}{by}"
    if r.deadline and 0 <= (r.deadline - today()).days <= SOON_DAYS:
        text += f", expires {_when(r.deadline).removeprefix('on ')}"
    return text


def _named(requests) -> str:
    shown = ", ".join(_describe(r) for r in requests[:MAX_LISTED])
    more = len(requests) - MAX_LISTED
    return shown + (f" and {more} more" if more > 0 else "")


def _link() -> list[tuple[str, str, dict]]:
    screen_id, route, title = APPROVALS_SCREEN
    return [("navigate", route, {"screen_id": screen_id, "title": title, "label": "Approvals"})]


def _problem(what: str, kind: str) -> LiveAnswer:
    return text_answer("Approvals", couldnt(f"read {what}", kind), authored=True)


# ── the queue and my requests ───────────────────────────────────────────

def approvals_answer(cap_id: str, refusal: str, got: RequestList, *, operation: str | None = None,
                     failed: RequestList | None = None) -> LiveAnswer:
    which, cap_operation, _ = APPROVAL_CAPS[cap_id]
    operation = operation or cap_operation
    if got.problem:
        answer = _problem("your approval requests", got.problem)
        answer.extra = _link()
        return answer

    rows, n = got.requests, len(got.requests)
    things = ("campaign request" if operation == "CAMPAIGN_CONFIRM" else
              f"{OPERATION_NOUN.get(operation, 'request')}")
    if which == "queue":
        if n == 0:
            sentences = [f"No {things}s are waiting for your approval right now." if operation
                         else "Nothing is waiting for your approval right now."]
        else:
            sentences = [f"{count_of(n, things)} {'is' if n == 1 else 'are'} waiting for your "
                         f"approval: {_named(rows)}."]
    elif cap_id == "campaign.withdraw_request":
        sentences = (["You have no campaign request awaiting approval."] if n == 0 else
                     [f"{count(n)} of your {things}s {'is' if n == 1 else 'are'} awaiting "
                      f"approval: {_named(rows)}."])
    else:   # resubmit
        sentences = (["None of your campaign requests has been sent back."] if n == 0 else
                     [f"{count(n)} of your {things}s {'was' if n == 1 else 'were'} sent back "
                      f"for changes: {_named(rows)}."])
    if got.partial:
        sentences.append(f"That covers the newest {len(rows)}.")
    if failed and failed.requests:
        k = len(failed.requests)
        sentences.append(f"{count(k)} approved {'request' if k == 1 else 'requests'} didn't go "
                         f"through: {_named(failed.requests)} - the approvals screen offers a retry.")
    # The OTP refusal only matters when there is something to act on.
    if n and cap_id != "navigate.approvals" and refusal:
        sentences.append(" ".join(refusal.split()))
    elif (n or (failed and failed.requests)) and cap_id == "navigate.approvals":
        sentences.append("Opening the approvals queue.")

    card = {"title": "Approvals",
            "items": [{"metric_id": "approvals_count", "label":
                       "Waiting for you" if which == "queue" else "Your requests",
                       "display": count(n)}],
            "requests": [r.as_meta() for r in rows[:10]]}
    if failed and failed.requests:
        card["items"].append({"metric_id": "approvals_failed", "label": "Approved, not carried out",
                              "display": count(len(failed.requests))})
    return LiveAnswer(facts=Facts(), card=card, template=" ".join(sentences),
                      extra=_link() if n or cap_id == "navigate.approvals" else [], authored=True)


OPERATION_NOUN = {"CREATIVE_REVIEW": "content review", "WALLET_TOPUP": "top-up request",
                  "MEMBER_FUND_ALLOCATE": "funding request", "MARK_INVOICE_PAID": "invoice request"}


# ── which request ───────────────────────────────────────────────────────

def pick_request(text: str, candidates: list[Request], *, chosen: str | None = None
                 ) -> tuple[Request | None, list[Request]]:
    """Chosen last turn, then a reference in the words (REQ-1136), then what it is about
    (a campaign's name), then the only open one - otherwise the options to ask with."""
    by_ref = {r.reference.upper(): r for r in candidates}
    if chosen and chosen.upper() in by_ref:
        return by_ref[chosen.upper()], []
    if m := REFERENCE.search(text):
        ref = "REQ-" + re.sub(r"\D", "", m.group(0))          # "req1136" or "REQ-1136"
        if ref in by_ref:
            return by_ref[ref], []
    named = [r for r in candidates if names(text, r.subject)]
    if named:
        return named[0], []
    open_ = [r for r in candidates if r.status in OPEN]
    pool = open_ or candidates
    if len(pool) == 1:
        return pool[0], []
    return None, pool[:MAX_LISTED]


def which_request_block(options: list[Request]) -> dict:
    return entity_choice("which_request", "Which request?",
                         [{"value": r.reference, "label": r.subject or r.operation_label,
                           "hint": f"{r.reference}, {r.status_label}"} for r in options])


def no_requests(as_maker: bool) -> LiveAnswer:
    text = ("You haven't raised any approval requests." if as_maker
            else "You don't have any approval requests.")
    return text_answer("Approvals", text, authored=True)


# ── one request ─────────────────────────────────────────────────────────

def _by(e) -> str:
    return (f" by {e.actor}" if e and e.actor else "") + (f" on {day(e.on)}" if e and e.on else "")


def status_answer(detail: RequestDetail | str, reference: str) -> LiveAnswer:
    if isinstance(detail, str):
        return _problem(f"request {reference}", detail)
    r = detail.request
    head = f"{r.reference}, {r.subject or r.operation_label}"
    if r.subject and r.operation != "CAMPAIGN_CONFIRM":
        head += f" ({r.operation_label.lower()})"
    if r.maker:
        head += f" raised by {r.maker}"
    if r.raised:
        head += f" on {day(r.raised)}"

    s = r.status
    if s == "PENDING":
        text = f"{head}, is pending"
        if r.stage and r.stage[1] > 1:
            text += f" at stage {r.stage[0]} of {r.stage[1]}"
        text += "." + (f" It expires {_when(r.deadline)}." if r.deadline else "")
    elif s == "SENT_BACK":
        e = detail.last("SENT_BACK")
        text = f"{head}, was sent back{_by(e)}."
        if e and e.reason:
            text += f" Reason: {e.reason}."
        if e and e.note:
            text += f' Note: "{e.note}".'
        text += " It can be changed and resubmitted."
    elif (s == "REJECTED" and (e := detail.last("REJECTED", "AUTO_REJECTED"))
          and e.event == "AUTO_REJECTED"):
        text = f"{head}, was rejected automatically because no one decided in time" + (
            f" ({day(e.on)})." if e.on else ".")
    elif s == "REJECTED":
        e = detail.last("REJECTED")
        text = f"{head}, was rejected{_by(e)}."
        if e and e.reason:
            text += f" Reason: {e.reason}."
        if e and e.note:
            text += f' Note: "{e.note}".'
        text += " A rejected request can't be resubmitted."
    elif (s == "EXECUTED" and (e := detail.last("APPROVED", "AUTO_APPROVED"))
          and e.event == "AUTO_APPROVED"):
        text = f"{head}, was approved automatically when no one decided in time, and carried out."
    elif s == "EXECUTED":
        text = f"{head}, was approved{_by(detail.last('APPROVED', 'EXECUTED'))} and carried out."
    elif s == "EXECUTING":
        text = f"{head}, is approved and being carried out."
    elif s == "EXECUTION_FAILED":
        text = (f"{head}, was approved but didn't go through"
                + (f": {detail.last_failure}" if detail.last_failure else "")
                + ". The approvals screen offers a retry.")
    elif s == "WITHDRAWN":
        text = f"{head}, was withdrawn{_by(detail.last('WITHDRAWN'))}."
    elif s == "EXPIRED":
        text = (f"{head}, expired" + (f" {_when(r.deadline)}" if r.deadline else "")
                + " before it was decided.")
    elif s in ("INVALIDATED", "FAILED"):
        why = TERMINAL_REASON.get(detail.terminal_code or "") or humanize(detail.terminal_code)
        text = (f"{head}, was closed" if s == "INVALIDATED" else f"{head}, failed") + (
            f": {why}" if why else "") + "."
    else:
        text = f"{head}, is {r.status_label}."
    card = {"title": r.reference, "items": [
        {"metric_id": "request_status", "label": "Status", "display": r.status_label.capitalize()}],
        "requests": [r.as_meta()]}
    return LiveAnswer(facts=Facts(), card=card, template=text, extra=_link(), authored=True)


# TerminalReason (ag-common-util on QA), where its meaning is plain. Others are humanized.
TERMINAL_REASON = {"ATTEMPTS_EXHAUSTED": "every attempt to carry it out failed",
                   "AUTO_REJECTED": "it was rejected automatically because no one decided in time",
                   "ENTITY_CANCELLED": "what it was for was cancelled",
                   "GATE_DISABLED": "approval was switched off for this kind of request",
                   "INSUFFICIENT_DEVICES": "the screens it needed are no longer available",
                   "NO_APPROVER_AVAILABLE": "no one was available to approve it",
                   "NO_OTP": "the OTP was never verified"}


def _field(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", key).replace("_", " ").lower()


def _value(key: str, v) -> str:
    if key in MONEY_QUANTITIES and (d := to_decimal(v)) is not None:
        return inr(d)
    return str(v)


def what_changed_answer(r: Request, drift: Drift | str) -> LiveAnswer:
    if isinstance(drift, str):
        return _problem(f"what changed in {r.reference}", drift)
    since = f"since {r.reference} was raised" + (f" on {day(r.raised)}" if r.raised else "")
    if drift.changes:
        parts = [f"{_field(k)} was {_value(k, a)} and is now {_value(k, b)}"
                 for k, (a, b) in drift.changes.items()]
        text = f"Changed {since}: " + "; ".join(parts) + "."
    elif drift.unchanged:
        text = f"Nothing has changed {since}."
    else:
        text = f"The dashboard reported nothing to compare for {r.reference}."
    card = {"title": r.reference, "items": [
        {"metric_id": "request_changes", "label": "Changed since raised",
         "display": count(len(drift.changes))}], "requests": [r.as_meta()]}
    return LiveAnswer(facts=Facts(), card=card, template=text, extra=_link(), authored=True)


# ── history ─────────────────────────────────────────────────────────────

def history_answer(got: RequestList, *, as_maker: bool) -> LiveAnswer:
    if got.problem:
        return _problem("your approval history", got.problem)
    rows = got.requests if as_maker else [r for r in got.requests if r.status not in ("PENDING",)]
    if not rows:
        return (no_requests(as_maker=True) if as_maker else
                text_answer("Approvals", "You haven't decided any approval requests yet.", authored=True))
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.status_label] = by_status.get(r.status_label, 0) + 1
    most_first = sorted(((v, k) for k, v in by_status.items()), reverse=True)
    breakdown = ", ".join(f"{v} {k}" for v, k in most_first)
    n = len(rows)
    lead = f"You've {'raised' if as_maker else 'decided'} {count_of(n, 'approval request')}"
    recent = "; ".join(f"{r.subject or r.operation_label} ({r.status_label})" for r in rows[:3])
    text = f"{lead}: {breakdown}. Most recent: {recent}."
    if got.partial:
        text += f" That covers the newest {n}."
    card = {"title": "Approval history",
            "items": [{"metric_id": f"approvals_{k.replace(' ', '_')}", "label": k.capitalize(),
                       "display": count(v)} for k, v in by_status.items()],
            "requests": [r.as_meta() for r in rows[:10]]}
    return LiveAnswer(facts=Facts(), card=card, template=text, extra=_link(), authored=True)


# ── the rules ───────────────────────────────────────────────────────────

ON_TIMEOUT = {"AUTO_APPROVE": "it is approved automatically",
              "AUTO_REJECT": "it is rejected automatically",
              "EXPIRE": "it expires"}


def _hours(h: int) -> str:
    return count_of(h // 24, "day") if h and h % 24 == 0 else count_of(h, "hour")


def _clauses(r) -> list[str]:
    """What one rule says, as independent clauses - any of them may be missing."""
    out = []
    stages = [and_list(list(dict.fromkeys(a_label(x) for x in roles)))
              for roles in r.approvers if roles]
    if stages:
        out.append("approved by " + ", then ".join(stages))
    outcome = ON_TIMEOUT.get(r.on_timeout or "")
    if r.hours and outcome:
        out.append(f"approvers have {_hours(r.hours)}, and if no one decides, {outcome}")
    elif r.hours:
        out.append(f"approvers have {_hours(r.hours)}, after which it still waits until "
                   "someone decides")
    elif outcome:
        out.append(f"if no one decides, {outcome}")
    else:                            # MANUAL, or nothing set: no deadline
        out.append("no deadline, so a request waits until someone decides")
    return out


def rules_answer(got: Rules, operation: str | None) -> LiveAnswer:
    """Who approves, how long they have and what happens if no one decides - every part
    either stated or said to be out of view. Request types with the same rule share a sentence."""
    if got.problem:
        return _problem("the approval settings", got.problem)
    rules = [r for r in got.rules if not operation or r.operation == operation]
    if not rules and got.agency is not None and not operation:
        rules = [got.agency]         # no per-type rows: the agency default is the rule
    if not rules:
        what = OPERATION_NOUN.get(operation or "", "campaign request" if operation else "request")
        text = (f"There are no approval settings for {what}s." if operation
                else "There are no approval settings for your account.")
        return text_answer("Approval settings", text, authored=True)
    groups: dict[tuple, list] = {}
    for r in rules:
        groups.setdefault(tuple(_clauses(r)), []).append(r)
    sentences = [f"{and_list([r.label for r in members])}: {'; '.join(clauses)}."
                 for clauses, members in groups.items()]
    if got.approvers_known is False:
        which = "it" if len(rules) == 1 else "them"
        sentences.append(f"I can't see who approves {which} with your access.")
    if all(r.inherited for r in rules):
        sentences.append("These are the platform defaults; they haven't been changed for your account.")
    card = {"title": "Approval settings", "items": [
        {"metric_id": "approval_sla_hours", "label": r.label,
         "display": _hours(r.hours) if r.hours else "No deadline",
         "on_timeout": ON_TIMEOUT.get(r.on_timeout or "", "waits")} for r in rules]}
    return LiveAnswer(facts=Facts(), card=card, template=" ".join(sentences), extra=_link(),
                      authored=True)
