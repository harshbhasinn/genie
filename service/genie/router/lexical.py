"""Rung 1.5 of the ladder: lexical match, no network.

Exists because NAVIGATE has a p95 budget of 200ms and an embedding call alone
costs more than that. "open the wallet" is not an authored utterance but
"open wallet management" is, and the two share every word that carries meaning.
Catching that here keeps navigation off the network entirely.

Deliberately dumb. It resolves paraphrase-by-omission, not synonymy — "show me
my credits" will not match "open billing" here, and should not: that is what the
vector rung is for, and pretending otherwise would route confidently on nothing.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Words that carry no routing signal. Kept short on purpose: an aggressive stop
# list makes "what is my reach" and "what is my spend" collide.
STOP = frozenset("""
a an the my our your me i we us
is are was were be been being am
do does did doing done
of to for in on at by from with about
please can could would should will shall
show tell give let get
""".split())

# Words that signal the SHAPE of the request rather than its subject. "Plan me a
# campaign" and "how is my campaign doing" share their only content word and mean
# entirely different things; nothing in token overlap can tell them apart, because
# the difference is the verb — and the verb is the part a stop list throws away.
NAVIGATE_CUES = frozenset("open go take navigate visit jump".split())

PLAN_CUES = frozenset("""
plan afford buy budget lakh lakhs crore cost costs spend
""".split())

DEFINITION_CUES = frozenset("""
mean means meaning explain define definition
difference differences versus vs between
""".split())

# Verbs that ask for something to HAPPEN. Every one is a write, and Genie holds
# no write paths — so these must reach a handoff and its authored refusal, not an
# explanation of the thing they wanted to do.
ACTION_CUES = frozenset("""
pause resume unpause cancel approve reject authorise authorize
topup recharge upload allocate withdraw resubmit
""".split())


def cue_modes(text: str) -> frozenset[str]:
    """Which genie_modes the shape of this query points at."""
    toks = frozenset(tokens(text)) | frozenset(normalise(text).split())
    out = set()
    if toks & NAVIGATE_CUES:
        out.add("navigate")
    if toks & PLAN_CUES:
        out.add("plan")
    if toks & DEFINITION_CUES:
        out.add("answer:definition")
    if toks & ACTION_CUES:
        out.add("handoff")
    return frozenset(out)

_PUNCT = re.compile(r"[^\w\s]+")
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    return [t for t in normalise(text).split() if t not in STOP]


@dataclass(frozen=True, slots=True)
class LexicalHit:
    capability_id: str
    utterance: str
    score: float


class LexicalIndex:
    """Token-overlap scoring over authored utterances.

    Scoring, in plain terms: of the meaningful words in the authored utterance,
    how many did the user say? Asymmetric on purpose — a user who says more than
    the utterance ("please just open wallet management for me now") should still
    match, while a user who says less than half of it should not.
    """

    def __init__(self, utterances: dict[str, str]):
        # utterance text -> capability id
        self._entries: list[tuple[str, str, frozenset[str], int]] = []
        for text, cap_id in utterances.items():
            toks = frozenset(tokens(text))
            if toks:
                self._entries.append((text, cap_id, toks, len(toks)))

    def search(self, query: str, *, limit: int = 60,
               floor: float = 0.6) -> list[LexicalHit]:
        q_tokens = frozenset(tokens(query))
        if not q_tokens:
            return []
        hits: list[LexicalHit] = []
        for text, cap_id, u_tokens, u_len in self._entries:
            shared = len(q_tokens & u_tokens)
            if not shared:
                continue
            # Coverage of the UTTERANCE, penalised slightly when the query is
            # much longer — a long query that happens to contain three common
            # words is a weaker signal than a short exact-ish one.
            coverage = shared / u_len
            noise = shared / len(q_tokens)
            score = coverage * (0.75 + 0.25 * noise)
            if score >= floor:
                hits.append(LexicalHit(cap_id, text, round(score, 4)))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def looks_like_navigation(self, query: str) -> bool:
        return bool(frozenset(tokens(query)) & NAVIGATE_CUES)
