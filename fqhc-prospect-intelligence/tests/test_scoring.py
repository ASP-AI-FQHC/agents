"""ICP scoring: factor curves, renormalization, and the composite."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    EinMatch,
    Filing,
    IngestRun,
    MatchStatus,
    Organization,
    RunStatus,
    Score,
)
from pipeline.scoring import (
    combine,
    score_all,
    score_grant_dependence,
    score_organization,
    score_revenue,
    score_sites,
    score_state,
)


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("revenue", "expected"),
    [
        (5_000_000, 100.0),    # lower edge of the sweet spot
        (20_000_000, 100.0),   # comfortably inside
        (50_000_000, 100.0),   # upper edge
    ],
)
def test_revenue_inside_the_sweet_spot_scores_full(
    config: Config, revenue: float, expected: float
) -> None:
    result = score_revenue(revenue, config.scoring.revenue)
    assert result.score == expected
    assert "inside the target revenue band" in result.detail


def test_revenue_below_the_sweet_spot_ramps_up(config: Config) -> None:
    # Floor 1M, sweet spot starts at 5M -> 3M sits halfway.
    result = score_revenue(3_000_000, config.scoring.revenue)
    assert result.score == pytest.approx(50.0)
    assert "below the" in result.detail


def test_revenue_above_the_sweet_spot_ramps_down(config: Config) -> None:
    # Sweet spot ends at 50M, ceiling 150M -> 100M sits halfway.
    result = score_revenue(100_000_000, config.scoring.revenue)
    assert result.score == pytest.approx(50.0)
    assert "above the" in result.detail


@pytest.mark.parametrize("revenue", [1_000_000, 500_000, 0])
def test_revenue_at_or_below_the_floor_scores_zero(
    config: Config, revenue: float
) -> None:
    assert score_revenue(revenue, config.scoring.revenue).score == 0.0


@pytest.mark.parametrize("revenue", [150_000_000, 400_000_000])
def test_revenue_at_or_above_the_ceiling_scores_zero(
    config: Config, revenue: float
) -> None:
    assert score_revenue(revenue, config.scoring.revenue).score == 0.0


def test_unknown_revenue_is_unavailable_not_zero(config: Config) -> None:
    """The distinction the whole design rests on."""
    unknown = score_revenue(None, config.scoring.revenue)
    genuinely_tiny = score_revenue(200_000, config.scoring.revenue)

    assert unknown.available is False
    assert unknown.score is None
    assert unknown.value == "Not available"

    assert genuinely_tiny.available is True
    assert genuinely_tiny.score == 0.0


def test_revenue_detail_names_the_tax_year(config: Config) -> None:
    result = score_revenue(20_000_000, config.scoring.revenue, tax_year=2023)
    assert "FY2023" in result.detail
    assert "FY2023" in result.value


def test_revenue_band_is_configurable(config: Config) -> None:
    config.scoring.revenue.sweet_spot_min = 20_000_000
    assert score_revenue(10_000_000, config.scoring.revenue).score < 100.0


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------


def test_site_target_scores_full(config: Config) -> None:
    assert score_sites(10, config.scoring.sites).score == 100.0
    assert score_sites(40, config.scoring.sites).score == 100.0


def test_meeting_the_site_minimum_scores_sixty(config: Config) -> None:
    result = score_sites(3, config.scoring.sites)
    assert result.score == pytest.approx(60.0)
    assert "meets the 3-site minimum" in result.detail


def test_sites_between_minimum_and_target_interpolate(config: Config) -> None:
    # Minimum 3 (=60) to target 10 (=100); 6 sites is 3/7 of the way.
    assert score_sites(6, config.scoring.sites).score == pytest.approx(
        60.0 + 40.0 * 3 / 7
    )


def test_single_site_scores_zero_and_two_sites_partial(config: Config) -> None:
    assert score_sites(1, config.scoring.sites).score == 0.0
    assert 0 < score_sites(2, config.scoring.sites).score < 60.0


def test_no_sites_scores_zero(config: Config) -> None:
    result = score_sites(0, config.scoring.sites)
    assert result.score == 0.0
    assert result.available is True  # zero sites is a fact, not a gap


def test_unknown_site_count_is_unavailable(config: Config) -> None:
    assert score_sites(None, config.scoring.sites).available is False


def test_site_singular_plural(config: Config) -> None:
    assert score_sites(1, config.scoring.sites).value == "1 site"
    assert score_sites(2, config.scoring.sites).value == "2 sites"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["IL", "WI", "IN", "MI", "il", " mi "])
def test_target_states_score_full(config: Config, state: str) -> None:
    assert score_state(state, config.scoring.state).score == 100.0


def test_other_states_use_the_configured_score(config: Config) -> None:
    assert score_state("OH", config.scoring.state).score == 0.0

    config.scoring.state.other_state_score = 25.0
    result = score_state("OH", config.scoring.state)
    assert result.score == 25.0
    assert "outside the target footprint" in result.detail


def test_missing_state_is_unavailable(config: Config) -> None:
    assert score_state(None, config.scoring.state).available is False
    assert score_state("", config.scoring.state).available is False


# ---------------------------------------------------------------------------
# Grant dependence
# ---------------------------------------------------------------------------


def test_high_grant_share_scores_full(config: Config) -> None:
    result = score_grant_dependence(6_000_000, 10_000_000, config.scoring.grant_dependence)
    assert result.score == 100.0
    assert result.value == "60%"


def test_low_grant_share_scores_zero(config: Config) -> None:
    result = score_grant_dependence(200_000, 10_000_000, config.scoring.grant_dependence)
    assert result.score == 0.0


def test_mid_grant_share_interpolates(config: Config) -> None:
    # 5% -> 0, 50% -> 100; 27.5% is the midpoint.
    result = score_grant_dependence(
        2_750_000, 10_000_000, config.scoring.grant_dependence
    )
    assert result.score == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("award", "revenue"),
    [(None, 10_000_000), (5_000_000, None), (None, None)],
)
def test_grant_dependence_needs_both_numbers(
    config: Config, award: float | None, revenue: float | None
) -> None:
    result = score_grant_dependence(award, revenue, config.scoring.grant_dependence)
    assert result.available is False
    assert "Cannot be derived" in result.detail


def test_grant_dependence_against_zero_revenue_is_unavailable(config: Config) -> None:
    """A ratio against zero revenue is not a meaningful number."""
    result = score_grant_dependence(5_000_000, 0.0, config.scoring.grant_dependence)
    assert result.available is False
    assert "not positive" in result.detail


def test_award_larger_than_revenue_is_capped_at_full(config: Config) -> None:
    result = score_grant_dependence(
        12_000_000, 10_000_000, config.scoring.grant_dependence
    )
    assert result.score == 100.0
    assert result.value == "100%"


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


def test_all_factors_perfect_scores_one_hundred(config: Config) -> None:
    factors = [
        score_revenue(20_000_000, config.scoring.revenue),
        score_sites(12, config.scoring.sites),
        score_state("IL", config.scoring.state),
        score_grant_dependence(6_000_000, 10_000_000, config.scoring.grant_dependence),
    ]
    assert combine(factors, config.scoring).composite == 100.0


def test_composite_is_the_weighted_average(config: Config) -> None:
    # revenue 100 (w35), sites 100 (w25), state 0 (w20), grant 0 (w20) = 60.
    factors = [
        score_revenue(20_000_000, config.scoring.revenue),
        score_sites(12, config.scoring.sites),
        score_state("OH", config.scoring.state),
        score_grant_dependence(200_000, 10_000_000, config.scoring.grant_dependence),
    ]
    assert combine(factors, config.scoring).composite == pytest.approx(60.0)


def test_unavailable_factors_are_dropped_and_weights_renormalized(
    config: Config,
) -> None:
    """Missing revenue must not drag the composite toward zero."""
    factors = [
        score_revenue(None, config.scoring.revenue),           # unavailable
        score_sites(12, config.scoring.sites),                 # 100, w25
        score_state("IL", config.scoring.state),               # 100, w20
        score_grant_dependence(None, None, config.scoring.grant_dependence),
    ]
    result = combine(factors, config.scoring)

    assert result.composite == 100.0
    assert sorted(result.unavailable) == ["grant_dependence", "revenue"]

    effective = {row["factor"]: row["effective_weight"] for row in result.breakdown}
    assert effective["sites"] == pytest.approx(55.6, abs=0.1)   # 25 / 45
    assert effective["state"] == pytest.approx(44.4, abs=0.1)   # 20 / 45
    assert effective["revenue"] == 0.0


def test_missing_data_does_not_score_worse_than_bad_data(config: Config) -> None:
    """An organization we know nothing about must not outrank, or be outranked
    by, one we know is a poor fit purely because of the missing value."""
    known_poor = combine(
        [
            score_revenue(500_000, config.scoring.revenue),       # 0
            score_sites(12, config.scoring.sites),
            score_state("IL", config.scoring.state),
            score_grant_dependence(None, 500_000, config.scoring.grant_dependence),
        ],
        config.scoring,
    )
    unknown = combine(
        [
            score_revenue(None, config.scoring.revenue),          # unavailable
            score_sites(12, config.scoring.sites),
            score_state("IL", config.scoring.state),
            score_grant_dependence(None, None, config.scoring.grant_dependence),
        ],
        config.scoring,
    )

    assert known_poor.composite < unknown.composite
    assert known_poor.composite == pytest.approx(56.3, abs=0.1)
    assert unknown.composite == 100.0


def test_breakdown_explains_every_factor(config: Config) -> None:
    factors = [
        score_revenue(None, config.scoring.revenue),
        score_sites(4, config.scoring.sites),
        score_state("WI", config.scoring.state),
        score_grant_dependence(1_000_000, None, config.scoring.grant_dependence),
    ]
    breakdown = combine(factors, config.scoring).breakdown

    assert len(breakdown) == 4
    assert {row["factor"] for row in breakdown} == {
        "revenue",
        "sites",
        "state",
        "grant_dependence",
    }
    for row in breakdown:
        assert row["detail"]
        assert row["label"]
        assert row["value"]
    unavailable = next(r for r in breakdown if r["factor"] == "revenue")
    assert unavailable["score"] is None
    assert unavailable["value"] == "Not available"


def test_weights_are_configurable(config: Config) -> None:
    factors = [
        score_revenue(20_000_000, config.scoring.revenue),   # 100
        score_sites(1, config.scoring.sites),                # 0
        score_state("IL", config.scoring.state),             # 100
        score_grant_dependence(200_000, 10_000_000, config.scoring.grant_dependence),
    ]
    baseline = combine(factors, config.scoring).composite

    config.scoring.weights.sites = 0
    config.scoring.weights.grant_dependence = 0
    assert combine(factors, config.scoring).composite == 100.0
    assert baseline < 100.0


def test_zero_available_weight_yields_zero_without_crashing(config: Config) -> None:
    factors = [
        score_revenue(None, config.scoring.revenue),
        score_sites(None, config.scoring.sites),
        score_state(None, config.scoring.state),
        score_grant_dependence(None, None, config.scoring.grant_dependence),
    ]
    result = combine(factors, config.scoring)
    assert result.composite == 0.0
    assert len(result.unavailable) == 4


# ---------------------------------------------------------------------------
# Organization-level scoring and the stage
# ---------------------------------------------------------------------------


def make_org(
    session: Session,
    name: str = "Erie Family Health Centers",
    *,
    state: str = "IL",
    site_count: int = 12,
    award: float | None = 6_000_000,
    ein: str | None = "362167869",
    status: MatchStatus = MatchStatus.AUTO,
) -> Organization:
    org = Organization(
        dedup_key=f"{name.lower()}|{state}",
        name=name,
        normalized_name=name.lower(),
        state=state,
        city="Chicago",
        site_count=site_count,
        federal_award_amount=award,
    )
    session.add(org)
    session.flush()
    if ein:
        session.add(EinMatch(organization_id=org.id, ein=ein, score=98.0, status=status))
    session.commit()
    return org


def test_scoring_uses_the_most_recent_filing_with_revenue(
    config: Config, session: Session
) -> None:
    org = make_org(session)
    filings = [
        Filing(ein="362167869", tax_year=2023, total_revenue=None),   # PDF only
        Filing(ein="362167869", tax_year=2022, total_revenue=20_000_000),
        Filing(ein="362167869", tax_year=2021, total_revenue=8_000_000),
    ]
    session.add_all(filings)
    session.commit()

    result = score_organization(org, filings, config.scoring)
    revenue_factor = next(f for f in result.factors if f.key == "revenue")

    assert "FY2022" in revenue_factor.detail
    assert revenue_factor.score == 100.0


def test_stage_persists_scores_and_breakdowns(config: Config, session: Session) -> None:
    make_org(session)
    session.add(Filing(ein="362167869", tax_year=2023, total_revenue=20_000_000))
    session.commit()

    result = score_all(session, config)

    assert result.scored == 1
    assert result.with_financials == 1
    assert result.fully_scored == 1

    score = session.scalars(select(Score)).one()
    # Revenue, sites and state are all perfect; the $6M award is 30% of $20M
    # revenue, partway up the 5%-50% grant-dependence ramp (55.6).
    assert score.composite == pytest.approx(91.1, abs=0.1)
    assert len(score.breakdown) == 4
    assert score.unavailable_factors == []


def test_stage_scores_organizations_without_financials(
    config: Config, session: Session
) -> None:
    """No 990 must not mean no score -- it means fewer factors."""
    make_org(session, ein=None, award=None)

    result = score_all(session, config)
    score = session.scalars(select(Score)).one()

    assert result.scored == 1
    assert result.with_financials == 0
    assert sorted(score.unavailable_factors) == ["grant_dependence", "revenue"]
    assert score.composite == 100.0  # sites and state are both perfect
    assert any("renormalized" in m for m in result.messages)


def test_unconfirmed_ein_financials_are_not_used(
    config: Config, session: Session
) -> None:
    """Filings must not reach an organization whose match is still pending."""
    make_org(session, status=MatchStatus.PENDING)
    session.add(Filing(ein="362167869", tax_year=2023, total_revenue=20_000_000))
    session.commit()

    score_all(session, config)
    score = session.scalars(select(Score)).one()

    assert "revenue" in score.unavailable_factors


def test_rescoring_updates_in_place(config: Config, session: Session) -> None:
    org = make_org(session, site_count=1, award=None, ein=None)
    score_all(session, config)
    first = session.scalars(select(Score)).one().composite

    org.site_count = 12
    session.commit()
    score_all(session, config)

    rows = session.scalars(select(Score)).all()
    assert len(rows) == 1
    assert rows[0].composite > first


def test_stage_records_a_run(config: Config, session: Session) -> None:
    make_org(session)
    score_all(session, config)

    run = session.scalars(select(IngestRun).where(IngestRun.stage == "scoring")).one()
    assert run.status == RunStatus.SUCCESS
    assert run.records_written == 1


def test_scoring_an_empty_database_is_not_a_failure(
    config: Config, session: Session
) -> None:
    """Nothing to score means the universe has not been built yet -- which the
    run should say plainly rather than reporting as a scoring failure."""
    result = score_all(session, config)

    assert result.scored == 0
    assert result.status is RunStatus.SUCCESS
    assert any("run the hrsa stage first" in m for m in result.messages)

    run = session.scalars(select(IngestRun).where(IngestRun.stage == "scoring")).one()
    assert run.status == RunStatus.SUCCESS
