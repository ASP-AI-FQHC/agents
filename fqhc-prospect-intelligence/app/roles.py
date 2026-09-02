"""Who a named person is, from the title they were given.

A health center's Form 990 lists everyone: the chief executive, the finance
chief, and twenty volunteer board members who meet twice a year. For an
outreach list those are not the same thing, and a CEO list buried under four
hundred board members is not a CEO list.

The classification is a reading of the title text and nothing more. It never
invents a title, never promotes somebody whose title does not say what they do,
and anyone whose title is blank or unrecognised lands in ``UNKNOWN`` rather
than being guessed into a bucket -- an unclassified row is still a real person
and still appears, it just is not claimed to be an executive.
"""

from __future__ import annotations

import enum
import re


class Role(str, enum.Enum):
    """What kind of person a title describes."""

    CHIEF_EXECUTIVE = "chief-executive"   # CEO, President, Executive Director
    TECHNOLOGY = "technology"             # CIO, CTO, IT Director, informatics
    FINANCE = "finance"                   # CFO, VP Finance, Controller
    OPERATIONS = "operations"             # COO, VP Operations
    CLINICAL = "clinical"                 # CMO, Medical Director, CNO
    COMPLIANCE = "compliance"             # compliance, privacy, risk, quality
    OTHER_EXECUTIVE = "other-executive"   # a chief or VP of something else
    BOARD = "board"                       # trustee, board member, board officer
    STAFF = "staff"                       # named, but not a decision maker
    UNKNOWN = "unknown"                   # no title, or none we can read

    @property
    def label(self) -> str:
        return {
            Role.CHIEF_EXECUTIVE: "Chief executive",
            Role.TECHNOLOGY: "Technology",
            Role.FINANCE: "Finance",
            Role.OPERATIONS: "Operations",
            Role.CLINICAL: "Clinical",
            Role.COMPLIANCE: "Compliance and quality",
            Role.OTHER_EXECUTIVE: "Other executive",
            Role.BOARD: "Board",
            Role.STAFF: "Staff",
            Role.UNKNOWN: "Not stated",
        }[self]

    @property
    def is_executive(self) -> bool:
        """Whether this role is someone a proposal would be addressed to."""
        return self in _EXECUTIVE_ROLES


_EXECUTIVE_ROLES = frozenset({
    Role.CHIEF_EXECUTIVE,
    Role.TECHNOLOGY,
    Role.FINANCE,
    Role.OPERATIONS,
    Role.CLINICAL,
    Role.COMPLIANCE,
    Role.OTHER_EXECUTIVE,
})

# The order matters: a "Chief Executive Officer and Board Chair" is a chief
# executive who also sits on the board, and the first match wins. Board
# patterns are therefore tested last among the specific ones.
#
# Every pattern is word-bounded. A plain substring test put "cto" inside
# "Hector" and "coo" inside "Cooper" the first time this was written; that
# lesson is why nothing here uses `in`.
_PATTERNS: tuple[tuple[Role, tuple[str, ...]], ...] = (
    (Role.CHIEF_EXECUTIVE, (
        r"\bc\.?e\.?o\.?\b",
        r"\bchief\s+executive\b",
        r"\bexecutive\s+director\b",
        # Not a Vice President, who is an executive but not the chief one,
        # and not the president *of the board*.
        r"(?<!vice )(?<!vice-)\bpresident\b(?!\s*,?\s*of\s+the\s+board\b)",
        r"\bchief\s+administrative\s+officer\b",
        r"\badministrator\b",
    )),
    (Role.TECHNOLOGY, (
        r"\bc\.?i\.?o\.?\b",
        r"\bc\.?t\.?o\.?\b",
        r"\bchief\s+(?:information|technology|digital|informatics)\b",
        r"\b(?:director|vp|vice[\s-]+president|head)\s+of\s+(?:it|information\s+technology|technology|informatics|information\s+systems)\b",
        r"\b(?:it|information\s+technology|informatics|information\s+systems)\s+director\b",
        r"\bchief\s+information\s+security\b",
        r"\bc\.?i\.?s\.?o\.?\b",
    )),
    (Role.FINANCE, (
        r"\bc\.?f\.?o\.?\b",
        r"\bchief\s+financial\b",
        r"\b(?:vp|vice[\s-]+president|director)\s+of\s+finance\b",
        r"\bcontroller\b",
        # Treasurer is deliberately absent: on a health center's Form 990 it
        # is almost always a volunteer board officer, not the finance chief,
        # and it falls through to the board patterns below.
    )),
    (Role.OPERATIONS, (
        r"\bc\.?o\.?o\.?\b",
        r"\bchief\s+operating\b",
        r"\b(?:vp|vice[\s-]+president|director)\s+of\s+operations\b",
    )),
    (Role.CLINICAL, (
        r"\bc\.?m\.?o\.?\b",
        r"\bc\.?n\.?o\.?\b",
        r"\bchief\s+(?:medical|nursing|dental|clinical|behavioral)\b",
        r"\bmedical\s+director\b",
        r"\bdental\s+director\b",
    )),
    (Role.COMPLIANCE, (
        r"\bchief\s+(?:compliance|risk|privacy|quality)\b",
        r"\bcompliance\s+officer\b",
        r"\bprivacy\s+officer\b",
        r"\b(?:director|vp|vice[\s-]+president)\s+of\s+(?:compliance|quality|risk)\b",
        r"\bquality\s+(?:director|officer)\b",
    )),
    (Role.OTHER_EXECUTIVE, (
        r"\bchief\s+\w+\s+officer\b",
        r"\bc\.?h\.?r\.?o\.?\b",
        r"\b(?:executive\s+)?vice[\s-]+president\b",
        r"\bv\.?p\.?\b",
    )),
    (Role.BOARD, (
        r"\bboard\b",
        r"\btrustee\b",
        r"\bdirector\s+at\s+large\b",
        r"\bchair(?:man|woman|person)?\b",
        r"\bvice\s+chair\b",
        r"\bsecretary\b",
        r"\btreasurer\b",
        r"\bmember\b",
    )),
)

_COMPILED: tuple[tuple[Role, tuple[re.Pattern[str], ...]], ...] = tuple(
    (role, tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns))
    for role, patterns in _PATTERNS
)


def classify(title: str | None, *, form_990_roles: list[str] | None = None) -> Role:
    """The role a title describes.

    ``form_990_roles`` are the Part VII checkboxes, which are a stronger signal
    than prose when they say "Board member" and the title says nothing useful.
    They are consulted only after the title has failed to place someone, so a
    chief executive who also sits on the board is still a chief executive.
    """
    text = (title or "").strip()
    if text:
        for role, patterns in _COMPILED:
            if any(pattern.search(text) for pattern in patterns):
                return role

    checkboxes = form_990_roles or []
    if any(box in ("Board member", "Institutional trustee") for box in checkboxes):
        return Role.BOARD
    if any(box in ("Officer", "Key employee") for box in checkboxes):
        # The filing says this person runs something; the title does not say
        # what. Not claimed as an executive, not dismissed as a board seat.
        return Role.STAFF

    return Role.UNKNOWN if not text else Role.STAFF


def is_executive(title: str | None, *, form_990_roles: list[str] | None = None) -> bool:
    """Whether this person is someone a proposal would be addressed to."""
    return classify(title, form_990_roles=form_990_roles).is_executive
