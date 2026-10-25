"""The wallet, read live as the user and turned into facts.

Field ALLOWLIST: only the fields named below are ever read from a response.
/wallet/kpis also returns adgridRevenue and apRevenueTotal — the AdGrid-to-agency
cost leg — and whether an agency may be shown that is an open question
(asks/BACKEND-CHECKS.md §3). Reading by allowlist means a new field the backend
adds tomorrow cannot leak into an answer by accident.

Sources follow the ontology (ontology/screens/wallet-management.yaml):
  main_wallet_balance   GET /api/v1/clients/{clientId}/wallet/balance          balance
  sub_wallet_total      GET /api/v1/clients/{clientId}/members/wallet-overview totalSubWalletBalance
  total_credits_owned   computed here: main + sub-wallets (a UI aggregate, never a ledger)
  month_consumption     GET /api/v1/clients/{clientId}/wallet/kpis             consumedThisMonth
  wallet_runway_days    GET /api/v1/clients/{clientId}/wallet/kpis      runwayMonths — MONTHS (R32)
  low balance flag      GET /api/v1/clients/{clientId}/wallet/kpis      lowBalance, lowBalanceThreshold
  manager_*             GET /api/v1/clients/{clientId}/members/wallet-overview members[]
  user_subwallet_balance GET /api/v1/clients/{clientId}/members/{userId}/wallet/summary currentBalance

Runway is read, never recomputed: the backend already decided what "runway" means.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..bff import BffClient, BffError
from .money import inr, inr_short, to_decimal

BALANCE = "/api/v1/clients/{clientId}/wallet/balance"
KPIS = "/api/v1/clients/{clientId}/wallet/kpis"
OVERVIEW = "/api/v1/clients/{clientId}/members/wallet-overview"
MY_SUBWALLET = "/api/v1/clients/{clientId}/members/{userId}/wallet/summary"


@dataclass(frozen=True, slots=True)
class LiveFact:
    metric_id: str
    label: str
    value: Decimal | bool | int
    display: str            # what the card shows, formatted by code
    short: str | None       # ₹15.75 L — offered to the narrator as well
    source: str             # endpoint template + field, for provenance

    def fact_line(self) -> str:
        """ "Main wallet: ₹15,75,000 (₹15.75 L)" - the figure as a model is given it."""
        return f"{self.label}: {self.display}" + (
            f" ({self.short})" if self.short and self.short != self.display else "")

    def as_meta(self) -> dict:
        v = self.value
        return {"metric_id": self.metric_id, "label": self.label, "display": self.display,
                "short": self.short, "source": self.source,
                "value": str(v) if isinstance(v, Decimal) else v}


@dataclass(slots=True)
class Manager:
    name: str
    balance: Decimal | None
    allocated: Decimal | None
    spent: Decimal | None

    def as_meta(self) -> dict:
        f = lambda d: inr(d) if d is not None else None  # noqa: E731
        return {"name": self.name, "balance": f(self.balance),
                "allocated": f(self.allocated), "spent": f(self.spent)}


@dataclass(slots=True)
class WalletView:
    facts: list[LiveFact] = field(default_factory=list)
    managers: list[Manager] = field(default_factory=list)
    manager_count: int | None = None
    # endpoint template -> BffError kind, for whatever could not be read
    problems: dict[str, str] = field(default_factory=dict)

    def fact(self, metric_id: str) -> LiveFact | None:
        return next((f for f in self.facts if f.metric_id == metric_id), None)


def _money(metric_id: str, label: str, raw, source: str) -> LiveFact | None:
    d = to_decimal(raw)
    if d is None:
        return None
    return LiveFact(metric_id, label, d, inr(d), inr_short(d), source)


async def agency_wallet(bff: BffClient, token: str, client_id: str, *,
                        with_managers: bool = True) -> WalletView:
    """Everything an agency admin's wallet position needs, read in parallel."""
    p = {"clientId": client_id}
    reads = {BALANCE: {"path_params": p}, KPIS: {"path_params": p}}
    if with_managers:
        reads[OVERVIEW] = {"path_params": p, "params": {"page": 0, "size": 100}}
    view = WalletView()
    by_ep, view.problems = await bff.get_all(token, reads)

    bal, kpis, ov = by_ep.get(BALANCE), by_ep.get(KPIS), by_ep.get(OVERVIEW)
    main = _money("main_wallet_balance", "Main wallet", (bal or {}).get("balance"),
                  f"{BALANCE} · balance") if bal else None
    sub = _money("sub_wallet_total", "Team sub-wallets", (ov or {}).get("totalSubWalletBalance"),
                 f"{OVERVIEW} · totalSubWalletBalance") if ov else None
    for f in (main, sub):
        if f:
            view.facts.append(f)
    if main and sub:
        total = main.value + sub.value
        view.facts.append(LiveFact("total_credits_owned", "Total credits", total, inr(total),
                                   inr_short(total), "computed · main wallet + team sub-wallets"))
    if kpis:
        spent = _money("month_consumption", "Spent this month", kpis.get("consumedThisMonth"),
                       f"{KPIS} · consumedThisMonth")
        if spent:
            view.facts.append(spent)
        months = to_decimal(kpis.get("runwayMonths"))
        if months is not None:
            m = months.quantize(Decimal("0.1"))
            view.facts.append(LiveFact("wallet_runway_days", "Runway", m, f"{m} months", None,
                                       f"{KPIS} · runwayMonths"))
        if isinstance(kpis.get("lowBalance"), bool):
            low = kpis["lowBalance"]
            threshold = to_decimal(kpis.get("lowBalanceThreshold"))
            view.facts.append(LiveFact(
                "low_balance", "Low balance", low,
                ("Below the low-balance threshold" if low else "Above the low-balance threshold")
                + (f" ({inr(threshold)})" if threshold is not None else ""),
                None, f"{KPIS} · lowBalance, lowBalanceThreshold"))
    if ov:
        view.manager_count = ov.get("subWalletCount")
        for m in ov.get("members") or []:
            view.managers.append(Manager(
                name=str(m.get("name") or "Unnamed"),
                balance=to_decimal(m.get("currentBalance")),
                allocated=to_decimal(m.get("totalAllocated")),
                spent=to_decimal(m.get("totalSpent"))))
        view.managers.sort(key=lambda m: (m.balance is None, m.balance or Decimal(0)))
    return view


async def my_subwallet(bff: BffClient, token: str, client_id: str, user_id: str) -> WalletView:
    """A campaign manager's own sub-wallet — what "my balance" means for that role."""
    view = WalletView()
    try:
        data = (await bff.get(MY_SUBWALLET, token=token,
                              path_params={"clientId": client_id, "userId": user_id})).data or {}
    except BffError as err:
        view.problems[MY_SUBWALLET] = err.kind
        return view
    for metric_id, label, key in (("user_subwallet_balance", "Your sub-wallet", "currentBalance"),
                                  ("manager_allocated", "Allocated to you", "totalAllocated"),
                                  ("manager_spent", "You have spent", "totalSpent")):
        f = _money(metric_id, label, data.get(key), f"{MY_SUBWALLET} · {key}")
        if f:
            view.facts.append(f)
    return view
