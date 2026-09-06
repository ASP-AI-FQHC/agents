"""Outreach compliance: who must not be contacted, and what every list must say.

Two things live here, and both exist because a contact list gets forwarded and
reused long after the conversation that produced it.

**The suppression list.** A file of addresses, domains and organization names
that must never appear in an export. An opt-out is worthless if it only lives
in somebody's inbox: it has to be enforced where the list is produced, every
time, without anyone remembering to. So the exports read it on every call, and
a row that matches is removed before the file is written -- not marked, not
sorted to the bottom, removed.

**The compliance block.** CAN-SPAM applies to commercial email including
business-to-business, and it requires an accurate sender, a non-deceptive
subject, a physical postal address and a working opt-out honoured within ten
business days. None of that is enforceable from here -- it is a property of the
message somebody sends, not of this file -- but the requirements travel at the
top of every contact export so they are in front of whoever writes the message.

Nothing in this module is legal advice, and it says so in the block itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# How long an opt-out has to be honoured under CAN-SPAM.
OPT_OUT_BUSINESS_DAYS = 10

_COMMENT = re.compile(r"\s*#.*$")


@dataclass
class Suppression:
    """Everyone who must not be contacted, however they were matched.

    Three kinds of entry, because an opt-out arrives in three shapes: a person
    ("stop emailing me"), an organization ("take us off your list"), and a
    whole domain ("nobody at our health center").
    """

    emails: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    organizations: set[str] = field(default_factory=set)
    source: Path | None = None

    @property
    def total(self) -> int:
        return len(self.emails) + len(self.domains) + len(self.organizations)

    @property
    def is_empty(self) -> bool:
        return self.total == 0

    def blocks(self, *, email: str | None = None, organization: str | None = None) -> str | None:
        """Why this contact is suppressed, or None.

        Returns the reason rather than a boolean so an audit can say which
        entry did it -- "opted out by domain" and "opted out personally" are
        different facts and a campaign log should carry the right one.
        """
        address = (email or "").strip().lower()
        if address:
            if address in self.emails:
                return "address is on the suppression list"
            domain = address.rsplit("@", 1)[-1] if "@" in address else ""
            if domain and domain in self.domains:
                return f"domain {domain} is on the suppression list"

        name = _normalize_organization(organization)
        if name and name in self.organizations:
            return "organization is on the suppression list"
        return None


def _normalize_organization(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def load_suppression(path: Path | None) -> Suppression:
    """Read a suppression file. A missing file is empty, not an error.

    One entry per line. A line containing ``@`` is an address; a line beginning
    ``@`` or looking like a bare domain suppresses everyone there; anything
    else is an organization name. ``#`` starts a comment, so the file can say
    who asked and when -- which is the part somebody will need in a year.
    """
    result = Suppression(source=path)
    if path is None or not path.exists():
        return result

    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return result

    for raw in lines:
        line = _COMMENT.sub("", raw).strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("@"):
            result.domains.add(lowered[1:])
        elif "@" in lowered:
            result.emails.add(lowered)
        elif re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", lowered):
            result.domains.add(lowered)
        else:
            result.organizations.add(_normalize_organization(line))
    return result


def compliance_block(
    company: str,
    postal_address: str | None,
    opt_out: str | None,
    *,
    generated_at: datetime | None = None,
) -> list[str]:
    """The lines that open every contact export.

    Written as instructions to whoever sends the message, because that is the
    only place these obligations can actually be met.
    """
    stamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    lines = [
        "OUTREACH COMPLIANCE -- read before sending",
        (
            "CAN-SPAM applies to commercial email, including business-to-business. "
            "Every message sent from this list must carry accurate sender and "
            "header information, a subject line that is not deceptive, a valid "
            "physical postal address, and a clear way to opt out."
        ),
        (
            f"Opt-outs must be honoured within {OPT_OUT_BUSINESS_DAYS} business "
            "days, and must be added to the suppression file so that later "
            "exports exclude them automatically."
        ),
        f"Sender: {company}",
    ]
    if postal_address:
        lines.append(f"Postal address for the message footer: {postal_address}")
    else:
        lines.append(
            "Postal address: NOT SET. Add app.postal_address to config.yaml -- "
            "a commercial message without one does not comply."
        )
    if opt_out:
        lines.append(f"Opt-out route to publish in the message: {opt_out}")
    else:
        lines.append(
            "Opt-out route: NOT SET. Add app.opt_out_contact to config.yaml."
        )
    lines.append(
        "These contacts are work addresses gathered for a professional purpose "
        "from public filings and organizations' own published pages. No patient "
        "information of any kind is held in this database."
    )
    lines.append(f"List generated {stamp}. Not legal advice.")
    return lines
