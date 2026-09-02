"""Who is still missing, and what would fill each gap.

    python -m pipeline.coverage --state IL

A contact list of thirty names out of a universe of forty-five organizations
looks complete until somebody notices the other fifteen. This prints the other
fifteen, and for each one says which source would supply it -- because "no CEO
found" has several different causes and they need different work:

* no confirmed EIN, so no filing can be attributed to the organization
* a confirmed EIN but no Form 990 XML in the local download
* a filing on hand, but nobody in it whose title says they run the place
* no UDS return loaded, which is where the direct email lives
* no web address published by HRSA, so there is nothing to crawl
* a site that was crawled and yielded nothing

The point is that the report names the next action rather than the shortfall.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.roles import Role, classify


@dataclass
class OrganizationCoverage:
    """What is known about one organization's leadership, and what is not."""

    name: str
    state: str | None
    ein: str | None
    website: str | None

    chief_executive: str | None = None
    chief_executive_email: str | None = None
    chief_executive_source: str | None = None
    executives: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def has_chief_executive(self) -> bool:
        return self.chief_executive is not None

    @property
    def has_email(self) -> bool:
        return bool(self.chief_executive_email)


@dataclass
class CoverageReport:
    state: str | None
    organizations: list[OrganizationCoverage] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.organizations)

    @property
    def with_chief_executive(self) -> list[OrganizationCoverage]:
        return [o for o in self.organizations if o.has_chief_executive]

    @property
    def with_email(self) -> list[OrganizationCoverage]:
        return [o for o in self.organizations if o.has_email]

    @property
    def missing(self) -> list[OrganizationCoverage]:
        return [o for o in self.organizations if not o.has_chief_executive]


def build(session: Session, state: str | None = None) -> CoverageReport:
    """Assemble the report from what is already in the database."""
    from app.models import (
        EinMatch,
        MatchStatus,
        Organization,
        Person,
        UdsReport,
        WebsiteCrawl,
        WebsitePerson,
    )

    statement = select(Organization).order_by(Organization.name)
    if state:
        statement = statement.where(Organization.state == state.upper())
    organizations = session.scalars(statement).all()

    report = CoverageReport(state=state.upper() if state else None)

    for organization in organizations:
        ein = organization.ein
        entry = OrganizationCoverage(
            name=organization.name,
            state=organization.state,
            ein=ein,
            website=organization.website,
        )

        # --- The UDS project director: the only source with a direct email ---
        director = session.scalars(
            select(UdsReport)
            .where(
                UdsReport.organization_id == organization.id,
                UdsReport.director_name.is_not(None),
            )
            .order_by(UdsReport.year.desc())
            .limit(1)
        ).first()
        if director is not None:
            entry.chief_executive = director.director_name
            entry.chief_executive_email = director.director_email
            entry.chief_executive_source = f"HRSA UDS {director.year}"
            entry.executives += 1

        # --- Form 990 Part VII ------------------------------------------------
        filing_people = (
            list(
                session.scalars(
                    select(Person).where(Person.ein == ein)
                ).all()
            )
            if ein
            else []
        )
        for person in filing_people:
            role = classify(person.title, form_990_roles=person.roles)
            if role.is_executive:
                entry.executives += 1
            if role is Role.CHIEF_EXECUTIVE and entry.chief_executive is None:
                entry.chief_executive = person.name
                entry.chief_executive_source = f"Form 990 FY{person.tax_year}"

        # --- The organization's own website ------------------------------------
        website_people = list(
            session.scalars(
                select(WebsitePerson).where(
                    WebsitePerson.organization_id == organization.id
                )
            ).all()
        )
        for person in website_people:
            role = classify(person.title)
            if role.is_executive:
                entry.executives += 1
            if role is Role.CHIEF_EXECUTIVE and entry.chief_executive is None:
                entry.chief_executive = person.name
                entry.chief_executive_source = "Website"
                entry.chief_executive_email = person.email

        # --- Why is anything missing? ------------------------------------------
        if entry.chief_executive is None or not entry.chief_executive_email:
            if director is None:
                entry.reasons.append(
                    "no UDS return loaded -- UDS is where the direct email lives"
                )

        if entry.chief_executive is None:
            match = session.scalar(
                select(EinMatch).where(EinMatch.organization_id == organization.id)
            )
            if not ein:
                if match is None:
                    entry.reasons.append("EIN never searched -- run --stage ein")
                elif MatchStatus(match.status) is MatchStatus.PENDING:
                    entry.reasons.append(
                        "EIN match is waiting in the review queue -- confirm it "
                        "and the filing attaches"
                    )
                else:
                    entry.reasons.append(
                        "no confirmed EIN, so no filing can be attributed here"
                    )
            elif not filing_people:
                entry.reasons.append(
                    "confirmed EIN but no Form 990 XML on this machine for it"
                )
            else:
                entry.reasons.append(
                    f"{len(filing_people)} people in the filing, none whose title "
                    "says they run the organization"
                )

            crawl = session.scalar(
                select(WebsiteCrawl).where(
                    WebsiteCrawl.organization_id == organization.id
                )
            )
            if not organization.website:
                entry.reasons.append("HRSA publishes no web address to crawl")
            elif crawl is None:
                entry.reasons.append("website not crawled yet -- run --stage website")
            elif not website_people:
                entry.reasons.append(f"website crawled: {crawl.outcome}")

        report.organizations.append(entry)

    return report


def render(report: CoverageReport) -> str:
    """The report as plain text, for a terminal."""
    where = report.state or "all states"
    lines = [
        f"Chief executive coverage, {where}",
        "=" * 60,
        f"  Organizations                {report.total:>5,}",
        f"  With a named chief executive {len(report.with_chief_executive):>5,}",
        f"  With a direct email          {len(report.with_email):>5,}",
        "",
    ]

    found = report.with_chief_executive
    if found:
        lines.append(f"Named ({len(found)})")
        lines.append("-" * 60)
        for entry in found:
            email = entry.chief_executive_email or "no email published"
            lines.append(f"  {entry.chief_executive}")
            lines.append(f"    {entry.name}")
            lines.append(f"    {email}  [{entry.chief_executive_source}]")
        lines.append("")

    missing = report.missing
    if missing:
        lines.append(f"No chief executive yet ({len(missing)})")
        lines.append("-" * 60)
        for entry in missing:
            lines.append(f"  {entry.name}")
            for reason in entry.reasons:
                lines.append(f"    - {reason}")
        lines.append("")

    # The single most common fix, stated once rather than on every line.
    without_uds = [entry for entry in report.organizations if not entry.has_email]
    if without_uds:
        lines.append(
            f"{len(without_uds):,} organizations have no direct email. The HRSA "
            "Uniform Data System names a project director with a phone number "
            "and an address for every Section 330 grantee; loading the UDS "
            "export for this state is the single largest gain available, and "
            "it is free."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.coverage",
        description="Who has a named chief executive, who does not, and why.",
    )
    parser.add_argument("--state", help="Two-letter state code, e.g. IL")
    parser.add_argument("--config", help="Path to config.yaml")
    arguments = parser.parse_args(argv)

    from app.config import get_config, load_config
    from app.db import session_scope

    config = load_config(arguments.config) if arguments.config else get_config()
    with session_scope(config) as session:
        print(render(build(session, arguments.state)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
