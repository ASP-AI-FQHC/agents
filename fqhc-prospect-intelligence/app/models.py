"""SQLAlchemy ORM models -- the whole prospect database in one SQLite file.

Design notes
------------
* One row in :class:`Organization` == one FQHC *grantee organization*, which is
  what a salesperson actually sells to. The individual delivery sites HRSA
  publishes are kept in :class:`Site` so the detail view can show them.
* Every externally-sourced fact carries provenance (``source_file``,
  ``fetched_at``) so the UI can label data freshness honestly.
* Nullable columns mean "we do not know", and the UI renders them as
  "Not available". Nothing downstream may substitute a default for a null.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, ClassVar

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def utcnow() -> datetime:
    """Timezone-aware UTC now (SQLite has no native tz support)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class GranteeType(str, enum.Enum):
    """How HRSA classifies the organization."""

    AWARDEE = "awardee"          # Section 330 grant recipient
    LOOK_ALIKE = "look-alike"    # meets requirements, no 330 grant
    UNKNOWN = "unknown"


class MatchStatus(str, enum.Enum):
    """Lifecycle of an organization -> EIN association."""

    AUTO = "auto"            # fuzzy score >= auto_accept_score
    PENDING = "pending"      # in the human review queue
    ACCEPTED = "accepted"    # a human confirmed it
    REJECTED = "rejected"    # a human rejected it
    UNMATCHED = "unmatched"  # nothing above the review threshold

    @property
    def is_usable(self) -> bool:
        """Whether financials keyed to this EIN may be attributed to the org."""
        return self in (MatchStatus.AUTO, MatchStatus.ACCEPTED)


class ChangeKind(str, enum.Enum):
    """What kind of movement a change event records."""

    APPEARED = "appeared"            # new to the HRSA universe
    DISAPPEARED = "disappeared"      # no longer published by HRSA
    SITES = "sites"                  # opened or closed delivery sites
    FILING = "filing"                # a newer Form 990 became available
    AWARD = "award"                  # federal award amount moved
    GRANTEE_TYPE = "grantee_type"    # look-alike became an awardee, or back
    PATIENTS = "patients"            # UDS patient volume moved year on year

    @property
    def label(self) -> str:
        return {
            ChangeKind.APPEARED: "New health center",
            ChangeKind.DISAPPEARED: "No longer listed",
            ChangeKind.SITES: "Delivery sites",
            ChangeKind.FILING: "New 990 filing",
            ChangeKind.AWARD: "Federal award",
            ChangeKind.GRANTEE_TYPE: "Grantee type",
            ChangeKind.PATIENTS: "Patient volume",
        }[self]


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"   # completed on cached/stale data
    FAILED = "failed"


class Organization(Base):
    """A deduplicated FQHC grantee organization."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)

    # HRSA's grantee identifier (BHCMIS ID / grant number) when available. It is
    # the most reliable dedup key; the normalized name+state is the fallback.
    hrsa_id: Mapped[str | None] = mapped_column(String(64), index=True)
    dedup_key: Mapped[str] = mapped_column(String(320), unique=True, index=True)

    name: Mapped[str] = mapped_column(String(320), index=True)
    normalized_name: Mapped[str] = mapped_column(String(320), index=True)

    street: Mapped[str | None] = mapped_column(String(240))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(2), index=True)
    zip_code: Mapped[str | None] = mapped_column(String(10))
    phone: Mapped[str | None] = mapped_column(String(32))
    website: Mapped[str | None] = mapped_column(String(320))

    site_count: Mapped[int] = mapped_column(Integer, default=0)
    grantee_type: Mapped[GranteeType] = mapped_column(
        String(16), default=GranteeType.UNKNOWN
    )

    # Federal Section 330 award from the HRSA awardee file, when published.
    # Used as the numerator of the grant-dependence score; null means the factor
    # is simply not computed for this organization.
    federal_award_amount: Mapped[float | None] = mapped_column(Float)
    grant_number: Mapped[str | None] = mapped_column(String(64))
    funding_program: Mapped[str | None] = mapped_column(String(240))
    # Every HRSA funding stream the organization receives, not just the first:
    # a health center often holds Community Health Center, Health Care for the
    # Homeless and Migrant Health awards at once, and each is a program area.
    funding_programs: Mapped[list[str] | None] = mapped_column(JSON)

    # IRS National Taxonomy of Exempt Entities code, from ProPublica. The code
    # is stored verbatim; only descriptions we can state accurately are shown.
    ntee_code: Mapped[str | None] = mapped_column(String(16))

    source_file: Mapped[str | None] = mapped_column(String(240))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    sites: Mapped[list["Site"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    ein_match: Mapped["EinMatch | None"] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    score: Mapped["Score | None"] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    @property
    def grantee_label(self) -> str:
        """Display form of the grantee type.

        SQLAlchemy round-trips the enum as a plain string, so this reads the
        value either way rather than relying on ``.value`` existing.
        """
        value = getattr(self.grantee_type, "value", self.grantee_type)
        return {
            GranteeType.AWARDEE.value: "Section 330 awardee",
            GranteeType.LOOK_ALIKE.value: "Look-alike",
        }.get(value, "Grantee type not available")

    @property
    def ein(self) -> str | None:
        """The EIN, but only when the match is auto-accepted or human-approved."""
        match = self.ein_match
        if match and match.ein and MatchStatus(match.status).is_usable:
            return match.ein
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Organization {self.id} {self.name!r} {self.state}>"


class Site(Base):
    """One HRSA service delivery site belonging to an organization."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    site_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(320))
    street: Mapped[str | None] = mapped_column(String(240))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(2))
    zip_code: Mapped[str | None] = mapped_column(String(10))
    site_type: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(64))

    organization: Mapped[Organization] = relationship(back_populates="sites")

    __table_args__ = (
        UniqueConstraint("organization_id", "site_id", name="uq_site_org_siteid"),
    )


class EinMatch(Base):
    """The organization -> EIN association, with its confidence and provenance."""

    __tablename__ = "ein_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, index=True
    )

    ein: Mapped[str | None] = mapped_column(String(16), index=True)
    matched_name: Mapped[str | None] = mapped_column(String(320))
    matched_city: Mapped[str | None] = mapped_column(String(120))
    matched_state: Mapped[str | None] = mapped_column(String(2))

    # rapidfuzz score, 0-100. Always displayed next to the EIN so a user can see
    # how much to trust it.
    score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[MatchStatus] = mapped_column(
        String(16), default=MatchStatus.UNMATCHED, index=True
    )

    # Runner-up candidates, kept so the review queue can offer alternatives.
    candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # EINs a human has rejected for this organization. Excluded from every
    # future search so a rejection is not re-proposed.
    rejected_eins: Mapped[list[str] | None] = mapped_column(JSON)

    searched_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)

    organization: Mapped[Organization] = relationship(back_populates="ein_match")


class Filing(Base):
    """One IRS Form 990 filing as reported by ProPublica Nonprofit Explorer."""

    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    ein: Mapped[str] = mapped_column(String(16), index=True)
    tax_year: Mapped[int] = mapped_column(Integer, index=True)

    total_revenue: Mapped[float | None] = mapped_column(Float)
    total_expenses: Mapped[float | None] = mapped_column(Float)
    total_assets: Mapped[float | None] = mapped_column(Float)
    # The other half of the balance sheet. Assets without liabilities says
    # what an organization holds but nothing about what it owes.
    total_liabilities: Mapped[float | None] = mapped_column(Float)
    # Form 990 Part I line 5: employees on a W-2 in the calendar year. Headcount
    # is the first thing a managed-services quote is sized on.
    employee_count: Mapped[int | None] = mapped_column(Integer)

    # Revenue composition, where the IRS extract provides it. This is the
    # funding mix: how much comes from grants and contributions versus billing
    # for services. Each stays NULL when the source does not report it.
    contributions: Mapped[float | None] = mapped_column(Float)
    program_service_revenue: Mapped[float | None] = mapped_column(Float)
    investment_income: Mapped[float | None] = mapped_column(Float)
    government_grants: Mapped[float | None] = mapped_column(Float)

    @property
    def has_composition(self) -> bool:
        """Whether any funding-mix detail is available for this filing.

        Mirrors the same property on the parse-time record. The templates read
        it off persisted rows, and a property that exists on only one of the two
        representations fails silently in Jinja -- an undefined attribute is
        just falsy, so the section renders empty rather than erroring.
        """
        return any(
            value is not None
            for value in (
                self.contributions,
                self.program_service_revenue,
                self.investment_income,
                self.government_grants,
            )
        )

    @property
    def contribution_share(self) -> float | None:
        """Contributions and grants as a share of total revenue."""
        if self.contributions is None or not self.total_revenue:
            return None
        return self.contributions / self.total_revenue

    @property
    def net_assets(self) -> float | None:
        """Assets less liabilities, only when both were actually reported.

        Deliberately not falling back to assets alone: an organization with
        unreported liabilities is not an organization with none.
        """
        if self.total_assets is None or self.total_liabilities is None:
            return None
        return self.total_assets - self.total_liabilities

    @property
    def surplus(self) -> float | None:
        """Revenue less expenses for the year, when both are known."""
        if self.total_revenue is None or self.total_expenses is None:
            return None
        return self.total_revenue - self.total_expenses

    def revenue_components(self) -> list[tuple[str, float]]:
        """Reported revenue lines, largest first.

        Government grants sit *inside* contributions on the Form 990 (Part VIII
        line 1e is one of the lines summing to line 1h), so listing both as
        given would count the same dollars twice. Where both are reported the
        remainder is shown as "Other contributions"; where only one is, only
        that one is shown. Nothing else is inferred -- no residual is invented
        to make the components add up to the total, because on screen a
        residual is indistinguishable from a reported figure.
        """
        components: list[tuple[str, float]] = []

        government = self.government_grants
        contributions = self.contributions
        if government:
            components.append(("Government grants", government))
            if contributions and contributions - government > 0:
                components.append(("Other contributions", contributions - government))
        elif contributions:
            components.append(("Contributions and grants", contributions))

        if self.program_service_revenue:
            components.append(("Program services", self.program_service_revenue))
        if self.investment_income:
            components.append(("Investment income", self.investment_income))

        return sorted(components, key=lambda item: item[1], reverse=True)

    form_type: Mapped[str | None] = mapped_column(String(32))
    pdf_url: Mapped[str | None] = mapped_column(String(500))

    # End of the tax period the filing covers. This is what "freshness" means
    # for a 990: the figures describe a year that ended on this date, typically
    # 6-18 months before the filing becomes publicly available.
    period_end: Mapped[datetime | None] = mapped_column(DateTime)
    # When ProPublica last updated its record of this filing.
    filing_date: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def has_financials(self) -> bool:
        """False for filings ProPublica lists but has extracted no data from."""
        return any(
            value is not None
            for value in (self.total_revenue, self.total_expenses, self.total_assets)
        )

    __table_args__ = (
        UniqueConstraint("ein", "tax_year", name="uq_filing_ein_year"),
        Index("ix_filing_ein_year_desc", "ein", "tax_year"),
    )


class Score(Base):
    """Composite ICP score plus the per-factor breakdown behind it."""

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, index=True
    )

    composite: Mapped[float] = mapped_column(Float, index=True)
    # [{factor, score, weight, effective_weight, available, detail}, ...]
    breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Which factors could not be computed, so the UI can say why.
    unavailable_factors: Mapped[list[str]] = mapped_column(JSON, default=list)
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organization: Mapped[Organization] = relationship(back_populates="score")


class OrganizationSnapshot(Base):
    """Last known state of an organization, used to detect what moved.

    One row per organization, overwritten on each run. The history lives in
    :class:`ChangeEvent`; this is only the baseline the next run compares to.
    """

    __tablename__ = "organization_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, index=True
    )

    site_count: Mapped[int | None] = mapped_column(Integer)
    grantee_type: Mapped[str | None] = mapped_column(String(16))
    federal_award_amount: Mapped[float | None] = mapped_column(Float)
    latest_tax_year: Mapped[int | None] = mapped_column(Integer)
    latest_revenue: Mapped[float | None] = mapped_column(Float)
    composite: Mapped[float | None] = mapped_column(Float)
    latest_uds_year: Mapped[int | None] = mapped_column(Integer)
    latest_patients: Mapped[int | None] = mapped_column(Integer)

    # True once HRSA stops publishing the organization, so a reappearance can
    # be distinguished from a first sighting.
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organization: Mapped[Organization] = relationship()


class ChangeEvent(Base):
    """One observed movement, kept as a browsable log.

    Only facts sourced from HRSA or the IRS are recorded. Score movements are
    deliberately excluded: editing a weight in config.yaml would otherwise
    generate an event for every organization in the database.
    """

    __tablename__ = "change_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[ChangeKind] = mapped_column(String(24), index=True)
    summary: Mapped[str] = mapped_column(String(320))
    previous_value: Mapped[str | None] = mapped_column(String(120))
    current_value: Mapped[str | None] = mapped_column(String(120))
    # Positive for growth, negative for contraction, None where not meaningful.
    direction: Mapped[int | None] = mapped_column(Integer)

    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    organization: Mapped[Organization] = relationship()

    @property
    def kind_label(self) -> str:
        """Display label for the kind.

        The column is a plain String, so SQLAlchemy returns a str rather than a
        ChangeKind on the way back out; this coerces either form.
        """
        return ChangeKind(self.kind).label

    __table_args__ = (Index("ix_change_org_detected", "organization_id", "detected_at"),)


class Person(Base):
    """An officer, director, trustee or key employee from Form 990 Part VII.

    Keyed by EIN and tax year rather than by organization, matching how the
    filings themselves are keyed, so a re-run replaces a year cleanly.

    There is no contact column and there will not be one: a 990 lists people
    care of the organization's own address, and no free authoritative source
    publishes their direct email or telephone.
    """

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    ein: Mapped[str] = mapped_column(String(16), index=True)
    tax_year: Mapped[int] = mapped_column(Integer, index=True)

    name: Mapped[str] = mapped_column(String(240))
    title: Mapped[str | None] = mapped_column(String(240))
    # Part VII checkbox roles, e.g. ["Board member", "Officer"].
    roles: Mapped[list[str] | None] = mapped_column(JSON)

    average_hours: Mapped[float | None] = mapped_column(Float)
    compensation: Mapped[float | None] = mapped_column(Float)
    related_compensation: Mapped[float | None] = mapped_column(Float)
    other_compensation: Mapped[float | None] = mapped_column(Float)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_people_ein_year", "ein", "tax_year"),
    )

    @property
    def is_board_member(self) -> bool:
        return any(
            role in ("Board member", "Institutional trustee")
            for role in (self.roles or [])
        )

    @property
    def role_label(self) -> str | None:
        return ", ".join(self.roles) if self.roles else None

    @property
    def total_compensation(self) -> float | None:
        parts = [
            value
            for value in (
                self.compensation,
                self.related_compensation,
                self.other_compensation,
            )
            if value is not None
        ]
        return sum(parts) if parts else None


class Contractor(Base):
    """An independent contractor paid over $100,000, from Part VII Section B.

    The most commercially interesting rows in a 990 for a managed services
    provider: this is where an incumbent IT, EHR or billing vendor is named.
    """

    __tablename__ = "contractors"

    id: Mapped[int] = mapped_column(primary_key=True)
    ein: Mapped[str] = mapped_column(String(16), index=True)
    tax_year: Mapped[int] = mapped_column(Integer, index=True)

    name: Mapped[str] = mapped_column(String(240))
    services: Mapped[str | None] = mapped_column(String(500))
    compensation: Mapped[float | None] = mapped_column(Float)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_contractors_ein_year", "ein", "tax_year"),
    )


class FilingProfile(Base):
    """What one Form 990 says about the filer itself.

    Kept apart from :class:`Filing` on purpose. ``Filing`` holds ProPublica's
    extract of the headline figures; this holds what was read out of the IRS's
    own e-file XML -- the mission statement, the year of formation, headcount,
    the balance sheet, the functional expense split and the audit answers.
    Two sources, two tables, each with its own provenance, so a row is never
    half from one and half from the other.
    """

    __tablename__ = "filing_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    ein: Mapped[str] = mapped_column(String(16), index=True)
    tax_year: Mapped[int] = mapped_column(Integer, index=True)

    mission: Mapped[str | None] = mapped_column(Text)
    formation_year: Mapped[int | None] = mapped_column(Integer)
    domicile_state: Mapped[str | None] = mapped_column(String(2))
    website: Mapped[str | None] = mapped_column(String(500))
    employee_count: Mapped[int | None] = mapped_column(Integer)
    volunteer_count: Mapped[int | None] = mapped_column(Integer)

    total_revenue: Mapped[float | None] = mapped_column(Float)
    total_expenses: Mapped[float | None] = mapped_column(Float)
    total_assets: Mapped[float | None] = mapped_column(Float)
    total_liabilities: Mapped[float | None] = mapped_column(Float)
    net_assets: Mapped[float | None] = mapped_column(Float)

    program_expenses: Mapped[float | None] = mapped_column(Float)
    management_expenses: Mapped[float | None] = mapped_column(Float)
    fundraising_expenses: Mapped[float | None] = mapped_column(Float)
    grants_paid: Mapped[float | None] = mapped_column(Float)
    salaries: Mapped[float | None] = mapped_column(Float)

    # Three-state: True, False, or NULL where the return does not answer.
    financials_audited: Mapped[bool | None] = mapped_column(Boolean)
    single_audit_required: Mapped[bool | None] = mapped_column(Boolean)
    single_audit_performed: Mapped[bool | None] = mapped_column(Boolean)
    audit_committee: Mapped[bool | None] = mapped_column(Boolean)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("ein", "tax_year", name="uq_filing_profile_ein_year"),
    )

    @property
    def age_years(self) -> int | None:
        """Years since formation, as of now."""
        if self.formation_year is None:
            return None
        return max(datetime.now(timezone.utc).year - self.formation_year, 0)

    # Columns read off the return, in one place so the "did we learn anything"
    # question below cannot drift out of step with the schema above.
    FACT_COLUMNS: ClassVar[tuple[str, ...]] = (
        "mission", "formation_year", "domicile_state", "website",
        "employee_count", "volunteer_count", "total_revenue", "total_expenses",
        "total_assets", "total_liabilities", "net_assets", "program_expenses",
        "management_expenses", "fundraising_expenses", "grants_paid",
        "salaries", "financials_audited", "single_audit_required",
        "single_audit_performed", "audit_committee",
    )

    @property
    def has_any(self) -> bool:
        """Whether this row carries any fact at all."""
        return any(
            getattr(self, name) is not None for name in self.FACT_COLUMNS
        )

    @property
    def has_balance_sheet(self) -> bool:
        return any(
            value is not None
            for value in (self.total_assets, self.total_liabilities, self.net_assets)
        )

    @property
    def has_expense_split(self) -> bool:
        """Whether Part IX's functional columns were reported.

        Mirrors the property of the same name on the parse-time record. Jinja
        treats an undefined attribute as merely falsy, so a property that
        exists on one of the two representations and not the other makes the
        section vanish in silence rather than raising -- which is exactly what
        happened here before this was added.
        """
        return any(
            value is not None
            for value in (
                self.program_expenses,
                self.management_expenses,
                self.fundraising_expenses,
            )
        )

    def expense_components(self) -> list[tuple[str, float]]:
        """The Part IX functional split, largest first."""
        named = (
            ("Program services", self.program_expenses),
            ("Management and general", self.management_expenses),
            ("Fundraising", self.fundraising_expenses),
        )
        return sorted(
            ((label, value) for label, value in named if value),
            key=lambda item: item[1],
            reverse=True,
        )

    @property
    def program_expense_share(self) -> float | None:
        """Share of expenses spent on programs rather than overhead."""
        if not self.program_expenses or not self.total_expenses:
            return None
        return self.program_expenses / self.total_expenses

    @property
    def audit_summary(self) -> str | None:
        """One line describing how the books were checked, or None if unstated."""
        if self.single_audit_performed:
            return "Single Audit performed under the Uniform Guidance"
        if self.single_audit_required:
            return "Subject to a Single Audit; the return does not confirm one was performed"
        if self.single_audit_required is False and self.financials_audited:
            return "Independently audited; not subject to a Single Audit"
        if self.financials_audited:
            return "Financial statements independently audited"
        if self.financials_audited is False:
            return "Financial statements not independently audited"
        return None


class GrantSource(str, enum.Enum):
    """Where a grant record came from, which decides what it means."""

    # Another nonprofit's Form 990 Schedule I named this organization as a
    # recipient. Historic, precise, and the grantor is named.
    SCHEDULE_I = "schedule-i"
    # A federal award file (USAspending or an agency's own export) loaded from
    # disk. Carries a period of performance, so it can be current.
    FEDERAL_AWARD = "federal-award"

    @property
    def label(self) -> str:
        return {
            GrantSource.SCHEDULE_I: "Form 990 Schedule I",
            GrantSource.FEDERAL_AWARD: "Federal award file",
        }[self]


class Grant(Base):
    """One grant awarded to an organization in this database.

    Two quite different things live here, told apart by ``source``.

    A **Schedule I** row was read from the *grantor's* Form 990. A nonprofit
    reports the grants it makes and never the grants it receives, so the only
    way to learn what a health center was given is to read everybody else's
    return and look for its EIN. That match is on an exact nine-digit EIN and
    nothing else -- never on a name -- and the grantor is named because the
    filing names it.

    A **federal award** row was read from a file the user downloaded. It
    carries a period of performance, so unlike a 990 it can say whether an
    award is still running.
    """

    __tablename__ = "grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[GrantSource] = mapped_column(String(24), index=True)

    # Who gave it. For Schedule I this is the filer whose return named us.
    grantor_name: Mapped[str | None] = mapped_column(String(320))
    grantor_ein: Mapped[str | None] = mapped_column(String(16), index=True)

    amount: Mapped[float | None] = mapped_column(Float)
    # Split out where the filing reports both, because non-cash assistance is
    # not money and lumping the two together would overstate the cash.
    cash_amount: Mapped[float | None] = mapped_column(Float)
    non_cash_amount: Mapped[float | None] = mapped_column(Float)

    purpose: Mapped[str | None] = mapped_column(Text)
    # Tax year of the grantor's return, for a Schedule I row.
    tax_year: Mapped[int | None] = mapped_column(Integer, index=True)

    # Federal award fields. All null for a Schedule I row.
    award_number: Mapped[str | None] = mapped_column(String(64))
    awarding_agency: Mapped[str | None] = mapped_column(String(240))
    program_title: Mapped[str | None] = mapped_column(String(320))
    cfda_number: Mapped[str | None] = mapped_column(String(32))
    start_date: Mapped[datetime | None] = mapped_column(DateTime)
    end_date: Mapped[datetime | None] = mapped_column(DateTime)

    source_file: Mapped[str | None] = mapped_column(String(320))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organization: Mapped["Organization"] = relationship()

    __table_args__ = (
        Index("ix_grants_org_source", "organization_id", "source"),
    )

    @property
    def is_active(self) -> bool | None:
        """Whether the award period covers today.

        None when no end date was reported -- which is the usual case for a
        Schedule I row, and the reason those are never described as "active".
        """
        if self.end_date is None:
            return None
        end = self.end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end >= datetime.now(timezone.utc)

    @property
    def period_label(self) -> str | None:
        """The award period, as far as it was reported."""
        if self.start_date is None and self.end_date is None:
            return None
        start = self.start_date.strftime("%b %Y") if self.start_date else "?"
        end = self.end_date.strftime("%b %Y") if self.end_date else "?"
        return f"{start} to {end}"


class ProgramArea(Base):
    """One Form 990 Part III program service accomplishment.

    The organization's own description of a program it runs, with the money
    spent on it and the money it earned. Ordered as the filer listed them --
    Part III puts the three largest first, which is itself information.
    """

    __tablename__ = "program_areas"

    id: Mapped[int] = mapped_column(primary_key=True)
    ein: Mapped[str] = mapped_column(String(16), index=True)
    tax_year: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    description: Mapped[str | None] = mapped_column(Text)
    expenses: Mapped[float | None] = mapped_column(Float)
    grants: Mapped[float | None] = mapped_column(Float)
    revenue: Mapped[float | None] = mapped_column(Float)
    activity_code: Mapped[str | None] = mapped_column(String(32))

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_program_areas_ein_year", "ein", "tax_year"),
    )

    @property
    def title(self) -> str | None:
        """A short label for the program, taken from its own first sentence."""
        if not self.description:
            return None
        text = " ".join(self.description.split())
        for stop in (". ", " - ", ": "):
            cut = text.find(stop)
            if 0 < cut <= 90:
                return text[:cut].strip(" .:-")
        return text if len(text) <= 90 else text[:87].rsplit(" ", 1)[0] + "..."

    @property
    def net_cost(self) -> float | None:
        """Expenses less revenue: what the program costs after what it earns."""
        if self.expenses is None or self.revenue is None:
            return None
        return self.expenses - self.revenue


class UdsReport(Base):
    """One year of a health center's Uniform Data System report.

    Every Section 330 grantee files UDS annually, and it carries the two facts
    a 990 never will: how many people the organization actually serves, and how
    many staff it employs. For sizing a managed-services engagement those are
    worth more than revenue -- headcount is what drives users, workstations and
    devices, and revenue is only a proxy for it.

    Everything here is reported by the organization to HRSA and published in
    aggregate. Nothing in UDS is patient-level.
    """

    __tablename__ = "uds_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    year: Mapped[int] = mapped_column(Integer, index=True)

    patients: Mapped[int | None] = mapped_column(Integer)
    visits: Mapped[int | None] = mapped_column(Integer)
    sites_reported: Mapped[int | None] = mapped_column(Integer)

    # Full-time equivalents, as reported on UDS Table 5.
    total_fte: Mapped[float | None] = mapped_column(Float)
    provider_fte: Mapped[float | None] = mapped_column(Float)

    # Payer mix, as a share of patients (0-1).
    medicaid_share: Mapped[float | None] = mapped_column(Float)
    medicare_share: Mapped[float | None] = mapped_column(Float)
    uninsured_share: Mapped[float | None] = mapped_column(Float)

    total_revenue: Mapped[float | None] = mapped_column(Float)
    grant_revenue: Mapped[float | None] = mapped_column(Float)

    # The project director HRSA holds on file, with the contact details the
    # organization itself gave them. Unlike a 990 -- which lists officers care
    # of the organization's address -- this is a named person and a direct
    # line, reported by the health center to its own funder.
    # From the UDS health-IT sheet: what the health center actually runs. The
    # single most commercially interesting fact in the file for an MSP, and the
    # one this application previously reported as unavailable anywhere free.
    ehr_vendor: Mapped[str | None] = mapped_column(String(240))
    ehr_product: Mapped[str | None] = mapped_column(String(240))

    director_name: Mapped[str | None] = mapped_column(String(240))
    director_phone: Mapped[str | None] = mapped_column(String(64))
    director_email: Mapped[str | None] = mapped_column(String(240))
    urban_rural: Mapped[str | None] = mapped_column(String(16))

    source_file: Mapped[str | None] = mapped_column(String(240))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organization: Mapped["Organization"] = relationship()

    __table_args__ = (
        Index("ix_uds_org_year", "organization_id", "year", unique=True),
    )

    @property
    def has_payer_mix(self) -> bool:
        return any(
            share is not None
            for share in (self.medicaid_share, self.medicare_share, self.uninsured_share)
        )

    @property
    def support_fte(self) -> float | None:
        """Non-provider staff: administration, enabling services, facilities.

        Reported as the remainder rather than as its own column, so it is only
        available when both totals are.
        """
        if self.total_fte is None or self.provider_fte is None:
            return None
        return max(self.total_fte - self.provider_fte, 0.0)


class WebsitePerson(Base):
    """A person named on the organization's own website.

    Deliberately a separate table from :class:`Person`. A 990 is a signed
    federal filing with a fixed schema; a leadership page is prose that happens
    to contain names, read by a heuristic that can be wrong. Keeping the two
    apart means the filing table stays exactly as authoritative as the filing
    is, and the UI can label each source for what it is.

    ``source_url`` is mandatory for that reason -- every row here points at the
    page it came from so a human can check it in one click.
    """

    __tablename__ = "website_people"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(240))
    title: Mapped[str | None] = mapped_column(String(240))
    # Only ever an address the organization itself published on that page.
    email: Mapped[str | None] = mapped_column(String(240))
    source_url: Mapped[str] = mapped_column(String(500))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    organization: Mapped["Organization"] = relationship()

    @property
    def is_board_member(self) -> bool:
        title = (self.title or "").lower()
        return any(
            word in title
            for word in ("board", "trustee", "director of the board", "chair")
        ) and "director of" not in title


class WebsiteCrawl(Base):
    """One attempt to read leadership information off an organization's site.

    Recorded whether or not it found anything, so the stage can tell "not
    crawled yet" from "crawled and the site says nothing", and so a re-run can
    skip sites visited recently.
    """

    __tablename__ = "website_crawls"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, unique=True
    )

    url: Mapped[str | None] = mapped_column(String(500))
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    people_found: Mapped[int] = mapped_column(Integer, default=0)
    # Short human-readable outcome: "ok", "blocked by robots.txt",
    # "unreachable: ConnectTimeout", "no website on file".
    outcome: Mapped[str | None] = mapped_column(String(240))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ApiCache(Base):
    """Raw HTTP responses from ProPublica, so re-runs only fetch what is stale."""

    __tablename__ = "api_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(500))
    status_code: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class IngestRun(Base):
    """Audit trail for pipeline executions -- powers the freshness banners."""

    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.RUNNING)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    records_read: Mapped[int] = mapped_column(Integer, default=0)
    records_written: Mapped[int] = mapped_column(Integer, default=0)

    # True when the stage ran entirely on cached data because the source was
    # unreachable or the cache was still fresh.
    used_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    cache_date: Mapped[datetime | None] = mapped_column(DateTime)
    source_reachable: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str | None] = mapped_column(Text)
