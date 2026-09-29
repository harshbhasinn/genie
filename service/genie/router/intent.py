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
from .lexical import LexicalIndex

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


@dataclass(slots=True)
class Candidate:
    capability: Capability
    score: float
    via: str

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"{self.capability.id}@{self.score:.3f}({self.via})"


@dataclass(slots=True)
class Route:
    capability: Capability | None = None
    confidence: float = 0.0
    method: str = "none"
    candidates: list[Candidate] = field(default_factory=list)
    reason: str | None = None

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
    def _allowed(self, cap: Capability, roles: frozenset[str],
                 permissions: frozenset[str]) -> bool:
        """UNKNOWN is not DENIED.

        Until S4 wires /api/v1/me, the caller has no permission set at all. An
        empty set must mean 'we do not know yet', not 'this user has none' —
        reading it the second way silently filters out every PERMISSION-gated
        capability, which is most of them, and the router then answers nothing
        while looking like it simply failed to match.

        Roles are different: when a role IS claimed, a capability restricted to
        other roles is genuinely not for this user, and g075 and g086 depend on
        that being enforced.
        """
        if cap.status is Status.BLOCKED:
            return False
        if roles and cap.authorization.roles:
            if not (set(cap.authorization.roles) & roles):
                return False
        if permissions and cap.authorization.gate == "PERMISSION":
            if cap.authorization.permission:
                return cap.authorization.permission in permissions
        return True

    def _names_a_metric(self, text: str, screen: str | None):
        """Does the query contain a metric's name? Returns (metric, score).

        Uses the same exact-lookup discipline as everything else about metrics —
        never a vector search, because 'budget utilization' and 'batch
        utilization' are within noise of each other on any embedding model.
        """
        from .answers import resolve_metric
        metric = resolve_metric(text, self._ontology, screen=screen)
        if metric is None:
            return None, 0.0
        return metric, 0.72

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
              roles: frozenset[str], permissions: frozenset[str]) -> list[Candidate]:
        """Collapse hits to one per capability, filter, boost, sort."""
        best: dict[str, tuple[float, str]] = {}
        for cap_id, score, via in raw:
            if cap_id not in best or score > best[cap_id][0]:
                best[cap_id] = (score, via)

        out: list[Candidate] = []
        for cap_id, (score, via) in best.items():
            cap = self._ontology.capability(cap_id)
            if cap is None or not self._allowed(cap, roles, permissions):
                continue
            if self._contextual(cap, screen):
                score += CONTEXT_BOOST
                via += "+context"
            out.append(Candidate(cap, round(min(score, 1.0), 4), via))
        out.sort(key=lambda c: -c.score)
        return out

    # ── the ladder ─────────────────────────────────────────────────────
    async def route(self, text: str, *, screen: str | None = None,
                    roles: frozenset[str] = frozenset(),
                    permissions: frozenset[str] = frozenset()) -> Route:

        # L-1 — exact. Deterministic, and the only rung the golden set can
        # assert on without a network.
        exact = self._ontology.exact_utterance(text)
        if exact and self._allowed(exact, roles, permissions):
            return Route(exact, 1.0, "exact",
                         [Candidate(exact, 1.0, "exact")])

        # L-1.5 — lexical, plus one structural signal: if the words name a
        # metric in the ontology, "what does X mean" is a live reading of them,
        # and explain.metric is the capability that owns definitions. Scored by
        # how well the name matched, so it competes rather than overrides.
        lex = [(h.capability_id, h.score, "lexical")
               for h in self._lexical.search(text)]
        named, strength = self._names_a_metric(text, screen)
        if named is not None:
            lex.append(("explain.metric", strength, "metric-name"))
        ranked = self._rank(lex, screen=screen, roles=roles, permissions=permissions)
        if ranked and self._decisive(ranked):
            return Route(ranked[0].capability, ranked[0].score, "lexical", ranked)

        # L-2 — vector. Merged with the lexical hits rather than replacing them:
        # the two disagree in useful ways, and a capability both rungs like is a
        # stronger signal than either alone.
        if self._index is not None and self._index.ready and self._provider:
            vec = [(h.capability_id, h.score, "vector")
                   for h in await self._index.search(self._provider, text)]
            if vec:
                ranked = self._rank(lex + vec, screen=screen, roles=roles,
                                    permissions=permissions)

        if not ranked:
            return Route(reason="nothing matched above the confidence floor")
        if self._decisive(ranked):
            return Route(ranked[0].capability, ranked[0].score,
                         ranked[0].via.split("+")[0], ranked)

        # L-3 — the model breaks the tie, choosing from a closed list.
        if self._provider is not None:
            chosen = await self._ask_model(text, ranked[:5], screen)
            if chosen is not None:
                return Route(chosen.capability, chosen.score, "llm", ranked)

        top = ranked[0]
        if top.score >= MIN_CONFIDENCE:
            return Route(top.capability, top.score, top.via.split("+")[0], ranked,
                         reason="ambiguous, took the leader")
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
                temperature=0.0, max_output_tokens=8,
            ):
                chunks.append(chunk)
        except LLMError as err:
            log.warning("disambiguation call failed: %s", err)
            return None

        answer = "".join(chunks).strip()
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
