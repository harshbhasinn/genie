"""Turning a message into a stream of blocks.

S2's job is the pipe, not the answer. Intent routing is S3 and live data is S4,
so this does three things and stops:

  1. exact utterance lookup (L-1) — no model, no vectors, proves the ontology is wired
  2. builds a system prompt from the screen the user is actually on
  3. streams from the provider, or emits a deterministic stub when none is configured

The guardrails in the system prompt are here from the start rather than bolted on
later, because a model that has been allowed to invent a number once will do it
again the moment the prompt gets longer.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..llm.base import LLMError, Message, Provider
from ..ontology import Ontology
from ..ontology.models import Status
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

Be brief and concrete. Use the user's vocabulary, not the backend's."""


@dataclass(slots=True)
class Block:
    """One renderable unit. The web panel decides how each type looks."""
    type: str
    content: str
    meta: dict | None = None


class Responder:
    def __init__(self, ontology: Ontology, provider: Provider | None):
        self._ontology = ontology
        self._provider = provider

    @property
    def mode(self) -> str:
        return "llm" if self._provider else "stub"

    # ── context ────────────────────────────────────────────────────────
    def _screen_brief(self, ctx: UIContext) -> str:
        """What the user is looking at, as prose the model can use.

        Only servable metrics are described. A blocked metric is listed by name
        as unavailable, so the model can say 'that is not served' instead of
        reaching for the nearest number that looks similar.
        """
        screen = self._ontology.screen(ctx.screen) if ctx.screen else None
        if not screen:
            return "The user's current screen is unknown."

        lines = [f'The user is on "{screen.title}" ({screen.route}).',
                 f"Purpose: {screen.purpose}"]
        if ctx.role:
            lines.append(f"Their role is {ctx.role}.")

        servable = [m for m in screen.metrics if m.servable]
        blocked = [m for m in screen.metrics if m.status is Status.BLOCKED]
        if servable:
            lines.append("\nMetrics on this screen (definitions only — you have no values):")
            for m in servable[:25]:
                definition = (m.definition or "").strip()
                lines.append(f"- {m.label}: {definition}" if definition
                             else f"- {m.label}")
        if blocked:
            names = ", ".join(m.label for m in blocked[:12])
            lines.append(f"\nNOT SERVED — no endpoint returns these, say so if asked: {names}")

        actions = [a for a in screen.actions if a.status is not Status.BLOCKED]
        if actions:
            otp = [a.label for a in actions if a.otp_gated]
            lines.append("\nActions here: " + ", ".join(a.label for a in actions))
            if otp:
                lines.append(f"Needing an OTP, so the user must do them: {', '.join(otp)}")
        return "\n".join(lines)

    def _system_prompt(self, ctx: UIContext) -> str:
        return f"{GUARDRAILS}\n\n--- CURRENT SCREEN ---\n{self._screen_brief(ctx)}"

    # ── the turn ───────────────────────────────────────────────────────
    async def respond(self, session: Session, text: str) -> AsyncIterator[Block]:
        ctx = session.context

        # L-1: exact utterance. Free, deterministic, and the thing S3 builds on.
        matched = self._ontology.exact_utterance(text)
        if matched:
            yield Block("capability", matched.id, {
                "mode": matched.mode.value,
                "otp_gated": matched.otp_gated,
                "match": "exact-utterance",
                "follow_ups": [
                    f.id for f in matched.follow_ups if not f.suppressed_on(ctx.screen)
                ],
            })

        if self._provider is None:
            async for block in self._stub(ctx, text, matched):
                yield block
            return

        session.add("user", text)
        history = list(session.history)[-12:]
        collected: list[str] = []
        try:
            async for chunk in self._provider.stream(
                history, system=self._system_prompt(ctx)
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

    async def _stub(self, ctx: UIContext, text: str, matched) -> AsyncIterator[Block]:
        """Deterministic, and honest about being a stub.

        Exists so S2's exit gate can be met with no API key, and so the transport
        can be tested without spending tokens or depending on a third party.
        """
        screen = self._ontology.screen(ctx.screen) if ctx.screen else None
        parts = [f'You said: "{text}".']
        if screen:
            parts.append(f'You are on {screen.title} ({screen.route})'
                         + (f' as {ctx.role}.' if ctx.role else '.'))
            servable = sum(1 for m in screen.metrics if m.servable)
            blocked = sum(1 for m in screen.metrics if m.status is Status.BLOCKED)
            parts.append(f"I know {servable} metric(s) here, and {blocked} that "
                         f"nothing currently serves.")
        else:
            parts.append("No screen context was sent, so I cannot narrow anything.")
        if matched:
            parts.append(f'That phrasing maps exactly to "{matched.label}".')
        parts.append("No language model is configured, so this is the stub responder.")
        for part in parts:
            yield Block("token", part + " ")
        yield Block("text", " ".join(parts), {"final": True, "stub": True})
