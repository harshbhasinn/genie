"""Building EXPLAIN and NAVIGATE answers out of the ontology.

Definitions are served VERBATIM from the YAML, never paraphrased and never
retrieved by similarity. The catalog says why, and it is worth repeating:
"budget utilization" and "batch utilization" score within noise of each other on
any embedding model, and the wrong answer is confidently wrong about a number on
the user's screen.

So the model never writes a definition. It may add connective tissue around one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ontology import Ontology
from ..ontology.models import Capability, Metric, Screen, Status
from .lexical import tokens


@dataclass(slots=True)
class Facts:
    """Everything true that the service found. The renderer decides what to show."""
    blocks: list[dict] = field(default_factory=list)
    prose_hint: str | None = None

    def add(self, kind: str, content: str, **meta) -> None:
        self.blocks.append({"type": kind, "content": content, "meta": meta or None})


# ── metric resolution ───────────────────────────────────────────────────

def resolve_metric(text: str, ontology: Ontology, *, screen: str | None,
                   visible: tuple[str, ...] = ()) -> Metric | None:
    """Which metric is the user asking about?

    Screen context first, because a metric the user can see is far likelier than
    one they cannot. Then label matching. Never a vector search.
    """
    q = frozenset(tokens(text))
    if not q:
        return None

    def score(metric: Metric) -> float:
        label = frozenset(tokens(metric.label))
        ident = frozenset(tokens(metric.id.replace("_", " ")))
        target = label | ident
        if not target:
            return 0.0
        shared = len(q & target)
        return shared / len(target) if shared else 0.0

    pools: list[list[Metric]] = []
    if visible:
        pools.append([m for mid in visible if (m := ontology.metric(mid))])
    if screen and (current := ontology.screen(screen)):
        pools.append(list(current.metrics))
    pools.append(list(ontology.metrics.values()))

    for pool in pools:
        ranked = sorted(((score(m), m) for m in pool), key=lambda p: -p[0])
        if ranked and ranked[0][0] >= 0.5:
            # A tie between two metrics in the same pool is a real ambiguity —
            # which_utilization exists for exactly this. Let the follow-up run.
            if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 1e-9:
                return None
            return ranked[0][1]
    return None


def metric_facts(metric: Metric) -> Facts:
    facts = Facts()
    if metric.definition:
        facts.add("definition", " ".join(metric.definition.split()),
                  metric_id=metric.id, label=metric.label, unit=metric.unit,
                  verbatim=True)
    if metric.formula:
        facts.add("formula", metric.formula, metric_id=metric.id)

    if metric.status is Status.BLOCKED:
        note = " ".join((metric.blocked_note or "").split())
        facts.add("unavailable", note or "No endpoint returns this.",
                  metric_id=metric.id, blocked_by=list(metric.blocked_by))
        facts.prose_hint = (
            "Explain what it means, then say plainly that the FIGURE is not "
            "available — the definition is known, the data is not served.")
    else:
        facts.prose_hint = (
            "Explain what it means. You do NOT have the current value, so do "
            "not state one.")

    for gotcha in metric.gotchas[:3]:
        text = " ".join(gotcha.split())
        # Internal provenance tags are for us, not the user.
        if text.startswith(("POLICY 2026", "R3", "CONTRACT v3.1")):
            continue
        facts.add("caveat", text, metric_id=metric.id)
    return facts


# ── screen and capability explanation ───────────────────────────────────

def screen_facts(screen: Screen, ontology: Ontology, role: str | None) -> Facts:
    facts = Facts()
    facts.add("screen", screen.purpose, screen_id=screen.id, title=screen.title,
              route=screen.route)
    servable = [m.label for m in screen.metrics if m.servable]
    blocked = [m.label for m in screen.metrics if m.status is Status.BLOCKED]
    if servable:
        facts.add("metric_list", ", ".join(servable), count=len(servable))
    if blocked:
        facts.add("unavailable_list", ", ".join(blocked), count=len(blocked))
    actions = [a for a in screen.actions if a.status is not Status.BLOCKED]
    if actions:
        facts.add("actions", ", ".join(a.label for a in actions),
                  otp_gated=[a.label for a in actions if a.otp_gated])
    facts.prose_hint = "Describe what this screen is for and what can be done here."
    return facts


def capability_facts(ontology: Ontology, roles: frozenset[str],
                     permissions: frozenset[str]) -> Facts:
    """Genie describing its own scope — filtered, so the list is true for whoever asks."""
    facts = Facts()
    by_mode: dict[str, list[str]] = {}
    for cap in ontology.capabilities.values():
        if cap.status is Status.BLOCKED:
            continue
        if roles and not cap.permitted_for(roles, permissions):
            continue
        by_mode.setdefault(cap.mode.value, []).append(cap.label)
    for mode, labels in sorted(by_mode.items()):
        facts.add("can", ", ".join(sorted(labels)[:12]), mode=mode,
                  count=len(labels))
    facts.prose_hint = (
        "Say what you can do, and be specific about the limits: you plan and "
        "fill in forms, the user submits, and anything needing an OTP has to be "
        "them. Do not just say 'I can help with campaigns'.")
    return facts


# ── navigation ──────────────────────────────────────────────────────────

def navigate_facts(cap: Capability, ontology: Ontology,
                   role: str | None) -> Facts:
    """No model involved. This is the path with a 200 ms budget."""
    facts = Facts()
    target_id = cap.target_screen
    screen = ontology.screen(target_id) if target_id else None
    if screen is None:
        facts.add("error", f"{cap.label} has no target screen recorded.")
        return facts

    if role and not screen.visible_to(role):
        # Deep-linking into a 403 is worse than saying no. This is the g086 case.
        facts.add("denied", f"{screen.title} is not available to {role}.",
                  screen_id=screen.id, role=role)
        facts.prose_hint = "Say this screen is not in their navigation, briefly."
        return facts

    facts.add("navigate", screen.route, screen_id=screen.id, title=screen.title,
              label=cap.label)
    return facts


def blocked_capability_facts(cap: Capability) -> Facts:
    """Routed correctly, but cannot be completed yet. Say which, and why."""
    facts = Facts()
    facts.add("capability_blocked",
              f"{cap.label} is understood but not available yet.",
              capability=cap.id, blocked_by=list(cap.blocked_by))
    facts.prose_hint = (
        "You understood the request. Say what it WOULD do, then say plainly that "
        "it is not available yet and why — do not pretend to do it, and do not "
        "act as though you failed to understand.")
    return facts


def handoff_facts(cap: Capability) -> Facts:
    """OTP-gated and other writes. The refusal text is AUTHORED, not generated,
    so an OTP refusal reads the same every time."""
    facts = Facts()
    refusal = " ".join((cap.refusal or "").split())
    facts.add("refusal", refusal or
              f"{cap.label} has to be done by you — I hold no write access.",
              capability=cap.id, otp_gated=cap.otp_gated, verbatim=True)
    if cap.target_screen:
        facts.add("navigate_offer", cap.target_screen, label=cap.label)
    facts.prose_hint = None  # the authored refusal is the answer; do not embellish
    return facts
