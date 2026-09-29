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


def decide(cap: Capability, *, screen: str | None,
           asked: dict[str, int] | None = None,
           already_known: frozenset[str] = frozenset()) -> Ask | None:
    """The first follow-up that still needs asking, or None.

    `asked` counts how many times each follow-up has fired this session, so
    max_asks is honoured across turns rather than within one.
    """
    asked = asked or {}
    for follow_up in cap.follow_ups:
        if follow_up.suppressed_on(screen):
            continue
        if follow_up.id in already_known:
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
