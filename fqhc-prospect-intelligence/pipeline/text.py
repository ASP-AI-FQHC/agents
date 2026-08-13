"""Normalization helpers shared by HRSA dedup and ProPublica EIN matching.

Both stages need to decide whether two differently-punctuated spellings of an
organization name refer to the same entity, so the rules live in one place.
"""

from __future__ import annotations

import re
import unicodedata

# Legal-form suffixes carry no identifying information and differ between HRSA
# and IRS records for the same organization ("Erie Family Health Center" vs
# "Erie Family Health Center, Inc."). They are stripped from the tail of a name.
_LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "llp",
    "lp",
    "ltd",
    "corp",
    "corporation",
    "co",
    "company",
    "pc",
    "pa",
    "plc",
    "sc",
}

_ABBREVIATIONS = {
    "&": " and ",
    "/": " ",
    "+": " and ",
}

# Words that appear in a large share of health-center names and therefore add
# little discriminating power. Kept in the normalized name (removing them loses
# real signal) but exposed so matching can weigh them if needed.
COMMON_HEALTH_TERMS = frozenset(
    {
        "health",
        "center",
        "centers",
        "medical",
        "community",
        "clinic",
        "clinics",
        "services",
        "care",
        "family",
        "healthcare",
    }
)

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")
_ZIP_RE = re.compile(r"(\d{5})(?:[-\s]?(\d{4}))?")


def clean(value: str | None) -> str | None:
    """Trim a CSV value, converting blanks and common null markers to None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.upper() in {"N/A", "NA", "NULL", "NONE", "-", "--", "UNKNOWN", "UNK"}:
        return None
    return text


def normalize_name(name: str | None) -> str:
    """Fold an organization name to a comparable key.

    Lowercases, strips accents and punctuation, expands ``&`` to ``and``, and
    drops trailing legal-form suffixes. Returns "" for empty input.

    >>> normalize_name("Erie Family Health Center, Inc.")
    'erie family health center'
    >>> normalize_name("ACCESS Community Health Network")
    'access community health network'
    """
    text = clean(name)
    if text is None:
        return ""

    # Decompose accents to their ASCII base characters.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()

    for symbol, replacement in _ABBREVIATIONS.items():
        text = text.replace(symbol, replacement)

    text = _PUNCT_RE.sub(" ", text)
    tokens = _WS_RE.sub(" ", text).strip().split()

    # Strip legal suffixes from the tail only -- "Inc" mid-name is rare, but
    # "Company" as the first word of a name should survive.
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()

    return " ".join(tokens)


def normalize_state(value: str | None) -> str | None:
    """Return a two-letter uppercase state code, or None."""
    text = clean(value)
    if text is None:
        return None
    text = text.strip().upper()
    return text[:2] if len(text) >= 2 else None


def normalize_zip(value: str | None) -> str | None:
    """Return ZIP as ``12345`` or ``12345-6789``; None when unparseable.

    HRSA files sometimes lose the leading zero on New England ZIPs when they
    pass through a spreadsheet, so short numeric values are zero-padded.
    """
    text = clean(value)
    if text is None:
        return None

    digits_only = re.sub(r"[^0-9]", "", text)
    if 0 < len(digits_only) < 5:
        digits_only = digits_only.zfill(5)
        return digits_only

    match = _ZIP_RE.search(digits_only if digits_only else text)
    if not match:
        return None
    base, plus4 = match.group(1), match.group(2)
    return f"{base}-{plus4}" if plus4 else base


def parse_money(value: str | None) -> float | None:
    """Parse a currency-ish string to a float.

    Returns None for anything unparseable -- never 0.0, because "we do not know"
    and "zero dollars" must stay distinguishable all the way to the UI.

    >>> parse_money("$1,234,567.00")
    1234567.0
    >>> parse_money("") is None
    True
    """
    text = clean(value)
    if text is None:
        return None

    negative = text.startswith("(") and text.endswith(")")
    stripped = re.sub(r"[^0-9.\-]", "", text)
    if stripped in {"", "-", ".", "-."}:
        return None
    try:
        amount = float(stripped)
    except ValueError:
        return None
    return -amount if negative and amount > 0 else amount


def parse_int(value: str | None) -> int | None:
    """Parse an integer-ish string; None when unparseable."""
    amount = parse_money(value)
    return int(amount) if amount is not None else None


def normalize_header(header: str) -> str:
    """Fold a CSV header to a comparison key: lowercase alphanumerics only.

    >>> normalize_header("Site Postal Code ")
    'sitepostalcode'
    """
    return re.sub(r"[^a-z0-9]", "", header.lower())


def dedup_key(name: str | None, state: str | None) -> str:
    """Fallback identity for an organization when HRSA publishes no ID."""
    return f"{normalize_name(name)}|{normalize_state(state) or '??'}"
