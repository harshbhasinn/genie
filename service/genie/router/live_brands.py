"""Links to screens that live under a brand, with the brand filled in.

/brands/{brandId}/creatives is not a link until {brandId} is a real brand. Genie
picks it the same way it picks a campaign: the brand on screen, then one the user
named, then the only one - otherwise it asks "Which brand?" with the user's own
brands. Navigation never calls a model.
"""
from __future__ import annotations

from ..data.brands import Brand, BrandList
from .live_answers import MAX_LISTED, LiveAnswer, couldnt, entity_choice, names, text_answer

# navigate capabilities whose target screen's route needs {brandId}
BRAND_CAPS = ("navigate.brands", "navigate.creatives", "campaign.open_wizard")


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


def which_brand_block(options: list[Brand]) -> dict:
    return entity_choice("which_brand", "Which brand?",
                         [{"value": b.id, "label": b.name, "hint": ""} for b in options])


def brand_link_answer(screen_id: str, title: str, route: str, brand: Brand | None,
                      got: BrandList) -> LiveAnswer:
    if brand is None:
        if got.problem:
            text = couldnt("read your brands", got.problem)
        else:
            text = f"{title} belongs to a brand, and you don't have any brands yet."
        return text_answer(title, text, authored=True)
    link = route.replace("{brandId}", brand.id)
    return text_answer(title, f"Opening {title} for {brand.name}.", authored=True,
                       extra=[("navigate", link, {"screen_id": screen_id, "title": title,
                                                  "brand": brand.name, "brand_id": brand.id})])
