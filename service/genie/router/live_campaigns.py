"""Answers about campaigns, built from live figures.

Same contract as the wallet (live_answers.py): the card is code, the sentence is
code, and a model — only when GENIE_LIVE_WORDING=model — may rephrase the facts,
number-checked against the card. The rules learnt from the wallet's live runs
apply from the start:

  · no raw ontology gotchas: one user-facing line per answer, written here
  · no invented screens: the only place offered is the campaign's own page,
    from its real route, and only in NEXT STEPS
  · short: one paragraph, at most three sentences when a model words it
  · lead with the figures; say something is missing only when it is
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.campaigns import (NOT_STARTED, RUNNING, STATE_LABEL, Breakdown, Campaign, CampaignList,
                              LaunchCheck,
                              CampaignView, Dayparts, GenderSplit, flight_days)
from ..data.fields import day, humanize
from ..data.money import count, count_of, pct
from .answers import Facts
from .lexical import tokens
from .live_answers import (LIVE_PROSE, MAX_LISTED, PROBLEM_TEXT, LiveAnswer, and_list, between,
                           couldnt,
                           entity_choice, names, text_answer)

PLAYED = frozenset({"COMPLETED"}) | RUNNING

# Capabilities about ONE campaign: Genie picks it from the user's own list.
# For the OTP-gated actions, only campaigns the action can apply to are candidates
# (catalog preconditions): pause a live one, resume a paused one, cancel one that
# has not finished. None = any state.
ACTION_STATES: dict[str, frozenset[str] | None] = {
    "campaign.open": None,
    "campaign.pause": frozenset({"LIVE", "NOT_PLAYING"}),
    "campaign.resume": frozenset({"PAUSED"}),
    "campaign.cancel": frozenset({"SCHEDULED", "LIVE", "PAUSED", "NOT_PLAYING"}),
}
LAUNCH_CAP = "creative.explain_launch_blocker"
# The campaigns a question can be about, by state (None = any): an action's own states, and
# drafts for "what's blocking my launch" - nothing else has a launch left to block.
ELIGIBLE = {**ACTION_STATES, LAUNCH_CAP: frozenset({"DRAFT"})}
ONE_CAMPAIGN = ("campaign.explain_performance", "campaign.explain_pacing",
                "campaign.explain_audience", "campaign.explain_breakdown",
                "campaign.explain_daypart", LAUNCH_CAP) + tuple(ACTION_STATES)
CAMPAIGN_CAPS = ONE_CAMPAIGN + ("campaign.list",)
_ACTION_WORDS = {"campaign.pause": ("pause", "paused"), "campaign.resume": ("resume", "resumed"),
                 "campaign.cancel": ("cancel", "cancelled")}


# catalog: "which_state" options -> the states each one means
STATE_CHOICES = {"live": RUNNING, "scheduled": frozenset({"SCHEDULED"}),
                 "draft": frozenset({"DRAFT"}), "all": None}
_STATE_WORDS = {"live": {"live", "running", "active", "playing", "delivering", "current"},
                "scheduled": {"scheduled", "upcoming", "planned", "starting"},
                "draft": {"draft", "drafts", "unfinished"}}


# ── which campaign ──────────────────────────────────────────────────────

@dataclass(slots=True)
class Pick:
    campaign: Campaign | None = None
    options: list[Campaign] | None = None      # ask "Which campaign?" with these
    wanted_id: str | None = None               # named by id but not in the list page


def _named(text: str, listing: CampaignList) -> Campaign | None:
    """A campaign whose name the user wrote. The longest match wins."""
    hits = [c for c in listing.campaigns if names(text, c.name)]
    return max(hits, key=lambda c: len(c.name)) if hits else None


def pick_campaign(text: str, listing: CampaignList, *, chosen_id: str | None = None,
                  on_screen_id: str | None = None,
                  eligible: frozenset[str] | None = None) -> Pick:
    """Chosen from last turn's options, then on screen, then named, then the only candidate.

    `eligible` narrows the candidates to the states an action applies to. A campaign
    the user chose or named is returned whatever its state, so the answer can say why
    the action does not apply to it.
    """
    for wanted in (chosen_id, on_screen_id):
        if wanted:
            found = listing.find(wanted)
            return Pick(campaign=found) if found else Pick(wanted_id=wanted)
    if (named := _named(text, listing)) is not None:
        return Pick(campaign=named)
    # Running first; then ones that ran or will run; drafts and cancelled only if
    # nothing else exists. One candidate is answered, never asked about (live QA
    # asked "Which campaign?" with a single option, and offered a draft first).
    ladder = (eligible,) if eligible is not None else (RUNNING, PLAYED | {"SCHEDULED"}, None)
    for states in ladder:
        pool = [c for c in listing.campaigns if states is None or c.state in states]
        if len(pool) == 1:
            return Pick(campaign=pool[0])
        if pool:
            return Pick(options=pool[:MAX_LISTED])                  # newest first
    return Pick()


def which_campaign_block(options: list[Campaign]) -> dict:
    return entity_choice("which_campaign", "Which campaign?",
                         [{"value": c.id, "label": c.name,
                           "hint": c.state_label + (f", ends {day(c.end)}" if c.end else "")}
                          for c in options])


# ── one campaign ────────────────────────────────────────────────────────

def _state_sentence(view: CampaignView) -> str:
    c = view.campaign
    days = flight_days(c, view.today)
    on_day = (f", on day {days[0]} of its {days[1]}-day flight ({round(100 * days[0] / days[1])}%)"
              if days else "")
    if c.state == "LIVE":
        return f"{c.name} is live{on_day}."
    if c.state == "PAUSED":
        return f"{c.name} is paused{on_day}."
    if c.state == "NOT_PLAYING":
        return (f"{c.name} is not playing right now"
                + (f": {c.not_playing_reason}." if c.not_playing_reason else "."))
    if c.state == "SCHEDULED":
        return (f"{c.name} is scheduled" + (f" to start on {day(c.start)}" if c.start else "")
                + ", so nothing has played yet.")
    if c.state == "DRAFT" and c.approval:
        ref, status = c.approval
        return f"{c.name} is a draft awaiting approval ({ref}), so nothing has played yet." \
            if status == "pending" else f"{c.name} is a draft; its approval request {ref} was {status}."
    if c.state == "DRAFT":
        return (f"{c.name} is a draft: it is not confirmed, so it holds no screens and nothing "
                "has played.")
    if c.state == "COMPLETED":
        return f"{c.name} finished" + (f" on {day(c.end)}." if c.end else ".")
    if c.state == "CANCELLED":
        return f"{c.name} was cancelled."
    return f"{c.name} is {c.state_label.lower()}."


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def performance_answer(view: CampaignView) -> LiveAnswer:
    c, f = view.campaign, view.fact
    sentences = [_state_sentence(view)]

    imp, reach, spend = f("campaign_impressions"), f("campaign_reach"), f("campaign_spend")
    when = between(view.window)
    if imp:
        sentences.append(f"It recorded {imp.display} impressions {when}.")

    # Reach and cost are the PLAN (data/campaigns.py), so they are said as the plan.
    plan = []
    if spend:
        if c.state == "COMPLETED" or (c.total_cost is not None and spend.value == c.total_cost):
            plan.append(f"it cost {spend.display}")
        else:
            plan.append(f"{spend.display} of its cost is scheduled to date")
    if reach:
        plan.append(f"it was booked to reach about {reach.display} people")
    if plan:
        sentences.append(_cap(" and ".join(plan)) + ".")

    screens, stores, cities = f("campaign_active_screens"), f("campaign_stores"), f("campaign_cities")
    if screens and c.state not in NOT_STARTED:
        verb = "ran" if c.state == "COMPLETED" else "runs"
        where = f"It {verb} on {count_of(screens.value, 'screen')}"
        if stores:
            where += f" in {count_of(stores.value, 'store')}"
        if cities:
            where += f" across {count_of(cities.value, 'city', 'cities')}"
        sentences.append(where + ".")

    missing = sorted({PROBLEM_TEXT.get(k, k) for k in view.problems.values()})
    if missing:
        sentences.append(f"Delivery figures are missing because {missing[0]}.")

    shown = view.facts
    facts = Facts()
    for x in shown:
        facts.add("live_value", x.fact_line(), metric_id=x.metric_id, source=x.source)
    if view.window:
        facts.add("live_value", f"Delivery window: {day(view.window[0])} to {day(view.window[1])}")
    if reach or spend:
        facts.add("caveat", "Planned reach is the audience the booked screens and time slots are "
                            "expected to reach, and cost to date is the campaign's cost spread evenly "
                            "over its days. Both are the plan, not a measure of plays; impressions are "
                            "the measured plays.")
    if c.route:
        # Not the route itself: a model handed a URL prints it (live QA, the number
        # check caught UUID fragments). The link goes out as its own navigate block.
        facts.add("next_steps", "The campaign's own page shows the daily figures.")
    for reason in missing:
        facts.add("unavailable", reason)
    facts.prose_hint = LIVE_PROSE

    card = {"title": c.name, "items": [x.as_meta() for x in shown], "campaign": c.as_meta()}
    if view.window:
        card["window"] = f"{day(view.window[0])} – {day(view.window[1])}"
    allowed = ([x.display for x in shown] + [x.short for x in shown if x.short]
               + [c.name, card.get("window", "")] + [day(d) for d in (c.start, c.end) if d])
    return LiveAnswer(facts=facts, card=card, template=" ".join(sentences), allowed=allowed)


def action_answer(cap_id: str, refusal: str, c: Campaign | None,
                  listing: CampaignList) -> LiveAnswer:
    """Open one campaign, or hand off an OTP-gated action on it with a link to its page.

    The refusal is the catalog's authored text; nothing here is reworded by a model.
    """
    if c is None:
        if listing.problem or not listing.campaigns:
            return none_to_show(listing)
        verb, _ = _ACTION_WORDS.get(cap_id, ("open", "opened"))
        wanted = {"campaign.pause": "live", "campaign.resume": "paused",
                  "campaign.cancel": "running or scheduled"}.get(cap_id, "")
        text = f"You have no {wanted} campaign to {verb} right now."
        return text_answer("Campaigns", text, authored=True)

    link = ([("navigate", c.route, {"screen_id": "campaign_detail", "title": c.name,
                                    "label": c.name, "campaign_id": c.id})] if c.route else [])
    states = ACTION_STATES.get(cap_id)
    if cap_id == "campaign.open":
        text = f"Opening {c.name}." if c.route else f"I couldn't find the page for {c.name}."
    elif states is not None and c.state not in states:
        _, past = _ACTION_WORDS[cap_id]
        text = f"{c.name} is {c.state_label.lower()}, so it can't be {past}."
    else:
        text = " ".join(refusal.split()) + (f" Here is {c.name}." if c.route else "")
    return LiveAnswer(facts=Facts(), card={"title": c.name, "items": [], "campaign": c.as_meta()},
                      template=text, extra=link, authored=True)


def pacing_answer(view: CampaignView) -> LiveAnswer:
    """Is it on track? Time elapsed and delivery are served; delivery against PLAN is not.

    The cost schedule follows the calendar by construction, so comparing it with time
    elapsed would always say "on track". It is shown, not used as a verdict.
    """
    base = performance_answer(view)
    c, f = view.campaign, view.fact
    sentences = [_state_sentence(view)]
    imp = f("campaign_impressions")
    when = between(view.window)
    if imp:
        sentences.append(f"It recorded {imp.display} impressions {when}.")
    if c.state in RUNNING or c.state == "COMPLETED":
        sentences.append("The dashboard doesn't report planned impressions, so I can't say whether "
                         "delivery is ahead of or behind plan.")
    base.template = " ".join(sentences)
    base.facts.add("unavailable", "Delivery against plan: no endpoint returns planned impressions.")
    return base


def audience_answer(c: Campaign, split: GenderSplit) -> LiveAnswer:
    """Who saw it: the gender split of its impressions. Age is not served per campaign."""
    if c.state in NOT_STARTED:
        text = f"{c.name} hasn't started, so there is no audience to report yet."
        return text_answer(c.name, text)
    if split.problem:
        text = couldnt(f"read the audience for {c.name}", split.problem)
        return text_answer(c.name, text)
    when = between(split.window)
    items = [{"metric_id": "campaign_audience_profile", "label": label,
              "display": pct(share)} for label, share in split.shares]
    if items:
        parts = ", ".join(f"{i['label']} {i['display']}" for i in items)
        total = f"{count(split.total)} " if split.total is not None else ""
        sentences = [f"{c.name}'s {total}impressions {when} split by gender: {parts}."]
    else:
        sentences = [f"The dashboard returned no gender split for {c.name} {when}."]
    sentences.append("Age isn't reported per campaign.")
    facts = Facts()
    for i in items:
        facts.add("live_value", f"Impressions to {i['label']}: {i['display']}")
    facts.add("caveat", "This splits impressions (plays), not people.")
    facts.prose_hint = LIVE_PROSE
    card = {"title": f"{c.name} · audience", "items": items, "campaign": c.as_meta()}
    allowed = ([i["display"] for i in items]
               + [count(split.total) if split.total is not None else "", c.name]
               + ([day(d) for d in split.window] if split.window else []))
    return LiveAnswer(facts=facts, card=card, template=" ".join(sentences), allowed=allowed)


# ── where and when it played ────────────────────────────────────────────

_GROUP_WORDS = (("CATEGORY", {"store", "stores", "shop", "shops", "category", "categories",
                              "type", "types",
                              "kind", "outlet", "outlets"}),
                ("DAYPART", {"daypart", "dayparts", "slot", "slots"}))
GROUP_NOUN = {"CITY": "city", "CATEGORY": "store type", "DAYPART": "daypart"}


def group_named(text: str) -> str:
    """CITY unless the words ask for store types or dayparts - the dashboard's own default."""
    said = set(tokens(text))
    return next((g for g, words in _GROUP_WORDS if said & words), "CITY")


def _not_started(c: Campaign) -> LiveAnswer:
    text = f"{c.name} hasn't started, so nothing has played yet."
    return text_answer(c.name, text)


def _nothing_to_split(c: Campaign, rows: list, total: int | None, when: str, what: str) -> LiveAnswer:
    """Rows that all read 0 are a measured zero; no rows at all is no breakdown."""
    if rows or total == 0:
        text = f"{c.name} recorded 0 impressions {when}, so there's {what}."
    else:
        text = f"The dashboard returned no breakdown for {c.name} {when}."
    facts = Facts()
    facts.add("live_value", text)
    return text_answer(c.name, text, facts=facts)


def breakdown_answer(c: Campaign, got: Breakdown) -> LiveAnswer:
    """Impressions by city, store type or daypart, most first. Plays, not people."""
    if c.state in NOT_STARTED:
        return _not_started(c)
    noun = GROUP_NOUN.get(got.group, got.group.lower())
    if got.problem:
        text = couldnt(f"read {c.name}'s impressions by {noun}", got.problem)
        return text_answer(c.name, text)
    when = between(got.window)
    rows = [r for r in got.rows if r[1]]
    if not rows:
        return _nothing_to_split(c, got.rows, got.total, when, f"no {noun} to compare")
    items = [{"metric_id": "campaign_impressions_by_" + noun.replace(" ", "_"), "label": label,
              "display": count(n), "share": pct(share) if share is not None else None,
              "screens": screens} for label, n, share, screens in rows[:10]]
    listed = ", ".join(f"{i['label']} {i['display']}" + (f" ({i['share']})" if i["share"] else "")
                       for i in items[:MAX_LISTED])
    more = len(rows) - MAX_LISTED
    total = f"{count(got.total)} " if got.total is not None else ""
    sentences = [f"{c.name}'s {total}impressions {when} by {noun}, most first: {listed}"
                 + (f" and {more} more." if more > 0 else ".")]
    facts = Facts()
    for i in items:
        facts.add("live_value", f"Impressions in {i['label']}: {i['display']}"
                  + (f" ({i['share']})" if i["share"] else "")
                  + (f", on {i['screens']} screens" if i["screens"] is not None else ""))
    facts.add("caveat", "These are impressions (plays), not people.")
    facts.prose_hint = LIVE_PROSE
    card = {"title": f"{c.name} · by {noun}", "items": items, "campaign": c.as_meta()}
    allowed = ([i["display"] for i in items] + [i["share"] or "" for i in items]
               + [str(i["screens"]) for i in items]
               + [count(got.total) if got.total is not None else "", c.name]
               + ([day(d) for d in got.window] if got.window else []))
    return LiveAnswer(facts=facts, card=card, template=" ".join(sentences), allowed=allowed)


def _hour(h: int) -> str:
    def h12(x: int) -> str:
        x %= 24
        return f"{x % 12 or 12} {'am' if x < 12 else 'pm'}"
    return f"{h12(h)} to {h12(h + 1)}"


def daypart_answer(c: Campaign, got: Dayparts) -> LiveAnswer:
    """When in the day its impressions came, and each daypart's busiest hour."""
    if c.state in NOT_STARTED:
        return _not_started(c)
    if got.problem:
        text = couldnt(f"read when {c.name} played", got.problem)
        return text_answer(c.name, text)
    when = between(got.window)
    slots = [s for s in got.slots if s[1]]
    if not slots:
        return _nothing_to_split(c, got.slots, got.total, when, "no time of day to compare")
    label = lambda code: (humanize(code) or "").capitalize()       # noqa: E731
    items = [{"metric_id": "campaign_impressions_by_daypart", "label": label(code), "display": count(n),
              "share": pct(share) if share is not None else None,
              "peak_hour": _hour(peak) if peak is not None else None} for code, n, share, peak in slots]
    top = items[0]
    lead = (f"Most of {c.name}'s impressions {when} came in the {top['label'].lower()}: "
            f"{top['display']}"
            + (f" ({top['share']})" if top["share"] else "")
            + (f", busiest from {top['peak_hour']}" if top["peak_hour"] else "") + ".")
    sentences = [lead]
    if len(items) > 1:
        sentences.append("Then " + ", ".join(f"{i['label'].lower()} {i['display']}"
                                             + (f" ({i['share']})" if i["share"] else "")
                                             for i in items[1:]) + ".")
    facts = Facts()
    for i in items:
        facts.add("live_value", f"{i['label']}: {i['display']} impressions"
                  + (f" ({i['share']})" if i["share"] else "")
                  + (f", busiest hour {i['peak_hour']}" if i["peak_hour"] else ""))
    if got.outside:
        facts.add("live_value", f"Impressions outside any daypart: {count(got.outside)}")
    facts.add("caveat", "These are impressions (plays), not people.")
    facts.prose_hint = LIVE_PROSE
    card = {"title": f"{c.name} · time of day", "items": items, "campaign": c.as_meta()}
    allowed = ([i["display"] for i in items] + [i["share"] or "" for i in items]
               + [i["peak_hour"] or "" for i in items] + [c.name, count(got.outside or 0)]
               + ([day(d) for d in got.window] if got.window else []))
    return LiveAnswer(facts=facts, card=card, template=" ".join(sentences), allowed=allowed)


# ── what a draft still needs ────────────────────────────────────────────

ZONES = (("HERO", "image"), ("SHOWCASE", "video"))         # zone -> the slot it fills
WAITING = {"AWAITING_AI_VERDICT": "being checked automatically",
           "PENDING_REVIEW": "waiting for a person to review"}


def launch_answer(c: Campaign, check: LaunchCheck) -> LiveAnswer:
    """Against the wizard's checklist: screens chosen, every slot filled, every creative
    approved. A campaign confirms only with all three - there is no launching with a
    creative still in review."""
    if check.problem:
        return text_answer(c.name, couldnt(f"read {c.name}", check.problem), authored=True)
    if c.state != "DRAFT":
        return text_answer(c.name, f"{c.name} is {c.state_label.lower()}, so it is already past "
                                   f"launch.", authored=True)
    wanted = {"HERO": check.image_slots, "SHOWCASE": check.video_slots}
    have = {zone: sum(1 for x in check.creatives if x.zone == zone) for zone, _ in ZONES}
    missing = []
    if not check.has_screens:
        missing.append("no screens are chosen yet")
    for zone, kind in ZONES:
        short = wanted[zone] - have[zone]
        if short > 0:
            missing.append(f"{count(short)} of {count_of(wanted[zone], kind + ' slot')} "
                           f"{'has' if short == 1 else 'have'} no creative")
    rejected = [x for x in check.creatives if x.review == "REJECTED"]
    if rejected:
        why = f" ({rejected[0].reason})" if len(rejected) == 1 and rejected[0].reason else ""
        missing.append(f"{count_of(len(rejected), 'creative')} "
                       f"{'was' if len(rejected) == 1 else 'were'} rejected{why}")
    for status, doing in WAITING.items():
        n = sum(1 for x in check.creatives if x.review == status)
        if n:
            missing.append(f"{count_of(n, 'creative')} {'is' if n == 1 else 'are'} {doing}")
    if missing:
        text = (f"{c.name} can't launch yet: {and_list(missing)}. It can be confirmed once every "
                f"slot has an approved creative.")
    elif c.approval and c.approval[1] == "pending":
        text = f"Nothing is missing from {c.name}: it is awaiting approval ({c.approval[0]})."
    else:
        text = (f"Nothing is missing from {c.name}: every slot has an approved creative, so it is "
                f"ready to confirm - confirming needs your OTP.")
    approved = sum(1 for x in check.creatives if x.review == "APPROVED")
    items = [{"metric_id": "launch_creatives", "label": f"{kind.capitalize()} creatives",
              "display": f"{have[zone]} of {wanted[zone]}"} for zone, kind in ZONES if wanted[zone]]
    items.append({"metric_id": "launch_creatives", "label": "Approved",
                  "display": f"{approved} of {len(check.creatives)}"})
    return LiveAnswer(facts=Facts(), card={"title": c.name, "items": items, "campaign": c.as_meta()},
                      template=text, authored=True)


def none_to_show(listing: CampaignList, *, one: bool = False) -> LiveAnswer:
    """No campaign to answer about: say why. `one` = a single campaign was asked for."""
    if listing.problem:
        what = "open that campaign" if one else "read your campaigns"
        text = couldnt(what, listing.problem)
    else:
        text = "You don't have any campaigns yet."
    facts = Facts()
    facts.add("unavailable" if listing.problem else "live_value", text)
    facts.prose_hint = LIVE_PROSE
    return text_answer("Campaigns", text, facts=facts)


# ── the list ────────────────────────────────────────────────────────────

def names_a_state(text: str) -> bool:
    """ "What campaigns are running" has already answered "Which campaigns?"."""
    return state_wanted(text, None) != "all"


def state_wanted(text: str, chosen: str | None) -> str:
    if chosen in STATE_CHOICES:
        return chosen
    said = set(tokens(text))
    for key, words in _STATE_WORDS.items():
        if said & words:
            return key
    return "all"


def list_answer(listing: CampaignList, text: str, *, chosen: str | None = None) -> LiveAnswer:
    if listing.problem or not listing.campaigns:
        return none_to_show(listing)
    wanted = state_wanted(text, chosen)
    states = STATE_CHOICES[wanted]
    groups = listing.by_state()
    total = listing.total if listing.total is not None else len(listing.campaigns)

    order = [s for s in STATE_LABEL if s in groups] + [s for s in groups if s not in STATE_LABEL]
    breakdown = ", ".join(f"{len(groups[s])} {STATE_LABEL.get(s, s).lower()}" for s in order)
    sentences = [f"You have {count_of(total, 'campaign')}"
                 + (f": {breakdown}." if breakdown else ".")]
    if listing.partial:
        sentences.append(f"That breakdown covers the newest {len(listing.campaigns)}.")

    picked = [c for c in listing.campaigns if states is None or c.state in states]
    label = {"live": "running", "scheduled": "scheduled", "draft": "drafts", "all": "newest"}[wanted]
    if picked:
        names = ", ".join(c.name + (f" (awaiting approval, {c.approval[0]})"
                                    if c.approval and c.approval[1] == "pending" else "")
                          for c in picked[:MAX_LISTED])
        more = len(picked) - MAX_LISTED
        sentences.append(f"{label.capitalize()}: {names}" + (f" and {more} more." if more > 0 else "."))
    elif wanted != "all":
        sentences.append(f"None is {'live' if wanted == 'live' else label.rstrip('s')} right now.")
    if wanted == "draft":
        sentences.append("Drafts show only the ones you started yourself.")

    facts = Facts()
    facts.add("live_value", f"Campaigns in total: {count(total)}")
    for s in order:
        facts.add("live_value", f"{STATE_LABEL.get(s, s)}: {len(groups[s])}")
    for c in picked[:10]:
        facts.add("live_value", f"{c.name}: {c.state_label}"
                  + (f", {day(c.start)} to {day(c.end)}" if c.start and c.end else ""))
    facts.prose_hint = LIVE_PROSE

    card = {"title": "Campaigns",
            "items": [{"metric_id": "campaign_count", "label": "Campaigns", "display": count(total)}]
                     + [{"metric_id": f"campaign_count_{s.lower()}", "label": STATE_LABEL.get(s, s),
                         "display": str(len(groups[s]))} for s in order],
            "campaigns": [c.as_meta() for c in picked[:10]]}
    allowed = ([count(total), str(len(listing.campaigns))] + [str(len(g)) for g in groups.values()]
               + [c.name for c in picked[:10]]
               + [day(d) for c in picked[:10] for d in (c.start, c.end) if d])
    return LiveAnswer(facts=facts, card=card, template=" ".join(sentences), allowed=allowed)
