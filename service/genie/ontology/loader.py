"""Load the ontology and catalog at boot, or refuse to start.

A service that boots with a half-loaded ontology answers questions about metrics
it does not understand. Better to not start, loudly, with the reason.

The invariant this enforces at runtime, in addition to the JSON Schemas:
NO CAPABILITY MAY CARRY A CALLABLE METHOD FOR A STATE-CHANGING ENDPOINT.
The catalog schema already rejects it, but the schema is a build-time check and
this is the last line before a request. If it ever fires, the process dies.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .models import (Action, Authorization, Capability, Execution, FollowUp,
                     FollowUpOption, Metric, Mode, Screen, Source, Status)

log = logging.getLogger(__name__)


class OntologyError(RuntimeError):
    """Raised at boot. Never caught — the service must not start."""


class Ontology:
    """Everything the service knows about the dashboard, held in memory.

    Small enough to load entirely: 12 screens, 94 metrics, 49 capabilities,
    244 utterances. No database, no cache, no lazy loading.
    """

    def __init__(self, screens: dict[str, Screen], capabilities: dict[str, Capability]):
        self.screens = screens
        self.capabilities = capabilities
        self.metrics: dict[str, Metric] = {}
        for screen in screens.values():
            for metric in screen.metrics:
                # A metric id can appear on several screens (avg_broadcasting_cpm
                # is on two). First wins for lookup; the screen-scoped copy is
                # still reachable through screens[x].metrics.
                self.metrics.setdefault(metric.id, metric)
        self._by_utterance: dict[str, str] = {}
        for cap in capabilities.values():
            for utterance in cap.utterances:
                self._by_utterance[utterance.strip().lower()] = cap.id

    # ── lookup ──────────────────────────────────────────────────────────
    def metric(self, metric_id: str) -> Metric | None:
        return self.metrics.get(metric_id)

    def screen(self, screen_id: str) -> Screen | None:
        return self.screens.get(screen_id)

    def capability(self, capability_id: str) -> Capability | None:
        return self.capabilities.get(capability_id)

    def exact_utterance(self, text: str) -> Capability | None:
        """L-1: exact match before anything else. No model, no vectors."""
        cap_id = self._by_utterance.get(text.strip().lower())
        return self.capabilities.get(cap_id) if cap_id else None

    def screens_for(self, role: str) -> list[Screen]:
        return [s for s in self.screens.values() if s.visible_to(role)]

    @property
    def permission_names(self) -> frozenset[str]:
        """Every permission the catalog gates on — the vocabulary /me grants are checked against."""
        return frozenset(c.authorization.permission for c in self.capabilities.values()
                         if c.authorization.permission)

    # ── stats, for /healthz ─────────────────────────────────────────────
    @property
    def stats(self) -> dict[str, int]:
        servable = sum(1 for m in self.metrics.values() if m.servable)
        return {
            "screens": len(self.screens),
            "metrics": len(self.metrics),
            "metrics_servable": servable,
            "metrics_blocked": sum(1 for m in self.metrics.values()
                                   if m.status is Status.BLOCKED),
            "capabilities": len(self.capabilities),
            "capabilities_otp_gated": sum(1 for c in self.capabilities.values()
                                          if c.otp_gated),
            "utterances": len(self._by_utterance),
            "follow_ups": sum(len(c.follow_ups) for c in self.capabilities.values()),
        }


# ── parsing ─────────────────────────────────────────────────────────────

def _status(raw: str | None, where: str) -> Status:
    try:
        return Status(raw or "pending_verification")
    except ValueError as err:
        raise OntologyError(f"{where}: unknown status {raw!r}") from err


def _source(raw: dict | None) -> Source | None:
    if not raw:
        return None
    return Source(api=raw.get("api"), method=raw.get("method"),
                  scope_key=raw.get("scope_key"), field_path=raw.get("field"))


def _tuple(raw) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(str(x) for x in raw)


def _parse_screen(doc: dict, path: Path) -> Screen:
    s = doc.get("screen")
    if not isinstance(s, dict):
        raise OntologyError(f"{path.name}: no top-level `screen:` key")
    for required in ("id", "title", "route", "purpose", "audience"):
        if required not in s:
            raise OntologyError(f"{path.name}: screen is missing `{required}`")

    metrics = []
    for raw in s.get("metrics") or []:
        where = f"{path.name}:{raw.get('id')}"
        status = _status(raw.get("status"), where)
        if status is Status.BLOCKED and not raw.get("blocked_by"):
            raise OntologyError(f"{where}: blocked without blocked_by — "
                                f"a gap with no reason recorded is a gap nobody can close")
        metrics.append(Metric(
            id=raw["id"], label=raw.get("label", raw["id"]), status=status,
            screen_id=s["id"], definition=raw.get("definition"),
            formula=raw.get("formula"), unit=raw.get("unit"),
            freshness=raw.get("freshness"), source=_source(raw.get("source")),
            user_notes=_tuple(raw.get("user_notes")),
            gotchas=_tuple(raw.get("gotchas")),
            blocked_by=_tuple(raw.get("blocked_by")),
            unavailable_note=raw.get("unavailable_note"),
        ))

    actions = tuple(
        Action(capability=a["capability"], label=a.get("label", a["capability"]),
               status=_status(a.get("status"), f"{path.name}:{a['capability']}"),
               otp_gated=bool(a.get("otp_gated")), mutating=bool(a.get("mutating")))
        for a in (s.get("actions") or [])
    )

    return Screen(
        id=s["id"], title=s["title"], route=s["route"],
        purpose=" ".join(str(s["purpose"]).split()),
        audience=_tuple(s["audience"]), nav_label=s.get("nav_label"),
        entity_route=s.get("entity_route"),
        metrics=tuple(metrics), actions=actions,
    )


def _parse_follow_up(raw: dict, where: str) -> FollowUp:
    options = tuple(
        FollowUpOption(value=o["value"], label=o.get("label", o["value"]),
                       hint=o.get("hint"))
        for o in (raw.get("options") or [])
    )
    if not options and not raw.get("options_from"):
        if raw.get("kind") in ("disambiguation", "entity_choice"):
            raise OntologyError(f"{where}: {raw.get('kind')} follow-up with no options "
                                f"and no options_from — it can never be answered")
    return FollowUp(
        id=raw["id"], kind=raw.get("kind", "disambiguation"),
        question=raw.get("question", ""), options=options,
        options_from=raw.get("options_from"),
        resolved_by_context=_tuple(raw.get("resolved_by_context")),
        default_after_asks=raw.get("default_after_asks"),
        max_asks=int(raw.get("max_asks", 1)),
    )


def _parse_capability(raw: dict, domain: str, where: str) -> Capability:
    try:
        mode = Mode(raw["genie_mode"])
    except (KeyError, ValueError) as err:
        raise OntologyError(f"{where}: bad or missing genie_mode "
                            f"{raw.get('genie_mode')!r}") from err

    ex_raw = raw.get("execution") or {}
    execution = Execution(
        state_changing=bool(ex_raw.get("state_changing")),
        method=ex_raw.get("method"), path=ex_raw.get("path"),
        layer=ex_raw.get("layer"),
    )

    # ── THE READ-ONLY INVARIANT, enforced at boot ──────────────────────
    # Not a lint. If a state-changing endpoint ever carries a callable method,
    # the process must not start. The catalog schema rejects this at build time;
    # this is the last check before a request could reach it.
    if execution.state_changing and execution.method not in (None, "NONE"):
        raise OntologyError(
            f"{where}: READ-ONLY INVARIANT VIOLATED — state_changing is true but "
            f"method is {execution.method!r}. Genie holds no write paths. "
            f"Refusing to start.")

    au_raw = raw.get("authorization") or {}
    return Capability(
        id=raw["id"], label=raw.get("label", raw["id"]), mode=mode,
        status=_status(raw.get("status"), where), domain=domain,
        description=raw.get("description"),
        utterances=_tuple(raw.get("utterances")),
        answers_with=_tuple(raw.get("answers_with")),
        follow_ups=tuple(_parse_follow_up(f, f"{where}.{f.get('id')}")
                         for f in (raw.get("follow_ups") or [])),
        authorization=Authorization(gate=au_raw.get("gate", "UNKNOWN"),
                                    permission=au_raw.get("permission"),
                                    roles=_tuple(au_raw.get("roles"))),
        execution=execution, gotchas=_tuple(raw.get("gotchas")),
        target_screen=raw.get("target_screen"), refusal=raw.get("refusal"),
        otp_gated=bool(raw.get("otp_gated")), mutating=bool(raw.get("mutating")),
        blocked_by=_tuple(raw.get("blocked_by")),
    )


def load(root: Path) -> Ontology:
    """Load everything, or raise. Called once, at boot."""
    screens_dir, catalog_dir = root / "ontology" / "screens", root / "catalog"
    if not screens_dir.is_dir():
        raise OntologyError(f"no ontology at {screens_dir} — is GENIE_ONTOLOGY_ROOT right?")

    screens: dict[str, Screen] = {}
    for path in sorted(screens_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        screen = _parse_screen(doc, path)
        if screen.id in screens:
            raise OntologyError(f"duplicate screen id {screen.id!r} in {path.name}")
        screens[screen.id] = screen

    capabilities: dict[str, Capability] = {}
    claimed: dict[str, str] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        domain = doc.get("domain", path.stem)
        for raw in doc.get("capabilities") or []:
            where = f"{path.name}:{raw.get('id')}"
            cap = _parse_capability(raw, domain, where)
            if cap.id in capabilities:
                raise OntologyError(f"duplicate capability id {cap.id!r}")
            capabilities[cap.id] = cap
            for utterance in cap.utterances:
                key = utterance.strip().lower()
                if key in claimed and claimed[key] != cap.id:
                    raise OntologyError(
                        f"utterance {utterance!r} claimed by both {claimed[key]} "
                        f"and {cap.id} — routing would be a coin flip")
                claimed[key] = cap.id

    if not screens:
        raise OntologyError("no screens loaded")
    if not capabilities:
        raise OntologyError("no capabilities loaded")

    # Cross-reference: a capability answering with a metric that does not exist
    # is a dead answer path, and the golden set would not catch it at runtime.
    known = {m.id for s in screens.values() for m in s.metrics}
    for cap in capabilities.values():
        for metric_id in cap.answers_with:
            if metric_id not in known:
                raise OntologyError(
                    f"{cap.id}: answers_with references unknown metric {metric_id!r}")

    ontology = Ontology(screens, capabilities)
    log.info("ontology loaded: %s", ontology.stats)
    return ontology
