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

from ..ontology.models import Capability, FollowUp
from .lexical import tokens


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
ACROSS_MARKERS = frozenset("""
each all every both any everyone
best worst most least top bottom highest lowest
which who whose ranked
""".split())


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
    if follow_up.kind == "slot_fill":
        return True

    said = set(tokens(query))

    if follow_up.kind == "entity_choice":
        return not (said & ACROSS_MARKERS)

    siblings = [f for f in cap.follow_ups if f.kind == "disambiguation"]
    if len(siblings) <= 1:
        return True

    subject = set(tokens(follow_up.id.replace("_", " ")))
    for option in follow_up.options:
        subject |= set(tokens(option.label))
    subject -= {"which", "whose", "what"}
    return bool(said & subject) if subject else True


def decide(cap: Capability, *, screen: str | None,
           query: str = "",
           asked: dict[str, int] | None = None,
           already_known: frozenset[str] = frozenset()) -> Ask | None:
    """The first RELEVANT follow-up that still needs asking, or None.

    `asked` counts how many times each follow-up has fired this session, so
    max_asks is honoured across turns rather than within one.
    """
    asked = asked or {}
    for follow_up in cap.follow_ups:
        if follow_up.suppressed_on(screen):
            continue
        if follow_up.id in already_known:
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
        if not options and not follow_up.options_from:
            continue
        return Ask(follow_up, follow_up.question, options)
    return None


def default_for(follow_up: FollowUp) -> str | None:
    """What to assume once the asks are exhausted. None means do not assume."""
    return follow_up.default_after_asks
