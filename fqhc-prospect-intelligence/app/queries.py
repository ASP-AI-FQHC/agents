"""Read-side queries for the dashboard: filtering, sorting, and summaries.

Kept separate from the routes so the same filter object drives the table, the
exports and the summary strip -- an export always contains exactly what the
screen was showing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import Config
from app.models import (
    EinMatch,
    Filing,
    GranteeType,
    IngestRun,
    MatchStatus,
    Organization,
    RunStatus,
    Score,
    UdsReport,
)

SortKey = Literal["score", "name", "state", "revenue", "sites", "match", "patients", "staff"]
SORT_KEYS: tuple[str, ...] = (
    "score", "name", "state", "revenue", "sites", "match", "patients", "staff",
)

# Match-status filter groups, expressed in the language of the sales workflow
# rather than the internal enum.
MATCH_FILTERS: dict[str, tuple[MatchStatus, ...]] = {
    "confirmed": (MatchStatus.AUTO, MatchStatus.ACCEPTED),
    "review": (MatchStatus.PENDING,),
    "unmatched": (MatchStatus.UNMATCHED, MatchStatus.REJECTED),
}


@dataclass
class Filters:
    """Everything the master table can be narrowed by."""

    q: str | None = None
    states: list[str] = field(default_factory=list)
    min_score: float | None = None
    min_sites: int | None = None
    min_revenue: float | None = None
    max_revenue: float | None = None
    match: str | None = None          # one of MATCH_FILTERS, or None for all
    grantee_type: str | None = None
    sort: str = "score"
    direction: str = "desc"
    page: int = 1

    def normalized(self) -> "Filters":
        self.states = [s.strip().upper() for s in self.states if s and s.strip()]
        self.sort = self.sort if self.sort in SORT_KEYS else "score"
        self.direction = "asc" if self.direction == "asc" else "desc"
        self.page = max(self.page, 1)
        if self.match not in MATCH_FILTERS:
            self.match = None
        return self

    @property
    def is_active(self) -> bool:
        """True when the view is narrower than the whole database."""
        return any(
            [
                self.q,
                self.states,
                self.min_score is not None,
                self.min_sites is not None,
                self.min_revenue is not None,
                self.max_revenue is not None,
                self.match,
                self.grantee_type,
            ]
        )

    def describe(self) -> str:
        """Human-readable filter summary, used in export headers."""
        parts: list[str] = []
        if self.q:
            parts.append(f'name contains "{self.q}"')
        if self.states:
            parts.append("state in " + ", ".join(self.states))
        if self.match:
            parts.append(f"EIN match {self.match}")
        if self.grantee_type:
            parts.append(f"grantee type {self.grantee_type}")
        if self.min_score is not None:
            parts.append(f"score >= {self.min_score:g}")
        if self.min_sites is not None:
            parts.append(f"sites >= {self.min_sites:g}")
        if self.min_revenue is not None:
            parts.append(f"revenue >= ${self.min_revenue:,.0f}")
        if self.max_revenue is not None:
            parts.append(f"revenue <= ${self.max_revenue:,.0f}")
        return "; ".join(parts) if parts else "none (all organizations)"

    def query_string(self, **overrides: Any) -> str:
        """Rebuild the query string, e.g. for sort links and export buttons."""
        from urllib.parse import urlencode

        values: dict[str, Any] = {
            "q": self.q or "",
            "state": self.states,
            "min_score": "" if self.min_score is None else self.min_score,
            "min_sites": "" if self.min_sites is None else self.min_sites,
            "min_revenue": "" if self.min_revenue is None else self.min_revenue,
            "max_revenue": "" if self.max_revenue is None else self.max_revenue,
            "match": self.match or "",
            "grantee_type": self.grantee_type or "",
            "sort": self.sort,
            "direction": self.direction,
            "page": self.page,
        }
        values.update(overrides)
        pairs: list[tuple[str, Any]] = []
        for key, value in values.items():
            if value in ("", None, []):
                continue
            if isinstance(value, list):
                pairs.extend((key, item) for item in value)
            else:
                pairs.append((key, value))
        return urlencode(pairs)


@dataclass
class ProspectRow:
    """One row of the master table, with everything needed to render it."""

    organization: Organization
    score: Score | None
    match: EinMatch | None
    revenue: float | None
    revenue_year: int | None
    revenue_period_end: datetime | None
    # Newest UDS year, where one has been loaded.
    patients: int | None = None
    staff_fte: float | None = None
    uds_year: int | None = None
    ehr_vendor: str | None = None

    @property
    def composite(self) -> float | None:
        return self.score.composite if self.score else None

    @property
    def match_status(self) -> MatchStatus:
        return MatchStatus(self.match.status) if self.match else MatchStatus.UNMATCHED

    @property
    def ein(self) -> str | None:
        return self.organization.ein


def latest_revenue_subquery():
    """Per-EIN latest filing that reports revenue.

    Filings without extracted figures are excluded here so a recent PDF-only
    year cannot present as "no revenue".
    """
    newest = (
        select(
            Filing.ein.label("ein"),
            func.max(Filing.tax_year).label("tax_year"),
        )
        .where(Filing.total_revenue.is_not(None))
        .group_by(Filing.ein)
        .subquery()
    )
    return (
        select(
            Filing.ein.label("ein"),
            Filing.total_revenue.label("revenue"),
            Filing.tax_year.label("tax_year"),
            Filing.period_end.label("period_end"),
        )
        .join(
            newest,
            and_(Filing.ein == newest.c.ein, Filing.tax_year == newest.c.tax_year),
        )
        .subquery()
    )


def latest_uds_subquery():
    """Per-organization newest UDS year, with the figures the table shows."""
    newest = (
        select(
            UdsReport.organization_id.label("organization_id"),
            func.max(UdsReport.year).label("year"),
        )
        .group_by(UdsReport.organization_id)
        .subquery()
    )
    return (
        select(
            UdsReport.organization_id.label("organization_id"),
            UdsReport.year.label("year"),
            UdsReport.patients.label("patients"),
            UdsReport.total_fte.label("total_fte"),
            UdsReport.ehr_vendor.label("ehr_vendor"),
        )
        .join(
            newest,
            and_(
                UdsReport.organization_id == newest.c.organization_id,
                UdsReport.year == newest.c.year,
            ),
        )
        .subquery()
    )


def base_query() -> tuple[Select, Any, Any, Any]:
    """Organizations joined to their score, EIN match and latest revenue."""
    revenue = latest_revenue_subquery()
    uds = latest_uds_subquery()
    usable = EinMatch.status.in_(
        [MatchStatus.AUTO.value, MatchStatus.ACCEPTED.value]
    )

    statement = (
        select(
            Organization, Score, EinMatch,
            revenue.c.revenue, revenue.c.tax_year, revenue.c.period_end,
            uds.c.patients, uds.c.total_fte, uds.c.year, uds.c.ehr_vendor,
        )
        .outerjoin(Score, Score.organization_id == Organization.id)
        .outerjoin(EinMatch, EinMatch.organization_id == Organization.id)
        # Revenue is attached only through a confirmed EIN, mirroring the rule
        # that unconfirmed matches never carry financials.
        .outerjoin(revenue, and_(revenue.c.ein == EinMatch.ein, usable))
        # UDS attaches to the organization directly -- it is HRSA's own data
        # about a HRSA grantee and needs no EIN to be trustworthy.
        .outerjoin(uds, uds.c.organization_id == Organization.id)
        .options(selectinload(Organization.sites))
    )
    return statement, Score, EinMatch, revenue


def apply_filters(statement: Select, revenue, filters: Filters) -> Select:
    conditions = []

    if filters.q:
        pattern = f"%{filters.q.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(Organization.name).like(pattern),
                func.lower(Organization.city).like(pattern),
                EinMatch.ein.like(f"%{filters.q.strip()}%"),
            )
        )
    if filters.states:
        conditions.append(Organization.state.in_(filters.states))
    if filters.grantee_type:
        conditions.append(Organization.grantee_type == filters.grantee_type)
    if filters.min_score is not None:
        conditions.append(Score.composite >= filters.min_score)
    if filters.min_sites is not None:
        conditions.append(Organization.site_count >= filters.min_sites)
    if filters.min_revenue is not None:
        conditions.append(revenue.c.revenue >= filters.min_revenue)
    if filters.max_revenue is not None:
        conditions.append(revenue.c.revenue <= filters.max_revenue)
    if filters.match:
        statuses = [status.value for status in MATCH_FILTERS[filters.match]]
        if filters.match == "unmatched":
            # Organizations that were never searched belong here too.
            conditions.append(
                or_(EinMatch.status.in_(statuses), EinMatch.id.is_(None))
            )
        else:
            conditions.append(EinMatch.status.in_(statuses))

    return statement.where(*conditions) if conditions else statement


def apply_sort(statement: Select, revenue, filters: Filters) -> Select:
    descending = filters.direction == "desc"

    columns = {
        "score": Score.composite,
        "name": Organization.name,
        "state": Organization.state,
        "revenue": revenue.c.revenue,
        "sites": Organization.site_count,
        "match": EinMatch.score,
        "patients": statement.selected_columns.patients,
        "staff": statement.selected_columns.total_fte,
    }
    column = columns.get(filters.sort, Score.composite)

    # Nulls last in both directions: an unknown value is not a small value, and
    # should never occupy the top of a sorted list.
    order = [column.is_(None), column.desc() if descending else column.asc()]
    if filters.sort != "name":
        order.append(Organization.name.asc())
    return statement.order_by(*order)


def fetch_rows(
    session: Session,
    filters: Filters,
    *,
    page_size: int | None = None,
    limit: int | None = None,
) -> tuple[list[ProspectRow], int]:
    """Return the filtered page of rows plus the total matching count."""
    filters = filters.normalized()
    statement, _, _, revenue = base_query()
    statement = apply_filters(statement, revenue, filters)

    count_statement = select(func.count()).select_from(statement.subquery())
    total = session.scalar(count_statement) or 0

    statement = apply_sort(statement, revenue, filters)
    if limit is not None:
        statement = statement.limit(limit)
    elif page_size:
        statement = statement.offset((filters.page - 1) * page_size).limit(page_size)

    rows = [
        ProspectRow(
            organization=organization,
            score=score,
            match=match,
            revenue=rev,
            revenue_year=tax_year,
            revenue_period_end=period_end,
            patients=patients,
            staff_fte=total_fte,
            uds_year=uds_year,
            ehr_vendor=ehr_vendor,
        )
        for (
            organization, score, match, rev, tax_year, period_end,
            patients, total_fte, uds_year, ehr_vendor,
        ) in session.execute(statement).all()
    ]
    return rows, total


@dataclass
class Summary:
    total: int = 0
    matched: int = 0
    needs_review: int = 0
    unmatched: int = 0
    with_financials: int = 0
    average_score: float | None = None
    total_sites: int = 0

    @property
    def matched_percent(self) -> float | None:
        return (self.matched / self.total * 100) if self.total else None


def summarize(session: Session) -> Summary:
    """Database-wide counts for the dashboard strip."""
    summary = Summary()
    summary.total = session.scalar(select(func.count()).select_from(Organization)) or 0
    if not summary.total:
        return summary

    summary.total_sites = (
        session.scalar(select(func.coalesce(func.sum(Organization.site_count), 0))) or 0
    )

    status_counts = dict(
        session.execute(
            select(EinMatch.status, func.count()).group_by(EinMatch.status)
        ).all()
    )
    summary.matched = sum(
        count
        for status, count in status_counts.items()
        if MatchStatus(status) in MATCH_FILTERS["confirmed"]
    )
    summary.needs_review = status_counts.get(MatchStatus.PENDING.value, 0)
    summary.unmatched = summary.total - summary.matched - summary.needs_review

    summary.average_score = session.scalar(select(func.avg(Score.composite)))

    revenue = latest_revenue_subquery()
    summary.with_financials = (
        session.scalar(
            select(func.count(func.distinct(EinMatch.organization_id)))
            .select_from(EinMatch)
            .join(revenue, revenue.c.ein == EinMatch.ein)
            .where(
                EinMatch.status.in_(
                    [MatchStatus.AUTO.value, MatchStatus.ACCEPTED.value]
                )
            )
        )
        or 0
    )
    return summary


def review_queue(session: Session, limit: int | None = None) -> list[ProspectRow]:
    """Organizations whose EIN match needs a human decision, worst first.

    Ordered by score ascending so the least certain matches -- the ones most
    likely to be wrong -- are reviewed first.
    """
    statement, _, _, revenue = base_query()
    statement = statement.where(EinMatch.status == MatchStatus.PENDING.value).order_by(
        EinMatch.score.asc().nulls_first(), Organization.name.asc()
    )
    if limit:
        statement = statement.limit(limit)

    return [
        ProspectRow(
            organization=organization,
            score=score,
            match=match,
            revenue=rev,
            revenue_year=tax_year,
            revenue_period_end=period_end,
            patients=patients,
            staff_fte=total_fte,
            uds_year=uds_year,
            ehr_vendor=ehr_vendor,
        )
        for (
            organization, score, match, rev, tax_year, period_end,
            patients, total_fte, uds_year, ehr_vendor,
        ) in session.execute(statement).all()
    ]


def organization_detail(
    session: Session, organization_id: int
) -> tuple[Organization | None, Score | None, EinMatch | None, list[Filing]]:
    """One organization with its score, match and filings (newest first)."""
    organization = session.get(
        Organization,
        organization_id,
        options=[
            selectinload(Organization.sites),
            selectinload(Organization.ein_match),
            selectinload(Organization.score),
        ],
    )
    if organization is None:
        return None, None, None, []

    filings: list[Filing] = []
    ein = organization.ein
    if ein:
        filings = list(
            session.scalars(
                select(Filing)
                .where(Filing.ein == ein)
                .order_by(Filing.tax_year.desc())
            ).all()
        )
    return organization, organization.score, organization.ein_match, filings


@dataclass
class SimilarOrganization:
    """A comparable organization, with why it is considered comparable.

    Nothing here is fetched: similarity is computed from the data already in
    this database, so the reasons are always things a user can check on the
    other organization's own page.
    """

    row: ProspectRow
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0


def similar_organizations(
    session: Session, organization: Organization, *, limit: int = 5
) -> list[SimilarOrganization]:
    """Organizations comparable to this one by footprint, size and programme.

    Deliberately a local computation rather than a claim about any relationship
    between the organizations -- there is no public source for that, and
    inventing one would be worse than offering nothing.
    """
    rows, _ = fetch_rows(session, Filters(sort="score", direction="desc"))
    target = next((r for r in rows if r.organization.id == organization.id), None)
    target_revenue = target.revenue if target else None
    target_ntee = (organization.ntee_code or "")[:3]

    scored: list[SimilarOrganization] = []
    for row in rows:
        other = row.organization
        if other.id == organization.id:
            continue

        # Same state is required. Every FQHC in a state shares its state and
        # its IRS classification, so without this the list would be "every
        # health center in Illinois" -- true, and useless.
        if not other.state or other.state != organization.state:
            continue

        reasons: list[str] = [f"also in {other.state}"]
        score = 3.0
        size_signals = 0

        if organization.site_count and other.site_count:
            ratio = min(other.site_count, organization.site_count) / max(
                other.site_count, organization.site_count
            )
            if ratio >= 0.5:
                score += 2 + ratio
                size_signals += 1
                reasons.append(f"similar footprint ({other.site_count} sites)")

        if target_revenue and row.revenue:
            ratio = min(row.revenue, target_revenue) / max(row.revenue, target_revenue)
            if ratio >= 0.5:
                score += 2 + ratio
                size_signals += 1
                reasons.append("similar revenue")

        # A resemblance in size is what makes two health centers comparable as
        # prospects. State and classification alone describe hundreds of them.
        if size_signals == 0:
            continue

        if target_ntee and (other.ntee_code or "")[:3] == target_ntee:
            score += 1
            reasons.append("same IRS classification")

        if (
            other.grantee_type
            and organization.grantee_type
            and other.grantee_type == organization.grantee_type
        ):
            score += 0.5

        scored.append(SimilarOrganization(row=row, reasons=reasons, score=score))

    scored.sort(key=lambda s: (s.score, s.row.composite or 0), reverse=True)
    return scored[:limit]


def organization_people(session: Session, ein: str | None):
    """Part VII people for the most recent filing year, board members first."""
    from app.models import Person

    if not ein:
        return []
    latest = session.scalar(
        select(func.max(Person.tax_year)).where(Person.ein == ein)
    )
    if latest is None:
        return []
    people = list(
        session.scalars(
            select(Person).where(Person.ein == ein, Person.tax_year == latest)
        ).all()
    )
    # Board first, then by compensation: the order someone reading a prospect
    # profile actually wants.
    people.sort(
        key=lambda p: (not p.is_board_member, -(p.total_compensation or 0), p.name)
    )
    return people


@dataclass
class ContactRow:
    """One named person, flattened for an outreach list.

    Both sources land in the same shape so a single list can be worked
    through, but ``source`` is carried on every row: a name from a signed
    federal filing and a name read off a web page are not the same claim, and
    the person calling needs to know which one they are holding.
    """

    organization: "Organization"
    composite: float | None
    ein: str | None
    name: str
    title: str | None
    role: str | None
    email: str | None
    source: str
    source_detail: str | None
    compensation: float | None = None
    is_board_member: bool = False


def fetch_contacts(session: Session, filters: Filters) -> list[ContactRow]:
    """Every named person at the organizations the current filters select.

    Ordered the way the list gets worked: best-fitting organization first,
    board members ahead of staff within each one.
    """
    rows, _ = fetch_rows(session, filters, page_size=None)

    from app.models import UdsReport

    directors = {
        report.organization_id: report
        for report in session.scalars(
            select(UdsReport)
            .where(UdsReport.director_name.is_not(None))
            .order_by(UdsReport.year)
        ).all()
    }

    contacts: list[ContactRow] = []
    for row in rows:
        organization = row.organization

        director = directors.get(organization.id)
        if director is not None:
            contacts.append(
                ContactRow(
                    organization=organization,
                    composite=row.composite,
                    ein=row.ein,
                    name=director.director_name,
                    title="Project Director",
                    role=None,
                    email=director.director_email,
                    source="HRSA UDS",
                    source_detail=(
                        f"{director.year} UDS return"
                        + (f" -- {director.director_phone}" if director.director_phone else "")
                    ),
                )
            )

        for person in organization_people(session, row.ein):
            contacts.append(
                ContactRow(
                    organization=organization,
                    composite=row.composite,
                    ein=row.ein,
                    name=person.name,
                    title=person.title,
                    role=person.role_label,
                    email=None,
                    source="IRS Form 990 Part VII",
                    source_detail=f"Tax year {person.tax_year}",
                    compensation=person.total_compensation,
                    is_board_member=person.is_board_member,
                )
            )

        for person in organization_website_people(session, organization.id):
            contacts.append(
                ContactRow(
                    organization=organization,
                    composite=row.composite,
                    ein=row.ein,
                    name=person.name,
                    title=person.title,
                    role=None,
                    email=person.email,
                    source="Organization website",
                    source_detail=person.source_url,
                    is_board_member=person.is_board_member,
                )
            )

    return contacts


def organization_uds(session: Session, organization_id: int):
    """UDS reports for one organization, newest year first."""
    from app.models import UdsReport

    return list(
        session.scalars(
            select(UdsReport)
            .where(UdsReport.organization_id == organization_id)
            .order_by(UdsReport.year.desc())
        ).all()
    )


def organization_website_people(session: Session, organization_id: int):
    """People named on the organization's own website, board roles first.

    Kept separate from :func:`organization_people` all the way to the template:
    these came from prose, not from a signed filing, and the page says so.
    """
    from app.models import WebsitePerson

    people = list(
        session.scalars(
            select(WebsitePerson)
            .where(WebsitePerson.organization_id == organization_id)
            .order_by(WebsitePerson.id)
        ).all()
    )
    people.sort(key=lambda p: (not p.is_board_member, p.name))
    return people


def organization_website_crawl(session: Session, organization_id: int):
    """The last attempt to read this organization's website, if any."""
    from app.models import WebsiteCrawl

    return session.scalar(
        select(WebsiteCrawl).where(WebsiteCrawl.organization_id == organization_id)
    )


def organization_contractors(session: Session, ein: str | None):
    """Part VII Section B contractors for the most recent filing year."""
    from app.models import Contractor

    if not ein:
        return []
    latest = session.scalar(
        select(func.max(Contractor.tax_year)).where(Contractor.ein == ein)
    )
    if latest is None:
        return []
    return list(
        session.scalars(
            select(Contractor)
            .where(Contractor.ein == ein, Contractor.tax_year == latest)
            .order_by(Contractor.compensation.desc().nulls_last())
        ).all()
    )


def organization_profile(session: Session, ein: str | None):
    """The most recent Form 990 profile read from the IRS XML, if any."""
    from app.models import FilingProfile

    if not ein:
        return None
    return session.scalars(
        select(FilingProfile)
        .where(FilingProfile.ein == ein)
        .order_by(FilingProfile.tax_year.desc())
        .limit(1)
    ).first()


def organization_grants(session: Session, organization_id: int):
    """Grants awarded to one organization, split by what kind of claim they are.

    Returns ``(federal_awards, philanthropic)``. They are kept apart because
    only one of them can say whether an award is still running: a federal award
    file carries a period of performance, and a Schedule I row -- which is a
    line on somebody else's tax return -- carries only a year.
    """
    from app.models import Grant, GrantSource

    rows = list(
        session.scalars(
            select(Grant).where(Grant.organization_id == organization_id)
        ).all()
    )

    federal = sorted(
        (row for row in rows if row.source == GrantSource.FEDERAL_AWARD),
        # Current awards first, then the largest.
        key=lambda g: (
            g.end_date is None,
            -(g.end_date.timestamp() if g.end_date else 0),
            -(g.amount or 0),
        ),
    )
    philanthropic = sorted(
        (row for row in rows if row.source == GrantSource.SCHEDULE_I),
        key=lambda g: (-(g.tax_year or 0), -(g.amount or 0)),
    )
    return federal, philanthropic


def organization_programs(session: Session, ein: str | None):
    """Form 990 Part III program areas for the most recent year, as filed."""
    from app.models import ProgramArea

    if not ein:
        return []
    latest = session.scalar(
        select(func.max(ProgramArea.tax_year)).where(ProgramArea.ein == ein)
    )
    if latest is None:
        return []
    return list(
        session.scalars(
            select(ProgramArea)
            .where(ProgramArea.ein == ein, ProgramArea.tax_year == latest)
            .order_by(ProgramArea.position, ProgramArea.id)
        ).all()
    )


# ---------------------------------------------------------------------------
# Headline figures with year-on-year movement
# ---------------------------------------------------------------------------


@dataclass
class Headline:
    """One KPI card: a figure, how it moved, and what it is made of.

    ``previous`` and the deltas are None whenever the prior year is missing or
    itself unreported. A card never shows a movement it cannot substantiate --
    "no change" and "we have one year of data" look identical on a card and are
    completely different facts.
    """

    label: str
    value: float | None
    year: int | None = None
    previous: float | None = None
    previous_year: int | None = None
    components: list[tuple[str, float]] = field(default_factory=list)
    source: str = "IRS Form 990"
    note: str | None = None

    @property
    def change(self) -> float | None:
        if self.value is None or self.previous is None:
            return None
        return self.value - self.previous

    @property
    def change_share(self) -> float | None:
        """Movement as a share of the prior year. None when that year was zero."""
        if self.change is None or not self.previous:
            return None
        return self.change / abs(self.previous)

    @property
    def direction(self) -> int:
        """1 up, -1 down, 0 flat or unknown."""
        change = self.change
        if change is None or change == 0:
            return 0
        return 1 if change > 0 else -1

    @property
    def component_total(self) -> float:
        return sum(value for _, value in self.components)

    def component_share(self, value: float) -> float:
        """A component's width on the mini bar, relative to the largest one."""
        largest = max((amount for _, amount in self.components), default=0)
        return (value / largest) if largest else 0.0


def _by_year(filings, attribute: str) -> dict[int, float]:
    return {
        filing.tax_year: getattr(filing, attribute)
        for filing in filings
        if getattr(filing, attribute) is not None
    }


def headline_figures(filings, profile=None) -> list[Headline]:
    """The four balance-sheet cards, newest year against the one before it.

    Values come from the filings themselves; the Form 990 XML profile only
    fills a gap ProPublica's extract left, and only for the same tax year, so
    a card is never half from one source and half from another.
    """
    ordered = sorted(filings, key=lambda f: f.tax_year, reverse=True)
    if not ordered:
        return []

    latest = ordered[0]
    prior = ordered[1] if len(ordered) > 1 else None
    use_profile = profile is not None and profile.tax_year == latest.tax_year

    def value(attribute: str) -> float | None:
        found = getattr(latest, attribute, None)
        if found is None and use_profile:
            return getattr(profile, attribute, None)
        return found

    cards = [
        Headline(
            label="Revenues",
            value=value("total_revenue"),
            year=latest.tax_year,
            previous=getattr(prior, "total_revenue", None),
            previous_year=getattr(prior, "tax_year", None),
            components=latest.revenue_components(),
        ),
        Headline(
            label="Expenses",
            value=value("total_expenses"),
            year=latest.tax_year,
            previous=getattr(prior, "total_expenses", None),
            previous_year=getattr(prior, "tax_year", None),
            components=profile.expense_components() if use_profile else [],
        ),
        Headline(
            label="Assets",
            value=value("total_assets"),
            year=latest.tax_year,
            previous=getattr(prior, "total_assets", None),
            previous_year=getattr(prior, "tax_year", None),
        ),
        Headline(
            label="Liabilities",
            value=value("total_liabilities"),
            year=latest.tax_year,
            previous=getattr(prior, "total_liabilities", None),
            previous_year=getattr(prior, "tax_year", None),
        ),
    ]
    return cards


@dataclass
class Fact:
    """One entry in the header strip.

    ``kind`` tells the template how to render the value, because a number is
    not automatically a quantity: 262 employees is thousands-separated, the
    year 1982 is not, and an EIN is punctuated differently from either.
    """

    label: str
    value: object
    kind: str = "text"  # text | count | year | ein


@dataclass
class ProfileFacts:
    """The strip of identifying facts that opens the profile.

    Every entry may have a value of None. The template renders "Not available"
    for a None rather than dropping the row, so the reader can tell the
    difference between a fact we do not hold and one we never look for.
    """

    items: list[Fact] = field(default_factory=list)

    def add(self, label: str, value, kind: str = "text") -> None:
        self.items.append(Fact(label, value, kind))


def profile_facts(organization, match, filings, profile, uds=None) -> ProfileFacts:
    """Assemble the header strip: identity, age, size, most recent filing."""
    facts = ProfileFacts()

    # ``status`` comes back as a bare string on rows loaded straight from
    # SQLite, so it is coerced rather than assumed to be the enum.
    usable_ein = None
    if match is not None and match.ein:
        try:
            usable = MatchStatus(match.status).is_usable
        except ValueError:
            usable = False
        usable_ein = match.ein if usable else None
    facts.add("EIN", usable_ein, "ein")
    facts.add(
        "IRS classification",
        "501(c)(3) public charity" if usable_ein and filings else None,
    )

    # Headcount: the 990 counts everyone on a W-2, UDS counts full-time
    # equivalents. They are different measures and the label says which.
    employees = next(
        (f.employee_count for f in filings if f.employee_count is not None), None
    )
    if employees is None and profile is not None:
        employees = profile.employee_count
    facts.add("Employees", employees, "count")

    facts.add("City", organization.city)
    facts.add("State", organization.state)
    facts.add(
        "Year formed",
        profile.formation_year if profile is not None else None,
        "year",
    )
    facts.add(
        "Most recent filing",
        f"FY{filings[0].tax_year}" if filings else None,
    )
    facts.add("NTEE code", organization.ntee_code)
    return facts


# Contractor descriptions are free text a filer typed into Part VII Section B,
# so they are grouped by keyword into the kinds of vendor a reader is looking
# for. The grouping is a label on top of the filed text, never a replacement
# for it -- the description as filed is always shown beside it.
VENDOR_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Health IT and software", (
        "software", "electronic health", "ehr", "emr", "information technology",
        "it services", "informatics", "hosting", "data center", "cloud",
        "network", "cyber", "help desk", "helpdesk", "computer",
    )),
    ("Billing and revenue cycle", (
        "billing", "revenue cycle", "coding", "claims", "collection",
        "accounts receivable", "reimbursement",
    )),
    ("Audit and accounting", (
        "audit", "accounting", "accountant", "cpa", "tax prep", "bookkeep",
    )),
    ("Clinical staffing and services", (
        "physician", "nursing", "nurse", "dental", "medical services",
        "clinical", "provider services", "locum", "behavioral", "pharmacy",
        "laboratory", "radiology", "interpret", "telehealth",
    )),
    ("Facilities and construction", (
        "construction", "renovation", "janitorial", "cleaning", "security",
        "maintenance", "architect", "engineering", "hvac", "landscap",
    )),
    ("Insurance and benefits", (
        "insurance", "benefit", "broker", "malpractice", "retirement",
        "third party administrator", "tpa",
    )),
    ("Consulting and management", (
        "consult", "management services", "advisory", "staffing", "recruit",
        "marketing", "legal", "attorney", "law",
    )),
)

# Keywords match at a word boundary on the left, and are free to run into a
# suffix on the right ("consult" catching "consulting"). A plain substring test
# put "independent AUDIT SERVICES" under health IT, because "it services" sits
# inside "audit services" -- the same trap that once classified "Hector" as a
# CTO.
_VENDOR_PATTERNS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (label, tuple(re.compile(r"\b" + re.escape(word)) for word in words))
    for label, words in VENDOR_CATEGORIES
)


def vendor_category(services: str | None) -> str:
    """Group a Part VII Section B service description into a vendor kind.

    Returns "Other services" when nothing matches and "Not described" when the
    filer left the description blank -- two different facts, and neither is a
    guess about what the vendor does.
    """
    if not services or not services.strip():
        return "Not described"
    text = services.lower()
    for label, patterns in _VENDOR_PATTERNS:
        if any(pattern.search(text) for pattern in patterns):
            return label
    return "Other services"


def grouped_contractors(contractors) -> list[tuple[str, list]]:
    """Contractors grouped by the kind of service they were paid for.

    Groups are ordered by total spend, and within a group by amount paid: the
    largest relationship in the most expensive category reads first.
    """
    groups: dict[str, list] = {}
    for contractor in contractors:
        groups.setdefault(vendor_category(contractor.services), []).append(contractor)

    for rows in groups.values():
        rows.sort(key=lambda c: -(c.compensation or 0))

    return sorted(
        groups.items(),
        key=lambda item: -sum(c.compensation or 0 for c in item[1]),
    )


@dataclass
class ProfilePerson:
    """One named person on the profile, whatever source named them.

    The three sources -- a signed Form 990, the organization's own website, and
    the contact HRSA holds for the UDS return -- carry very different weight,
    so ``sources`` travels with every row and the table shows it. Where the
    same person appears in more than one, the row is merged and lists both:
    that agreement is itself worth seeing.
    """

    name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    compensation: float | None = None
    average_hours: float | None = None
    roles: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    as_of: datetime | None = None
    as_of_label: str | None = None
    source_url: str | None = None
    is_board_member: bool = False

    @property
    def source_label(self) -> str:
        return " + ".join(self.sources)


def _merge_key(name: str) -> str:
    """A comparison key for one person's name.

    Deliberately conservative: case, punctuation and middle initials are
    ignored, but nothing else is. Two people who share a first and last name at
    the same organization stay two rows -- wrongly fusing a father and son on a
    board is worse than listing one person twice with the source of each stated.
    """
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in name.lower())
    parts = [part for part in cleaned.split() if len(part) > 1]
    if len(parts) < 2:
        return " ".join(parts)
    return f"{parts[0]} {parts[-1]}"


def profile_people(
    filing_people, website_people, uds=None
) -> tuple[list[ProfilePerson], list[ProfilePerson]]:
    """Everyone named for this organization, split into staff and board.

    Returns ``(key_personnel, board_members)``. Key personnel are sorted by
    reported compensation, board members alphabetically -- the orders a reader
    actually wants from each list.
    """
    merged: dict[str, ProfilePerson] = {}
    order: list[str] = []

    def absorb(person: ProfilePerson) -> None:
        key = _merge_key(person.name)
        existing = merged.get(key)
        if existing is None:
            merged[key] = person
            order.append(key)
            return
        # Fill gaps only. A value already established by an earlier -- more
        # authoritative -- source is never overwritten by a later one.
        for attribute in (
            "title",
            "email",
            "phone",
            "compensation",
            "average_hours",
            "source_url",
            "as_of",
            "as_of_label",
        ):
            if getattr(existing, attribute) is None:
                setattr(existing, attribute, getattr(person, attribute))
        for role in person.roles:
            if role not in existing.roles:
                existing.roles.append(role)
        for source in person.sources:
            if source not in existing.sources:
                existing.sources.append(source)
        existing.is_board_member = existing.is_board_member or person.is_board_member

    # Form 990 first: a signed federal filing outranks a web page.
    for person in filing_people:
        absorb(
            ProfilePerson(
                name=person.name,
                title=person.title,
                compensation=person.total_compensation,
                average_hours=person.average_hours,
                roles=list(person.roles or []),
                sources=["Form 990"],
                as_of=person.fetched_at,
                as_of_label=f"FY{person.tax_year}" if person.tax_year else None,
                is_board_member=person.is_board_member,
            )
        )

    # The UDS project director: a named person with a direct line, given to
    # HRSA by the health center itself.
    if uds is not None and uds.director_name:
        absorb(
            ProfilePerson(
                name=uds.director_name,
                title="Project Director",
                email=uds.director_email,
                phone=uds.director_phone,
                sources=["HRSA UDS"],
                as_of=uds.fetched_at,
                as_of_label=str(uds.year) if uds.year else None,
            )
        )

    for person in website_people:
        absorb(
            ProfilePerson(
                name=person.name,
                title=person.title,
                email=person.email,
                sources=["Website"],
                as_of=person.fetched_at,
                as_of_label=(
                    person.fetched_at.strftime("%b %Y") if person.fetched_at else None
                ),
                source_url=person.source_url,
                is_board_member=person.is_board_member,
            )
        )

    people = [merged[key] for key in order]
    board = sorted(
        (person for person in people if person.is_board_member),
        key=lambda p: p.name,
    )
    staff = sorted(
        (person for person in people if not person.is_board_member),
        key=lambda p: (-(p.compensation or 0), p.name),
    )
    return staff, board


@dataclass
class Tag:
    """One classification pill, with the source that justifies it."""

    label: str
    basis: str
    tone: str = "info"


def profile_tags(organization, filings, profile, uds=None, contractors=()) -> list[
    tuple[str, list[Tag]]
]:
    """Grouped classification pills, each carrying why it is there.

    Every pill is a restatement of something already on the page -- an NTEE
    code, a HRSA program, a checkbox on the return. None is a judgement, and
    hovering one shows the source, so a pill can always be traced back to a
    filing rather than taken on trust.
    """
    from app import ntee as ntee_module

    types: list[Tag] = []
    issues: list[Tag] = []
    characteristics: list[Tag] = []

    if organization.grantee_type == GranteeType.AWARDEE:
        types.append(
            Tag(
                "Federally Qualified Health Center",
                "HRSA Section 330 grant awardee",
                "green",
            )
        )
    elif organization.grantee_type == GranteeType.LOOK_ALIKE:
        types.append(
            Tag(
                "FQHC Look-Alike",
                "Meets HRSA health center requirements without a Section 330 grant",
                "blue",
            )
        )

    if filings:
        types.append(
            Tag("501(c)(3) nonprofit", f"Files an IRS Form 990 (FY{filings[0].tax_year})")
        )

    specific, group = ntee_module.describe(organization.ntee_code)
    if specific:
        types.append(Tag(specific, f"NTEE code {organization.ntee_code}"))
    if group and group != specific:
        issues.append(Tag(group, f"NTEE group for code {organization.ntee_code}"))

    for programme in organization.funding_programs or (
        [organization.funding_program] if organization.funding_program else []
    ):
        issues.append(Tag(programme, "HRSA funding program awarded to this organization"))

    if organization.site_count and organization.site_count > 1:
        characteristics.append(
            Tag(
                f"{organization.site_count} delivery sites",
                "HRSA health center service delivery site file",
            )
        )

    if organization.federal_award_amount:
        characteristics.append(
            Tag("Receives federal funding", "HRSA award published for this grantee")
        )

    if profile is not None:
        if profile.single_audit_performed:
            characteristics.append(
                Tag(
                    "Single Audit performed",
                    f"Form 990 FY{profile.tax_year} Part XII",
                    "purple",
                )
            )
        elif profile.financials_audited:
            characteristics.append(
                Tag("Independently audited", f"Form 990 FY{profile.tax_year} Part XII")
            )
        if profile.audit_committee:
            characteristics.append(
                Tag("Has an audit committee", f"Form 990 FY{profile.tax_year} Part XII")
            )
        if profile.volunteer_count:
            characteristics.append(
                Tag(
                    f"{profile.volunteer_count:,} volunteers",
                    f"Form 990 FY{profile.tax_year} Part I line 6",
                )
            )

    if uds is not None:
        if uds.patients:
            characteristics.append(
                Tag(
                    f"{uds.patients:,} patients",
                    f"HRSA Uniform Data System, {uds.year}",
                    "orange",
                )
            )
        if uds.ehr_vendor:
            characteristics.append(
                Tag(
                    f"Uses {uds.ehr_vendor}",
                    f"HRSA UDS health IT return, {uds.year}",
                    "purple",
                )
            )
        if uds.urban_rural:
            characteristics.append(
                Tag(uds.urban_rural, f"HRSA Uniform Data System, {uds.year}")
            )

    if contractors:
        characteristics.append(
            Tag(
                f"{len(contractors)} disclosed contractors",
                "Form 990 Part VII Section B, contractors paid over $100,000",
            )
        )

    groups = [
        ("Nonprofit types", types),
        ("Issues and program areas", issues),
        ("Characteristics", characteristics),
    ]
    return [(name, tags) for name, tags in groups if tags]


def organization_changes(
    session: Session, organization_id: int, *, limit: int = 20
):
    """Change history for one organization, newest first."""
    from app.models import ChangeEvent

    return list(
        session.scalars(
            select(ChangeEvent)
            .where(ChangeEvent.organization_id == organization_id)
            .order_by(ChangeEvent.detected_at.desc(), ChangeEvent.id.desc())
            .limit(limit)
        ).all()
    )


@dataclass
class DataStatus:
    """Where the data came from and when -- shown as a banner on every page."""

    runs: dict[str, IngestRun] = field(default_factory=dict)
    has_data: bool = False

    @property
    def latest_run(self) -> IngestRun | None:
        finished = [run for run in self.runs.values() if run.finished_at]
        return max(finished, key=lambda r: r.finished_at) if finished else None

    @property
    def on_cached_data(self) -> bool:
        return any(run.used_cache for run in self.runs.values())

    @property
    def any_source_unreachable(self) -> bool:
        return any(not run.source_reachable for run in self.runs.values())

    @property
    def cache_date(self) -> datetime | None:
        dates = [run.cache_date for run in self.runs.values() if run.cache_date]
        return min(dates) if dates else None

    @property
    def problems(self) -> list[str]:
        return [
            f"{run.stage}: {run.message}"
            for run in self.runs.values()
            if run.message and run.status in (RunStatus.PARTIAL, RunStatus.FAILED)
        ]


def data_status(session: Session) -> DataStatus:
    """Most recent completed run per stage."""
    status = DataStatus()
    newest = (
        select(IngestRun.stage, func.max(IngestRun.id).label("id"))
        .where(IngestRun.finished_at.is_not(None))
        .group_by(IngestRun.stage)
        .subquery()
    )
    runs = session.scalars(
        select(IngestRun).join(newest, IngestRun.id == newest.c.id)
    ).all()
    status.runs = {run.stage: run for run in runs}
    status.has_data = (
        session.scalar(select(func.count()).select_from(Organization)) or 0
    ) > 0
    return status
