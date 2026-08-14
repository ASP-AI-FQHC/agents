"""Score each organization against Allstar's ideal client profile.

Four factors, each producing 0-100, combined into a weighted composite:

======================  =====================================================
Revenue                 Full credit inside the sweet spot, tapering to zero
                        at the outer bounds. Taken from the most recent 990
                        that actually carries figures.
Delivery sites          Meeting the site minimum scores 60; the target site
                        count scores 100.
State                   In the target footprint, or the configured score for
                        everything else.
Grant dependence        Federal Section 330 award as a share of total revenue
                        -- the higher the dependence, the stronger the
                        compliance and funding-protection angle.
======================  =====================================================

The important rule is what happens when a factor *cannot* be computed. It is
marked unavailable, its weight is removed, and the remaining weights are
renormalized -- so an organization with no 990 on file is scored on what is
actually known about it. Scoring an unknown as zero would rank "we have no
data" alongside "genuinely poor fit", which is a different claim entirely.
Every factor records why it scored what it did, and the breakdown is persisted
for display.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import (
    Config,
    GrantDependenceScoring,
    RevenueScoring,
    ScoringSettings,
    SitesScoring,
    StateScoring,
)
from app.formatting import money, percent
from app.models import (
    Filing,
    IngestRun,
    Organization,
    RunStatus,
    Score,
    utcnow,
)

ProgressFn = Callable[[str], None]

# Score awarded for exactly meeting the site minimum; the remainder is earned
# on the way to the target site count.
MINIMUM_SITES_SCORE = 60.0

FACTOR_LABELS = {
    "revenue": "Annual revenue",
    "sites": "Delivery sites",
    "state": "State footprint",
    "grant_dependence": "Grant dependence",
}


@dataclass(frozen=True)
class FactorResult:
    """One scored factor, with everything the UI needs to explain it."""

    key: str
    score: float | None      # None means the factor could not be computed
    weight: float            # configured weight
    detail: str              # why it scored what it did
    value: str               # the underlying value, already display-formatted

    @property
    def available(self) -> bool:
        return self.score is not None

    @property
    def label(self) -> str:
        return FACTOR_LABELS.get(self.key, self.key)

    def as_dict(self, effective_weight: float) -> dict[str, Any]:
        return {
            "factor": self.key,
            "label": self.label,
            "score": None if self.score is None else round(self.score, 1),
            "weight": self.weight,
            "effective_weight": round(effective_weight, 1),
            "available": self.available,
            "detail": self.detail,
            "value": self.value,
        }


@dataclass
class ScoreResult:
    composite: float
    factors: list[FactorResult]
    breakdown: list[dict[str, Any]]
    unavailable: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Individual factors
# ---------------------------------------------------------------------------


def score_revenue(
    revenue: float | None, settings: RevenueScoring, *, tax_year: int | None = None
) -> FactorResult:
    """Full credit inside the sweet spot, tapering to zero at the bounds."""
    weight_key = "revenue"
    if revenue is None:
        return FactorResult(
            key=weight_key,
            score=None,
            weight=0.0,
            detail="No Form 990 with reported revenue is available",
            value="Not available",
        )

    label = money(revenue)
    year_suffix = f" (FY{tax_year})" if tax_year else ""

    if revenue <= settings.floor:
        score, detail = 0.0, (
            f"{label} is at or below the ${settings.floor:,.0f} floor"
        )
    elif revenue < settings.sweet_spot_min:
        span = settings.sweet_spot_min - settings.floor
        score = 100.0 * (revenue - settings.floor) / span
        detail = f"{label} is below the ${settings.sweet_spot_min:,.0f} sweet spot"
    elif revenue <= settings.sweet_spot_max:
        score, detail = 100.0, f"{label} is inside the target revenue band"
    elif revenue < settings.ceiling:
        span = settings.ceiling - settings.sweet_spot_max
        score = 100.0 * (settings.ceiling - revenue) / span
        detail = f"{label} is above the ${settings.sweet_spot_max:,.0f} sweet spot"
    else:
        score, detail = 0.0, (
            f"{label} is at or above the ${settings.ceiling:,.0f} ceiling"
        )

    return FactorResult(
        key=weight_key,
        score=score,
        weight=0.0,
        detail=detail + year_suffix,
        value=label + year_suffix,
    )


def score_sites(site_count: int | None, settings: SitesScoring) -> FactorResult:
    """Meeting the minimum scores 60; reaching the target scores 100."""
    if site_count is None:
        return FactorResult(
            key="sites",
            score=None,
            weight=0.0,
            detail="Site count is not available",
            value="Not available",
        )

    plural = "site" if site_count == 1 else "sites"
    value = f"{site_count} {plural}"

    if site_count >= settings.target:
        score = 100.0
        detail = f"{value}, at or above the {settings.target}-site target"
    elif site_count >= settings.minimum:
        span = max(settings.target - settings.minimum, 1)
        score = MINIMUM_SITES_SCORE + (100.0 - MINIMUM_SITES_SCORE) * (
            site_count - settings.minimum
        ) / span
        detail = f"{value}, meets the {settings.minimum}-site minimum"
    elif site_count <= 0:
        score, detail = 0.0, "No active delivery sites recorded"
    else:
        # Ramp from a single site up to (but not reaching) the minimum.
        span = max(settings.minimum - 1, 1)
        score = MINIMUM_SITES_SCORE * (site_count - 1) / span
        detail = f"{value}, below the {settings.minimum}-site minimum"

    return FactorResult(key="sites", score=score, weight=0.0, detail=detail, value=value)


def score_state(state: str | None, settings: StateScoring) -> FactorResult:
    """Full credit inside the target footprint."""
    if not state:
        return FactorResult(
            key="state",
            score=None,
            weight=0.0,
            detail="State is not available",
            value="Not available",
        )

    normalized = state.strip().upper()
    footprint = ", ".join(settings.target_states)
    if normalized in settings.target_states:
        return FactorResult(
            key="state",
            score=100.0,
            weight=0.0,
            detail=f"{normalized} is in the target footprint ({footprint})",
            value=normalized,
        )

    return FactorResult(
        key="state",
        score=settings.other_state_score,
        weight=0.0,
        detail=f"{normalized} is outside the target footprint ({footprint})",
        value=normalized,
    )


def score_grant_dependence(
    federal_award: float | None,
    revenue: float | None,
    settings: GrantDependenceScoring,
) -> FactorResult:
    """Federal award as a share of revenue. Unavailable unless both are known."""
    if federal_award is None or revenue is None:
        missing = []
        if federal_award is None:
            missing.append("no federal award amount published by HRSA")
        if revenue is None:
            missing.append("no reported revenue on file")
        return FactorResult(
            key="grant_dependence",
            score=None,
            weight=0.0,
            detail="Cannot be derived: " + " and ".join(missing),
            value="Not available",
        )

    if revenue <= 0:
        # A ratio against zero or negative revenue is not meaningful.
        return FactorResult(
            key="grant_dependence",
            score=None,
            weight=0.0,
            detail="Cannot be derived: reported revenue is not positive",
            value="Not available",
        )

    ratio = federal_award / revenue
    display = percent(min(ratio, 1.0))

    if ratio >= settings.full_credit_ratio:
        score = 100.0
        detail = (
            f"{money(federal_award)} federal award is {display} of revenue, at or "
            f"above the {percent(settings.full_credit_ratio)} threshold"
        )
    elif ratio <= settings.zero_credit_ratio:
        score = 0.0
        detail = (
            f"{money(federal_award)} federal award is {display} of revenue, at or "
            f"below the {percent(settings.zero_credit_ratio)} threshold"
        )
    else:
        span = settings.full_credit_ratio - settings.zero_credit_ratio
        score = 100.0 * (ratio - settings.zero_credit_ratio) / span
        detail = f"{money(federal_award)} federal award is {display} of revenue"

    return FactorResult(
        key="grant_dependence",
        score=score,
        weight=0.0,
        detail=detail,
        value=display,
    )


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


def combine(factors: Sequence[FactorResult], settings: ScoringSettings) -> ScoreResult:
    """Weighted average over the factors that could be computed.

    Weights of unavailable factors are removed and the rest renormalized, so
    the composite always answers "how good a fit is this, given what we know".
    """
    weights = settings.weights.as_dict()
    weighted = [
        FactorResult(
            key=factor.key,
            score=factor.score,
            weight=weights.get(factor.key, 0.0),
            detail=factor.detail,
            value=factor.value,
        )
        for factor in factors
    ]

    available = [f for f in weighted if f.available and f.weight > 0]
    total_weight = sum(f.weight for f in available)

    if total_weight <= 0:
        composite = 0.0
        effective = {f.key: 0.0 for f in weighted}
    else:
        composite = sum((f.score or 0.0) * f.weight for f in available) / total_weight
        effective = {
            f.key: (f.weight / total_weight * 100.0) if f in available else 0.0
            for f in weighted
        }

    return ScoreResult(
        composite=round(composite, 1),
        factors=weighted,
        breakdown=[f.as_dict(effective[f.key]) for f in weighted],
        unavailable=[f.key for f in weighted if not f.available],
    )


def score_organization(
    organization: Organization,
    filings: Sequence[Filing],
    settings: ScoringSettings,
) -> ScoreResult:
    """Score one organization from its HRSA record and its filings."""
    latest = _latest_filing_with_revenue(filings)
    revenue = latest.total_revenue if latest else None
    tax_year = latest.tax_year if latest else None

    factors = [
        score_revenue(revenue, settings.revenue, tax_year=tax_year),
        score_sites(organization.site_count, settings.sites),
        score_state(organization.state, settings.state),
        score_grant_dependence(
            organization.federal_award_amount, revenue, settings.grant_dependence
        ),
    ]
    return combine(factors, settings)


def _latest_filing_with_revenue(filings: Sequence[Filing]) -> Filing | None:
    """Newest filing that reports revenue.

    A PDF-only year is skipped rather than being read as missing revenue, so a
    recent unparsed filing does not hide last year's real figures.
    """
    with_revenue = [f for f in filings if f.total_revenue is not None]
    return max(with_revenue, key=lambda f: f.tax_year) if with_revenue else None


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


@dataclass
class ScoringResult:
    scored: int = 0
    with_financials: int = 0
    fully_scored: int = 0          # every factor available
    average_composite: float = 0.0
    unavailable_counts: dict[str, int] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    @property
    def status(self) -> RunStatus:
        # Only consulted when scoring completed without raising -- a genuine
        # failure sets the run record directly. Zero organizations means the
        # database is empty, which is not a scoring failure; the accompanying
        # message says so.
        return RunStatus.SUCCESS


def score_all(
    session: Session,
    config: Config,
    *,
    on_progress: ProgressFn | None = None,
) -> ScoringResult:
    """Re-score every organization and persist the breakdowns."""
    report = on_progress or (lambda _message: None)
    settings = config.scoring
    result = ScoringResult()

    run = IngestRun(stage="scoring", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    try:
        organizations = session.scalars(
            select(Organization).options(selectinload(Organization.ein_match))
        ).all()

        # One query for every filing, grouped by EIN, rather than per organization.
        filings_by_ein: dict[str, list[Filing]] = {}
        for filing in session.scalars(select(Filing)).all():
            filings_by_ein.setdefault(filing.ein, []).append(filing)

        existing = {
            score.organization_id: score
            for score in session.scalars(select(Score)).all()
        }

        total = 0.0
        for organization in organizations:
            ein = organization.ein  # None unless the match is auto or accepted
            filings = filings_by_ein.get(ein, []) if ein else []

            scored = score_organization(organization, filings, settings)

            row = existing.get(organization.id)
            if row is None:
                row = Score(organization_id=organization.id)
                session.add(row)
            row.composite = scored.composite
            row.breakdown = scored.breakdown
            row.unavailable_factors = scored.unavailable
            row.scored_at = utcnow()

            result.scored += 1
            total += scored.composite
            if any(f.key == "revenue" and f.available for f in scored.factors):
                result.with_financials += 1
            if not scored.unavailable:
                result.fully_scored += 1
            for key in scored.unavailable:
                result.unavailable_counts[key] = (
                    result.unavailable_counts.get(key, 0) + 1
                )

        session.commit()
        result.average_composite = round(total / result.scored, 1) if result.scored else 0.0

    except Exception as exc:
        session.rollback()
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.message = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise

    if not result.scored:
        result.messages.append(
            "No organizations to score -- run the hrsa stage first to build the "
            "prospect universe"
        )
        report(result.messages[-1])
    else:
        missing_revenue = result.unavailable_counts.get("revenue", 0)
        if missing_revenue:
            result.messages.append(
                f"{missing_revenue:,} of {result.scored:,} organizations were scored "
                "without revenue data; their weights were renormalized"
            )
        report(
            f"Scored {result.scored:,} organizations "
            f"(average {result.average_composite:.1f}, "
            f"{result.fully_scored:,} with every factor available)"
        )

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.scored
    run.records_written = result.scored
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result
