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

import math
import re
import unicodedata
from collections import Counter
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
top topup recharge upload allocate withdraw resubmit
""".split())

# Navigation is IMPERATIVE. "Open the approvals queue" is a request to go
# somewhere; "why can't I launch this campaign" and "who saw this campaign" are
# questions that merely mention a thing you could navigate to. Without this,
# campaign.open and navigate.wallet won nine queries on vector similarity alone,
# because "take me to the wallet" is semantically near anything about wallets.
INTERROGATIVES = frozenset("""
why who what how when where which whose
is are was were do does did can could will would should am
""".split())


def is_question(text: str) -> bool:
    return bool(frozenset(normalise(text).split()) & INTERROGATIVES)


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


def _singular(token: str) -> str:
    """Crudest possible stemmer, and enough. "how many SCREENS do I have" must
    reach which_screen_COUNT, and the plural was the only thing in the way."""
    if len(token) > 3 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es") and token[-3] in "sxzh":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokens(text: str) -> list[str]:
    return [_singular(t) for t in normalise(text).split() if t not in STOP]


def subject_tokens(text: str) -> list[str]:
    """Tokens minus navigation verbs, for SCORING.

    "open billing", "go to wallet management" and "take me to credits" all share
    their verb and nothing else. Keeping it made every navigate capability tie at
    the same score on any "open X" query, and the tie-break then went to
    whichever happened to sort first.

    Only navigation verbs are dropped. "plan" stays, because in "plan a new
    campaign" it is the subject as much as the intent.
    """
    return [t for t in tokens(text) if t not in NAVIGATE_CUES]


@dataclass(frozen=True, slots=True)
class LexicalHit:
    capability_id: str
    utterance: str
    score: float


class LexicalIndex:
    """Token-overlap scoring over authored utterances, weighted by rarity.

    Plain coverage — "how many of the utterance's words did the user say?" —
    breaks once navigation verbs are stripped, because "open the campaign"
    collapses to the single token {campaign} and then ANY sentence mentioning a
    campaign matches it perfectly. campaign.open swallowed seven other
    capabilities that way.

    So each token is weighted by inverse document frequency across the whole
    utterance set. "campaign" appears in dozens of phrasings and discriminates
    almost nothing; "utilisation", "approvals" and "daypart" appear in one or two
    and discriminate almost everything. A word shared with every capability
    should not be evidence for any of them.
    """

    def __init__(self, utterances: dict[str, str]):
        self._entries: list[tuple[str, str, frozenset[str], float]] = []
        prepared = []
        seen: Counter[str] = Counter()
        for text, cap_id in utterances.items():
            toks = frozenset(subject_tokens(text))
            if toks:
                prepared.append((text, cap_id, toks))
                seen.update(toks)

        total = max(len(prepared), 1)
        # +1 inside the log keeps the weight positive for a token that appears
        # everywhere, rather than zeroing it and making the utterance unmatchable.
        self._idf = {tok: math.log(1 + total / count) for tok, count in seen.items()}

        for text, cap_id, toks in prepared:
            weight = sum(self._idf.get(t, 1.0) for t in toks)
            self._entries.append((text, cap_id, toks, weight))

    def _weight(self, toks) -> float:
        return sum(self._idf.get(t, 1.0) for t in toks)

    def search(self, query: str, *, limit: int = 60,
               floor: float = 0.6) -> list[LexicalHit]:
        q_tokens = frozenset(subject_tokens(query))
        if not q_tokens:
            return []
        q_weight = self._weight(q_tokens) or 1.0
        hits: list[LexicalHit] = []
        for text, cap_id, u_tokens, u_weight in self._entries:
            shared = q_tokens & u_tokens
            if not shared:
                continue
            shared_weight = self._weight(shared)
            # Coverage of the UTTERANCE by weight, damped slightly when the
            # query carries a lot the utterance does not account for.
            coverage = shared_weight / u_weight
            noise = shared_weight / q_weight
            score = coverage * (0.75 + 0.25 * noise)
            if score >= floor:
                hits.append(LexicalHit(cap_id, text, round(score, 4)))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def looks_like_navigation(self, query: str) -> bool:
        return bool(frozenset(tokens(query)) & NAVIGATE_CUES)
