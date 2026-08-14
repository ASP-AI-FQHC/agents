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
from typing import Any

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

    @property
    def label(self) -> str:
        return {
            ChangeKind.APPEARED: "New health center",
            ChangeKind.DISAPPEARED: "No longer listed",
            ChangeKind.SITES: "Delivery sites",
            ChangeKind.FILING: "New 990 filing",
            ChangeKind.AWARD: "Federal award",
            ChangeKind.GRANTEE_TYPE: "Grantee type",
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
