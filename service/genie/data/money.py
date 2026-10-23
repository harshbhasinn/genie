"""Rupees, formatted by code — and the check that keeps the model honest about them.

The model never computes or formats a figure. It is handed figures already
written the way an Indian finance user reads them (₹15,75,000 and ₹15.75 L), and
everything it writes is checked against that list before the user sees it.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

LAKH = Decimal(100_000)
CRORE = Decimal(10_000_000)


def to_decimal(value) -> Decimal | None:
    """The BFF sends money as strings ("1575000.00"). None for anything unreadable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _indian_grouping(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        head, tail = "", s
    else:
        head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail]) if parts else tail


def inr(amount: Decimal) -> str:
    """₹15,75,000 — paise shown only when there are some."""
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    rupees = int(amount)
    paise = int((amount - rupees) * 100 + Decimal("0.5"))
    if paise == 100:
        rupees, paise = rupees + 1, 0
    body = _indian_grouping(rupees) + (f".{paise:02d}" if paise else "")
    return f"{sign}₹{body}"


def inr_short(amount: Decimal) -> str:
    """₹15.75 L / ₹1.90 Cr / ₹48,000 — how the dashboard's own cards read."""
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    if a >= CRORE:
        return f"{sign}₹{(a / CRORE).quantize(Decimal('0.01'))} Cr"
    if a >= LAKH:
        return f"{sign}₹{(a / LAKH).quantize(Decimal('0.01'))} L"
    return sign + inr(a)


# ── the number check ────────────────────────────────────────────────────

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _canon(token: str) -> str:
    t = token.replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t or "0"


def numbers_in(text: str) -> set[str]:
    return {_canon(m) for m in _NUMBER.findall(text or "")}


def unexplained_numbers(narration: str, allowed_texts: list[str]) -> list[str]:
    """Numbers in the narration that appear in none of the allowed texts.

    `allowed_texts` is every display string Genie handed the model, plus the
    user's own message (repeating their "10 lakh" back is not inventing it).
    """
    allowed: set[str] = set()
    for t in allowed_texts:
        allowed |= numbers_in(t)
    return sorted(n for n in numbers_in(narration) if n not in allowed)
