"""The period a question is about: "this month", "last month", "in September", "last 7 days".

Read from the words, or from the option chosen when Genie asked "Over what period?". A
period the user names is sent to the BFF; with none, an answer uses its own default.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from ..data.fields import Window
from ..data.fields import today as _today_ist   # `today` is also these functions' parameter
from .lexical import normalise

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS |= {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_MONTHS["sept"] = 9
_YEAR = re.compile(r"\b(20\d\d)\b")
_LAST_DAYS = re.compile(r"\b(?:last|past) (\d{1,3}) days\b")
# Any period the user can name, with the word that introduces it - what without_period drops.
_PERIOD = re.compile(
    r"\b(?:(?:in|for|over|during) )?(?:all time|ever|this month|(?:last|previous) month"
    r"|(?:the )?(?:last|past) \d{1,3} days"
    r"|(?:in|for|of) (?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")(?: 20\d\d)?)\b")
_CHOSEN_DAYS = re.compile(r"^last_(\d{1,3})_days$")       # a follow-up option: last_7_days


def month_window(year: int, month: int, today: date) -> Window:
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return Window(start, min(end, today), f"{start:%B %Y}", named=True)


def all_time(text: str, chosen: str | None = None) -> bool:
    """ "all time" / "ever" - the whole history, so no dates are sent."""
    return chosen == "all_time" or bool(re.search(r"\b(all time|ever)\b", normalise(text)))


def period_from(text: str, chosen: str | None = None, today: date | None = None) -> Window | None:
    """The window the user named - or chose from "Over what period?" - if any."""
    today = today or _today_ist()
    said = normalise(text)
    if chosen == "this_month" or "this month" in said:
        return Window(today.replace(day=1), today, "this month")
    if chosen == "last_month" or "last month" in said or "previous month" in said:
        prev = today.replace(day=1) - timedelta(days=1)
        return month_window(prev.year, prev.month, today)
    if m := (_CHOSEN_DAYS.match(chosen or "") or _LAST_DAYS.search(said)):
        n = int(m.group(1))
        return Window(today - timedelta(days=n - 1), today, f"the last {n} days", rolling=True)
    words = said.split()
    for i, w in enumerate(words):
        # "may" is a month only as "in May", "for May" or with a year - not "may I see".
        if w in _MONTHS and (w != "may" or (i and words[i - 1] in ("in", "of", "for"))
                             or _YEAR.search(text)):
            month = _MONTHS[w]
            year = int(m.group(1)) if (m := _YEAR.search(text)) else (
                today.year if month <= today.month else today.year - 1)
            return month_window(year, month, today)
    return None


def without_period(text: str) -> str:
    """The words with the period taken out. "How is my brand doing all time" is a question
    about a brand: the period is what it is asked over, read by period_from - not what it is
    about, so it plays no part in routing."""
    return re.sub(r"\s{2,}", " ", _PERIOD.sub(" ", text)).strip()


def recent_months(n: int, today: date | None = None) -> list[date]:
    """The last n months, this one included, newest first."""
    m = (today or _today_ist()).replace(day=1)
    out = []
    for _ in range(n):
        out.append(m)
        m = (m - timedelta(days=1)).replace(day=1)
    return out
