"""The retrieval ladder. Each rung costs more than the one above it.

  L-1   exact utterance       dict lookup           ~0.01 ms   no network
  L-1.5 lexical overlap       token sets            ~1 ms      no network
  L-2   vector similarity     244 x 3072 matmul     ~250 ms    one embed call
  L-3   model disambiguation  closed-list choice    ~1 s       one generate call

The ladder exists because routing wrong is worse than routing slowly, and because
NAVIGATE has a 200 ms p95 budget that a single embedding call would blow on its
own. Most navigation is authored, so it never leaves the first two rungs.

The model, when it is used at all, picks from a CLOSED LIST of candidates that
have already passed role and permission filtering. It never invents a capability
and it never sees one the user is not allowed to use.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..llm.base import LLMError, Message, Provider
from ..ontology import Ontology
from ..ontology.models import Capability, Status
from .embeddings import CapabilityIndex
from .lexical import LexicalIndex, cue_modes, is_question

log = logging.getLogger(__name__)

# A vector lead this large over the runner-up means the model adds nothing.
DECISIVE_MARGIN = 0.08
# Below this, no candidate is worth routing to at all.
MIN_CONFIDENCE = 0.55
# A LONE candidate needs a higher bar than a leading one. "What's the weather in
# Delhi" matches inventory.check_availability at 0.61 with nothing behind it —
# an uncontested weak match looks decisive and is not. Below this, the model
# gets to veto, and answering 0 (none of these) is a valid outcome.
LONE_CANDIDATE_FLOOR = 0.78
# Capabilities whose home screen the user is already on get this much of a nudge.
CONTEXT_BOOST = 0.05
# And capabilities whose MODE matches the shape of the request get this much.
# Larger than the context boost because the verb is a stronger signal than the
# screen: someone on Campaign Detail saying "plan me a campaign" wants the
# planner, not the campaign they are looking at.
CUE_BOOST = 0.09
# And a candidate whose mode CONTRADICTS the shape of the request loses this
# much. Symmetry matters: a scorer that only rewards agreement lets a plausible
# but wrong-shaped capability win whenever its wording happens to be close.
CUE_PENALTY = 0.14
# A capability the role cannot use still RANKS — it just ranks below one the
# role can. Excluding it outright was the third appearance of one mistake:
# blocked, then role, then permission, each time turning "you cannot do that"
# into "I don't understand you", and in the navigate case silently opening a
# DIFFERENT screen. Saying no is an answer; substituting is not.
DENIED_PENALTY = 0.20


@dataclass(slots=True)
class Candidate:
    capability: Capability
    score: float
    via: str
    denied: bool = False

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"{self.capability.id}@{self.score:.3f}({self.via})"


@dataclass(slots=True)
class Route:
    capability: Capability | None = None
    confidence: float = 0.0
    method: str = "none"
    candidates: list[Candidate] = field(default_factory=list)
    reason: str | None = None
    denied: bool = False

    @property
    def routed(self) -> bool:
        return self.capability is not None


class IntentRouter:
    def __init__(self, ontology: Ontology, provider: Provider | None,
                 index: CapabilityIndex | None = None):
        self._ontology = ontology
        self._provider = provider
        self._index = index
        utterances = {u: c.id for c in ontology.capabilities.values()
                      for u in c.utterances}
        self._lexical = LexicalIndex(utterances)

    # ── permission ─────────────────────────────────────────────────────
    def _denied_for(self, cap: Capability, roles: frozenset[str],
                    permissions: frozenset[str]) -> bool:
        """Can this user actually use it? Ranking, not filtering.

        UNKNOWN is still not DENIED: until S4 wires /api/v1/me there is no
        permission set at all, and an empty one must mean "not asked yet".
        """
        if roles and cap.authorization.roles:
            if not (set(cap.authorization.roles) & roles):
                return True
        if permissions and cap.authorization.gate == "PERMISSION":
            if cap.authorization.permission:
                return cap.authorization.permission not in permissions
        return False

    def _names_a_metric(self, text: str, screen: str | None):
        """Does the query contain a metric's name? Returns (metric, score).

        Same exact-lookup discipline as everything else about metrics — never a
        vector search, because "budget utilization" and "batch utilization" sit
        within noise of each other on any embedding model.
        """
        from .answers import resolve_metric_scored
        metric, quality = resolve_metric_scored(text, self._ontology, screen=screen)
        if metric is None:
            return None, 0.0
        # Scale with how well the NAME matched. A flat 0.72 meant this signal
        # fired once in 106 queries and lost the rest by hundredths: "what's my
        # share of voice" names share_of_voice exactly and still lost to a
        # topical capability at 0.73.
        return metric, round(0.55 + 0.40 * quality, 4)

    @staticmethod
    def _contradicts_shape(cap: Capability, cues: frozenset[str],
                           interrogative: bool) -> bool:
        """Is this candidate the wrong SHAPE for what was asked?

        Only navigation is tested, because it is the mode whose utterances sit
        closest to everything else: "take me to the wallet" embeds near any
        sentence about wallets, so it wins on similarity while being the wrong
        kind of answer. A question is not a navigation request, and neither is a
        sentence whose verb asks for an action or a plan.
        """
        if cap.mode.value != "navigate" or "navigate" in cues:
            return False
        return interrogative or bool(cues & {"handoff", "plan"})

    @staticmethod
    def _matches_cue(cap: Capability, cues: frozenset[str]) -> bool:
        mode = cap.mode.value
        if mode in cues:
            return True
        # A definitional cue points at the capabilities that own meanings, not at
        # every capability that happens to answer something.
        return "answer:definition" in cues and cap.id.startswith("explain.")

    def _contextual(self, cap: Capability, screen: str | None) -> bool:
        """Is this capability at home on the screen the user is looking at?"""
        if not screen:
            return False
        if cap.target_screen == screen:
            return True
        current = self._ontology.screen(screen)
        if current is None:
            return False
        if any(a.capability == cap.id for a in current.actions):
            return True
        if cap.answers_with:
            on_screen = {m.id for m in current.metrics}
            return bool(on_screen & set(cap.answers_with))
        return False

    def _rank(self, raw: list[tuple[str, float, str]], *, screen: str | None,
              roles: frozenset[str], permissions: frozenset[str],
              cues: frozenset[str] = frozenset(),
              interrogative: bool = False) -> list[Candidate]:
        """Collapse hits to one per capability, filter, boost, sort."""
        best: dict[str, tuple[float, str]] = {}
        for cap_id, score, via in raw:
            if cap_id not in best or score > best[cap_id][0]:
                best[cap_id] = (score, via)

        out: list[Candidate] = []
        for cap_id, (score, via) in best.items():
            cap = self._ontology.capability(cap_id)
            if cap is None:
                continue
            denied = self._denied_for(cap, roles, permissions)
            if denied:
                score -= DENIED_PENALTY
                via += "-denied"
            if self._contextual(cap, screen):
                score += CONTEXT_BOOST
                via += "+context"
            if cues and self._matches_cue(cap, cues):
                score += CUE_BOOST
                via += "+cue"
            elif self._contradicts_shape(cap, cues, interrogative):
                score -= CUE_PENALTY
                via += "-shape"
            out.append(Candidate(cap, round(min(score, 1.0), 4), via, denied))
        out.sort(key=lambda c: -c.score)
        return out

    # ── the ladder ─────────────────────────────────────────────────────
    async def route(self, text: str, *, screen: str | None = None,
                    roles: frozenset[str] = frozenset(),
                    permissions: frozenset[str] = frozenset()) -> Route:

        # L-1 — exact. Deterministic, and the only rung the golden set can
        # assert on without a network.
        exact = self._ontology.exact_utterance(text)
        if exact is not None:
            denied = self._denied_for(exact, roles, permissions)
            return Route(exact, 1.0, "exact",
                         [Candidate(exact, 1.0, "exact", denied)], denied=denied)

        # L-1.5 — lexical, plus one structural signal: if the words name a
        # metric in the ontology, "what does X mean" is a live reading of them,
        # and explain.metric is the capability that owns definitions. Scored by
        # how well the name matched, so it competes rather than overrides.
        cues = cue_modes(text)
        interrogative = is_question(text)
        lex = [(h.capability_id, h.score, "lexical")
               for h in self._lexical.search(text)]
        named, strength = self._names_a_metric(text, screen)
        if named is not None and not (cues & {"navigate", "handoff", "plan"}):
            lex.append(("explain.metric", strength, "metric-name"))
        ranked = self._rank(lex, screen=screen, roles=roles, permissions=permissions,
                            cues=cues, interrogative=interrogative)
        if ranked and self._decisive(ranked):
            return Route(ranked[0].capability, ranked[0].score, "lexical", ranked,
                         denied=ranked[0].denied)

        # L-2 — vector. Merged with the lexical hits rather than replacing them:
        # the two disagree in useful ways, and a capability both rungs like is a
        # stronger signal than either alone.
        if self._index is not None and self._index.ready and self._provider:
            vec = [(h.capability_id, h.score, "vector")
                   for h in await self._index.search(self._provider, text)]
            if vec:
                ranked = self._rank(lex + vec, screen=screen, roles=roles,
                                    permissions=permissions, cues=cues,
                                    interrogative=interrogative)

        if not ranked:
            return Route(reason="nothing matched above the confidence floor")
        if self._decisive(ranked):
            return Route(ranked[0].capability, ranked[0].score,
                         ranked[0].via.split("+")[0].split("-")[0], ranked,
                         denied=ranked[0].denied)

        # L-3 — the model breaks the tie, choosing from a closed list.
        if self._provider is not None:
            chosen = await self._ask_model(text, ranked[:5], screen)
            if chosen is not None:
                return Route(chosen.capability, chosen.score, "llm", ranked,
                                 denied=chosen.denied)

        top = ranked[0]
        if top.score >= MIN_CONFIDENCE:
            return Route(top.capability, top.score, top.via.split("+")[0].split("-")[0],
                         ranked, reason="ambiguous, took the leader",
                         denied=top.denied)
        return Route(candidates=ranked, reason="no candidate cleared the floor")

    @staticmethod
    def _decisive(ranked: list[Candidate]) -> bool:
        if not ranked or ranked[0].score < MIN_CONFIDENCE:
            return False
        if len(ranked) == 1:
            return ranked[0].score >= LONE_CANDIDATE_FLOOR
        return (ranked[0].score - ranked[1].score) >= DECISIVE_MARGIN

    async def _ask_model(self, text: str, options: list[Candidate],
                         screen: str | None) -> Candidate | None:
        numbered = "\n".join(
            f"{i}. {c.capability.id} — {c.capability.label}: "
            f"{(c.capability.description or '').strip()[:140]}"
            for i, c in enumerate(options, 1))
        where = f"\nThe user is on the {screen} screen." if screen else ""
        prompt = (
            f'A user of an advertising dashboard said: "{text}"{where}\n\n'
            f"Which ONE of these does it mean?\n{numbered}\n\n"
            f"Reply with the number alone, or 0 if none of them fit. "
            f"No explanation.")
        try:
            chunks = []
            async for chunk in self._provider.stream(
                [Message(role="user", text=prompt)],
                system="You classify intent. You reply with a single digit and nothing else.",
                # Not 8: a reasoning model spends its budget before the first
                # visible character, and this rung answered empty 20/20.
                temperature=0.0, max_output_tokens=512,
            ):
                chunks.append(chunk)
        except LLMError as err:
            log.warning("disambiguation call failed: %s", err)
            return None

        answer = "".join(chunks).strip()
        if not answer:
            log.warning("disambiguation returned nothing for %r", text[:40])
            return None
        for token in answer.split():
            cleaned = token.strip(".,:;()[]")
            if cleaned.isdigit():
                pick = int(cleaned)
                if pick == 0:
                    return None
                if 1 <= pick <= len(options):
                    return options[pick - 1]
                return None
        log.warning("disambiguation returned %r, ignoring", answer[:40])
        return None
