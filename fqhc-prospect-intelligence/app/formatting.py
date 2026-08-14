"""Display helpers shared by the scoring explanations and the templates.

The single rule enforced here: an absent value renders as "Not available".
No helper in this module ever substitutes a zero, a dash or an estimate for
missing data.
"""

from __future__ import annotations

from datetime import datetime, timezone

NOT_AVAILABLE = "Not available"


def money(value: float | int | None, *, compact: bool = True) -> str:
    """Format an amount, or "Not available" when it is unknown.

    >>> money(89412355)
    '$89.4M'
    >>> money(842119)
    '$842K'
    >>> money(0)
    '$0'
    >>> money(None)
    'Not available'
    """
    if value is None:
        return NOT_AVAILABLE

    amount = float(value)
    sign = "-" if amount < 0 else ""
    magnitude = abs(amount)

    if not compact:
        return f"{sign}${magnitude:,.0f}"

    if magnitude >= 1_000_000_000:
        return f"{sign}${magnitude / 1_000_000_000:.1f}B"
    if magnitude >= 1_000_000:
        return f"{sign}${magnitude / 1_000_000:.1f}M"
    if magnitude >= 1_000:
        return f"{sign}${magnitude / 1_000:.0f}K"
    return f"{sign}${magnitude:,.0f}"


def number(value: int | float | None) -> str:
    """Thousands-separated integer, or "Not available"."""
    return NOT_AVAILABLE if value is None else f"{value:,.0f}"


def percent(value: float | None, *, decimals: int = 0) -> str:
    """Format a 0-1 ratio as a percentage, or "Not available"."""
    if value is None:
        return NOT_AVAILABLE
    return f"{value * 100:.{decimals}f}%"


def text(value: str | None) -> str:
    """Any string field, or "Not available" when blank."""
    cleaned = (value or "").strip()
    return cleaned or NOT_AVAILABLE


def months_since(value: datetime | None, *, now: datetime | None = None) -> int | None:
    """Whole months between ``value`` and now; None when unknown."""
    if value is None:
        return None
    reference = now or datetime.now(timezone.utc)
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    months = (reference.year - moment.year) * 12 + (reference.month - moment.month)
    if reference.day < moment.day:
        months -= 1
    return max(months, 0)


def age_label(value: datetime | None, *, now: datetime | None = None) -> str:
    """Human phrasing for data age, e.g. "14 months old"."""
    months = months_since(value, now=now)
    if months is None:
        return NOT_AVAILABLE
    if months < 1:
        return "this month"
    if months == 1:
        return "1 month old"
    if months < 24:
        return f"{months} months old"
    years = months // 12
    return f"{years} years old" if years > 1 else "1 year old"
