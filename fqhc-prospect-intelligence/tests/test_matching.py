"""EIN matching: scoring, threshold routing, and human-decision safeguards."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    EinMatch,
    IngestRun,
    MatchStatus,
    Organization,
    RunStatus,
    utcnow,
)
from pipeline.matching import (
    decide,
    match_organizations,
    name_similarity,
    query_variants,
    score_candidates,
    should_search,
)
from pipeline.propublica import ProPublicaClient, SearchHit
from tests.test_propublica_client import FakeClock


def hit(ein: str, name: str, city: str = "Chicago", state: str = "IL", **extra):
    return SearchHit(ein=ein, name=name, city=city, state=state, raw=extra)


def add_org(session: Session, name: str, state: str = "IL", city: str = "Chicago", **kw):
    org = Organization(
        dedup_key=f"{name.lower()}|{state}",
        name=name,
        normalized_name=name.lower(),
        state=state,
        city=city,
        site_count=kw.pop("site_count", 4),
        **kw,
    )
    session.add(org)
    session.commit()
    return org


# ---------------------------------------------------------------------------
# Name similarity
# ---------------------------------------------------------------------------


def test_legal_suffixes_and_case_do_not_reduce_similarity() -> None:
    assert name_similarity(
        "Erie Family Health Centers, Inc.", "ERIE FAMILY HEALTH CENTERS INC"
    ) == 100.0


def test_word_order_does_not_matter() -> None:
    assert name_similarity("Health Center of Gary", "Gary Health Center") > 90


def test_extra_words_legitimately_cost_points() -> None:
    """A superset name must not score as a perfect match."""
    score = name_similarity(
        "Erie Family Health", "Erie Family Health Network of Northern Illinois"
    )
    assert score < 90


def test_unrelated_names_score_low() -> None:
    assert name_similarity("Erie Family Health Centers", "Milwaukee Health Services") < 50


def test_empty_names_score_zero() -> None:
    assert name_similarity(None, "Erie Family Health") == 0.0
    assert name_similarity("Erie Family Health", "") == 0.0


# ---------------------------------------------------------------------------
# Candidate scoring
# ---------------------------------------------------------------------------


def test_state_mismatch_is_discarded(config: Config) -> None:
    candidates = score_candidates(
        "Erie Family Health Centers",
        "IL",
        "Chicago",
        [
            hit("362167869", "Erie Family Health Centers", state="IL"),
            hit("999999999", "Erie Family Health Centers", state="OH", city="Erie"),
        ],
        config.matching,
    )
    assert [c.ein for c in candidates] == ["362167869"]


def test_state_mismatch_is_kept_when_the_check_is_disabled(config: Config) -> None:
    config.matching.require_state_match = False
    candidates = score_candidates(
        "Erie Family Health Centers",
        "IL",
        "Chicago",
        [hit("999999999", "Erie Family Health Centers", state="OH")],
        config.matching,
    )
    assert len(candidates) == 1
    assert candidates[0].state_match is False


def test_city_agreement_breaks_ties_without_inflating_the_score(
    config: Config,
) -> None:
    """The displayed confidence stays pure name similarity."""
    candidates = score_candidates(
        "Community Health Partners",
        "IL",
        "Chicago",
        [
            hit("111111111", "Community Health Partners", city="Peoria"),
            hit("222222222", "Community Health Partners", city="Chicago"),
        ],
        config.matching,
    )
    assert [c.ein for c in candidates] == ["222222222", "111111111"]
    assert candidates[0].score == candidates[1].score == 100.0


def test_rejected_eins_are_excluded(config: Config) -> None:
    candidates = score_candidates(
        "Erie Family Health Centers",
        "IL",
        "Chicago",
        [
            hit("362167869", "Erie Family Health Centers"),
            hit("111111111", "Erie Family Health Center"),
        ],
        config.matching,
        excluded_eins={"362167869"},
    )
    assert [c.ein for c in candidates] == ["111111111"]


# ---------------------------------------------------------------------------
# Threshold routing
# ---------------------------------------------------------------------------


def test_high_confidence_is_auto_accepted(config: Config) -> None:
    candidates = score_candidates(
        "Erie Family Health Centers",
        "IL",
        "Chicago",
        [hit("362167869", "ERIE FAMILY HEALTH CENTERS INC")],
        config.matching,
    )
    outcome = decide(candidates, config.matching)

    assert outcome.status is MatchStatus.AUTO
    assert outcome.ein == "362167869"
    assert outcome.score == 100.0


def test_middling_confidence_goes_to_human_review(config: Config) -> None:
    candidates = score_candidates(
        "Prairie Rural Health Clinic",
        "IN",
        "Lafayette",
        [hit("333333333", "Prairie Rural Health Services", state="IN", city="Lafayette")],
        config.matching,
    )
    outcome = decide(candidates, config.matching)

    assert 70 <= (outcome.score or 0) < 90
    assert outcome.status is MatchStatus.PENDING
    assert "review" in (outcome.note or "")


def test_low_confidence_is_unmatched_and_claims_no_ein(config: Config) -> None:
    """Below the review line, no EIN may be attached to the organization."""
    candidates = score_candidates(
        "Erie Family Health Centers",
        "IL",
        "Chicago",
        [hit("444444444", "Northwestern Memorial Hospital")],
        config.matching,
    )
    outcome = decide(candidates, config.matching)

    assert outcome.status is MatchStatus.UNMATCHED
    assert outcome.ein is None
    assert outcome.candidates  # still recorded, so a human can look
    assert "below the" in (outcome.note or "")


def test_two_near_identical_candidates_go_to_review_despite_high_scores(
    config: Config,
) -> None:
    """A perfect score on two different EINs is ambiguity, not confidence."""
    candidates = score_candidates(
        "Community Health Partners",
        "IL",
        "Chicago",
        [
            hit("111111111", "Community Health Partners"),
            hit("222222222", "Community Health Partners Inc"),
        ],
        config.matching,
    )
    outcome = decide(candidates, config.matching)

    assert candidates[0].score >= config.matching.auto_accept_score
    assert outcome.status is MatchStatus.PENDING
    assert "cannot distinguish" in (outcome.note or "")


def test_clear_winner_above_the_margin_is_still_auto_accepted(config: Config) -> None:
    candidates = score_candidates(
        "Erie Family Health Centers",
        "IL",
        "Chicago",
        [
            hit("362167869", "Erie Family Health Centers"),
            hit("222222222", "Erie Family Dental"),
        ],
        config.matching,
    )
    outcome = decide(candidates, config.matching)
    assert outcome.status is MatchStatus.AUTO


def test_no_candidates_is_unmatched(config: Config) -> None:
    outcome = decide([], config.matching, searched_terms="erie", had_hits=False)
    assert outcome.status is MatchStatus.UNMATCHED
    assert outcome.ein is None
    assert "no results" in (outcome.note or "")


def test_thresholds_are_configurable(config: Config) -> None:
    """Lowering auto-accept must change routing with no code change."""
    candidates = score_candidates(
        "Prairie Rural Health Clinic",
        "IN",
        "Lafayette",
        [hit("333333333", "Prairie Rural Health Services", state="IN")],
        config.matching,
    )
    assert decide(candidates, config.matching).status is MatchStatus.PENDING

    config.matching.auto_accept_score = 70
    assert decide(candidates, config.matching).status is MatchStatus.AUTO


# ---------------------------------------------------------------------------
# Re-search policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "force", "expected"),
    [
        (MatchStatus.ACCEPTED, False, False),
        (MatchStatus.ACCEPTED, True, False),   # a human decision is final
        (MatchStatus.AUTO, False, False),
        (MatchStatus.AUTO, True, True),
        (MatchStatus.PENDING, False, False),   # waiting on a human
        (MatchStatus.PENDING, True, True),
        (MatchStatus.UNMATCHED, True, True),
        (MatchStatus.REJECTED, True, True),
    ],
)
def test_should_search_respects_human_decisions(
    status: MatchStatus, force: bool, expected: bool
) -> None:
    match = EinMatch(organization_id=1, status=status, searched_at=utcnow())
    assert should_search(match, force=force, refresh_after_days=30) is expected


def test_unmatched_organizations_are_retried_once_stale() -> None:
    fresh = EinMatch(organization_id=1, status=MatchStatus.UNMATCHED, searched_at=utcnow())
    stale = EinMatch(
        organization_id=1,
        status=MatchStatus.UNMATCHED,
        searched_at=utcnow() - timedelta(days=31),
    )
    assert should_search(fresh, force=False, refresh_after_days=30) is False
    assert should_search(stale, force=False, refresh_after_days=30) is True


def test_organizations_without_a_match_record_are_always_searched() -> None:
    assert should_search(None, force=False, refresh_after_days=30) is True


def test_query_variants_add_fallbacks_for_long_names() -> None:
    variants = query_variants("Erie Family Health Centers of Greater Chicago, Inc.")
    assert variants[0] == "Erie Family Health Centers of Greater Chicago, Inc."
    assert "erie family health centers of greater chicago" in variants
    assert "erie family health centers" in variants


# ---------------------------------------------------------------------------
# Stage behaviour
# ---------------------------------------------------------------------------


SEARCH_RESULTS = {
    "erie": [
        {
            "ein": 362167869,
            "name": "ERIE FAMILY HEALTH CENTERS INC",
            "city": "Chicago",
            "state": "IL",
            "have_filings": True,
        }
    ],
    "prairie": [
        {
            "ein": 333333333,
            "name": "PRAIRIE RURAL HEALTH SERVICES",
            "city": "Lafayette",
            "state": "IN",
        }
    ],
    "riverbend": [
        {
            "ein": 444444444,
            "name": "NORTHWESTERN MEMORIAL HOSPITAL",
            "city": "Chicago",
            "state": "IL",
        }
    ],
}


def search_handler(request: httpx.Request) -> httpx.Response:
    query = request.url.params.get("q", "").lower()
    for keyword, organizations in SEARCH_RESULTS.items():
        if keyword in query:
            return httpx.Response(
                200, json={"total_results": len(organizations), "organizations": organizations}
            )
    return httpx.Response(200, json={"total_results": 0, "organizations": []})


def make_client(config: Config, session: Session, handler=search_handler):
    clock = FakeClock()
    return ProPublicaClient(
        config,
        session,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def test_stage_routes_every_organization(config: Config, session: Session) -> None:
    add_org(session, "Erie Family Health Centers", "IL")
    add_org(session, "Prairie Rural Health Clinic", "IN", city="Lafayette")
    add_org(session, "Riverbend Health Access", "IL")
    add_org(session, "Nowhere Community Health", "IL")

    result = match_organizations(session, config, client=make_client(config, session))

    assert result.searched == 4
    assert result.auto_accepted == 1   # Erie
    assert result.needs_review == 1    # Prairie
    assert result.unmatched == 2       # Riverbend (low score) + Nowhere (no hits)

    erie = session.scalars(
        select(Organization).where(Organization.name.startswith("Erie"))
    ).one()
    assert erie.ein == "362167869"
    assert erie.ein_match.score == 100.0
    assert erie.ein_match.candidates[0]["ein"] == "362167869"


def test_unmatched_organization_exposes_no_ein(config: Config, session: Session) -> None:
    add_org(session, "Riverbend Health Access", "IL")
    match_organizations(session, config, client=make_client(config, session))

    org = session.scalars(select(Organization)).one()
    assert org.ein is None
    assert org.ein_match.status == MatchStatus.UNMATCHED
    # The near-miss is still recorded so a human can inspect it.
    assert org.ein_match.candidates[0]["name"] == "NORTHWESTERN MEMORIAL HOSPITAL"


def test_pending_match_is_not_treated_as_resolved(
    config: Config, session: Session
) -> None:
    add_org(session, "Prairie Rural Health Clinic", "IN", city="Lafayette")
    match_organizations(session, config, client=make_client(config, session))

    org = session.scalars(select(Organization)).one()
    assert org.ein_match.status == MatchStatus.PENDING
    assert org.ein_match.ein == "333333333"
    assert org.ein is None  # not usable until a human confirms


def test_rerun_skips_settled_organizations(config: Config, session: Session) -> None:
    add_org(session, "Erie Family Health Centers", "IL")
    match_organizations(session, config, client=make_client(config, session))

    second = match_organizations(session, config, client=make_client(config, session))
    assert second.searched == 0
    assert second.skipped == 1


def test_accepted_match_survives_a_forced_rerun(
    config: Config, session: Session
) -> None:
    org = add_org(session, "Erie Family Health Centers", "IL")
    session.add(
        EinMatch(
            organization_id=org.id,
            ein="999999999",
            score=74.0,
            status=MatchStatus.ACCEPTED,
            decided_by="analyst",
        )
    )
    session.commit()

    match_organizations(session, config, client=make_client(config, session), force=True)

    session.expire_all()
    org = session.scalars(select(Organization)).one()
    assert org.ein == "999999999"
    assert org.ein_match.decided_by == "analyst"


def test_rejected_ein_is_not_proposed_again(config: Config, session: Session) -> None:
    org = add_org(session, "Erie Family Health Centers", "IL")
    session.add(
        EinMatch(
            organization_id=org.id,
            status=MatchStatus.REJECTED,
            rejected_eins=["362167869"],
            searched_at=utcnow() - timedelta(days=60),
        )
    )
    session.commit()

    match_organizations(session, config, client=make_client(config, session))

    session.expire_all()
    org = session.scalars(select(Organization)).one()
    assert org.ein_match.ein != "362167869"
    assert org.ein_match.status == MatchStatus.UNMATCHED


def test_stage_stops_after_repeated_api_failures(
    config: Config, session: Session
) -> None:
    """Do not fire hundreds of doomed requests when the API is unreachable."""
    for index in range(10):
        add_org(session, f"Health Center {index}", "IL")

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked", request=request)

    config.propublica.max_retries = 0
    result = match_organizations(
        session, config, client=make_client(config, session, failing)
    )

    assert result.searched == 0
    assert result.failed == 3
    assert result.source_reachable is False
    assert any("stopped after" in m for m in result.messages)

    run = session.scalars(select(IngestRun)).one()
    assert run.status == RunStatus.FAILED


def test_stage_records_a_partial_run_on_stale_cache(
    config: Config, session: Session
) -> None:
    add_org(session, "Erie Family Health Centers", "IL")
    client = make_client(config, session)
    client.stale_responses = 1  # simulate a cache-served response

    result = match_organizations(session, config, client=client)

    assert result.source_reachable is False
    assert result.used_cache is True
    run = session.scalars(select(IngestRun)).one()
    assert run.status == RunStatus.PARTIAL
    assert "expired cache" in (run.message or "")
