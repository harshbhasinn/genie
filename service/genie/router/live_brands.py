"""Brands, live: the list, how one is doing, which is doing best, who owns it - and links
to screens that live under a brand.

A brand is picked the way a campaign is: chosen last turn, on screen, named, the only one
- otherwise Genie asks "Which brand?" with the user's own brands. The Brand Summary's
figures are said as the ontology defines them: booked value is ESTIMATED (booked, not
spent), and impressions and reach are per day.

/brand-management/library/{brandId} is not a link until {brandId} is a real brand, and
navigation never calls a model. navigate.brands is NOT a brand link: /brand-management is
a real page - the list of brands - so "open brand management" is answerable without
naming one. Only the screens that have no parameterless form belong in BRAND_LINKS.
"""
from __future__ import annotations

from ..data.brands import Brand, BrandContacts, BrandList, BrandStats
from ..data.fields import Window
from ..data.money import count, count_of, inr, inr_short, pct
from .answers import Facts
from .lexical import tokens
from .live_answers import (LIVE_PROSE, MAX_LISTED, LiveAnswer, and_list, couldnt, entity_choice,
                           names, text_answer)

# Capabilities whose target screen has NO parameterless route, so a brand must be
# resolved before there is a link at all.
BRAND_LINKS = ("navigate.creatives", "campaign.open_wizard")
BRAND_ANSWERS = ("brand.list", "brand.explain_performance", "brand.explain_contacts")
BRAND_CAPS = BRAND_LINKS + BRAND_ANSWERS
MAX_RANKED = 20                   # brands compared for "which brand is doing best"
_RANKING = frozenset(tokens("best top most highest which leading strongest"))


def pick_brand(text: str, got: BrandList, *, chosen_id: str | None = None,
               on_screen_id: str | None = None) -> tuple[Brand | None, list[Brand]]:
    """(the brand, or None) and the options to ask with when there is no single answer."""
    for wanted in (chosen_id, on_screen_id):
        if wanted and (found := got.find(wanted)):
            return found, []
    named = [b for b in got.brands if names(text, b.name)]
    if named:
        return max(named, key=lambda b: len(b.name)), []
    if len(got.brands) == 1:
        return got.brands[0], []
    return None, got.brands[:MAX_LISTED]


def asks_for_ranking(text: str, got: BrandList) -> bool:
    """ "Which brand is performing best" - about all of them, not one. With a single brand
    there is nothing to compare: the answer is that brand's figures."""
    return (len(got.brands) > 1 and bool(set(tokens(text)) & _RANKING)
            and not any(names(text, b.name) for b in got.brands))


def which_brand_block(options: list[Brand]) -> dict:
    return entity_choice("which_brand", "Which brand?",
                         [{"value": b.id, "label": b.name, "hint": ""} for b in options])


def no_brands(got: BrandList) -> LiveAnswer:
    if got.problem:
        return text_answer("Brands", couldnt("read your brands", got.problem), authored=True)
    return text_answer("Brands", "You don't have any brands yet.", authored=True)


def brand_link_answer(screen_id: str, title: str, route: str, brand: Brand | None,
                      got: BrandList) -> LiveAnswer:
    if brand is None:
        if got.problem:
            return no_brands(got)
        return text_answer(title, f"{title} belongs to a brand, and you don't have any brands yet.",
                           authored=True)
    link = route.replace("{brandId}", brand.id)
    return text_answer(title, f"Opening {title} for {brand.name}.", authored=True,
                       extra=[("navigate", link, {"screen_id": screen_id, "title": title,
                                                  "brand": brand.name, "brand_id": brand.id})])


def _when(window: Window | None) -> str:
    if window is None:
        return "all time"
    return f"in {window.label}" if window.named or window.rolling else window.label


def _answer(title: str, items: list[dict], sentences: list[str], lines: list[str],
            caveats: tuple[str, ...] = ()) -> LiveAnswer:
    facts = Facts()
    for line in lines:
        facts.add("live_value", line)
    for c in caveats:
        facts.add("caveat", c)
    facts.prose_hint = LIVE_PROSE
    allowed = [i["display"] for i in items] + [i.get("short") or "" for i in items] + lines
    return LiveAnswer(facts=facts, card={"title": title, "items": items},
                      template=" ".join(sentences), allowed=allowed)


SUMMARY_CAVEATS = ("Booked value is the value of campaigns booked for the brand - an estimate, not "
                   "money already spent.",
                   "Impressions and reach here are per day; reach is people in front of the screens "
                   "each day, not unique people over the period.")


# ── the list ────────────────────────────────────────────────────────────

def brand_list_answer(got: BrandList) -> LiveAnswer:
    """ "List my brands": names, with each brand's campaigns."""
    if got.problem or not got.brands:
        return no_brands(got)
    total = got.total if got.total is not None else len(got.brands)
    named = ", ".join(b.name + (f" ({count_of(b.campaigns, 'campaign')})" if b.campaigns is not None else "")
                      for b in got.brands[:MAX_LISTED])
    more = total - min(len(got.brands), MAX_LISTED)
    sentences = [f"You have {count_of(total, 'brand')}: {named}" + (f" and {count(more)} more." if more > 0 else ".")]
    items = [{"metric_id": "brands_managed", "label": "Brands", "display": count(total)}]
    lines = [f"Brands in all: {count(total)}"] + [
        f"{b.name}: " + ", ".join(x for x in (
            count_of(b.campaigns, "campaign") if b.campaigns is not None else "",
            f"budget {inr(b.budget)}" if b.budget is not None else "") if x)
        for b in got.brands[:10]]
    answer = _answer("Brands", items, sentences, lines)
    answer.card["brands"] = [{"id": b.id, "name": b.name, "campaigns": b.campaigns,
                              "budget": inr(b.budget) if b.budget is not None else None}
                             for b in got.brands[:10]]
    return answer


# ── one brand's figures ─────────────────────────────────────────────────

def brand_stats_answer(brand: Brand, stats: BrandStats, window: Window | None) -> LiveAnswer:
    if stats.problem:
        return text_answer(brand.name, couldnt(f"read {brand.name}'s figures", stats.problem))
    when = _when(window)
    figures = [(m, label, v, fmt) for m, label, v, fmt in (
        ("brand_live_campaigns", "Live campaigns", stats.live_campaigns, count),
        ("screens_used", "Screens in use", stats.screens_used, count),
        ("brand_daily_impressions", "Impressions per day", stats.impressions_per_day, count),
        ("brand_audience_reach_per_day", "Reach per day", stats.reach_per_day, count),
        ("brand_booked_revenue", "Booked value (estimated)", stats.booked_value, inr),
    ) if v is not None]
    items = [{"metric_id": m, "label": label, "display": fmt(v),
              "short": inr_short(v) if fmt is inr else None} for m, label, v, fmt in figures]
    parts = []
    if stats.live_campaigns is not None:
        parts.append(count_of(stats.live_campaigns, "live campaign")
                     + (f" on {count_of(stats.screens_used, 'screen')}" if stats.screens_used is not None else ""))
    if stats.impressions_per_day is not None:
        parts.append(f"about {count(stats.impressions_per_day)} impressions a day")
    if stats.reach_per_day is not None:
        parts.append(f"{count(stats.reach_per_day)} people a day in front of its screens")
    sentences = [f"{brand.name}, {when}: {and_list(parts)}."] if parts else []
    if stats.booked_value is not None:
        sentences.append(f"Campaigns booked for it are worth an estimated {inr(stats.booked_value)} - "
                         f"booked, not yet spent.")
    if stats.by_city:
        cities = ", ".join(f"{c} {pct(p)} ({count(n)})" if p is not None else f"{c} ({count(n)})"
                           for c, n, p in stats.by_city[:MAX_LISTED])
        sentences.append(f"Its screens: {cities}.")
    if not figures:
        sentences = [f"The dashboard returned no figures for {brand.name} {when}."]
    elif not any(v for _, _, v, _ in figures) and not any(n for _, n, _ in stats.by_city):
        sentences = [f"{brand.name} had nothing running {when}: no screens, impressions or reach, "
                     f"and nothing booked."]
    lines = [f"{label}: {fmt(v)}" for _, label, v, fmt in figures] + [
        f"Screens in {c}: {count(n)}" + (f" ({pct(p)})" if p is not None else "") for c, n, p in stats.by_city]
    return _answer(brand.name, items, sentences, lines, SUMMARY_CAVEATS)


def ranked_answer(got: BrandList, stats: list[BrandStats], window: Window | None) -> LiveAnswer:
    """ "Which brand is doing best": ranked by impressions a day, the measured figure."""
    when = _when(window)
    rows = sorted(((b, s) for b, s in zip(got.brands, stats)
                   if not s.problem and s.impressions_per_day), key=lambda bs: -bs[1].impressions_per_day)
    if not rows:
        failed = next((s.problem for s in stats if s.problem), None)
        text = (couldnt("read your brands' figures", failed) if failed
                else f"None of your brands recorded impressions {when}.")
        return text_answer("Brands", text)
    top = ", ".join(f"{b.name} ({count(s.impressions_per_day)} a day"
                    + (f", {count_of(s.live_campaigns, 'live campaign')}" if s.live_campaigns is not None else "")
                    + ")" for b, s in rows[:3])
    sentences = [f"By impressions a day {when}, your leading brands are {top}."]
    total = got.total if got.total is not None else len(got.brands)
    if total > len(stats):
        sentences.append(f"That compares the first {count(len(stats))} of your {count(total)} brands.")
    items = [{"metric_id": "brand_daily_impressions", "label": b.name,
              "display": count(s.impressions_per_day)} for b, s in rows[:10]]
    lines = [f"{b.name}: {count(s.impressions_per_day)} impressions a day"
             + (f", booked value {inr(s.booked_value)}" if s.booked_value is not None else "")
             for b, s in rows[:10]]
    return _answer("Brands by impressions a day", items, sentences, lines, SUMMARY_CAVEATS)


# ── who owns a brand ────────────────────────────────────────────────────

def contacts_answer(brand: Brand, who: BrandContacts) -> LiveAnswer:
    """The brand's own contact, and the partner's campaign manager for it - two people."""
    if who.problem:
        return text_answer(brand.name, couldnt(f"read {brand.name}'s contacts", who.problem),
                           authored=True)
    sentences = []
    if who.contact:
        reach_them = " - ".join(x for x in (who.email, who.phone) if x)
        sentences.append(f"{brand.name}'s contact is {who.contact}"
                         + (f" ({who.designation})" if who.designation else "")
                         + (f": {reach_them}" if reach_them else "") + ".")
    else:
        sentences.append(f"{brand.name} has no contact on record.")
    sentences.append(f"{who.managed_by} manages it on your side." if who.managed_by
                     else "No campaign manager is assigned to it.")
    items = [{"metric_id": "brand_contact", "label": label, "display": value}
             for label, value in (("Contact", who.contact), ("Managed by", who.managed_by)) if value]
    return LiveAnswer(facts=Facts(), card={"title": brand.name, "items": items},
                      template=" ".join(sentences), authored=True)
