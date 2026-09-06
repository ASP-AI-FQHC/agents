"""A per-organization research brief for outreach, built only from held sources.

    python -m pipeline.dossier --state IL --gaps-only > illinois-brief.md

Six sections per organization, in the order a researcher asked for them:

1. Official name and website
2. Main location and service area
3. Operational priorities and service lines
4. Published general contact
5. Up to three decision-maker roles, named only where the name is verified
6. Source URLs and a verification date

The rules that make it worth reading:

**Every claim carries its source and the date that source describes.** A name
from a Form 990 says which tax year; a name from a web page links the page and
gives the day it was read. A reader can always get back to the evidence.

**A role is always given; a name only sometimes.** The decision-maker section
lists the roles worth approaching whether or not anyone is named, because
"Chief Information Officer, name not established" is a usable research task and
a fabricated name is not.

**Anything unconfirmed is flagged, not quietly included.** A name read off a
web page by a heuristic, a contact older than a year, an organization whose EIN
is still in the review queue -- each is marked for manual verification with the
reason.

**No personal or patient data.** Only work contact details an organization
published about itself, and nothing from a patient portal, a login-gated page
or a filing's personal fields. The database has no column for anything else.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.formatting import job_title, money, number, person_name
from app.roles import Role, classify

# The functions worth approaching, in the order they are usually approached,
# with what each one owns. Roles are listed whether or not this database can
# name the person holding them: an unfilled role is a research task, and a
# guessed name is a liability.
TARGET_ROLES: tuple[tuple[Role, str, str], ...] = (
    (
        Role.TECHNOLOGY,
        "Technology",
        "Owns the network, endpoints, identity and backup around the EHR -- "
        "the systems a managed-service or security engagement actually touches.",
    ),
    (
        Role.COMPLIANCE,
        "Compliance and quality",
        "Answers for HIPAA and, where the organization has a Single Audit, for "
        "internal controls over federal programs.",
    ),
    (
        Role.FINANCE,
        "Finance",
        "Owns revenue cycle and signs for anything with a recurring cost.",
    ),
    (
        Role.OPERATIONS,
        "Operations",
        "Owns the multi-site footprint and whatever runs at each location.",
    ),
    (
        Role.CHIEF_EXECUTIVE,
        "Chief executive",
        "Signs. At a health center this size, often the only person who does.",
    ),
)

STALE_DAYS = 365


@dataclass
class Sourced:
    """One fact, with where it came from and what date that source describes."""

    value: str
    source: str
    url: str | None = None
    as_of: str | None = None
    verified: bool = True
    caveat: str | None = None


@dataclass
class Dossier:
    organization_id: int
    name: str
    website: Sourced | None = None
    location: str | None = None
    service_area: list[str] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)
    phone: Sourced | None = None
    email: Sourced | None = None
    people: list[tuple[str, str, Sourced | None]] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def named_people(self) -> int:
        return sum(1 for _role, _why, person in self.people if person is not None)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _age_flag(when: datetime | None, what: str) -> str | None:
    if when is None:
        return None
    moment = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - moment).days
    if days > STALE_DAYS:
        return f"{what} was last confirmed {days // 30} months ago -- re-check before use"
    return None


def build(session: Session, organization) -> Dossier:
    """Assemble one organization's brief from what the database already holds."""
    from app.models import (
        Contractor,
        EinMatch,
        MatchStatus,
        Person,
        UdsReport,
        WebsiteCrawl,
        WebsitePerson,
    )

    dossier = Dossier(organization_id=organization.id, name=organization.name)

    # --- 1. Name and website ------------------------------------------------
    if organization.website:
        dossier.website = Sourced(
            value=organization.website,
            source="HRSA health center site file",
            as_of=organization.last_seen_at.strftime("%Y-%m-%d"),
        )
    else:
        dossier.flags.append(
            "No web address published by HRSA -- find the official site manually "
            "before any outreach, and confirm it is the right organization"
        )

    # --- 2. Location and service area ---------------------------------------
    parts = [organization.city, organization.state]
    dossier.location = ", ".join(p for p in parts if p) or None
    cities = sorted(
        {site.city for site in organization.sites if site.city},
    )
    if cities:
        dossier.service_area = cities
    dossier.sources.append(
        ("HRSA health center service delivery site file", "https://data.hrsa.gov/data/download")
    )

    # --- 3. Operational priorities ------------------------------------------
    for programme in organization.funding_programs or (
        [organization.funding_program] if organization.funding_program else []
    ):
        dossier.priorities.append(f"HRSA funding stream: {programme}")

    if organization.site_count:
        dossier.priorities.append(
            f"{organization.site_count} delivery site"
            f"{'' if organization.site_count == 1 else 's'} to support"
        )

    uds = session.scalars(
        select(UdsReport)
        .where(UdsReport.organization_id == organization.id)
        .order_by(UdsReport.year.desc())
        .limit(1)
    ).first()
    if uds:
        if uds.patients:
            dossier.priorities.append(
                f"{number(uds.patients)} patients served ({uds.year} UDS)"
            )
        if uds.total_fte:
            dossier.priorities.append(
                f"{uds.total_fte:,.0f} staff FTE ({uds.year} UDS) -- the population "
                "any per-seat quote is sized on"
            )
        if uds.ehr_vendor:
            dossier.priorities.append(
                f"Electronic health record: {uds.ehr_vendor}"
                + (f" ({uds.ehr_product})" if uds.ehr_product else "")
                + f", reported to HRSA on the {uds.year} UDS health IT return"
            )
        dossier.sources.append(
            (f"HRSA Uniform Data System {uds.year}", "https://data.hrsa.gov/tools/data-reporting")
        )

    if organization.federal_award_amount:
        dossier.priorities.append(
            f"{money(organization.federal_award_amount)} Section 330 federal award "
            "-- award conditions apply to how its systems are controlled"
        )

    vendors = (
        list(
            session.scalars(
                select(Contractor).where(Contractor.ein == organization.ein)
            ).all()
        )
        if organization.ein
        else []
    )
    for vendor in vendors[:4]:
        dossier.priorities.append(
            f"Discloses a contractor: {vendor.name}"
            + (f" -- {vendor.services}" if vendor.services else "")
            + f" (Form 990 FY{vendor.tax_year})"
        )

    # --- 4. Published general contact ---------------------------------------
    if organization.phone:
        dossier.phone = Sourced(
            value=organization.phone,
            source="HRSA health center site file",
            as_of=organization.last_seen_at.strftime("%Y-%m-%d"),
        )
    if uds and uds.director_email:
        dossier.email = Sourced(
            value=uds.director_email,
            source=f"HRSA UDS {uds.year}, project director",
            as_of=str(uds.year),
        )

    # --- 5. Decision-maker roles --------------------------------------------
    crawl = session.scalar(
        select(WebsiteCrawl).where(WebsiteCrawl.organization_id == organization.id)
    )
    filing_people = (
        list(
            session.scalars(select(Person).where(Person.ein == organization.ein)).all()
        )
        if organization.ein
        else []
    )
    website_people = list(
        session.scalars(
            select(WebsitePerson).where(
                WebsitePerson.organization_id == organization.id
            )
        ).all()
    )

    candidates: list[tuple[str, str, Sourced | None]] = []
    for role, label, why in TARGET_ROLES:
        found: Sourced | None = None

        if role is Role.CHIEF_EXECUTIVE and uds and uds.director_name:
            found = Sourced(
                value=person_name(uds.director_name),
                source=f"HRSA UDS {uds.year}, project director",
                as_of=str(uds.year),
            )

        if found is None:
            for person in filing_people:
                if classify(person.title, form_990_roles=person.roles) is role:
                    found = Sourced(
                        value=person_name(person.name),
                        source=f"IRS Form 990 Part VII, FY{person.tax_year}",
                        as_of=f"FY{person.tax_year}",
                        caveat=(
                            "A filing lags 12-24 months; confirm the post is "
                            "still held"
                        ),
                    )
                    break

        if found is None:
            for person in website_people:
                if classify(person.title) is role:
                    found = Sourced(
                        value=person.name,
                        source="Organization's own website",
                        url=person.source_url,
                        as_of=(
                            person.fetched_at.strftime("%Y-%m-%d")
                            if person.fetched_at
                            else None
                        ),
                        verified=False,
                        caveat=(
                            "Read from a public page by a heuristic -- confirm "
                            "against the linked page before using the name"
                        ),
                    )
                    break

        candidates.append((label, why, found))

    # Up to three, and a role this database can name beats one it cannot. The
    # first version of this kept the first three in priority order, which threw
    # away a named chief executive to make room for a blank finance row -- three
    # roles with two names is worse than three roles with three.
    named = [entry for entry in candidates if entry[2] is not None]
    unnamed = [entry for entry in candidates if entry[2] is None]
    dossier.people = (named + unnamed)[:3]

    # --- 6. Sources and flags -----------------------------------------------
    if filing_people:
        dossier.sources.append(
            ("IRS Form 990 e-file XML, Part VII", "https://www.irs.gov/charities-non-profits/form-990-series-downloads")
        )
    if organization.ein:
        dossier.sources.append(
            (
                f"ProPublica Nonprofit Explorer, EIN {organization.ein}",
                f"https://projects.propublica.org/nonprofits/organizations/{organization.ein}",
            )
        )
    if crawl and crawl.url:
        dossier.sources.append(
            (f"Organization website, read {crawl.fetched_at:%Y-%m-%d}", crawl.url)
        )

    match = session.scalar(
        select(EinMatch).where(EinMatch.organization_id == organization.id)
    )
    if match and MatchStatus(match.status) is MatchStatus.PENDING:
        dossier.flags.append(
            "EIN match is unconfirmed and sits in the review queue -- no filing "
            "data here can be attributed to this organization until it is settled"
        )
    elif not organization.ein:
        dossier.flags.append(
            "No confirmed EIN, so nothing from a Form 990 appears above"
        )

    if dossier.named_people == 0:
        dossier.flags.append(
            "No decision-maker name established from any held source -- the roles "
            "above are the research task, not a finding"
        )

    for person in website_people:
        if person.email:
            break
    else:
        if not dossier.email:
            dossier.flags.append(
                "No published work email held. Establish the official domain "
                "first, then use a finder tool against the role -- never against "
                "a guessed address"
            )

    stale = _age_flag(crawl.fetched_at if crawl else None, "The website")
    if stale:
        dossier.flags.append(stale)

    return dossier


def render(dossier: Dossier) -> str:
    """One organization, as Markdown."""
    lines = [f"## {dossier.name}", ""]

    lines.append("**1. Official name and website**")
    lines.append("")
    lines.append(f"- Name as published by HRSA: {dossier.name}")
    if dossier.website:
        lines.append(
            f"- Website: {dossier.website.value}  \n"
            f"  _{dossier.website.source}, as of {dossier.website.as_of}_"
        )
    else:
        lines.append("- Website: **not published by HRSA — manual verification required**")
    lines.append("")

    lines.append("**2. Main location and service area**")
    lines.append("")
    lines.append(f"- Main location: {dossier.location or 'Not available'}")
    if dossier.service_area:
        shown = ", ".join(dossier.service_area[:12])
        more = (
            f" (+{len(dossier.service_area) - 12} more)"
            if len(dossier.service_area) > 12
            else ""
        )
        lines.append(f"- Service area, from its delivery sites: {shown}{more}")
    else:
        lines.append("- Service area: no site-level detail held")
    lines.append("")

    lines.append("**3. Operational priorities and service lines**")
    lines.append("")
    if dossier.priorities:
        lines.extend(f"- {item}" for item in dossier.priorities)
    else:
        lines.append("- Nothing held beyond the HRSA listing")
    lines.append("")

    lines.append("**4. Published general contact**")
    lines.append("")
    if dossier.phone:
        lines.append(
            f"- Phone: {dossier.phone.value}  \n  _{dossier.phone.source}_"
        )
    else:
        lines.append("- Phone: Not available")
    if dossier.email:
        lines.append(
            f"- Email: {dossier.email.value}  \n  _{dossier.email.source}_"
        )
    else:
        lines.append(
            "- Email: none published in any held source. "
            "**Do not guess an address from the domain.**"
        )
    lines.append("")

    lines.append("**5. Decision-maker roles**")
    lines.append("")
    for label, why, person in dossier.people:
        if person is None:
            lines.append(f"- **{label}** — name not established. {why}")
        else:
            mark = "" if person.verified else " ⚠"
            lines.append(f"- **{label}**: {person.value}{mark}")
            lines.append(f"  - {why}")
            detail = person.source + (
                f", as of {person.as_of}" if person.as_of else ""
            )
            lines.append(f"  - Source: {detail}")
            if person.url:
                lines.append(f"  - Page: {person.url}")
            if person.caveat:
                lines.append(f"  - ⚠ {person.caveat}")
    lines.append("")

    lines.append("**6. Sources and verification**")
    lines.append("")
    for label, url in dossier.sources:
        lines.append(f"- {label} — {url}")
    lines.append(f"- Brief generated {_today()} from data already held locally.")
    lines.append("")

    if dossier.flags:
        lines.append("**Requires manual verification**")
        lines.append("")
        lines.extend(f"- {flag}" for flag in dossier.flags)
        lines.append("")

    return "\n".join(lines)


def render_all(dossiers: list[Dossier], *, state: str | None) -> str:
    where = state or "all states"
    header = [
        f"# FQHC outreach research brief — {where}",
        "",
        f"{len(dossiers)} organizations. Generated {_today()}.",
        "",
        "Every line below comes from a source already loaded into this database: "
        "HRSA's health center files, the HRSA Uniform Data System, IRS Form 990 "
        "filings, and organizations' own public pages. Nothing was inferred, and "
        "no name appears without the source that published it.",
        "",
        "**No patient data of any kind is held or used.** Contact details are "
        "work addresses and main telephone numbers an organization published "
        "about itself. Pages behind a login, patient portals, billing and "
        "appointment pages are never read.",
        "",
        "Items marked ⚠ were read from a web page by a heuristic rather than "
        "taken from a filing, and need confirming against the linked page before "
        "the name is used.",
        "",
        "---",
        "",
    ]
    return "\n".join(header) + "\n".join(render(d) for d in dossiers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.dossier",
        description="A per-organization outreach research brief, from held data only.",
    )
    parser.add_argument("--state", help="Two-letter state code, e.g. IL")
    parser.add_argument(
        "--gaps-only",
        action="store_true",
        help="Only organizations with no decision-maker name established.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--config")
    arguments = parser.parse_args(argv)

    from app.config import get_config, load_config
    from app.db import session_scope
    from app.models import Organization

    config = load_config(arguments.config) if arguments.config else get_config()
    with session_scope(config) as session:
        statement = select(Organization).order_by(Organization.name)
        if arguments.state:
            statement = statement.where(Organization.state == arguments.state.upper())
        organizations = session.scalars(statement).all()

        dossiers = [build(session, organization) for organization in organizations]
        if arguments.gaps_only:
            dossiers = [d for d in dossiers if d.named_people == 0]
        if arguments.limit:
            dossiers = dossiers[: arguments.limit]

        print(render_all(dossiers, state=arguments.state))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
