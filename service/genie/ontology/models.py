"""Typed view of the ontology and catalog.

Deliberately narrow: only the fields the service reads at runtime. The YAML is
the source of truth and the JSON Schemas in ontology/schema/ and catalog/schema/
are what validate its full shape — these models exist so the service fails at
boot on a structural problem rather than at 3am on a KeyError.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..roles import access


class Status(str, Enum):
    VERIFIED = "verified"
    PENDING = "pending_verification"
    BLOCKED = "blocked"
    PENDING_TRANSCRIPTION = "pending_transcription"


class Mode(str, Enum):
    """What Genie is permitted to do for a capability."""
    ANSWER = "answer"
    NAVIGATE = "navigate"
    PLAN = "plan"
    PREFILL = "prefill"
    HANDOFF = "handoff"


@dataclass(frozen=True, slots=True)
class Source:
    api: str | None = None
    method: str | None = None
    scope_key: str | None = None
    field_path: str | None = None

    @property
    def is_derived(self) -> bool:
        return bool(self.api and self.api.startswith("DERIVED"))


@dataclass(frozen=True, slots=True)
class Metric:
    id: str
    label: str
    status: Status
    screen_id: str
    definition: str | None = None
    formula: str | None = None
    unit: str | None = None
    freshness: str | None = None
    source: Source | None = None
    # Written for users: the only notes a user, or a model, is ever given.
    user_notes: tuple[str, ...] = ()
    # Written for engineers: rulings, contract detail, advice to Genie. Never shown.
    gotchas: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    unavailable_note: str | None = None      # for users: the only "why not" a model is given

    @property
    def servable(self) -> bool:
        """True when Genie may put a NUMBER on screen for this metric.

        Blocked means nothing serves it. The DEFINITION is still answerable —
        that is an ontology lookup and needs no endpoint — which is why
        explain.metric can answer about a blocked metric while a data read cannot.
        """
        return self.status is not Status.BLOCKED and self.source is not None


@dataclass(frozen=True, slots=True)
class Action:
    capability: str
    label: str
    status: Status
    otp_gated: bool = False
    mutating: bool = False


@dataclass(frozen=True, slots=True)
class Screen:
    id: str
    title: str
    route: str
    purpose: str
    audience: tuple[str, ...]
    nav_label: str | None = None
    # The per-entity form of `route`, when the screen has both. /brand-management
    # lists brands, /brand-management/{brandId} is one of them. NAVIGATE uses this
    # only once the entity is resolved.
    entity_route: str | None = None
    metrics: tuple[Metric, ...] = ()
    actions: tuple[Action, ...] = ()

    def visible_to(self, role: str) -> bool:
        return access(role) in self.audience


@dataclass(frozen=True, slots=True)
class FollowUpOption:
    value: str
    label: str
    hint: str | None = None


@dataclass(frozen=True, slots=True)
class FollowUp:
    id: str
    kind: str
    question: str
    options: tuple[FollowUpOption, ...] = ()
    options_from: str | None = None
    resolved_by_context: tuple[str, ...] = ()
    default_after_asks: str | None = None
    max_asks: int = 1

    def suppressed_on(self, screen_id: str | None) -> bool:
        """Screen context answering the question is the whole point of embedding.

        `what is my wallet balance` on the Command Centre must ask which balance.
        The same words on Wallet Management must not, because that screen already
        shows all three side by side.
        """
        return bool(screen_id) and screen_id in self.resolved_by_context


@dataclass(frozen=True, slots=True)
class Authorization:
    gate: str = "UNKNOWN"
    permission: str | None = None
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Execution:
    state_changing: bool = False
    method: str | None = None
    path: str | None = None
    layer: str | None = None


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    label: str
    mode: Mode
    status: Status
    domain: str
    description: str | None = None
    utterances: tuple[str, ...] = ()
    answers_with: tuple[str, ...] = ()
    follow_ups: tuple[FollowUp, ...] = ()
    authorization: Authorization = field(default_factory=Authorization)
    execution: Execution = field(default_factory=Execution)
    gotchas: tuple[str, ...] = ()
    target_screen: str | None = None
    refusal: str | None = None
    otp_gated: bool = False
    mutating: bool = False
    blocked_by: tuple[str, ...] = ()

    @property
    def writes(self) -> bool:
        return self.execution.state_changing

    def role_allowed(self, roles: frozenset[str]) -> bool:
        """Whether any of these roles - or the role whose access it shares - may use it."""
        return not self.authorization.roles or bool(
            set(self.authorization.roles) & {access(r) for r in roles})

    def permitted_for(self, roles: frozenset[str], permissions: frozenset[str]) -> bool:
        """Role and permission gating. Code, never a prompt instruction."""
        auth = self.authorization
        if not self.role_allowed(roles):
            return False
        if auth.gate == "PERMISSION" and auth.permission:
            return auth.permission in permissions
        return True
