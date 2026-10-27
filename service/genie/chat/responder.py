"""Turning a message into a stream of blocks.

S3: route the intent, then answer from the ontology. The shape of a turn:

    route  →  follow-up?  →  facts from the ontology  →  prose around them

Three things never involve the model:
  · which capability the words mean, when the utterance is authored or lexically clear
  · whether a follow-up fires (screen context decides)
  · the text of a definition or a refusal — both are served verbatim

S4 adds live figures, read from the BFF as the user: a card built by code and a sentence
under it (the template, or a model's number-checked rewording). Without live access an
answer says so and still explains what the number means.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ..bff import BffClient
from ..data.money import unexplained_numbers
from ..data.approvals import (approval_rules, awaiting_me, campaign_requests, decided_by_me,
                              failed_after_approval, preview_today, raised_by_me, request_detail)
from ..data.brands import brand_contacts, brand_stats, list_brands, many_brand_stats
from ..data.campaigns import (campaign_breakdown, campaign_dayparts, campaign_gender, campaign_view,
                              launch_check,
                              list_campaigns, one_campaign)
from ..data.spending import bills, ledger, monthly, owed, spend_by_brand
from ..data.wallet import agency_wallet, my_subwallet
from ..llm.base import LLMError, Message, Provider
from ..ontology import Ontology
from ..ontology.models import Capability, Mode, Status
from ..roles import ADMIN_OF, a_label, is_campaign_manager
from ..roles import LABEL as ROLE_LABELS
from ..roles import label as role_label
from ..router import answers as build
from ..router.followups import decide as decide_follow_up
from ..router.intent import IntentRouter
from ..router.lexical import normalise
from ..router.live_answers import WALLET_CAPS, LiveAnswer, is_overview, text_answer, wallet_answer
from ..router.live_approvals import (APPROVAL_CAPS, HISTORY_CAP, REQUEST_CAPS, RULES_CAP,
                                     approvals_answer, history_answer, no_requests, operation_named,
                                     pick_request, rules_answer, status_answer, what_changed_answer,
                                     which_request_block)
from ..router.live_brands import (BRAND_CAPS, BRAND_LINKS, MAX_RANKED, asks_for_ranking,
                                  brand_link_answer, brand_list_answer, brand_stats_answer,
                                  contacts_answer, no_brands, pick_brand, ranked_answer,
                                  which_brand_block)
from ..router.live_campaigns import (ACTION_STATES, CAMPAIGN_CAPS, ELIGIBLE, LAUNCH_CAP,
                                     ONE_CAMPAIGN, action_answer, launch_answer,
                                     audience_answer, breakdown_answer, daypart_answer, group_named,
                                     list_answer, names_a_state, none_to_show, pacing_answer,
                                     performance_answer, pick_campaign, which_campaign_block)
from ..router.live_spending import (FALLBACK_MONTHS, INVOICE_CAP, SPENDING_CAP, bill_answer,
                                    brands_answer, ledger_answer, month_answer, owed_answer,
                                    spending_kind)
from ..router.periods import all_time, period_from, recent_months, without_period
from .session import Session, UIContext

log = logging.getLogger(__name__)

# Explanations: main's voice (commit 0dd25ac), verbatim.
GUARDRAILS = """You are Genie, the assistant inside the AdGrid partner dashboard.
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
9. Name a screen only from SCREENS, by its title. Never name a panel, table,
   chart, tab or section of a screen unless FACTS names it, and never say that
   something is shown on a screen, or will be, unless FACTS says so.

VOICE
Plain and direct. You sit in a panel beside the dashboard, so three to six
sentences for most answers, a short paragraph more for "how does this work".
No preamble, no restating the question, no "great question", no bullet lists
unless the content is genuinely a list. Say the useful thing first.

Where the honest answer is that you do not have a figure, say so in one line
and then spend the rest on what IS known — what the number means, what it
excludes, where they can see it. "I don't have that" on its own wastes the
turn."""

# Live cards: the code has written the answer and the model only rewords it, so it
# is held tighter - the live runs showed invented screens and long answers there.
LIVE_GUARDRAILS = """You are Genie (AdGuide in the designs), the assistant in the AdGrid partner
dashboard. You answer questions about the product and about the user's own data,
in a side panel next to the screen they are on.

Rules:
1. Use only what CURRENT SCREEN, SCREENS and FACTS give you. If something is not
   there, you do not know it: say so in one short sentence. Never guess, and never
   say whether something exists unless FACTS says so.
2. Never write a number that is not in FACTS or in the user's message, and never
   calculate one.
3. Name a screen only from SCREENS, by its title. Never mention a tab, button, menu
   or section that is not given to you.
4. You cannot change anything. If asked to, say it is theirs to do; give a reason
   only if FACTS states one.
5. Definitions in FACTS are exact: keep their wording. Impressions (plays), reach
   (people) and footfall (visits) are different things: never compare or combine them.
6. Never mention internal names: endpoints, field names, ids, or codes like R4.

Style: answer first, in plain prose, no lists or headings. At most three sentences
unless WHAT TO DO asks for more."""


@dataclass(slots=True)
class Block:
    type: str
    content: str
    meta: dict | None = None


# The product's own words, for both voices (ADGRID_KNOWLEDGE_BASE.md §2).
NAMES = ("Call the user's organisation \"you\" - it is a Media Partner or an Advertising Partner; "
         "never an agency, an Ad Partner or AP. Name roles only as: "
         + ", ".join(sorted(set(ROLE_LABELS.values()))) + "; never as a code like CAMPAIGN_MANAGER.")


# Output caps. A thinking model's reasoning counts toward them (Gemini 3.5 cannot
# turn it off; "minimal" is its floor), so each leaves room above the answer itself.
LIVE_CAP, NARRATE_CAP, UNROUTED_CAP = 300, 500, 250

def denied_text(cap: Capability, role: str | None) -> str:
    text = (f"{cap.label} isn't available to {a_label(role)}." if role
            else f"{cap.label} isn't available to you.")
    admin = ADMIN_OF.get(role or "")
    return text + (f" Your {role_label(admin)} can help." if admin else "")


# Offered when nothing matched - real catalog phrasings that route exactly.
UNROUTED_EXAMPLES = ("how is my campaign doing", "what campaigns are running",
                     "what's my wallet balance",
                     "what needs approving", "launch a new campaign")

LIVE_CAPS = (WALLET_CAPS + CAMPAIGN_CAPS + tuple(APPROVAL_CAPS) + REQUEST_CAPS
             + (HISTORY_CAP, RULES_CAP, SPENDING_CAP, INVOICE_CAP) + BRAND_CAPS)


def _ask(session: Session, text: str, cap_id: str, block: dict) -> Block:
    """Ask a follow-up, remembering its options so the next message can answer it - and
    the question it was about, so "Last month" still answers "which brand spends most"."""
    session.pending = {"capability": cap_id, "text": text,
                       "options": [(o["value"], o["label"]) for o in block["options"]]}
    session.add("user", text)
    session.add("model", block["question"])
    return Block("follow_up", block["question"], block)


def _remember_campaigns(session: Session, requests) -> None:
    """A campaign confirmation's subject is a campaign name the user has now been shown."""
    session.remember("campaign", (r.subject for r in requests if r.operation == "CAMPAIGN_CONFIRM"))


def _campaign_in_context(ctx: UIContext) -> str | None:
    """`selected_entity` is whatever the panel has selected, so it is a campaign
    id only on the campaign page. Reading it anywhere else would fill
    /campaign/{campaignId} with a brand or a widget id."""
    return ctx.selected_entity if ctx.screen == "campaign_detail" else None


class Responder:
    def __init__(self, ontology: Ontology, provider: Provider | None,
                 router: IntentRouter | None = None, bff: BffClient | None = None,
                 live_wording: str = "template"):
        self._ontology = ontology
        self._provider = provider
        self._router = router or IntentRouter(ontology, provider)
        self._bff = bff
        # Who writes the sentence under a live card: "template" (code, the default -
        # it cannot invent a screen or a number) or "model" (rephrases, number-checked).
        self._live_wording = live_wording

    @property
    def mode(self) -> str:
        return "llm" if self._provider else "stub"

    # ── prompt ─────────────────────────────────────────────────────────
    def _screen_brief(self, ctx: UIContext) -> str:
        screen = self._ontology.screen(ctx.screen) if ctx.screen else None
        lines = ([f'"{screen.title}": {" ".join((screen.purpose or "").split())}']
                 if screen else ["Unknown."])
        if ctx.role:
            lines.append(f"The user's role: {role_label(ctx.role, ctx.role)}.")
        if screen:
            blocked = [m.label for m in screen.metrics if m.status is Status.BLOCKED]
            if blocked:
                lines.append("Not available on this screen: " + ", ".join(blocked[:12]) + ".")
        return "\n".join(lines)

    def _screens_for(self, role: str | None) -> str:
        """The screens this user can open, by title - the only ones the model may name."""
        return ", ".join(sc.title for sc in self._ontology.screens.values()
                         if sc.route and sc.route != "/*" and (not role or sc.visible_to(role)))

    def _system_prompt(self, ctx: UIContext, facts: build.Facts | None, *,
                       live: bool = False) -> str:
        # The screens list is data, in both: the only screens that exist for this user.
        parts = [LIVE_GUARDRAILS if live else GUARDRAILS, "--- NAMES ---", NAMES,
                 "--- CURRENT SCREEN ---", self._screen_brief(ctx),
                 "--- SCREENS ---", self._screens_for(ctx.role)]
        if facts and facts.blocks:
            rendered = "\n".join(
                f"[{b['type']}] {b['content']}" for b in facts.blocks)
            parts += ["--- FACTS ---" if live else "--- FACTS (authoritative) ---", rendered]
            if facts.prose_hint:
                parts += ["--- WHAT TO DO ---", facts.prose_hint]
        return "\n\n".join(parts)

    # ── the turn ───────────────────────────────────────────────────────
    def _answers_pending(self, session: Session, text: str
                         ) -> tuple[Capability, str, str | None] | None:
        """Is this message the answer to the question asked last turn?

        "Main wallet" after "Which balance do you mean?" picks that option. Matched
        on the option's label or value; anything else is a new question and the
        pending one is dropped. The question that was asked comes back too, so
        "This month" still answers "which brand is spending the most".
        """
        pending, session.pending = session.pending, None
        if not pending:
            return None
        said = normalise(text)
        for value, label in pending["options"]:
            if normalise(label) in said or normalise(value.replace("_", " ")) in said:
                cap = self._ontology.capability(pending["capability"])
                return (cap, value, pending.get("text")) if cap else None
        return None

    async def respond(self, session: Session, text: str,
                      token: str | None = None) -> AsyncIterator[Block]:
        ctx = session.context
        roles = frozenset({ctx.role} if ctx.role else ())
        permissions = frozenset(session.permissions)

        # Session first: an answer to last turn's question is not a new question.
        answered = self._answers_pending(session, text)
        if answered:
            cap, chosen, asked = answered
            yield Block("capability", cap.id, {"mode": cap.mode.value, "match": "follow_up",
                                               "confidence": 1.0, "chosen": chosen})
            if self._goes_live(cap, session, token):
                live_text = f"{asked} ({text})" if asked else text
                async for block in self._live(session, live_text, cap, token, chosen):
                    yield block
                return
            text = f"{text} ({cap.label})"   # otherwise fall through with the choice in view

        await self._learn_names(session, token)
        route = await self._router.route(self._as_routed(session, text), screen=ctx.screen,
                                         roles=roles, permissions=permissions)
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
            for block in self._authored(session, text, denied_text(cap, ctx.role)):
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
        own_wallet_only = cap.id in WALLET_CAPS and is_campaign_manager(ctx.role)
        # "Which campaign?" needs the user's real campaigns as its options, so the live
        # path asks it. The window defaults to campaign-to-date and is echoed back.
        asks_live = (cap.id in ONE_CAMPAIGN or cap.id in APPROVAL_CAPS or cap.id in BRAND_CAPS
                     or cap.id in REQUEST_CAPS or cap.id in (HISTORY_CAP, RULES_CAP, INVOICE_CAP)
                     # "show me recent transactions" means the latest ones: no period to ask
                     or (cap.id == SPENDING_CAP and spending_kind(text) == "ledger")
                     or (cap.id == "campaign.list" and names_a_state(text)))
        # decide() also silences an entity_choice with nothing to choose, which is
        # why it gets the ontology and the panel's context: /brand-management is
        # the list of brands, so "open brand management" needs no brand.
        ask = None if (own_wallet_only or asks_live) else decide_follow_up(
            cap, screen=ctx.screen, query=text, asked=session.asked,
            ontology=self._ontology, brand_id=ctx.brand_id,
            campaign_id=_campaign_in_context(ctx))
        if ask is not None:
            session.asked[ask.follow_up.id] = session.asked.get(ask.follow_up.id, 0) + 1
            yield _ask(session, text, cap.id, ask.as_block())
            return

        # Live figures, read as the user.
        if self._goes_live(cap, session, token):
            async for block in self._live(session, text, cap, token, None):
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

        # HANDOFF: the authored refusal IS the answer - an OTP refusal must read the
        # same every time, so no model rewords it.
        if cap.mode is Mode.HANDOFF:
            refusal = next((b["content"] for b in facts.blocks if b["type"] == "refusal"), "")
            for block in self._authored(session, text, refusal):
                yield block
            return

        if self._provider is None:
            async for block in self._stub(ctx, text, cap, facts):
                yield block
            return

        async for block in self._narrate(session, text, ctx, facts):
            yield block

    async def _learn_names(self, session: Session, token: str | None) -> None:
        """The user's own campaign and brand names, read once per session before the first
        question is routed - so "how is Monsoon Malabar doing" routes as a question about a
        brand from the first turn, not only after a list has been shown. A read that fails
        leaves routing as it was."""
        if not self._can_read(session, token):
            return
        grant = session.identity.grant_for(session.context.client_id)
        if grant is None or session.names_read_for == grant.client_id:
            return
        session.names_read_for = grant.client_id
        campaigns, brands = await asyncio.gather(list_campaigns(self._bff, token, grant.client_id),
                                                 list_brands(self._bff, token, grant.client_id))
        session.remember("campaign", (c.name for c in campaigns.campaigns))
        session.remember("brand", (b.name for b in brands.brands))

    @staticmethod
    def _as_routed(session: Session, text: str) -> str:
        """The words as routing should read them. Each known name is read as what it is:
        "how is GENIE TEST - approval flow doing" routes as "how is my campaign doing", not
        as a question about approvals, and "open the Monsoon Sale campaign" as "open my
        campaign". The period is dropped: it is what a question is asked over, not what it
        is about. Picking WHICH campaign, and over what period, still reads the user's own
        words."""
        said = normalise(text)
        for name, kind in sorted(session.entities.items(), key=lambda e: -len(e[0])):
            if (n := normalise(name)) and re.search(rf"\b{re.escape(n)}\b", said):
                said = re.sub(rf"\b(?:the |my |this )?{re.escape(n)}(?: {kind})?\b", f"my {kind}", said)
        said = without_period(said)
        return said if said != normalise(text) else text

    # ── live answers ───────────────────────────────────────────────────
    def _goes_live(self, cap: Capability, session: Session, token: str | None) -> bool:
        """Live data ENRICHES an action or a link (which campaign, which brand) but is not
        needed for one: without it, a handoff still gives its authored refusal and a
        navigation its route, exactly as without the live path. Only an answer needs the
        figures, and says so when it cannot read them."""
        if cap.id not in LIVE_CAPS:
            return False
        return self._can_read(session, token) or cap.mode is Mode.ANSWER

    def _can_read(self, session: Session, token: str | None) -> bool:
        return self._bff is not None and bool(token) and session.identity is not None

    async def _live(self, session: Session, text: str, cap: Capability,
                    token: str | None, chosen: str | None) -> AsyncIterator[Block]:
        """Figures read now as this user: a card built by code, then the words under it."""
        identity = session.identity
        if not self._can_read(session, token):
            async for block in self._signed_out(session, text, cap):
                yield block
            return
        grant = identity.grant_for(session.context.client_id)
        if grant is None:
            yield Block("denied", "You don't have access to that account's data.",
                        {"reason": "no_grant"})
            return
        if cap.id in WALLET_CAPS:
            answer = await self._wallet(text, cap, token, grant, identity, chosen)
        elif cap.id == SPENDING_CAP:
            answer = await self._spending(text, token, grant, chosen)
        elif cap.id == INVOICE_CAP:
            answer = await self._invoices(text, token, grant)
        elif cap.id == RULES_CAP:
            answer = rules_answer(await approval_rules(self._bff, token, grant.client_id),
                                  operation_named(text))
        elif cap.id in APPROVAL_CAPS:
            which, operation, status = APPROVAL_CAPS[cap.id]
            if cap.id == "approvals.approve_request":
                operation = operation_named(text)        # "approve the top-up request"
            got = (await awaiting_me(self._bff, token, operation=operation) if which == "queue"
                   else await raised_by_me(self._bff, token, status=status, operation=operation))
            failed = (await failed_after_approval(self._bff, token)
                      if cap.id == "navigate.approvals" else None)
            _remember_campaigns(session, got.requests)
            answer = approvals_answer(cap.id, cap.refusal or "", got, operation=operation,
                                      failed=failed)
        else:
            answer = None
            if cap.id in BRAND_CAPS:
                source = self._brands(session, text, cap, token, grant, identity, chosen)
            elif cap.id in REQUEST_CAPS or cap.id == HISTORY_CAP:
                source = self._requests(session, text, cap, token, grant, chosen)
            else:
                source = self._campaigns(session, text, cap, token, grant, chosen)
            async for item in source:
                if isinstance(item, Block):
                    yield item              # "Which campaign?" - the turn ends on the question
                    return
                answer = item
        if answer.card["items"]:            # no empty card for "you have no campaigns"
            yield Block("metrics", answer.card["title"], answer.card)
        for kind, content, meta in answer.extra:
            yield Block(kind, content, meta)
        final, by, rejected = await self._words(session, text, answer)
        session.add("user", text)
        session.add("model", final)
        yield Block("token", final)
        yield Block("text", final, {"final": True, "live": True, "narrated_by": by,
                                    "rejected_numbers": rejected or None})

    async def _signed_out(self, session: Session, text: str,
                          cap: Capability) -> AsyncIterator[Block]:
        """No live access (not signed in, or no BFF). Say so once, then what the numbers MEAN."""
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

    async def _wallet(self, text, cap, token, grant, identity, chosen) -> LiveAnswer:
        # A campaign manager's "my balance" is their own sub-wallet (catalog gotcha).
        own = is_campaign_manager(grant.role)
        if own:
            view = await my_subwallet(self._bff, token, grant.client_id, identity.user_id)
        else:
            view = await agency_wallet(
                self._bff, token, grant.client_id,
                with_managers=cap.id != "wallet.explain_runway" or is_overview(text))
        return wallet_answer(cap.id, text, view, self._ontology, chosen=chosen,
                             own_subwallet=own)

    async def _spending(self, text, token, grant, chosen) -> LiveAnswer:
        """The ledger, the brand split or a month's rollup - whichever the question asks."""
        window, kind, cid = period_from(text, chosen), spending_kind(text), grant.client_id
        if kind == "ledger":
            return ledger_answer(await ledger(self._bff, token, cid, window), window)
        # A rolling window is not a calendar month, so the rollup cannot answer it.
        if kind == "brands" or (window and window.rolling):
            return brands_answer(await spend_by_brand(self._bff, token, cid, window), window)
        first = window.start if window else None
        return month_answer(await monthly(self._bff, token, cid, first, first), window)

    async def _invoices(self, text, token, grant) -> LiveAnswer:
        """One named month's bill, or what is owed now - from the last months' bills when the
        summary is switched off."""
        window, cid = period_from(text), grant.client_id
        if window and window.named:
            return bill_answer(await bills(self._bff, token, cid, [window.start]), window)
        got = await owed(self._bff, token, cid)
        fallback = (await bills(self._bff, token, cid, recent_months(FALLBACK_MONTHS))
                    if got.problem in ("forbidden", "not_found") else None)
        return owed_answer(got, fallback)

    async def _campaigns(self, session, text, cap, token, grant, chosen):
        """Yields one LiveAnswer — or one follow_up Block when it must ask which campaign.

        The BFF scopes the list to what this user may see (a campaign manager gets
        their own), so the options are never someone else's campaigns.
        """
        listing = await list_campaigns(self._bff, token, grant.client_id)
        session.remember("campaign", (c.name for c in listing.campaigns))
        if any(c.state == "DRAFT" and not c.approval_known for c in listing.campaigns):
            # A draft with an open request reads "awaiting approval (REQ-1136)". The row
            # says so itself; only a row without the approval fields needs the lookup.
            pending = await campaign_requests(self._bff, token, grant.role)
            for c in listing.campaigns:
                if (r := pending.get(c.id)) is not None:
                    c.approval = (r.reference, r.status_label)
        if cap.id == "campaign.list":
            yield list_answer(listing, text, chosen=chosen)
            return
        ctx = session.context
        on_screen = ctx.selected_entity if ctx.screen == "campaign_detail" else None
        pick = pick_campaign(text, listing, chosen_id=chosen, on_screen_id=on_screen,
                             eligible=ELIGIBLE.get(cap.id))
        campaign = pick.campaign
        if campaign is None and pick.wanted_id:
            got = await one_campaign(self._bff, token, pick.wanted_id)
            if isinstance(got, str):
                listing.problem = got
            else:
                campaign = got
        if campaign is None and pick.options:
            yield _ask(session, text, cap.id, which_campaign_block(pick.options))
            return
        if cap.id in ACTION_STATES:
            if campaign is None and pick.wanted_id:
                yield none_to_show(listing, one=True)
            else:
                yield action_answer(cap.id, cap.refusal or "", campaign, listing)
            return
        if cap.id == LAUNCH_CAP and campaign is None and not pick.wanted_id and listing.campaigns:
            yield text_answer("Campaigns", "None of your campaigns is a draft, so nothing is waiting "
                                           "to launch.", authored=True)
            return
        if campaign is None:
            yield none_to_show(listing, one=bool(pick.wanted_id))
            return
        if cap.id == LAUNCH_CAP:
            yield launch_answer(campaign, await launch_check(self._bff, token, campaign))
            return
        if cap.id == "campaign.explain_audience":
            yield audience_answer(campaign, await campaign_gender(self._bff, token, campaign))
            return
        if cap.id == "campaign.explain_breakdown":
            yield breakdown_answer(campaign, await campaign_breakdown(self._bff, token, campaign,
                                                                     group_named(text)))
            return
        if cap.id == "campaign.explain_daypart":
            yield daypart_answer(campaign, await campaign_dayparts(self._bff, token, campaign))
            return
        view = await campaign_view(self._bff, token, campaign)
        yield pacing_answer(view) if cap.id == "campaign.explain_pacing" else performance_answer(view)

    async def _requests(self, session, text, cap, token, grant, chosen):
        """Yields one LiveAnswer - or a follow_up Block when it must ask which request.

        A maker's requests are their own (mine); an approver also has their queue.
        """
        as_maker = is_campaign_manager(grant.role)
        if cap.id == HISTORY_CAP:
            got = (await raised_by_me(self._bff, token) if as_maker
                   else await decided_by_me(self._bff, token))
            _remember_campaigns(session, got.requests)
            yield history_answer(got, as_maker=as_maker)
            return
        mine = await raised_by_me(self._bff, token)
        queue = None if as_maker else await awaiting_me(self._bff, token)
        everything = mine.requests + (queue.requests if queue else [])
        _remember_campaigns(session, everything)
        seen, candidates = set(), []
        for r in everything:                    # an approver's own request is in both lists
            if r.reference not in seen:
                seen.add(r.reference)
                candidates.append(r)
        if not candidates:
            yield no_requests(as_maker)
            return
        request, options = pick_request(text, candidates, chosen=chosen)
        if request is None:
            yield _ask(session, text, cap.id, which_request_block(options))
            return
        if cap.id == "approvals.what_changed":
            yield what_changed_answer(request, await preview_today(self._bff, token, request.reference))
        else:
            yield status_answer(await request_detail(self._bff, token, request.reference),
                                request.reference)

    async def _brands(self, session, text, cap, token, grant, identity, chosen):
        """Yields one LiveAnswer - or a follow_up Block when it must ask which brand, or over
        what period."""
        got = await list_brands(self._bff, token, grant.client_id)
        session.remember("brand", (b.name for b in got.brands))
        if cap.id == "brand.list":
            yield brand_list_answer(got)
            return
        ranking = cap.id == "brand.explain_performance" and asks_for_ranking(text, got)
        brand, options = (None, []) if ranking else pick_brand(
            text, got, chosen_id=chosen, on_screen_id=session.context.brand_id)
        if brand is None and len(options) > 1:
            yield _ask(session, text, cap.id, which_brand_block(options))
            return
        if cap.id in BRAND_LINKS:
            screen = self._ontology.screen(cap.target_screen)
            yield brand_link_answer(screen.id, screen.title, screen.route, brand, got)
            return
        if brand is None and not ranking:
            yield no_brands(got)
            return
        if cap.id == "brand.explain_contacts":
            yield contacts_answer(brand, await brand_contacts(self._bff, token, brand.id))
            return
        # How a brand is doing: over the period named - or asked once, as the catalog says.
        window = None if all_time(text, chosen) else period_from(text, chosen)
        if window is None and not all_time(text, chosen):
            picked = frozenset(f.id for f in cap.follow_ups if f.kind == "entity_choice")  # the brand
            ask = decide_follow_up(cap, screen=session.context.screen, query=text, asked=session.asked,
                                   already_known=picked)
            if ask is not None:
                session.asked[ask.follow_up.id] = session.asked.get(ask.follow_up.id, 0) + 1
                yield _ask(session, text, cap.id, ask.as_block())
                return
            default = next((f.default_after_asks for f in cap.follow_ups if f.kind == "scope"), None)
            window = period_from("", default)       # asked already: the catalog's default
        if ranking:
            ranked = got.brands[:MAX_RANKED]
            yield ranked_answer(got, await many_brand_stats(self._bff, token, ranked, window), window)
        else:
            yield brand_stats_answer(brand, await brand_stats(self._bff, token, brand.id, window), window)

    async def _words(self, session: Session, text: str,
                     answer: LiveAnswer) -> tuple[str, str, list[str]]:
        """The sentence under the card: the template, or a model's number-checked rewording."""
        if (self._provider is None or self._live_wording != "model" or answer.authored
                or not answer.card["items"]):
            return answer.template, "template", []
        # The model rewords the code's answer rather than rebuilding it from the figures,
        # so it cannot lose what the template says ("none is live right now").
        facts = build.Facts(blocks=[{"type": "answer", "content": answer.template, "meta": None}]
                            + answer.facts.blocks, prose_hint=answer.facts.prose_hint)
        # Buffered, not streamed: the number check has to run before the user
        # reads a word, and a card is already on screen while this happens.
        draft: list[str] = []
        try:
            async for chunk in self._provider.stream(
                    [Message(role="user", text=text)],
                    system=self._system_prompt(session.context, facts, live=True),
                    temperature=0.2, max_output_tokens=LIVE_CAP):
                draft.append(chunk)
        except LLMError as err:
            log.warning("live narration failed, using template: %s", err)
        written = "".join(draft).strip()
        # Any number in what the model was given may be repeated - the card's figures,
        # and the caveats and definitions beside them ("18% GST").
        given = [b["content"] for b in facts.blocks]
        rejected = unexplained_numbers(written, answer.allowed + given + [text]) if written else []
        if written and not rejected:
            return written, "model", []
        if rejected:
            log.warning("narration invented %s; using the template", rejected)
        return answer.template, "template", rejected

    def _authored(self, session: Session, text: str, final: str) -> list[Block]:
        """An answer written by us, not a model: a refusal, a denial."""
        session.add("user", text)
        session.add("model", final)
        return [Block("token", final), Block("text", final, {"final": True, "narrated_by": "authored"})]

    async def _narrate(self, session: Session, text: str, ctx: UIContext,
                       facts: build.Facts) -> AsyncIterator[Block]:
        session.add("user", text)
        collected: list[str] = []
        try:
            async for chunk in self._provider.stream(
                list(session.history)[-12:],
                system=self._system_prompt(ctx, facts), max_output_tokens=NARRATE_CAP,
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
            # The panel says which brand's pages the user is in; a selected
            # campaign is the campaign they are looking at. Both fill a route
            # without a BFF call, which is what the 200 ms budget is for.
            return build.navigate_facts(
                cap, self._ontology, ctx.role, brand_id=ctx.brand_id,
                campaign_id=_campaign_in_context(ctx))
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
            screen = self._ontology.screen(metric.screen_id)
            return build.metric_facts(metric, screen.title if screen and screen.route else None)

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
        facts.add("examples", "Questions you can answer: " + "; ".join(UNROUTED_EXAMPLES))
        facts.prose_hint = ("The message did not match anything you can do. In one or two "
                            "sentences, say you are not sure what they mean and suggest two "
                            "of the example questions.")
        collected: list[str] = []
        try:
            async for chunk in self._provider.stream(
                [Message(role="user", text=text)],
                system=self._system_prompt(ctx, facts), max_output_tokens=UNROUTED_CAP,
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
