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

from ..bff import BffClient
from ..data.money import unexplained_numbers
from ..data.wallet import agency_wallet, my_subwallet
from ..llm.base import LLMError, Message, Provider
from ..ontology import Ontology
from ..ontology.models import Capability, Mode, Status
from ..router import answers as build
from ..router.followups import decide as decide_follow_up
from ..router.intent import IntentRouter, Route
from ..router.lexical import normalise
from ..router.lexical import tokens as lex_tokens
from ..router.live_answers import WALLET_CAPS, is_overview, wallet_answer
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
6. Definitions, formulas and refusal text in FACTS are AUTHORITATIVE. Use their
   wording. Do not paraphrase them — a definition that drifts is a wrong answer
   about a number on someone's screen.
7. Everything else is yours to write: why the figure matters, what it does NOT
   include, the mistake people make with it, what they would do next. The
   caveats in FACTS are the most useful thing you have — work them into the
   answer rather than mentioning them in passing.

8. Never surface internal machinery: ledger keys like WALLET:CLIENT:{apId},
   table or column names, endpoint paths, or our conflict ids (R4, D10, C2).
   The caveats carry them because they were written for engineers. Say what
   they MEAN — "sub-wallets are separate accounts and are not in this figure" —
   and leave the identifier out.

VOICE
Plain and direct. You sit in a panel beside the dashboard, so three to six
sentences for most answers, a short paragraph more for "how does this work".
No preamble, no restating the question, no "great question", no bullet lists
unless the content is genuinely a list. Say the useful thing first.

Where the honest answer is that you do not have a figure, say so in one line
and then spend the rest on what IS known — what the number means, what it
excludes, where they can see it. "I don't have that" on its own wastes the
turn."""


@dataclass(slots=True)
class Block:
    type: str
    content: str
    meta: dict | None = None


class Responder:
    def __init__(self, ontology: Ontology, provider: Provider | None,
                 router: IntentRouter | None = None, bff: BffClient | None = None):
        self._ontology = ontology
        self._provider = provider
        self._router = router or IntentRouter(ontology, provider)
        self._bff = bff

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
    def _answers_pending(self, session: Session, text: str) -> tuple[Capability, str] | None:
        """Is this message the answer to the question asked last turn?

        "Main wallet" after "Which balance do you mean?" picks that option. Matched
        on the option's label or value; anything else is a new question and the
        pending one is dropped.
        """
        pending, session.pending = session.pending, None
        if not pending:
            return None
        said = normalise(text)
        for value, label in pending["options"]:
            if normalise(label) in said or normalise(value.replace("_", " ")) in said:
                cap = self._ontology.capability(pending["capability"])
                return (cap, value) if cap else None
        return None

    async def respond(self, session: Session, text: str,
                      token: str | None = None) -> AsyncIterator[Block]:
        ctx = session.context
        roles = frozenset({ctx.role} if ctx.role else ())
        permissions = frozenset(session.permissions)

        # Session first: an answer to last turn's question is not a new question.
        answered = self._answers_pending(session, text)
        if answered:
            cap, chosen = answered
            yield Block("capability", cap.id, {"mode": cap.mode.value, "match": "follow_up",
                                               "confidence": 1.0, "chosen": chosen})
            if cap.id in WALLET_CAPS:
                async for block in self._live_wallet(session, text, cap, token, chosen):
                    yield block
                return
            text = f"{text} ({cap.label})"   # otherwise fall through with the choice in view

        route = await self._router.route(text, screen=ctx.screen, roles=roles,
                                         permissions=permissions)
        yield Block("capability", route.capability.id if route.routed else "",
                    {"mode": route.capability.mode.value if route.routed else None,
                     "otp_gated": route.capability.otp_gated if route.routed else False,
                     "match": route.method, "confidence": route.confidence,
                     "denied": route.denied,
                     "reason": route.reason,
                     "candidates": [c.capability.id for c in route.candidates[:4]]})

        if not route.routed:
            async for block in self._unrouted(ctx, text):
                yield block
            return

        cap = route.capability

        # Denied outranks everything else: no follow-up, no facts, no deep link
        # into a screen that will refuse them.
        if route.denied:
            facts = build.denied_facts(cap, ctx.role)
            for block in facts.blocks:
                yield Block(block["type"], block["content"], block["meta"])
            if self._provider is not None:
                async for block in self._narrate(session, text, ctx, facts):
                    yield block
            return

        # Follow-up before anything else. Asking beats guessing, and the screen
        # having already answered beats asking.
        #
        # But a follow-up only fires when the ambiguity it resolves is ACTUALLY
        # PRESENT. "What does verified impressions mean" names its metric, so
        # asking "which utilisation did you mean?" is noise — the question
        # belongs to "what's my utilisation", which names nothing.
        # A campaign manager's wallet is their own sub-wallet: "which balance?" would
        # offer three agency pots the BFF refuses them (live QA: 403 on all three).
        own_wallet_only = cap.id in WALLET_CAPS and ctx.role == "CAMPAIGN_MANAGER"
        ask = None if own_wallet_only else decide_follow_up(cap, screen=ctx.screen, query=text,
                                                            asked=session.asked)
        if ask is not None:
            session.asked[ask.follow_up.id] = session.asked.get(ask.follow_up.id, 0) + 1
            session.pending = {"capability": cap.id, "follow_up": ask.follow_up.id,
                               "options": [(o["value"], o["label"]) for o in ask.options]}
            yield Block("follow_up", ask.question, ask.as_block())
            session.add("user", text)
            session.add("model", ask.question)
            return

        # Live figures, read as the user. Wallet first; S4 adds the rest the same way.
        if cap.id in WALLET_CAPS:
            async for block in self._live_wallet(session, text, cap, token, None):
                yield block
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

        async for block in self._narrate(session, text, ctx, facts):
            yield block

    # ── live answers ───────────────────────────────────────────────────
    async def _live_wallet(self, session: Session, text: str, cap: Capability,
                           token: str | None, chosen: str | None) -> AsyncIterator[Block]:
        """The wallet, read now as this user: a card built by code, then words.

        The words are the template unless a model is configured, and a model's
        words are kept only if every number in them is on the card.
        """
        identity = session.identity
        if self._bff is None or not token or identity is None:
            # No live access (not signed in, or the BFF is not configured). Say so
            # once, then answer what the numbers MEAN — which is still useful.
            notice = ("I can't read live figures right now — you're not signed in to the "
                      "dashboard API.")
            yield Block("unavailable", notice, {"reason": "signed_out"})
            facts = self._facts_for(cap, text, session.context, frozenset(), frozenset())
            for block in facts.blocks:
                yield Block(block["type"], block["content"], block["meta"])
            if self._provider is not None:
                async for block in self._narrate(session, text, session.context, facts):
                    yield block
                return
            # No model: still end with a real answer — the notice plus what it means.
            meaning = next((b["content"] for b in facts.blocks if b["type"] == "definition"), "")
            final = f"{notice} {meaning}".strip()
            yield Block("token", final)
            yield Block("text", final, {"final": True, "live": False, "narrated_by": "template"})
            return

        grant = identity.grant_for(session.context.client_id)
        if grant is None:
            yield Block("denied", "You don't have access to that agency's wallet.",
                        {"reason": "no_grant"})
            return
        # A campaign manager's "my balance" is their own sub-wallet (catalog gotcha).
        own = grant.role == "CAMPAIGN_MANAGER"
        if own:
            view = await my_subwallet(self._bff, token, grant.client_id, identity.user_id)
        else:
            view = await agency_wallet(
                self._bff, token, grant.client_id,
                with_managers=cap.id != "wallet.explain_runway" or is_overview(text))
        answer = wallet_answer(cap.id, text, view, self._ontology, chosen=chosen,
                               own_subwallet=own)
        yield Block("metrics", answer.card["title"], answer.card)

        final, by, rejected = answer.template, "template", []
        if self._provider is not None and answer.card["items"]:
            # Buffered, not streamed: the number check has to run before the user
            # reads a word, and a card is already on screen while this happens.
            draft: list[str] = []
            try:
                async for chunk in self._provider.stream(
                        [Message(role="user", text=text)],
                        system=self._system_prompt(session.context, answer.facts),
                        temperature=0.2, max_output_tokens=400):
                    draft.append(chunk)
            except LLMError as err:
                log.warning("live narration failed, using template: %s", err)
            written = "".join(draft).strip()
            rejected = unexplained_numbers(written, answer.allowed + [text]) if written else []
            if written and not rejected:
                final, by = written, "model"
            elif rejected:
                log.warning("narration invented %s; using the template", rejected)
        session.add("user", text)
        session.add("model", final)
        yield Block("token", final)
        yield Block("text", final, {"final": True, "live": True, "narrated_by": by,
                                    "rejected_numbers": rejected or None})

    async def _narrate(self, session: Session, text: str, ctx: UIContext,
                       facts: build.Facts) -> AsyncIterator[Block]:
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
