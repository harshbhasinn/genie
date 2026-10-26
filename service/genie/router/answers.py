"""Building EXPLAIN and NAVIGATE answers out of the ontology.

Definitions are served VERBATIM from the YAML, never paraphrased and never
retrieved by similarity. The catalog says why, and it is worth repeating:
"budget utilization" and "batch utilization" score within noise of each other on
any embedding model, and the wrong answer is confidently wrong about a number on
the user's screen.

So the model never writes a definition. It may add connective tissue around one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ontology import Ontology
from ..ontology.models import Capability, Metric, Screen, Status
from ..roles import a_label

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
    metric, _ = resolve_metric_scored(text, ontology, screen=screen, visible=visible)
    return metric


def resolve_metric_scored(text: str, ontology: Ontology, *, screen: str | None,
                          visible: tuple[str, ...] = ()) -> tuple[Metric | None, float]:
    """Which metric is the user asking about?

    Screen context first, because a metric the user can see is far likelier than
    one they cannot. Then label matching. Never a vector search.
    """
    q = frozenset(tokens(text))
    if not q:
        return None, 0.0

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
                return None, 0.0
            return ranked[0][1], ranked[0][0]
    return None, 0.0


def metric_facts(metric: Metric, screen_title: str | None = None) -> Facts:
    """The definition, the user-written notes, and where it is shown - and a hint that
    asks only for what these hold. Asked for an exclusion or a common mistake the notes
    do not give, a model supplies a plausible one (live QA 2026-10-25)."""
    facts = Facts()
    if metric.definition:
        facts.add("definition", " ".join(metric.definition.split()),
                  metric_id=metric.id, label=metric.label, unit=metric.unit,
                  verbatim=True)
    if metric.formula:
        facts.add("formula", metric.formula, metric_id=metric.id)

    if metric.status is Status.BLOCKED:
        # Only the user-written reason; the YAML's blocked_note is for engineers.
        note = " ".join((metric.unavailable_note or "").split())
        facts.add("unavailable", note or "This figure isn't available in the dashboard yet.",
                  metric_id=metric.id, blocked_by=list(metric.blocked_by))

    # Only notes written for users. A metric with none gets its definition alone.
    notes = [" ".join(n.split()) for n in metric.user_notes][:3]
    for text in notes:
        facts.add("caveat", text, metric_id=metric.id)
    # A blocked figure is shown nowhere, so it gets no "shown on" fact.
    shown = screen_title if metric.status is not Status.BLOCKED else None
    if shown:
        facts.add("screen", f"Shown on the {shown} screen.", metric_id=metric.id)

    hint = ["Lead with what it means, in the definition's own words."]
    if metric.status is Status.BLOCKED:
        hint.append("Then say plainly that the FIGURE is not served — the definition is "
                    "known, the data is not.")
    else:
        hint.append("You do NOT have the current value — say so once, briefly, and do not "
                    "invent one.")
    if notes:
        hint.append("Then make it useful with the caveats: what they say it excludes, the "
                    "mistake people make reading it, why it matters.")
    if shown:
        hint.append("Say which screen shows it.")
    hint.append("Add nothing about what it excludes, how it is misread or where it appears "
                "beyond what FACTS says; if FACTS says little, the answer is short.")
    facts.prose_hint = " ".join(hint)
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
    facts.prose_hint = (
        "Say what this screen is for, then what is worth doing on it. Mention "
        "what is NOT served here if anything, and flag anything needing an OTP "
        "as theirs to do. Each of these only where FACTS says it; otherwise leave it out.")
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
    yours = by_mode.pop("handoff", [])
    for mode, labels in sorted(by_mode.items()):
        facts.add("can", ", ".join(sorted(labels)[:12]), mode=mode,
                  count=len(labels))
    if yours:
        facts.add("theirs", "The user does these; Genie only opens the right screen: "
                  + ", ".join(sorted(yours)[:12]), mode="handoff", count=len(yours))
    facts.prose_hint = (
        "Say what you can do, and be specific about the limits: you plan and "
        "fill in forms, the user submits, and anything needing an OTP has to be "
        "them. Do not just say 'I can help with campaigns'.")
    return facts


# ── navigation ──────────────────────────────────────────────────────────

_ROUTE_PARAM = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")

# What a path parameter is called when Genie has to ask for it.
_PARAM_NOUN = {"brandId": "brand", "campaignId": "campaign", "screenId": "screen",
               "userId": "team member", "resellerId": "advertising partner",
               "tab": "tab"}


def route_params(route: str) -> tuple[str, ...]:
    """The path parameters a route still needs, in order."""
    return tuple(_ROUTE_PARAM.findall(route or ""))


def fill_route(route: str, known: dict[str, str | None]) -> str | None:
    """The route with every parameter filled, or None if any is still open.

    Never returns a route with a placeholder left in it. That is the whole
    point: `/brand-management/{brandId}` was shipped to users verbatim, because
    navigate_facts handed back screen.route and nothing ever looked at it.
    """
    out = route
    for name in route_params(route):
        value = known.get(name)
        if not value:
            return None
        out = out.replace("{" + name + "}", str(value))
    return out


def navigate_resolved(cap: Capability, ontology: Ontology, *,
                      brand_id: str | None = None,
                      campaign_id: str | None = None) -> bool:
    """Is this navigate capability's route already complete?

    Used to silence an entity_choice that has nothing to choose: "open brand
    management" resolves to /brand-management, so "Which brand?" is a question
    nobody posed. It is only a real question for a screen with no
    parameterless route, like a brand's creative library.
    """
    screen = ontology.screen(cap.target_screen) if cap.target_screen else None
    if screen is None:
        return False
    known = {"brandId": brand_id, "campaignId": campaign_id}
    return fill_route(screen.route, known) is not None


def navigate_facts(cap: Capability, ontology: Ontology, role: str | None, *,
                   brand_id: str | None = None,
                   campaign_id: str | None = None) -> Facts:
    """No model involved. This is the path with a 200 ms budget."""
    facts = Facts()
    target_id = cap.target_screen
    screen = ontology.screen(target_id) if target_id else None
    if screen is None:
        facts.add("error", f"{cap.label} has no target screen recorded.")
        return facts

    if role and not screen.visible_to(role):
        # Deep-linking into a 403 is worse than saying no. This is the g086 case.
        facts.add("denied", f"{screen.title} is not available to {a_label(role)}.",
                  screen_id=screen.id, role=role)
        facts.prose_hint = (
        "Say this screen is not in their navigation and who does have it. One "
        "or two sentences — do not offer a link that will refuse them.")
        return facts

    known = {"brandId": brand_id, "campaignId": campaign_id}
    held = any(known.values())

    # The per-entity page when the entity is in hand, the index page otherwise.
    # A screen with both is a real pair of pages: /brand-management lists the
    # brands and /brand-management/{brandId} is one of them, so "open brand
    # management" is answerable without naming a brand and used to ask anyway.
    ordered: list[str] = []
    for route in ((screen.entity_route,) if held else ()) + (screen.route, screen.entity_route):
        if route and route not in ordered:
            ordered.append(route)

    for route in ordered:
        if (link := fill_route(route, known)) is not None:
            facts.add("navigate", link, screen_id=screen.id, title=screen.title,
                      label=cap.label, entity=link != screen.route)
            return facts

    # Every route this screen has needs something we do not hold. Say which --
    # a link with a placeholder in it is not a link.
    missing = [p for r in ordered for p in route_params(r) if not known.get(p)]
    noun = _PARAM_NOUN.get(missing[0], "item") if missing else "item"
    facts.add("navigate_needs", f"{screen.title} opens for one {noun}. "
              f"Tell me which {noun} and I will take you there.",
              screen_id=screen.id, title=screen.title, needs=missing[:1],
              verbatim=True)
    facts.prose_hint = None   # authored: NAVIGATE does not call a model
    return facts


def denied_facts(cap: Capability, role: str | None) -> Facts:
    """Understood, and not this user's to do. Say which, not "I don't know"."""
    facts = Facts()
    who = f" to {a_label(role)}" if role else ""
    facts.add("denied", f"{cap.label} is not available{who}.",
              capability=cap.id, role=role,
              roles_allowed=list(cap.authorization.roles),
              permission=cap.authorization.permission)
    facts.prose_hint = (
        "You understood them. Say briefly that this is not theirs to do and who "
        "can — do not pretend to misunderstand, and do not offer a link into a "
        "screen that will refuse them.")
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
