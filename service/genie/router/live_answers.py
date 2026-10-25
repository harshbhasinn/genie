"""Answers built from live figures — wallet first.

The card is code: every figure is chosen, labelled and formatted here. The
sentence under it is ALSO written here, as a template, so the feature is whole
with no model at all. When a model is configured it may rephrase the template's
facts into something warmer, and what it writes is number-checked against this
card before anyone sees it (responder.py). The template is the fallback either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..data.fields import day
from ..data.money import count_of
from ..data.wallet import WalletView
from ..ontology import Ontology
from .answers import Facts
from .followups import OVERVIEW_WORDS
from .lexical import normalise, tokens

# Capabilities answered from the live wallet.
WALLET_CAPS = ("wallet.explain_balance", "wallet.explain_runway", "wallet.explain_manager_wallets")

# How a model words a live card, when GENIE_LIVE_WORDING=model. Shared by every live answer.
LIVE_PROSE = ("[answer] is the reply, already written from the user's live figures. Say it in "
              "your own words, in at most three sentences: keep every figure exactly as written "
              "(full or short form) and keep its meaning, including anything it says is none, "
              "zero or not available. The other FACTS may add context; add nothing else.")

PROBLEM_TEXT = {
    "signed_out": "your session has expired, so sign in again to see live figures",
    "forbidden": "your account does not have access to that",
    "not_found": "the dashboard could not find it",
    "rejected": "the dashboard couldn't answer that request",
    "unavailable": "the dashboard did not answer just now",
    "bad_response": "the dashboard sent something I could not read",
}


MAX_LISTED = 5            # names said in a sentence, or offered in "Which campaign?"


def between(window: tuple[date, date] | None) -> str:
    """ "between 16 Oct 2026 and 20 Oct 2026", or "so far" when the response gave no window."""
    return f"between {day(window[0])} and {day(window[1])}" if window else "so far"


def names(text: str, name: str | None) -> bool:
    """Whether the words name it: the whole name, or every word of it longer than two letters."""
    if not name:
        return False
    whole, words = normalise(name), {t for t in tokens(name) if len(t) > 2}
    return (len(whole) >= 3 and whole in normalise(text)) or bool(words and words <= set(tokens(text)))


def text_answer(title: str, text: str, *, authored: bool = False, facts: Facts | None = None,
                extra: list | None = None) -> LiveAnswer:
    """An answer that is a sentence and no figures: the card under it is empty."""
    return LiveAnswer(facts=facts or Facts(), card={"title": title, "items": []}, template=text,
                      extra=extra or [], authored=authored)


def entity_choice(follow_up_id: str, question: str, options: list[dict]) -> dict:
    """ "Which campaign?" with the user's own campaigns - the same block shape as a catalog
    follow-up (followups.Ask.as_block), so the panel renders one kind of question."""
    return {"id": follow_up_id, "kind": "entity_choice", "question": question, "options": options}


def couldnt(action: str, kind: str) -> str:
    """ "I couldn't read the wallet: the dashboard did not answer just now." The reason
    carries the timing, so the action never says "just now" itself."""
    return f"I couldn't {action}: {PROBLEM_TEXT.get(kind, kind)}."


def is_overview(text: str) -> bool:
    return bool(set(tokens(text)) & OVERVIEW_WORDS)


@dataclass(slots=True)
class LiveAnswer:
    facts: Facts                                   # for the narrator's prompt
    card: dict                                     # the metrics block the panel renders
    template: str                                  # the answer, written by code
    allowed: list[str] = field(default_factory=list)   # every figure the narrator may use
    # Blocks shown between the card and the words - e.g. a navigate link to one campaign.
    extra: list[tuple[str, str, dict]] = field(default_factory=list)
    # True when the words are authored text (an OTP refusal): a model never rewords them.
    authored: bool = False


def _caveats(ontology: Ontology, metric_ids: list[str], facts: Facts) -> None:
    for mid in metric_ids:
        m = ontology.metric(mid)
        if not m:
            continue
        # Definitions only. The ontology's gotchas were written for engineers
        # (payer rules, advisor thresholds); on a live card they read as noise.
        if m.definition:
            facts.add("definition", " ".join(m.definition.split()), metric_id=mid, label=m.label)


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
            sentences.append("That is the money your campaigns draw from. The main wallet "
                              "is separate, and only your admin can move money into yours.")
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
        sentences.append("That money is already allocated, so it can't be spent from the main wallet.")
    elif cap_id == "wallet.explain_runway":
        show = ["wallet_runway_days", "low_balance", "main_wallet_balance", "month_consumption"]
        run, main, spent, low = (f("wallet_runway_days"), f("main_wallet_balance"),
                                 f("month_consumption"), f("low_balance"))
        if run:
            sentences.append(f"At the recent spending rate, the main wallet lasts about {run.display}, "
                             f"as the dashboard calculates it.")
        elif spent and spent.value == 0:
            # runwayMonths is null when nothing was spent: no rate to project from.
            note = ("Runway isn't calculated: nothing has been spent this month, so there is no "
                    "rate to project from.")
            sentences.append(note)
            facts.add("live_value", note, metric_id="wallet_runway_days")
        if main and spent:
            sentences.append(f"It holds {main.display}, and {spent.display} has been spent this month.")
        if low and low.value is True:
            threshold = low.display.split("(")[-1].rstrip(")")
            sentences.append(f"It is below its low-balance threshold — {threshold} — "
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
                             + (f" across {count_of(n, 'manager')}" if n else "")
                             + " — already handed out, so you can't spend it.")
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
        facts.add("live_value", fact.fact_line(), metric_id=fact.metric_id, source=fact.source)
    # The managers go to the narrator too, not just the card: a model that only sees
    # the total says "I don't have a list of managers" while the card shows one.
    listed = view.managers[:10] if (cap_id == "wallet.explain_manager_wallets"
                                     and not own_subwallet) else []
    for m in listed:
        meta = m.as_meta()
        facts.add("live_value", f"{meta['name']}: balance {meta['balance'] or 'not reported'}"
                  + (f", allocated {meta['allocated']}" if meta["allocated"] else "")
                  + (f", spent {meta['spent']}" if meta["spent"] else ""),
                  metric_id="manager_balance", source="members/wallet-overview · members[]")
    _caveats(ontology, show, facts)

    kinds = sorted(set(view.problems.values()), key=lambda k: PROBLEM_TEXT.get(k, k))
    missing = [PROBLEM_TEXT.get(k, k) for k in kinds]
    if missing and not shown:
        sentences = [couldnt("read the wallet", kinds[0])]
    elif missing:
        sentences.append(f"Some figures are missing because {missing[0]}.")
    for reason in missing:
        facts.add("unavailable", reason)

    facts.prose_hint = LIVE_PROSE

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
