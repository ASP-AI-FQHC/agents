"""IRS National Taxonomy of Exempt Entities codes.

ProPublica returns an NTEE code on the organization record, which is the closest
thing to a published "program area" classification for a nonprofit.

The tables here are deliberately incomplete. NTEE has several hundred codes and
inventing a plausible-sounding description for one would be exactly the kind of
fabrication this project avoids, so only entries that can be stated accurately
are included. Anything else falls back to its major group, and failing that to
the bare code, which is still the truth.
"""

from __future__ import annotations

# The 26 NTEE major groups, keyed by the leading letter of the code.
MAJOR_GROUPS: dict[str, str] = {
    "A": "Arts, culture and humanities",
    "B": "Education",
    "C": "Environment",
    "D": "Animal-related",
    "E": "Health care",
    "F": "Mental health and crisis intervention",
    "G": "Voluntary health associations and medical disciplines",
    "H": "Medical research",
    "I": "Crime and legal-related",
    "J": "Employment",
    "K": "Food, agriculture and nutrition",
    "L": "Housing and shelter",
    "M": "Public safety, disaster preparedness and relief",
    "N": "Recreation and sports",
    "O": "Youth development",
    "P": "Human services",
    "Q": "International and foreign affairs",
    "R": "Civil rights, social action and advocacy",
    "S": "Community improvement and capacity building",
    "T": "Philanthropy, voluntarism and grantmaking",
    "U": "Science and technology",
    "V": "Social science",
    "W": "Public and societal benefit",
    "X": "Religion-related",
    "Y": "Mutual and membership benefit",
    "Z": "Unknown",
}

# Specific codes an FQHC is likely to carry. Kept short on purpose: every entry
# here is one we can state with confidence.
CODES: dict[str, str] = {
    "E20": "Hospitals and primary medical care facilities",
    "E21": "Community health systems",
    "E30": "Health treatment facilities, primarily outpatient",
    "E31": "Group health practice",
    "E32": "Ambulatory health center / community clinic",
    "E60": "Health support services",
    "E70": "Public health programmes",
    "F32": "Community mental health centre",
    "P20": "Human service organisations",
}


def normalize_code(code: str | None) -> str | None:
    """Upper-cased, whitespace-stripped code, or None."""
    cleaned = (code or "").strip().upper()
    return cleaned or None


def describe(code: str | None) -> tuple[str | None, str | None]:
    """Return ``(specific_description, major_group)`` for an NTEE code.

    Either element may be None. A code we hold no description for still yields
    its major group, and an unrecognized letter yields neither -- the caller
    displays the raw code in that case.

    >>> describe("E320")
    ('Ambulatory health center / community clinic', 'Health care')
    >>> describe("E999")[0] is None
    True
    >>> describe(None)
    (None, None)
    """
    cleaned = normalize_code(code)
    if not cleaned:
        return None, None

    # ProPublica returns codes such as "E320"; the classification is the first
    # three characters, with any trailing digit being a further subdivision.
    specific = CODES.get(cleaned[:3])
    group = MAJOR_GROUPS.get(cleaned[:1])
    return specific, group


def label(code: str | None) -> str | None:
    """Best available single-line description, or None when nothing is known."""
    specific, group = describe(code)
    return specific or group
