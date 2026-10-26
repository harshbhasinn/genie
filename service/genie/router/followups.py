"""Whether to ask, and what.

The rule that matters: a follow-up listed in `resolved_by_context` does NOT fire
when the user is on one of those screens. "What is my wallet balance" on the
Command Centre must ask which of three; the same words on Wallet Management must
not, because that screen shows all three side by side.

That difference is the entire argument for embedding Genie in the dashboard
rather than putting it in a chat window, so it is enforced here rather than left
to a prompt.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ontology import Ontology
from ..ontology.models import Capability, FollowUp, Mode
from .answers import navigate_resolved
from .lexical import normalise, tokens


@dataclass(frozen=True, slots=True)
class Ask:
    follow_up: FollowUp
    question: str
    options: tuple[dict[str, str | None], ...]

    def as_block(self) -> dict:
        return {
            "id": self.follow_up.id,
            "kind": self.follow_up.kind,
            "question": self.question,
            "options": [dict(o) for o in self.options],
        }


# Words that say the user is asking ACROSS the entities, not about one of them.
# "How much has EACH manager spent" and "WHO has budget left" are questions about
# all of them; answering "which manager?" is answering a question nobody asked.
# A stated period answers a scope follow-up before it is asked.
PERIOD_WORDS = frozenset("""
today yesterday tomorrow week month quarter year
daily weekly monthly quarterly yearly annual
jan feb mar apr may jun jul aug sep oct nov dec
january february march april june july august september october november december
""".split())

ACROSS_MARKERS = frozenset("""
each all every both any everyone
best worst most least top bottom highest lowest
which who whose ranked
""".split())

# Words asking for the WHOLE picture. "Explain my wallet position" wants every
# balance, so "which balance do you mean?" answers a question nobody asked.
OVERVIEW_WORDS = frozenset(tokens(
    "position overview summary stand standing snapshot breakdown overall everything picture"))

# Only where the answer really does show every option at once. The wallet's live
# answer carries all three balances on one card; "campaign summary" still has to
# ask which campaigns, so this must not reach campaign.list or explain.metric.
OVERVIEW_ANSWERS = frozenset({"wallet.explain_balance"})


def _is_relevant(follow_up: FollowUp, cap: Capability, query: str) -> bool:
    """Should this follow-up fire for these words?

    Three kinds, three rules, because they fail differently:

    SLOT_FILL asks for something genuinely missing — a date, a budget. Nothing
    in the query can make it irrelevant, because its absence is the point.

    ENTITY_CHOICE narrows to one of many. It is irrelevant when the user is
    asking across all of them: "who has budget left" wants every manager, and
    "which brand is performing best" wants the ranking, not one brand.

    DISAMBIGUATION resolves one word meaning several things. When a capability
    has only ONE, it is that capability's ambiguity and fires whenever the
    capability does — "how much money do I have" never says "wallet" but is
    exactly the three-balance question. When a capability has SEVERAL, ordering
    would otherwise decide, so the query must touch the right one's subject:
    explain.metric asked "which utilisation?" for "what's my reach" purely
    because which_utilization is listed first.
    """
    said = set(tokens(query))

    if follow_up.kind == "slot_fill":
        return True

    if follow_up.kind == "scope":
        return not (said & PERIOD_WORDS)

    if follow_up.kind == "entity_choice":
        if said & ACROSS_MARKERS:
            return False
        noun = next((w for w in tokens(follow_up.id.replace("_", " "))
                     if w not in {"which", "whose", "what"}), None)
        raw = normalise(query).split()
        # "I" is capitalised and names nobody. Without this, "how much revenue
        # did I book this month" looked like it referred to a specific brand.
        named = any(w[:1].isupper() and w.strip(".,!?'\"").lower() not in ("i", "i'm", "i've")
                    and len(w.strip(".,!?'\"")) > 1
                    for w in query.split()[1:])

        # Whether an unnamed entity NEEDS asking depends on what the capability
        # is going to do with it.
        #
        # NAVIGATE, PLAN and PREFILL have to land on exactly one: you cannot
        # open "the creatives" or price a campaign for no brand. Silence there
        # means the slot is empty and must be filled.
        #
        # ANSWER can legitimately aggregate. "How many impressions did we serve"
        # spans everything, and asking which campaign answers a question nobody
        # posed — while "how is my campaign doing" names one and should ask.
        if cap.mode.value in ("navigate", "plan", "prefill"):
            return not named
        if noun and (noun + "s" in raw or noun + "es" in raw):
            return False          # plural: the question is about all of them
        return bool(noun and noun in raw) or named

    # A disambiguation splits one word into several meanings; a request for the
    # overview wants all of them, so there is nothing to choose between.
    if cap.id in OVERVIEW_ANSWERS and said & OVERVIEW_WORDS:
        return False

    siblings = [f for f in cap.follow_ups if f.kind == "disambiguation"]
    if len(siblings) <= 1:
        return True

    subject = set(tokens(follow_up.id.replace("_", " ")))
    for option in follow_up.options:
        subject |= set(tokens(option.label))
    subject -= {"which", "whose", "what"}
    return bool(said & subject) if subject else True


def _settled(cap: Capability, ontology: Ontology | None,
             brand_id: str | None, campaign_id: str | None) -> frozenset[str]:
    """entity_choice ids that have nothing left to choose.

    A navigate capability whose target route is already complete - either it
    takes no parameter, or the panel already named the entity - does not need
    to ask which one.
    """
    if ontology is None or cap.mode is not Mode.NAVIGATE:
        return frozenset()
    if not navigate_resolved(cap, ontology, brand_id=brand_id,
                             campaign_id=campaign_id):
        return frozenset()
    return frozenset(f.id for f in cap.follow_ups if f.kind == "entity_choice")


def decide(cap: Capability, *, screen: str | None,
           query: str = "",
           asked: dict[str, int] | None = None,
           already_known: frozenset[str] = frozenset(),
           ontology: Ontology | None = None,
           brand_id: str | None = None,
           campaign_id: str | None = None) -> Ask | None:
    """The first RELEVANT follow-up that still needs asking, or None.

    `asked` counts how many times each follow-up has fired this session, so
    max_asks is honoured across turns rather than within one.

    `ontology` lets a NAVIGATE entity_choice check whether it has anything to
    choose: /brand-management is the list of brands, so "open brand management"
    needs no brand and asking for one is noise. This lived in the responder for
    one commit, which meant the golden runner called decide() directly and got
    the old answer - the rule belongs with the decision, not with one caller.
    """
    asked = asked or {}
    settled = _settled(cap, ontology, brand_id, campaign_id) | already_known
    for follow_up in cap.follow_ups:
        if follow_up.suppressed_on(screen):
            continue
        if follow_up.id in settled:
            continue
        if query and not _is_relevant(follow_up, cap, query):
            continue
        if asked.get(follow_up.id, 0) >= follow_up.max_asks:
            # Out of asks. The catalog says what to assume instead — and where
            # it says nothing, we stop rather than guess.
            continue
        options = tuple(
            {"value": o.value, "label": o.label, "hint": o.hint}
            for o in follow_up.options
        )
        # A slot_fill asks for a value the user must supply — dates, a budget —
        # so having no options is its normal shape, not a defect. Skipping it
        # meant "can I book 500 screens in Delhi" never asked for the window.
        if not options and not follow_up.options_from                 and follow_up.kind != "slot_fill":
            continue
        return Ask(follow_up, follow_up.question, options)
    return None


def default_for(follow_up: FollowUp) -> str | None:
    """What to assume once the asks are exhausted. None means do not assume."""
    return follow_up.default_after_asks
