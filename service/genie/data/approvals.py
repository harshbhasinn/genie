"""Approval requests, read live as the user.

Lists, called exactly as the approvals page of the AdOps dashboard on QA calls them
(the same BFF; the agency Partner Hub's frontend was not available to read):
  waiting for me    GET /api/v1/approvals/queue?view=AWAITING_ME&status=PENDING
                    - rows where myVerdict is set are already decided by me (the
                      screen's own pending count drops them the same way)
  failed after OK   GET /api/v1/approvals/queue?view=AWAITING_ME&status=EXECUTION_FAILED
  decided by me     GET /api/v1/approvals/queue?view=DECIDED_BY_ME
  raised by me      GET /api/v1/approvals/mine?status=...
One request:
  in full           GET /api/v1/approvals/{referenceCode}       trail[], stage, deadline, failure
  what changed      GET /api/v1/approvals/{referenceCode}/preview-today   requested, today, delta
The rules:
  deadlines, timeout GET /api/v1/clients/{clientId}/approval-automation      per operation
  who approves       GET /api/v1/approval-configs                           stages[].roles

The enums are not in the spec; these are the values the dashboard sends and labels
(and ag-common-util's ApprovalStatus / ApprovalTrailEvent / OnTimeout / TerminalReason on QA):
  operationCode  CAMPAIGN_CONFIRM, CREATIVE_REVIEW, WALLET_TOPUP, MEMBER_FUND_ALLOCATE,
                 MARK_INVOICE_PAID
  status         PENDING, EXECUTING, SENT_BACK, EXECUTED, EXECUTION_FAILED, FAILED,
                 REJECTED, WITHDRAWN, EXPIRED, INVALIDATED

Live QA 2026-10-23: a campaign manager's confirm only RAISES a request once the
manager verifies their own OTP (purpose APPROVAL_RAISE); until then the request
does not exist (404).

Field allowlist, as for the wallet: quantityValues, frozenArgs and the preview
JSON blobs are not read - their meaning per operation is not in the contract.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date

from ..bff import BffClient, BffError
from ..roles import is_campaign_manager
from .fields import day, from_epoch_ms, humanize

QUEUE = "/api/v1/approvals/queue"
MINE = "/api/v1/approvals/mine"
DETAIL = "/api/v1/approvals/{referenceCode}"
PREVIEW = "/api/v1/approvals/{referenceCode}/preview-today"
AUTOMATION = "/api/v1/clients/{clientId}/approval-automation"
CONFIGS = "/api/v1/approval-configs"
PAGE_SIZE = 100
MAX_PAGES = 5                       # 500 requests: past that, counts say "at least"

OPERATION_LABEL = {"CAMPAIGN_CONFIRM": "Campaign confirmation", "CREATIVE_REVIEW": "Content review",
                   "WALLET_TOPUP": "Wallet top-up", "MEMBER_FUND_ALLOCATE": "Member funding",
                   "MARK_INVOICE_PAID": "Mark invoice paid"}
STATUS_LABEL = {"PENDING": "pending", "EXECUTING": "being carried out", "SENT_BACK": "sent back",
                "EXECUTED": "approved and carried out",
                "EXECUTION_FAILED": "approved but failed to go through", "FAILED": "failed",
                "REJECTED": "rejected", "WITHDRAWN": "withdrawn", "EXPIRED": "expired",
                "INVALIDATED": "closed"}
OPEN = frozenset({"PENDING", "SENT_BACK", "EXECUTING"})
# A request's reference wherever it is written: REQ-1136, req1136.
REFERENCE = re.compile(r"\bREQ-?\d+\b", re.IGNORECASE)
# The quantity keys that are money (quantityValues and preview-today on live QA carry
# amount and campaignValue). Any other key is shown as returned, never guessed at.
MONEY_QUANTITIES = frozenset({"amount", "campaignValue"})


@dataclass(slots=True)
class Request:
    reference: str
    operation: str
    status: str
    maker: str | None
    subject: str | None              # scopeLabel - what the request is about, e.g. the campaign
    raised: date | None
    decided_by_me: bool = False      # myVerdict is set
    scope_ref: str | None = None     # e.g. the campaign id
    stage: tuple[int, int] | None = None
    deadline: date | None = None

    @property
    def operation_label(self) -> str:
        return OPERATION_LABEL.get(self.operation) or (humanize(self.operation) or "").capitalize()

    @property
    def status_label(self) -> str:
        return STATUS_LABEL.get(self.status, humanize(self.status) or "unknown")

    @classmethod
    def from_payload(cls, d: dict) -> "Request":
        cur, tot = d.get("currentStage"), d.get("totalStages")
        return cls(reference=str(d.get("referenceCode") or ""),
                   operation=str(d.get("operationCode") or "UNKNOWN"),
                   status=str(d.get("status") or "UNKNOWN"),
                   maker=d.get("makerName") or None, subject=d.get("scopeLabel") or None,
                   raised=from_epoch_ms(d.get("createdOnEpochMs")),
                   decided_by_me=bool(d.get("myVerdict")),
                   scope_ref=d.get("scopeRef") or None,
                   stage=(cur, tot) if isinstance(cur, int) and isinstance(tot, int) and tot else None,
                   deadline=from_epoch_ms(d.get("finalDeadlineAtEpochMs")))

    def as_meta(self) -> dict:
        return {"reference": self.reference, "operation": self.operation_label,
                "subject": self.subject, "maker": self.maker, "status": self.status_label,
                "raised": day(self.raised) if self.raised else None,
                "deadline": day(self.deadline) if self.deadline else None,
                "stage": f"{self.stage[0]} of {self.stage[1]}" if self.stage else None}


@dataclass(slots=True)
class RequestList:
    requests: list[Request] = field(default_factory=list)
    total: int | None = None
    problem: str | None = None

    @property
    def partial(self) -> bool:
        return self.total is not None and self.total > len(self.requests)


async def _read(bff: BffClient, token: str, path: str, params: dict) -> RequestList:
    """Every page up to MAX_PAGES, so counts are true for any normal queue."""
    out = RequestList()
    for page in range(MAX_PAGES):
        try:
            data = (await bff.get(path, token=token,
                                  params=params | {"page": page, "size": PAGE_SIZE})).data or {}
        except BffError as err:
            return RequestList(problem=err.kind) if page == 0 else out
        rows = [r for r in data.get("requests") or [] if isinstance(r, dict)]
        out.requests += [Request.from_payload(r) for r in rows]
        if isinstance(data.get("totalElements"), int):
            out.total = data["totalElements"]
        if len(rows) < PAGE_SIZE or (out.total is not None and len(out.requests) >= out.total):
            break
    return out


async def awaiting_me(bff: BffClient, token: str, *, operation: str | None = None) -> RequestList:
    """Requests waiting on this user's decision, as the approvals screen counts them."""
    params = {"view": "AWAITING_ME", "status": "PENDING"}
    if operation:
        params["operationCode"] = operation
    got = await _read(bff, token, QUEUE, params)
    keep = [r for r in got.requests if not r.decided_by_me]
    if got.total is not None:
        got.total -= len(got.requests) - len(keep)    # decided rows are not "waiting"
    got.requests = keep
    return got


async def failed_after_approval(bff: BffClient, token: str) -> RequestList:
    """Approved, then failed to execute - the dashboard offers a retry on these."""
    return await _read(bff, token, QUEUE, {"view": "AWAITING_ME", "status": "EXECUTION_FAILED"})


async def decided_by_me(bff: BffClient, token: str, *, operation: str | None = None) -> RequestList:
    params = {"view": "DECIDED_BY_ME"} | ({"operationCode": operation} if operation else {})
    return await _read(bff, token, QUEUE, params)


async def raised_by_me(bff: BffClient, token: str, *, status: str | None = None,
                       operation: str | None = None) -> RequestList:
    params = (({"status": status} if status else {})
              | ({"operationCode": operation} if operation else {}))
    return await _read(bff, token, MINE, params)


# ── one request ─────────────────────────────────────────────────────────

@dataclass(slots=True)
class Event:
    event: str                       # e.g. RAISED, APPROVED, SENT_BACK, REJECTED, RESUBMITTED
    actor: str | None
    reason: str | None               # reasonCode, humanized
    note: str | None
    on: date | None


@dataclass(slots=True)
class RequestDetail:
    request: Request
    trail: list[Event] = field(default_factory=list)
    last_failure: str | None = None
    terminal_code: str | None = None        # TerminalReason, as returned

    def last(self, *events: str) -> Event | None:
        return next((e for e in reversed(self.trail) if e.event in events), None)


async def request_detail(bff: BffClient, token: str, reference: str) -> RequestDetail | str:
    """The request in full, or the BffError kind explaining why not."""
    try:
        d = (await bff.get(DETAIL, token=token, path_params={"referenceCode": reference})).data or {}
    except BffError as err:
        return err.kind
    trail = [Event(str(t.get("event") or ""), t.get("actorName") or None,
                   humanize(t.get("reasonCode")), (t.get("note") or "").strip() or None,
                   from_epoch_ms(t.get("occurredAtEpochMs")))
             for t in sorted((t for t in d.get("trail") or [] if isinstance(t, dict)),
                             key=lambda t: t.get("seq") or 0)]
    return RequestDetail(Request.from_payload(d), trail,
                         last_failure=humanize(d.get("lastFailureCode")),
                         terminal_code=d.get("terminalReason") or None)


@dataclass(slots=True)
class Drift:
    """What changed since the request was raised: field -> (requested, today)."""
    changes: dict[str, tuple[object, object]] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)


async def preview_today(bff: BffClient, token: str, reference: str) -> Drift | str:
    """requested / today / delta are untyped objects in the spec, so they are read
    generically: a scalar field present in both, compared as returned."""
    try:
        d = (await bff.get(PREVIEW, token=token, path_params={"referenceCode": reference})).data or {}
    except BffError as err:
        return err.kind
    requested, today = d.get("requested") or {}, d.get("today") or {}
    out = Drift()
    for key in sorted(set(requested) & set(today)):
        a, b = requested[key], today[key]
        if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
            continue
        if a == b:
            out.unchanged.append(key)
        else:
            out.changes[key] = (a, b)
    return out


async def campaign_requests(bff: BffClient, token: str, role: str | None) -> dict[str, Request]:
    """Open requests by campaign id - so a draft awaiting approval can say so."""
    if is_campaign_manager(role):
        got = await raised_by_me(bff, token, operation="CAMPAIGN_CONFIRM")
    else:
        mine, queue = await asyncio.gather(raised_by_me(bff, token, operation="CAMPAIGN_CONFIRM"),
                                           awaiting_me(bff, token, operation="CAMPAIGN_CONFIRM"))
        got = RequestList(requests=mine.requests + queue.requests)
    out: dict[str, Request] = {}
    for r in got.requests:                       # newest wins: the list comes newest first
        if r.scope_ref and r.scope_ref not in out:
            out[r.scope_ref] = r
    return out


# ── the rules ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class Rule:
    operation: str
    label: str                       # displayName, else the operation's own label
    hours: int | None                # effectiveSlaHours: how long approvers have
    on_timeout: str | None           # effectiveOnTimeout: AUTO_APPROVE, AUTO_REJECT, EXPIRE, MANUAL
    inherited: bool | None           # the platform default, not set by the agency
    approvers: list[list[str]] = field(default_factory=list)   # roles per stage, from the config


@dataclass(slots=True)
class Rules:
    rules: list[Rule] = field(default_factory=list)      # one per request type
    agency: Rule | None = None       # the client-level row (no operationCode): the default
    approvers_known: bool = False    # whether the configs could be read at all
    problem: str | None = None


async def approval_rules(bff: BffClient, token: str, client_id: str) -> Rules:
    """Deadlines and timeouts per request type; who approves when the configs are readable.

    The configs endpoint is not client-scoped and may be AdOps-only: if it is refused,
    the answer is built from the agency's automation settings alone.
    """
    got, problems = await bff.get_all(token, {AUTOMATION: {"path_params": {"clientId": client_id}},
                                              CONFIGS: {"params": {"enabled": True}}})
    if AUTOMATION in problems:
        return Rules(problem=problems[AUTOMATION])
    stages: dict[str, list[list[str]]] = {}
    out = Rules(approvers_known=CONFIGS in got)
    if CONFIGS in got:
        for c in got[CONFIGS] or []:
            if isinstance(c, dict) and c.get("requiresApproval") and c.get("operationCode"):
                stages.setdefault(c["operationCode"], [
                    [str(x) for x in st.get("roles") or []]
                    for st in sorted(c.get("stages") or [], key=lambda st: st.get("stageNo") or 0)
                    if isinstance(st, dict)])
    for a in got[AUTOMATION] or []:
        if not isinstance(a, dict):
            continue
        op = str(a.get("operationCode") or "")
        hours = a.get("effectiveSlaHours", a.get("slaHours"))
        flag = lambda k: a.get(k) if isinstance(a.get(k), bool) else None   # noqa: E731
        rule = Rule(op, str(a.get("displayName") or OPERATION_LABEL.get(op, humanize(op) or "")),
                    hours if isinstance(hours, int) else None,
                    a.get("effectiveOnTimeout") or a.get("onTimeout") or None,
                    flag("inheritedFromDefault"), stages.get(op, []))
        if op:
            out.rules.append(rule)
        else:                        # "Client Level": the agency's default for every type
            out.agency = rule
    return out
