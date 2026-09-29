"""Turning a message into a stream of blocks.

S3: route the intent, then answer from the ontology. The shape of a turn:

    route  →  follow-up?  →  facts from the ontology  →  prose around them

Three things never involve the model:
  · which capability the words mean, when the utterance is authored or lexically clear
  · whether a follow-up fires (screen context decides)
  · the text of a definition or a refusal — both are served verbatim

Live data is S4. Until then the service knows what every number MEANS and says
plainly that it does not have the value.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..llm.base import LLMError, Message, Provider
from ..ontology import Ontology
from ..ontology.models import Capability, Mode, Status
from ..router import answers as build
from ..router.followups import decide as decide_follow_up
from ..router.intent import IntentRouter, Route
from ..router.lexical import tokens as lex_tokens
from .session import Session, UIContext

log = logging.getLogger(__name__)

GUARDRAILS = """You are Genie, the assistant inside the AdGrid Ad Partner Agency Admin Dashboard.
The designs call you AdGuide.

ABSOLUTE RULES — these are not style preferences.

1. You hold NO write paths. You never create, edit, pause, cancel, approve or pay
   for anything. When asked to, say plainly that it has to be the user, say why
   (most of these need an OTP sent to their registered mobile), and offer to take
   them to the right screen.
2. NEVER invent a number. If a figure was not given to you in this prompt, you do
   not have it. Say so. A plausible wrong number about someone's wallet is worse
   than no answer.
3. NEVER do arithmetic that matters. Costs, reach, GST and budgets are computed by
   the service and passed to you. Repeat them; do not recompute them.
4. Suppressed is NOT zero. If data is marked suppressed, it means fewer than 10
   people were seen and the figure is withheld — not that nothing happened.
5. Impressions are playback events. Reach is people. Footfall is visits. These are
   three different quantities and the product genuinely uses all three.
6. FACTS below are authoritative and already correct. Use their wording for
   definitions. Do not restate them in your own words and do not add to them.

Be brief and concrete. Two or three sentences unless asked for more."""


@dataclass(slots=True)
class Block:
    type: str
    content: str
    meta: dict | None = None


class Responder:
    def __init__(self, ontology: Ontology, provider: Provider | None,
                 router: IntentRouter | None = None):
        self._ontology = ontology
        self._provider = provider
        self._router = router or IntentRouter(ontology, provider)

    @property
    def mode(self) -> str:
        return "llm" if self._provider else "stub"

    # ── prompt ─────────────────────────────────────────────────────────
    def _screen_brief(self, ctx: UIContext) -> str:
        screen = self._ontology.screen(ctx.screen) if ctx.screen else None
        if not screen:
            return "The user's current screen is unknown."
        lines = [f'The user is on "{screen.title}" ({screen.route}).',
                 f"Purpose: {screen.purpose}"]
        if ctx.role:
            lines.append(f"Their role is {ctx.role}.")
        blocked = [m.label for m in screen.metrics if m.status is Status.BLOCKED]
        if blocked:
            lines.append("NOT SERVED — no endpoint returns these, say so if asked: "
                         + ", ".join(blocked[:12]))
        return "\n".join(lines)

    def _system_prompt(self, ctx: UIContext, facts: build.Facts | None) -> str:
        parts = [GUARDRAILS, "--- CURRENT SCREEN ---", self._screen_brief(ctx)]
        if facts and facts.blocks:
            rendered = "\n".join(
                f"[{b['type']}] {b['content']}" for b in facts.blocks)
            parts += ["--- FACTS (authoritative) ---", rendered]
            if facts.prose_hint:
                parts += ["--- WHAT TO DO ---", facts.prose_hint]
        return "\n\n".join(parts)

    # ── the turn ───────────────────────────────────────────────────────
    async def respond(self, session: Session, text: str) -> AsyncIterator[Block]:
        ctx = session.context
        roles = frozenset({ctx.role} if ctx.role else ())
        permissions = frozenset(session.permissions)

        route = await self._router.route(text, screen=ctx.screen, roles=roles,
                                         permissions=permissions)
        yield Block("capability", route.capability.id if route.routed else "",
                    {"mode": route.capability.mode.value if route.routed else None,
                     "otp_gated": route.capability.otp_gated if route.routed else False,
                     "match": route.method, "confidence": route.confidence,
                     "reason": route.reason,
                     "candidates": [c.capability.id for c in route.candidates[:4]]})

        if not route.routed:
            async for block in self._unrouted(ctx, text):
                yield block
            return

        cap = route.capability

        # Follow-up before anything else. Asking beats guessing, and the screen
        # having already answered beats asking.
        #
        # But a follow-up only fires when the ambiguity it resolves is ACTUALLY
        # PRESENT. "What does verified impressions mean" names its metric, so
        # asking "which utilisation did you mean?" is noise — the question
        # belongs to "what's my utilisation", which names nothing.
        ask = decide_follow_up(cap, screen=ctx.screen, query=text,
                               asked=session.asked)
        if ask is not None:
            session.asked[ask.follow_up.id] = session.asked.get(ask.follow_up.id, 0) + 1
            yield Block("follow_up", ask.question, ask.as_block())
            session.add("user", text)
            session.add("model", ask.question)
            return

        facts = self._facts_for(cap, text, ctx, roles, permissions)

        # NAVIGATE never calls the model. It has a 200 ms budget and the answer
        # is a route, not a sentence.
        if cap.mode is Mode.NAVIGATE:
            for block in facts.blocks:
                yield Block(block["type"], block["content"], block["meta"])
            return

        for block in facts.blocks:
            yield Block(block["type"], block["content"], block["meta"])

        if self._provider is None:
            async for block in self._stub(ctx, text, cap, facts):
                yield block
            return

        session.add("user", text)
        collected: list[str] = []
        try:
            async for chunk in self._provider.stream(
                list(session.history)[-12:],
                system=self._system_prompt(ctx, facts),
            ):
                collected.append(chunk)
                yield Block("token", chunk)
        except LLMError as err:
            log.warning("llm failed: %s", err)
            yield Block("error", str(err), {"retryable": err.retryable})
            return

        answer = "".join(collected)
        session.add("model", answer)
        yield Block("text", answer, {"final": True})

    # ── facts per mode ─────────────────────────────────────────────────
    def _facts_for(self, cap: Capability, text: str, ctx: UIContext,
                   roles: frozenset[str], permissions: frozenset[str]) -> build.Facts:
        if cap.mode is Mode.NAVIGATE:
            return build.navigate_facts(cap, self._ontology, ctx.role)
        if cap.mode is Mode.HANDOFF:
            return build.handoff_facts(cap)
        if cap.status is Status.BLOCKED:
            return build.blocked_capability_facts(cap)

        if cap.id == "explain.screen" and ctx.screen:
            screen = self._ontology.screen(ctx.screen)
            if screen:
                return build.screen_facts(screen, self._ontology, ctx.role)
        if cap.id == "explain.capabilities":
            return build.capability_facts(self._ontology, roles, permissions)

        # explain.metric and anything answering with metrics: resolve WHICH.
        metric = build.resolve_metric(text, self._ontology, screen=ctx.screen,
                                      visible=ctx.visible_metrics)
        if metric is None and cap.answers_with:
            for mid in cap.answers_with:
                if (m := self._ontology.metric(mid)) is not None:
                    metric = m
                    break
        if metric is not None:
            return build.metric_facts(metric)

        facts = build.Facts()
        facts.prose_hint = ("Answer from what you know about the product. If the "
                            "specific figure or fact is not in this prompt, say so.")
        return facts

    # ── fallbacks ──────────────────────────────────────────────────────
    async def _unrouted(self, ctx: UIContext, text: str) -> AsyncIterator[Block]:
        """Nothing matched. Say so rather than improvising a capability."""
        if self._provider is None:
            msg = ("I could not match that to anything I know how to do. "
                   "Try asking what a metric on this screen means.")
            yield Block("token", msg)
            yield Block("text", msg, {"final": True, "stub": True, "unrouted": True})
            return
        facts = build.Facts()
        facts.prose_hint = (
            "You could not match this to a known capability. Say briefly that you "
            "are not sure what they mean, and name two or three things you CAN do "
            "on this screen. Do not guess.")
        collected: list[str] = []
        try:
            async for chunk in self._provider.stream(
                [Message(role="user", text=text)],
                system=self._system_prompt(ctx, facts), max_output_tokens=300,
            ):
                collected.append(chunk)
                yield Block("token", chunk)
        except LLMError as err:
            yield Block("error", str(err), {"retryable": err.retryable})
            return
        yield Block("text", "".join(collected), {"final": True, "unrouted": True})

    async def _stub(self, ctx: UIContext, text: str, cap: Capability,
                    facts: build.Facts) -> AsyncIterator[Block]:
        parts = [f'Matched "{cap.label}" ({cap.mode.value}).']
        for block in facts.blocks:
            if block["type"] in ("definition", "refusal", "unavailable"):
                parts.append(block["content"])
        parts.append("No language model is configured, so this is the stub responder.")
        for part in parts:
            yield Block("token", part + " ")
        yield Block("text", " ".join(parts), {"final": True, "stub": True})
