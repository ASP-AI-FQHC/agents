"""Display helpers shared by the scoring explanations and the templates.

The single rule enforced here: an absent value renders as "Not available".
No helper in this module ever substitutes a zero, a dash or an estimate for
missing data.
"""

from __future__ import annotations

import re
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


# Names that are correctly all-capitals, and prefixes whose following letter
# is capitalized too. Applied only to text that is entirely uppercase.
_NAME_PARTICLES = {
    "de", "del", "della", "der", "di", "du", "la", "le", "van", "von", "der",
    "bin", "al", "da", "dos", "das", "ter", "ten",
}
_NAME_SUFFIXES = {"ii", "iii", "iv", "v", "vi", "jr", "sr", "md", "do", "rn",
                  "np", "pa", "dds", "dmd", "cpa", "mba", "mph", "msn", "phd",
                  "lcsw", "faafp", "esq"}


def person_name(value: str | None) -> str:
    """A person's name in ordinary casing.

    Form 990 returns are filed in capitals, which reads as shouting in a table
    next to names taken from a web page. Names already carrying lower-case
    letters are left exactly as the source wrote them -- only an all-capitals
    string is re-cased, and then conservatively: Mc/Mac/O' prefixes,
    hyphenated names, particles like "van" and post-nominals like "MD" and
    "III" are each handled rather than blindly title-cased.

    >>> person_name("BERNEICE MILLS-THOMAS")
    'Berneice Mills-Thomas'
    >>> person_name("SEAN O'BRIEN MD")
    "Sean O'Brien MD"
    >>> person_name("Ada Nwosu")
    'Ada Nwosu'
    """
    if not value:
        return NOT_AVAILABLE
    text = value.strip()
    if not text or text != text.upper():
        return text

    def cap_word(word: str) -> str:
        stripped = word.strip(".,")
        key = stripped.lower()
        if key in _NAME_SUFFIXES:
            # Credentials and roman numerals keep their capitals; "Jr" and
            # "Sr" are ordinary words and do not.
            if key in {"jr", "sr"}:
                return word[0].upper() + word[1:].lower()
            return word
        if key in _NAME_PARTICLES:
            return key
        for prefix in ("mc", "mac", "o'", "d'", "l'"):
            if key.startswith(prefix) and len(key) > len(prefix) + 1:
                head = word[: len(prefix)].capitalize()
                if prefix in ("o'", "d'", "l'"):
                    head = word[0].upper() + word[1]
                return head + word[len(prefix)].upper() + word[len(prefix) + 1:].lower()
        return word[0].upper() + word[1:].lower()

    def cap_part(part: str) -> str:
        # A single-letter part is an initial and stays capitalized.
        if len(part.strip(".")) <= 1:
            return part.upper()
        return "-".join(cap_word(piece) for piece in part.split("-"))

    return " ".join(cap_part(part) for part in text.split())


# Kept as capitals when a job title filed in capitals is re-cased. Everything
# here is an initialism a reader expects to see capitalized; a word not on the
# list is treated as an ordinary word.
_TITLE_ACRONYMS = frozenset({
    "ceo", "cfo", "coo", "cio", "cto", "cmo", "cno", "cco", "cqo", "cpo",
    "cao", "cdo", "cso", "chro", "vp", "evp", "svp", "avp",
    "md", "do", "rn", "lpn", "np", "pa", "dds", "dmd", "dnp", "phd", "psyd",
    "mba", "mph", "msn", "msw", "lcsw", "lcpc", "faafp", "facp", "esq", "cpa",
    "it", "hr", "hit", "ehr", "emr", "qi", "rcm", "hipaa", "fqhc", "hrsa",
    "uds", "pcmh", "ii", "iii", "iv",
})
_TITLE_LOWER = frozenset({"of", "and", "the", "for", "to", "in", "at", "a", "an"})


def job_title(value: str | None) -> str:
    """A job title in ordinary casing.

    Form 990 titles are filed in capitals. Left alone they shout beside a title
    read off a web page, and the two sit in the same column. As with
    :func:`person_name`, a title that already carries lower-case letters is
    left exactly as its source wrote it.

    >>> job_title("PRESIDENT AND CEO")
    'President and CEO'
    >>> job_title("Director of Human Resources")
    'Director of Human Resources'
    """
    if not value:
        return NOT_AVAILABLE
    text = value.strip()
    if not text or text != text.upper():
        return text

    def cap_piece(piece: str, *, may_lower: bool) -> str:
        key = piece.strip(".,()[]&").lower()
        if key in _TITLE_ACRONYMS:
            return piece
        if may_lower and key in _TITLE_LOWER:
            return piece.lower()
        return piece[:1].upper() + piece[1:].lower()

    words = text.split()
    out: list[str] = []
    for index, word in enumerate(words):
        may_lower = index not in (0, len(words) - 1)
        # A compound like "SECRETARY/TREASURER" or "VICE-PRESIDENT" is two
        # words wearing one space; each half is cased on its own. The split
        # keeps the separators, so they are passed through untouched.
        out.append(
            "".join(
                part if part in "-/" else cap_piece(part, may_lower=may_lower)
                for part in re.split(r"([-/])", word)
                if part
            )
        )
    return " ".join(out)


def signed_money(value: float | int | None) -> str:
    """An amount carrying its own sign, for a change rather than a level.

    >>> signed_money(-3_000_000)
    '-$3.0M'
    >>> signed_money(240_000)
    '+$240K'
    """
    if value is None:
        return NOT_AVAILABLE
    if value == 0:
        return "no change"
    return ("+" if value > 0 else "-") + money(abs(value))


def signed_percent(value: float | None, *, decimals: int = 1) -> str:
    """A 0-1 ratio as a signed percentage, for a year-on-year movement."""
    if value is None:
        return NOT_AVAILABLE
    return f"{'+' if value > 0 else ''}{value * 100:.{decimals}f}%"


def relative_date(value: datetime | None, *, now: datetime | None = None) -> str:
    """When something happened, in the phrasing a person would use.

    >>> import re
from datetime import datetime, timezone
    >>> now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    >>> relative_date(datetime(2026, 8, 28, tzinfo=timezone.utc), now=now)
    'yesterday'
    >>> relative_date(datetime(2026, 6, 1, tzinfo=timezone.utc), now=now)
    '2 months ago'
    """
    if value is None:
        return NOT_AVAILABLE

    reference = now or datetime.now(timezone.utc)
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    days = (reference - moment).days

    if days < 0:
        return moment.strftime("%-d %B %Y")
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 14:
        return "last week"
    if days < 60:
        return f"{days // 7} weeks ago"
    months = months_since(moment, now=reference)
    if months is not None and months < 24:
        return f"{months} months ago"
    return moment.strftime("%B %Y")


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
