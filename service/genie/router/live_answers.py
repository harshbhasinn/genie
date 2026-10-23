"""Answers built from live figures — wallet first.

The card is code: every figure is chosen, labelled and formatted here. The
sentence under it is ALSO written here, as a template, so the feature is whole
with no model at all. When a model is configured it may rephrase the template's
facts into something warmer, and what it writes is number-checked against this
card before anyone sees it (responder.py). The template is the fallback either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..data.wallet import WalletView
from ..ontology import Ontology
from .answers import Facts, _INTERNAL_TAG
from .followups import OVERVIEW_WORDS
from .lexical import tokens

# Capabilities answered from the live wallet.
WALLET_CAPS = ("wallet.explain_balance", "wallet.explain_runway", "wallet.explain_manager_wallets")

PROBLEM_TEXT = {
    "signed_out": "your session has expired, so sign in again to see live figures",
    "forbidden": "your account does not have access to that",
    "not_found": "the dashboard could not find it",
    "unavailable": "the dashboard did not answer just now",
    "bad_response": "the dashboard sent something I could not read",
}


def is_overview(text: str) -> bool:
    return bool(set(tokens(text)) & OVERVIEW_WORDS)


@dataclass(slots=True)
class LiveAnswer:
    facts: Facts                                   # for the narrator's prompt
    card: dict                                     # the metrics block the panel renders
    template: str                                  # the answer, written by code
    allowed: list[str] = field(default_factory=list)   # every figure the narrator may use


def _caveats(ontology: Ontology, metric_ids: list[str], facts: Facts) -> None:
    for mid in metric_ids:
        m = ontology.metric(mid)
        if not m:
            continue
        if m.definition:
            facts.add("definition", " ".join(m.definition.split()), metric_id=mid, label=m.label)
        for g in m.gotchas[:2]:
            text = " ".join(g.split())
            if not _INTERNAL_TAG.match(text):
                facts.add("caveat", text, metric_id=mid)


def wallet_answer(cap_id: str, text: str, view: WalletView, ontology: Ontology, *,
                  chosen: str | None = None, own_subwallet: bool = False) -> LiveAnswer:
    """`chosen` is a metric id the user picked from "which balance?", if they did."""
    facts = Facts()
    f = view.fact
    show: list[str]
    sentences: list[str] = []

    if own_subwallet:
        show = ["user_subwallet_balance", "manager_allocated", "manager_spent"]
        mine, alloc, spent = f("user_subwallet_balance"), f("manager_allocated"), f("manager_spent")
        if mine:
            s = f"Your sub-wallet has {mine.display} left"
            if alloc and spent:
                s += f": {alloc.display} was allocated to you and you have spent {spent.display}"
            sentences.append(s + ".")
            sentences.append("That is the money your campaigns draw from. The agency's own "
                             "wallet is separate and only an admin can move money into yours.")
    elif cap_id == "wallet.explain_manager_wallets":
        show = ["sub_wallet_total"]
        sub = f("sub_wallet_total")
        n = view.manager_count if view.manager_count is not None else len(view.managers)
        if sub:
            sentences.append(f"Your {n} campaign manager sub-wallets hold {sub.display} between them.")
        lowest = [m for m in view.managers if m.balance is not None][:3]
        if lowest:
            listed = ", ".join(f"{m.name} {m.as_meta()['balance']}" for m in lowest)
            sentences.append(f"Lowest balances first: {listed}.")
        sentences.append("That money is already allocated, so the agency cannot spend it directly.")
    elif cap_id == "wallet.explain_runway":
        show = ["wallet_runway_days", "low_balance", "main_wallet_balance", "month_consumption"]
        run, main, spent, low = (f("wallet_runway_days"), f("main_wallet_balance"),
                                 f("month_consumption"), f("low_balance"))
        if run:
            sentences.append(f"At the recent spending rate, the main wallet lasts about {run.display}, "
                             f"as the dashboard calculates it.")
        if main and spent:
            sentences.append(f"It holds {main.display}, and {spent.display} has been spent this month.")
        if low and low.value is True:
            sentences.append(f"It is below its low-balance threshold — {low.display.split('(')[-1].rstrip(')')} — "
                             f"so a top-up request is worth starting.")
    else:   # wallet.explain_balance
        trio = ["main_wallet_balance", "sub_wallet_total", "total_credits_owned"]
        if chosen in trio:
            show = [chosen]
        elif is_overview(text):
            show = trio + ["month_consumption", "wallet_runway_days", "low_balance"]
        else:
            show = trio
        main, sub, total = f("main_wallet_balance"), f("sub_wallet_total"), f("total_credits_owned")
        if main and "main_wallet_balance" in show:
            sentences.append(f"The main wallet has {main.display} — what campaigns draw from and "
                             f"what you can allocate.")
        if sub and "sub_wallet_total" in show:
            n = view.manager_count
            sentences.append(f"Team sub-wallets hold {sub.display}"
                             + (f" across {n} managers" if n else "")
                             + " — already handed out, so the agency cannot spend it.")
        if total and "total_credits_owned" in show:
            sentences.append(f"Together that is {total.display} in total credits, a sum of the two "
                             f"rather than an account you spend from.")
        spent, run, low = f("month_consumption"), f("wallet_runway_days"), f("low_balance")
        if spent and "month_consumption" in show:
            sentences.append(f"{spent.display} has been spent this month"
                             + (f", which leaves about {run.display} of runway at that pace."
                                if run and "wallet_runway_days" in show else "."))
        if low and low.value is True and "low_balance" in show:
            sentences.append("The main wallet is below its low-balance threshold.")

    shown = [x for x in (f(m) for m in show) if x is not None]
    for fact in shown:
        facts.add("live_value", f"{fact.label}: {fact.display}"
                  + (f" ({fact.short})" if fact.short and fact.short != fact.display else ""),
                  metric_id=fact.metric_id, source=fact.source)
    # The managers go to the narrator too, not just the card: a model that only sees
    # the total says "I don't have a list of managers" while the card shows one.
    listed = view.managers[:10] if (cap_id == "wallet.explain_manager_wallets"
                                     and not own_subwallet) else []
    for m in listed:
        meta = m.as_meta()
        facts.add("live_value", f"Campaign manager {meta['name']}: balance {meta['balance'] or 'not reported'}"
                  + (f", allocated {meta['allocated']}" if meta["allocated"] else "")
                  + (f", spent {meta['spent']}" if meta["spent"] else ""),
                  metric_id="manager_balance", source="members/wallet-overview · members[]")
    _caveats(ontology, show, facts)

    missing = sorted({PROBLEM_TEXT.get(k, k) for k in view.problems.values()})
    if missing and not shown:
        sentences = [f"I couldn't read the wallet just now: {missing[0]}."]
    elif missing:
        sentences.append(f"Some figures are missing because {missing[0]}.")
    for reason in missing:
        facts.add("unavailable", reason)

    facts.prose_hint = (
        "These are the user's LIVE figures, read just now from the dashboard as them. "
        "Lead with the answer in two to four sentences. Use the figures exactly as written — "
        "the full or the short form — and do not add, subtract or compute any new number. "
        "Explain what each figure means using the definitions and caveats. If a figure is "
        "missing, say so plainly; never fill it in.")

    card = {"title": "Wallet", "items": [x.as_meta() for x in shown]}
    if cap_id == "wallet.explain_manager_wallets" and not own_subwallet:
        card["managers"] = [m.as_meta() for m in view.managers[:10]]
    allowed = [x.display for x in shown] + [x.short for x in shown if x.short]
    for m in view.managers[:10]:
        meta = m.as_meta()
        allowed += [meta["balance"] or "", meta["allocated"] or "", meta["spent"] or ""]
    if view.manager_count is not None:
        allowed.append(str(view.manager_count))
    return LiveAnswer(facts=facts, card=card, template=" ".join(sentences), allowed=allowed)
