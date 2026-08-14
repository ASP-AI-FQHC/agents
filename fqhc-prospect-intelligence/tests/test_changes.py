"""Change detection between pipeline runs."""

from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    ChangeEvent,
    ChangeKind,
    EinMatch,
    Filing,
    GranteeType,
    IngestRun,
    MatchStatus,
    Organization,
    OrganizationSnapshot,
    RunStatus,
    Score,
    utcnow,
)
from pipeline.changes import detect_changes, recent_changes


def add_org(
    session: Session,
    name: str = "Erie Family Health",
    *,
    state: str = "IL",
    sites: int = 8,
    award: float | None = 6_000_000,
    ein: str | None = "362167869",
    grantee_type: GranteeType = GranteeType.AWARDEE,
) -> Organization:
    org = Organization(
        dedup_key=f"{name.lower()}|{state}",
        name=name,
        normalized_name=name.lower(),
        state=state,
        city="Chicago",
        site_count=sites,
        federal_award_amount=award,
        grantee_type=grantee_type,
    )
    session.add(org)
    session.flush()
    if ein:
        session.add(
            EinMatch(organization_id=org.id, ein=ein, score=98.0, status=MatchStatus.AUTO)
        )
    session.commit()
    return org


def add_filing(session: Session, ein: str, year: int, revenue: float | None) -> None:
    session.add(Filing(ein=ein, tax_year=year, total_revenue=revenue))
    session.commit()


def events(session: Session) -> list[ChangeEvent]:
    return list(session.scalars(select(ChangeEvent)).all())


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def test_first_run_records_a_baseline_and_reports_nothing(
    config: Config, session: Session
) -> None:
    """Announcing the whole database as "new" on day one would be useless."""
    add_org(session)
    add_org(session, "Milwaukee Health Services", state="WI")

    result = detect_changes(session, config)

    assert result.baseline is True
    assert result.events == 0
    assert events(session) == []
    assert len(session.scalars(select(OrganizationSnapshot)).all()) == 2
    assert any("baseline" in m for m in result.messages)


def test_second_run_with_no_movement_produces_no_events(
    config: Config, session: Session
) -> None:
    add_org(session)
    detect_changes(session, config)

    result = detect_changes(session, config)

    assert result.baseline is False
    assert result.events == 0
    assert events(session) == []


# ---------------------------------------------------------------------------
# Site movement
# ---------------------------------------------------------------------------


def test_opening_sites_is_recorded(config: Config, session: Session) -> None:
    org = add_org(session, sites=8)
    detect_changes(session, config)

    org.site_count = 11
    session.commit()
    result = detect_changes(session, config)

    assert result.events == 1
    event = events(session)[0]
    assert event.kind == ChangeKind.SITES
    assert event.summary == "Opened 3 delivery sites (8 to 11)"
    assert event.direction == 1
    assert (event.previous_value, event.current_value) == ("8", "11")


def test_closing_a_single_site_reads_naturally(config: Config, session: Session) -> None:
    org = add_org(session, sites=5)
    detect_changes(session, config)

    org.site_count = 4
    session.commit()
    detect_changes(session, config)

    event = events(session)[0]
    assert event.summary == "Closed 1 delivery site (5 to 4)"
    assert event.direction == -1


# ---------------------------------------------------------------------------
# Filings
# ---------------------------------------------------------------------------


def test_a_newer_990_is_reported_with_the_revenue_move(
    config: Config, session: Session
) -> None:
    add_org(session)
    add_filing(session, "362167869", 2023, 20_000_000)
    detect_changes(session, config)

    add_filing(session, "362167869", 2024, 23_000_000)
    result = detect_changes(session, config)

    assert result.events == 1
    event = events(session)[0]
    assert event.kind == ChangeKind.FILING
    assert "FY2024 990 now available" in event.summary
    assert "up 15%" in event.summary
    assert "$20.0M in FY2023" in event.summary
    assert event.direction == 1


def test_a_first_filing_is_reported_without_a_comparison(
    config: Config, session: Session
) -> None:
    add_org(session)
    detect_changes(session, config)

    add_filing(session, "362167869", 2023, 20_000_000)
    detect_changes(session, config)

    event = events(session)[0]
    assert "First 990 on file: FY2023" in event.summary
    assert event.direction is None


def test_an_older_filing_appearing_is_not_an_event(
    config: Config, session: Session
) -> None:
    """Backfilling FY2021 after FY2023 is not news."""
    add_org(session)
    add_filing(session, "362167869", 2023, 20_000_000)
    detect_changes(session, config)

    add_filing(session, "362167869", 2021, 15_000_000)
    result = detect_changes(session, config)

    assert result.events == 0


def test_filings_behind_an_unconfirmed_ein_are_ignored(
    config: Config, session: Session
) -> None:
    """The rule holds here too: a pending match carries no financials."""
    org = add_org(session, ein=None)
    session.add(
        EinMatch(
            organization_id=org.id,
            ein="362167869",
            score=77.0,
            status=MatchStatus.PENDING,
        )
    )
    session.commit()
    detect_changes(session, config)

    add_filing(session, "362167869", 2024, 23_000_000)
    result = detect_changes(session, config)

    assert result.events == 0


# ---------------------------------------------------------------------------
# Awards and grantee type
# ---------------------------------------------------------------------------


def test_award_movement_is_reported_as_a_percentage(
    config: Config, session: Session
) -> None:
    org = add_org(session, award=6_000_000)
    detect_changes(session, config)

    org.federal_award_amount = 7_500_000
    session.commit()
    detect_changes(session, config)

    event = events(session)[0]
    assert event.kind == ChangeKind.AWARD
    assert "increased 25%" in event.summary
    assert "$6.0M to $7.5M" in event.summary


def test_award_appearing_from_nothing_is_not_reported(
    config: Config, session: Session
) -> None:
    """HRSA starting to publish a figure is not a funding decision."""
    org = add_org(session, award=None)
    detect_changes(session, config)

    org.federal_award_amount = 5_000_000
    session.commit()
    result = detect_changes(session, config)

    assert result.events == 0


def test_becoming_an_awardee_is_reported(config: Config, session: Session) -> None:
    org = add_org(session, grantee_type=GranteeType.LOOK_ALIKE)
    detect_changes(session, config)

    org.grantee_type = GranteeType.AWARDEE
    session.commit()
    detect_changes(session, config)

    event = events(session)[0]
    assert event.kind == ChangeKind.GRANTEE_TYPE
    assert "look-alike to awardee" in event.summary
    assert event.direction == 1


def test_falling_back_to_unknown_is_not_reported(
    config: Config, session: Session
) -> None:
    """Losing a classification is a data gap, not a real-world change."""
    org = add_org(session, grantee_type=GranteeType.AWARDEE)
    detect_changes(session, config)

    org.grantee_type = GranteeType.UNKNOWN
    session.commit()
    result = detect_changes(session, config)

    assert result.events == 0


# ---------------------------------------------------------------------------
# Appearance and disappearance
# ---------------------------------------------------------------------------


def test_a_new_organization_is_reported_after_the_baseline(
    config: Config, session: Session
) -> None:
    add_org(session)
    detect_changes(session, config)

    add_org(session, "Northwoods Community Health", state="MI", sites=1, ein=None)
    result = detect_changes(session, config)

    assert result.events == 1
    event = events(session)[0]
    assert event.kind == ChangeKind.APPEARED
    assert "1 delivery site" in event.summary


def run_hrsa(session: Session, *, dropped: tuple[Organization, ...] = ()) -> None:
    """Simulate an HRSA ingestion.

    Mirrors what the real stage does: every organization still published gets
    its last_seen_at refreshed, and rows are never deleted. An organization
    "disappears" purely by not being touched.
    """
    # Space successive runs a day apart, as monthly re-runs would be. Without
    # this they land microseconds apart and the ordering becomes meaningless.
    previous = session.scalar(
        select(func.max(IngestRun.started_at)).where(IngestRun.stage == "hrsa")
    )
    if previous is None:
        started = utcnow()
    else:
        started = (
            previous if previous.tzinfo else previous.replace(tzinfo=timezone.utc)
        ) + timedelta(days=1)

    dropped_ids = {org.id for org in dropped}
    for org in session.scalars(select(Organization)).all():
        if org.id not in dropped_ids:
            org.last_seen_at = started + timedelta(seconds=1)
    session.add(
        IngestRun(
            stage="hrsa",
            status=RunStatus.SUCCESS,
            started_at=started,
            finished_at=started + timedelta(minutes=1),
        )
    )
    session.commit()


def test_a_disappearing_organization_is_reported_but_kept(
    config: Config, session: Session
) -> None:
    """The row survives -- human review decisions hang off it."""
    add_org(session)
    other = add_org(session, "Milwaukee Health Services", state="WI")
    run_hrsa(session)
    detect_changes(session, config)

    run_hrsa(session, dropped=(other,))
    result = detect_changes(session, config)

    assert result.events == 1
    assert events(session)[0].kind == ChangeKind.DISAPPEARED
    assert session.get(Organization, other.id) is not None


def test_a_disappearance_is_only_reported_once(config: Config, session: Session) -> None:
    add_org(session)
    other = add_org(session, "Milwaukee Health Services", state="WI")
    run_hrsa(session)
    detect_changes(session, config)

    run_hrsa(session, dropped=(other,))
    detect_changes(session, config)
    run_hrsa(session, dropped=(other,))
    result = detect_changes(session, config)

    assert result.events == 0
    assert len(events(session)) == 1


def test_an_organization_returning_to_the_file_is_reported(
    config: Config, session: Session
) -> None:
    add_org(session)
    other = add_org(session, "Milwaukee Health Services", state="WI")
    run_hrsa(session)
    detect_changes(session, config)

    run_hrsa(session, dropped=(other,))
    detect_changes(session, config)

    run_hrsa(session)
    result = detect_changes(session, config)

    assert result.events == 1
    assert events(session)[-1].summary == "Listed by HRSA again"


def test_a_missing_organizations_stale_figures_are_not_reported_as_movement(
    config: Config, session: Session
) -> None:
    """Once HRSA drops an organization its numbers are frozen, not news."""
    other = add_org(session, "Milwaukee Health Services", state="WI", sites=5)
    run_hrsa(session)
    detect_changes(session, config)

    other.site_count = 9  # a stale figure being corrected, not an expansion
    run_hrsa(session, dropped=(other,))
    result = detect_changes(session, config)

    assert result.events == 1
    assert events(session)[0].kind == ChangeKind.DISAPPEARED


# ---------------------------------------------------------------------------
# Deliberate exclusions and bookkeeping
# ---------------------------------------------------------------------------


def test_score_movement_alone_is_not_an_event(config: Config, session: Session) -> None:
    """Retuning a weight in config.yaml must not fire an event per organization."""
    org = add_org(session)
    session.add(Score(organization_id=org.id, composite=60.0, breakdown=[]))
    session.commit()
    detect_changes(session, config)

    org.score.composite = 88.0
    session.commit()
    result = detect_changes(session, config)

    assert result.events == 0
    # The snapshot still tracks it, for display.
    snapshot = session.scalars(select(OrganizationSnapshot)).one()
    assert snapshot.composite == 88.0


def test_several_movements_produce_several_events(
    config: Config, session: Session
) -> None:
    org = add_org(session, sites=8, award=6_000_000)
    add_filing(session, "362167869", 2023, 20_000_000)
    detect_changes(session, config)

    org.site_count = 12
    org.federal_award_amount = 9_000_000
    add_filing(session, "362167869", 2024, 26_000_000)
    session.commit()
    result = detect_changes(session, config)

    assert result.events == 3
    assert {e.kind for e in events(session)} == {
        ChangeKind.SITES,
        ChangeKind.FILING,
        ChangeKind.AWARD,
    }


def test_run_is_recorded(config: Config, session: Session) -> None:
    add_org(session)
    detect_changes(session, config)

    run = session.scalars(select(IngestRun).where(IngestRun.stage == "changes")).one()
    assert run.status == RunStatus.SUCCESS
    assert run.records_read == 1
    assert run.finished_at is not None


def test_recent_changes_returns_newest_first(config: Config, session: Session) -> None:
    org = add_org(session, sites=4)
    detect_changes(session, config)

    org.site_count = 6
    session.commit()
    detect_changes(session, config)
    org.site_count = 9
    session.commit()
    detect_changes(session, config)

    recent = recent_changes(session)
    assert len(recent) == 2
    assert recent[0].current_value == "9"
    assert recent[0].organization.name == "Erie Family Health"


def test_recent_changes_can_be_filtered_by_kind(
    config: Config, session: Session
) -> None:
    org = add_org(session, sites=4, award=1_000_000)
    detect_changes(session, config)

    org.site_count = 6
    org.federal_award_amount = 2_000_000
    session.commit()
    detect_changes(session, config)

    assert len(recent_changes(session, kind=ChangeKind.SITES.value)) == 1
    assert len(recent_changes(session, kind=ChangeKind.AWARD.value)) == 1


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        (ChangeKind.SITES, "Delivery sites"),
        (ChangeKind.FILING, "New 990 filing"),
        (ChangeKind.APPEARED, "New health center"),
    ],
)
def test_kinds_have_display_labels(kind: ChangeKind, label: str) -> None:
    assert kind.label == label


def test_persisted_events_still_expose_a_label(
    config: Config, session: Session
) -> None:
    """The kind column round-trips as a plain string, not an enum member, so
    the label has to be reachable from whatever comes back out."""
    org = add_org(session, sites=3)
    detect_changes(session, config)
    org.site_count = 7
    session.commit()
    detect_changes(session, config)

    session.expire_all()
    event = session.scalars(select(ChangeEvent)).one()
    assert isinstance(event.kind, str)
    assert event.kind_label == "Delivery sites"
