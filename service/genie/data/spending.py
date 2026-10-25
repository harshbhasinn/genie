"""Where the agency's money went, and what it owes - read live as the admin.

Sources (live spec; the AdOps dashboard on QA calls the same paths):
  spend by brand      GET /api/v1/clients/{clientId}/wallet/spending-analysis   brands[], totals
  month by month      GET /api/v1/clients/{clientId}/wallet/consumption/monthly rows[]
  ledger lines        GET /api/v1/clients/{clientId}/wallet/statement           lines[]
  what is owed        GET /api/v1/clients/{clientId}/wallet/settlements/summary
  one month's bill    GET /api/v1/clients/{clientId}/wallet/settlements?months=

The screen's Transaction History is /wallet/transactions, but its row type in the
spec is "Row" - one name shared by 16 different responses, so the contract does not
say what a transaction row holds. /wallet/statement is the same ledger with a typed
line (description, entryType, amount, balanceAfter), so the ledger is read there.

Dates: a window is sent only when the user names one. Each endpoint gets the format it
returns itself (live QA 2026-10-24): from/to as YYYY-MM-DD; consumption/monthly's months
as YYYY-MM (its rows read "2026-10"); settlements' months as YYYY-MM-01 (its rows read
"2026-10-01", and "2026-09" is refused with a 400). Otherwise the backend's default is
used and its own from/to echoed.

byChargeType is an object keyed by charge type in camelCase ({"rental": "275.33"}) on
live QA, not the ChargeTypeTotal[] the spec shows.

Field allowlist, as for the wallet. Settlements are what AdGrid bills the agency
(the Command Centre's Platform Invoice); only totals and charge-type totals are
read - never the per-contract charge lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..bff import BffClient, BffError
from .fields import humanize, parse_date, parse_month, to_int, window_of
from .money import to_decimal

SPEND_BY_BRAND = "/api/v1/clients/{clientId}/wallet/spending-analysis"
MONTHLY = "/api/v1/clients/{clientId}/wallet/consumption/monthly"
STATEMENT = "/api/v1/clients/{clientId}/wallet/statement"
OWED = "/api/v1/clients/{clientId}/wallet/settlements/summary"
SETTLEMENTS = "/api/v1/clients/{clientId}/wallet/settlements"

PAGE_SIZE = 50
# LedgerEntryType (ag-billing-service on QA). Anything else is humanized.
ENTRY_LABEL = {"DEBIT": "Campaign spend", "TARGETED_CAMPAIGN_CONSUMPTION": "Campaign spend",
               "TOPUP": "Top-up", "ALLOCATION": "Allocation to a manager",
               "DEALLOCATION": "Taken back from a manager", "REALLOCATION": "Reallocation",
               "REFUND": "Refund", "REVERSAL": "Reversal", "ADJUSTMENT": "Adjustment",
               "PAYMENT_RECEIVED": "Payment received", "GST": "GST", "GST_REFUND": "GST refund",
               "RENTAL": "Screen rental", "PLATFORM_FEE": "Platform fee",
               "MIN_GUARANTEE": "Minimum guarantee"}
# SettlementChargeType (ag-billing-service on QA).
CHARGE_LABEL = {"REVENUE_SHARING": "Revenue share", "RENTAL": "Screen rental",
                "MIN_GUARANTEE": "Minimum guarantee", "PLATFORM_FEE": "Platform fee",
                "RESALE_CONSUMPTION": "Resale consumption", "RESALE_FLOOR": "Resale floor",
                "SHAREGRID_PAYOUT": "ShareGrid payout"}


def _label(table: dict[str, str], code: str | None) -> str:
    """The label for a code, or the code read as words when it has none."""
    return table.get(code or "") or (humanize(code) or "unknown").capitalize()


def _charges(raw) -> list[tuple[str, Decimal]]:
    """byChargeType as returned - {"revenueSharing": "0.00", "rental": "275.33"} - labelled."""
    pairs = raw.items() if isinstance(raw, dict) else ()
    out = []
    for key, value in pairs:
        code = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).upper()       # rental -> RENTAL
        if (amount := to_decimal(value)) is not None:
            out.append((_label(CHARGE_LABEL, code), amount))
    return out


@dataclass(frozen=True, slots=True)
class Window:
    start: date
    end: date
    label: str                      # "this month", "September 2026", "the last 30 days"
    named: bool = False             # a calendar month the user named ("September", "last month")
    rolling: bool = False           # a span of days, not a calendar month ("the last 30 days")

    def params(self) -> dict:
        return {"from": self.start.isoformat(), "to": self.end.isoformat()}


async def _get(bff: BffClient, token: str, path: str, client_id: str, params: dict | None = None):
    """(data, None) or (None, BffError kind)."""
    try:
        res = await bff.get(path, token=token, path_params={"clientId": client_id}, params=params)
    except BffError as err:
        return None, err.kind
    return res.data or {}, None


# ── spend by brand ──────────────────────────────────────────────────────

@dataclass(slots=True)
class BrandSpend:
    name: str
    amount: Decimal | None
    share: Decimal | None           # percentageOfTotal, as returned
    campaigns: int | None           # campaignsContributing


@dataclass(slots=True)
class SpendByBrand:
    brands: list[BrandSpend] = field(default_factory=list)
    total: Decimal | None = None
    window: tuple[date, date] | None = None
    count: int | None = None        # totalElements: brands in all, not one page
    problem: str | None = None


async def spend_by_brand(bff: BffClient, token: str, client_id: str,
                         window: Window | None = None) -> SpendByBrand:
    data, problem = await _get(bff, token, SPEND_BY_BRAND, client_id,
                               {"page": 0, "size": PAGE_SIZE} | (window.params() if window else {}))
    if problem:
        return SpendByBrand(problem=problem)
    out = SpendByBrand(total=to_decimal(data.get("totalCreditsConsumed")),
                       count=to_int(data.get("totalElements")))
    out.window = window_of(data) or ((window.start, window.end) if window else None)
    for b in data.get("brands") or []:
        if isinstance(b, dict):
            out.brands.append(BrandSpend(str(b.get("brandName") or "Unnamed brand"),
                                         to_decimal(b.get("creditsConsumed")),
                                         to_decimal(b.get("percentageOfTotal")),
                                         to_int(b.get("campaignsContributing"))))
    out.brands.sort(key=lambda b: b.amount or Decimal(0), reverse=True)
    return out


# ── month by month ──────────────────────────────────────────────────────

@dataclass(slots=True)
class MonthLine:
    month: date
    added: Decimal | None
    consumed: Decimal | None
    campaigns: int | None


@dataclass(slots=True)
class Months:
    rows: list[MonthLine] = field(default_factory=list)
    problem: str | None = None

    def get(self, month: date) -> MonthLine | None:
        return next((r for r in self.rows if r.month == month), None)


async def monthly(bff: BffClient, token: str, client_id: str, first: date | None = None,
                  last: date | None = None) -> Months:
    params = {}
    if first:
        params["fromMonth"] = f"{first:%Y-%m}"
    if last:
        params["toMonth"] = f"{last:%Y-%m}"
    data, problem = await _get(bff, token, MONTHLY, client_id, params or None)
    if problem:
        return Months(problem=problem)
    out = Months()
    for r in data.get("rows") or []:
        if isinstance(r, dict) and (m := parse_month(r.get("month"))):
            out.rows.append(MonthLine(m, to_decimal(r.get("added")), to_decimal(r.get("consumed")),
                                      to_int(r.get("campaignCount"))))
    out.rows.sort(key=lambda r: r.month, reverse=True)
    return out


# ── the ledger ──────────────────────────────────────────────────────────

@dataclass(slots=True)
class Entry:
    kind: str                       # entryType, labelled
    debit: bool | None              # side
    amount: Decimal | None
    balance_after: Decimal | None
    on: date | None
    description: str | None
    campaign: str | None


@dataclass(slots=True)
class Ledger:
    entries: list[Entry] = field(default_factory=list)
    total: int | None = None
    problem: str | None = None


async def ledger(bff: BffClient, token: str, client_id: str, window: Window | None = None,
                 size: int = 10) -> Ledger:
    data, problem = await _get(bff, token, STATEMENT, client_id,
                               {"page": 0, "size": size} | (window.params() if window else {}))
    if problem:
        return Ledger(problem=problem)
    out = Ledger(total=to_int(data.get("totalElements")))
    for line in data.get("lines") or []:
        if not isinstance(line, dict):
            continue
        side = str(line.get("side") or "").upper()
        campaign = line.get("campaign") if isinstance(line.get("campaign"), dict) else {}
        code = line.get("entryType")
        out.entries.append(Entry(
            kind=_label(ENTRY_LABEL, code),
            debit=True if side.startswith("D") else False if side.startswith("C") else None,
            amount=to_decimal(line.get("amount")), balance_after=to_decimal(line.get("balanceAfter")),
            on=parse_date(line.get("postedAt")),
            description=(line.get("description") or "").strip() or None,
            campaign=campaign.get("name") or campaign.get("campaignName") or None))
    return out


# ── what is owed ────────────────────────────────────────────────────────

@dataclass(slots=True)
class Owed:
    outstanding: Decimal | None = None
    unpaid_months: list[tuple[date, Decimal | None]] = field(default_factory=list)
    current: tuple[date | None, Decimal | None, str | None] | None = None   # month, amount, status
    paid_term: tuple[date | None, date | None, Decimal | None] | None = None
    problem: str | None = None


@dataclass(slots=True)
class MonthBill:
    month: date
    status: str | None              # paymentStatus: PAID / UNPAID / ...
    total: Decimal | None
    unpaid: Decimal | None
    gst: Decimal | None
    by_charge: list[tuple[str, Decimal]] = field(default_factory=list)
    due: date | None = None         # the earliest due date among the month's contracts


@dataclass(slots=True)
class Bills:
    months: list[MonthBill] = field(default_factory=list)
    unpaid: Decimal | None = None   # grandTotals.unpaid
    problem: str | None = None


async def owed(bff: BffClient, token: str, client_id: str) -> Owed:
    data, problem = await _get(bff, token, OWED, client_id)
    if problem:
        return Owed(problem=problem)
    out = Owed(outstanding=to_decimal(data.get("totalOutstanding")))
    for m in data.get("outstandingMonths") or []:
        if isinstance(m, dict) and (month := parse_month(m.get("settlementMonth"))):
            out.unpaid_months.append((month, to_decimal(m.get("unpaidAmount"))))
    out.unpaid_months.sort(key=lambda x: x[0])
    if isinstance(cur := data.get("currentInvoice"), dict):
        out.current = (parse_month(cur.get("settlementMonth")), to_decimal(cur.get("amount")),
                       cur.get("status") or None)
    if isinstance(term := data.get("paidThisTerm"), dict):
        out.paid_term = (parse_date(term.get("from")), parse_date(term.get("to")),
                         to_decimal(term.get("amount")))
    return out


async def bills(bff: BffClient, token: str, client_id: str, months: list[date]) -> Bills:
    data, problem = await _get(bff, token, SETTLEMENTS, client_id,
                               {"months": [m.replace(day=1).isoformat() for m in months]})
    if problem:
        return Bills(problem=problem)
    out = Bills(unpaid=to_decimal((data.get("grandTotals") or {}).get("unpaid")))
    for m in data.get("months") or []:
        if not isinstance(m, dict) or not (month := parse_month(m.get("settlementMonth"))):
            continue
        t = m.get("totals") or {}
        dues = [d for c in m.get("contracts") or [] if isinstance(c, dict)
                if (d := parse_date(c.get("dueDate")))]
        out.months.append(MonthBill(
            month, m.get("paymentStatus") or None, to_decimal(t.get("total")),
            to_decimal(t.get("unpaid")), to_decimal(t.get("gst")),
            by_charge=_charges(m.get("byChargeType")),
            due=min(dues) if dues else None))
    out.months.sort(key=lambda b: b.month, reverse=True)
    return out

