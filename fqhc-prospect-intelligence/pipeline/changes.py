"""Detect what moved since the last pipeline run.

A prospect list is worth reading once. A list that says which health centers
opened sites, filed a newer 990, or became Section 330 awardees since last month
is worth reading every month -- that is the sales signal, and it is the thing a
static export cannot give you.

Each run compares the current state of every organization against the snapshot
left by the previous run, writes a :class:`ChangeEvent` for each difference, and
replaces the snapshot. Two deliberate choices:

* **Score movements are not recorded.** The composite is derived, so editing a
  weight in ``config.yaml`` would generate an event for every organization in
  the database and drown the real signal.
* **The first run is silent.** With no baseline, every organization would read
  as "new". The first run records snapshots and reports nothing, so the first
  set of events describes genuine movement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import Config
from app.formatting import money
from app.models import (
    ChangeEvent,
    ChangeKind,
    Filing,
    IngestRun,
    Organization,
    OrganizationSnapshot,
    RunStatus,
    UdsReport,
    utcnow,
)

ProgressFn = Callable[[str], None]


def _as_utc(value):
    """SQLite returns naive datetimes; compare them as UTC."""
    from datetime import timezone

    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _last_hrsa_run_start(session: Session):
    """When the most recent usable HRSA ingestion began.

    Organizations are never deleted -- the HRSA stage upserts, so that accepted
    and rejected EIN matches survive a refresh. "No longer listed" therefore
    means an organization was not touched by the latest HRSA run, not that its
    row vanished.
    """
    return session.scalar(
        select(func.max(IngestRun.started_at)).where(
            IngestRun.stage == "hrsa",
            IngestRun.status.in_([RunStatus.SUCCESS.value, RunStatus.PARTIAL.value]),
        )
    )


@dataclass
class CurrentState:
    """The facts about one organization that are watched for movement."""

    site_count: int | None
    grantee_type: str | None
    federal_award_amount: float | None
    latest_tax_year: int | None
    latest_revenue: float | None
    composite: float | None
    latest_uds_year: int | None = None
    latest_patients: int | None = None


@dataclass
class ChangeResult:
    organizations: int = 0
    events: int = 0
    baseline: bool = False
    by_kind: dict[str, int] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    @property
    def status(self) -> RunStatus:
        return RunStatus.SUCCESS


def _current_state(
    organization: Organization,
    filings_by_ein: dict[str, list[Filing]],
    uds_by_org: dict[int, UdsReport] | None = None,
) -> CurrentState:
    ein = organization.ein  # None unless the match is confirmed
    filings = filings_by_ein.get(ein, []) if ein else []
    with_revenue = [f for f in filings if f.total_revenue is not None]
    latest = max(with_revenue, key=lambda f: f.tax_year) if with_revenue else None
    uds = (uds_by_org or {}).get(organization.id)

    return CurrentState(
        site_count=organization.site_count,
        grantee_type=getattr(
            organization.grantee_type, "value", organization.grantee_type
        ),
        federal_award_amount=organization.federal_award_amount,
        latest_tax_year=latest.tax_year if latest else None,
        latest_revenue=latest.total_revenue if latest else None,
        composite=organization.score.composite if organization.score else None,
        latest_uds_year=uds.year if uds else None,
        latest_patients=uds.patients if uds else None,
    )


def _site_event(
    organization: Organization, snapshot: OrganizationSnapshot, state: CurrentState
) -> ChangeEvent | None:
    previous, current = snapshot.site_count, state.site_count
    if previous is None or current is None or previous == current:
        return None

    delta = current - previous
    verb = "opened" if delta > 0 else "closed"
    count = abs(delta)
    return ChangeEvent(
        organization_id=organization.id,
        kind=ChangeKind.SITES,
        summary=(
            f"{verb.capitalize()} {count} delivery site{'' if count == 1 else 's'} "
            f"({previous} to {current})"
        ),
        previous_value=str(previous),
        current_value=str(current),
        direction=1 if delta > 0 else -1,
    )


def _filing_event(
    organization: Organization, snapshot: OrganizationSnapshot, state: CurrentState
) -> ChangeEvent | None:
    """A newer tax year is the event; the revenue move is the detail."""
    previous_year, current_year = snapshot.latest_tax_year, state.latest_tax_year
    if current_year is None or (previous_year is not None and current_year <= previous_year):
        return None

    previous_revenue = snapshot.latest_revenue
    if previous_year is None:
        summary = (
            f"First 990 on file: FY{current_year}, revenue {money(state.latest_revenue)}"
        )
        direction = None
    else:
        summary = (
            f"FY{current_year} 990 now available: revenue {money(state.latest_revenue)}"
        )
        if previous_revenue is not None and state.latest_revenue is not None:
            delta = state.latest_revenue - previous_revenue
            share = abs(delta) / previous_revenue if previous_revenue else 0
            movement = "up" if delta > 0 else "down"
            summary += (
                f", {movement} {share:.0%} from {money(previous_revenue)} "
                f"in FY{previous_year}"
            )
            direction = 1 if delta > 0 else -1
        else:
            direction = None

    return ChangeEvent(
        organization_id=organization.id,
        kind=ChangeKind.FILING,
        summary=summary,
        previous_value=None if previous_year is None else str(previous_year),
        current_value=str(current_year),
        direction=direction,
    )


def _award_event(
    organization: Organization, snapshot: OrganizationSnapshot, state: CurrentState
) -> ChangeEvent | None:
    previous, current = snapshot.federal_award_amount, state.federal_award_amount
    # An award appearing or vanishing is usually HRSA publishing changes rather
    # than a funding decision, so only a move between two known figures counts.
    if previous is None or current is None or previous == current:
        return None

    delta = current - previous
    share = abs(delta) / previous if previous else 0
    movement = "increased" if delta > 0 else "decreased"
    return ChangeEvent(
        organization_id=organization.id,
        kind=ChangeKind.AWARD,
        summary=(
            f"Federal award {movement} {share:.0%}, "
            f"{money(previous)} to {money(current)}"
        ),
        previous_value=f"{previous:.0f}",
        current_value=f"{current:.0f}",
        direction=1 if delta > 0 else -1,
    )


def _grantee_type_event(
    organization: Organization, snapshot: OrganizationSnapshot, state: CurrentState
) -> ChangeEvent | None:
    previous, current = snapshot.grantee_type, state.grantee_type
    if not previous or not current or previous == current or current == "unknown":
        return None

    return ChangeEvent(
        organization_id=organization.id,
        kind=ChangeKind.GRANTEE_TYPE,
        summary=f"Grantee type changed from {previous} to {current}",
        previous_value=previous,
        current_value=current,
        # Becoming a Section 330 awardee is a step up; the reverse is not.
        direction=1 if current == "awardee" else -1,
    )


def _patients_event(
    organization: Organization, snapshot: OrganizationSnapshot, state: CurrentState
) -> ChangeEvent | None:
    """A newer UDS year is the event; the change in patients is the detail.

    Patient volume is the clearest growth signal a health center publishes --
    it moves before revenue does and long before a 990 is filed. Only a *new*
    reporting year counts: re-reading the same year is not movement.
    """
    previous_year, current_year = snapshot.latest_uds_year, state.latest_uds_year
    if current_year is None or (
        previous_year is not None and current_year <= previous_year
    ):
        return None

    previous, current = snapshot.latest_patients, state.latest_patients
    if current is None:
        return None

    if previous_year is None or previous is None:
        summary = f"First UDS on file: {current:,} patients in {current_year}"
        direction = None
    else:
        delta = current - previous
        share = abs(delta) / previous if previous else 0
        movement = "up" if delta > 0 else "down" if delta < 0 else "unchanged"
        if delta == 0:
            summary = f"{current_year} UDS: {current:,} patients, unchanged"
            direction = 0
        else:
            summary = (
                f"{current_year} UDS: {current:,} patients, {movement} {share:.0%} "
                f"from {previous:,} in {previous_year}"
            )
            direction = 1 if delta > 0 else -1

    return ChangeEvent(
        organization_id=organization.id,
        kind=ChangeKind.PATIENTS,
        summary=summary,
        previous_value=None if previous is None else f"{previous:,}",
        current_value=f"{current:,}",
        direction=direction,
    )


DETECTORS = (
    _site_event, _filing_event, _award_event, _grantee_type_event, _patients_event,
)


def _apply(snapshot: OrganizationSnapshot, state: CurrentState) -> None:
    snapshot.site_count = state.site_count
    snapshot.grantee_type = state.grantee_type
    snapshot.federal_award_amount = state.federal_award_amount
    snapshot.latest_tax_year = state.latest_tax_year
    snapshot.latest_revenue = state.latest_revenue
    snapshot.composite = state.composite
    snapshot.latest_uds_year = state.latest_uds_year
    snapshot.latest_patients = state.latest_patients
    snapshot.is_present = True
    snapshot.taken_at = utcnow()


def detect_changes(
    session: Session,
    config: Config,
    *,
    on_progress: ProgressFn | None = None,
) -> ChangeResult:
    """Compare every organization to its last snapshot and log what moved."""
    report = on_progress or (lambda _message: None)
    result = ChangeResult()

    run = IngestRun(stage="changes", status=RunStatus.RUNNING)
    session.add(run)
    session.commit()

    try:
        snapshots = {
            snapshot.organization_id: snapshot
            for snapshot in session.scalars(select(OrganizationSnapshot)).all()
        }
        # No snapshots at all means this is the first run: record a baseline and
        # report nothing, rather than announcing the whole database as new.
        result.baseline = not snapshots

        filings_by_ein: dict[str, list[Filing]] = {}
        for filing in session.scalars(select(Filing)).all():
            filings_by_ein.setdefault(filing.ein, []).append(filing)

        # Newest UDS year per organization, ordered so the last write wins.
        uds_by_org: dict[int, UdsReport] = {
            row.organization_id: row
            for row in session.scalars(select(UdsReport).order_by(UdsReport.year)).all()
        }

        organizations = session.scalars(
            select(Organization).options(
                selectinload(Organization.ein_match),
                selectinload(Organization.score),
            )
        ).all()
        result.organizations = len(organizations)
        hrsa_started = _last_hrsa_run_start(session)
        if hrsa_started is not None:
            hrsa_started = _as_utc(hrsa_started)

        for organization in organizations:
            state = _current_state(organization, filings_by_ein, uds_by_org)
            snapshot = snapshots.get(organization.id)
            # Without an HRSA run to compare against, assume everything present.
            present = (
                hrsa_started is None
                or _as_utc(organization.last_seen_at) >= hrsa_started
            )

            if snapshot is None:
                snapshot = OrganizationSnapshot(organization_id=organization.id)
                session.add(snapshot)
                if not result.baseline:
                    # Genuinely new since the last run.
                    session.add(
                        ChangeEvent(
                            organization_id=organization.id,
                            kind=ChangeKind.APPEARED,
                            summary=(
                                f"New to the HRSA universe with "
                                f"{state.site_count} delivery site"
                                f"{'' if state.site_count == 1 else 's'}"
                            ),
                            current_value=str(state.site_count),
                            direction=1,
                        )
                    )
                    result.by_kind[ChangeKind.APPEARED.value] = (
                        result.by_kind.get(ChangeKind.APPEARED.value, 0) + 1
                    )
                    result.events += 1
                _apply(snapshot, state)
                continue

            def record(event: ChangeEvent) -> None:
                session.add(event)
                result.events += 1
                result.by_kind[event.kind.value] = (
                    result.by_kind.get(event.kind.value, 0) + 1
                )

            if not present:
                # Its figures are frozen at whatever HRSA last published, so
                # only the disappearance itself is news -- and only once.
                if snapshot.is_present and not result.baseline:
                    record(
                        ChangeEvent(
                            organization_id=organization.id,
                            kind=ChangeKind.DISAPPEARED,
                            summary="No longer published in the HRSA site file",
                            direction=-1,
                        )
                    )
                snapshot.is_present = False
                snapshot.taken_at = utcnow()
                continue

            if not result.baseline:
                if not snapshot.is_present:
                    record(
                        ChangeEvent(
                            organization_id=organization.id,
                            kind=ChangeKind.APPEARED,
                            summary="Listed by HRSA again",
                            direction=1,
                        )
                    )
                for detector in DETECTORS:
                    event = detector(organization, snapshot, state)
                    if event is not None:
                        record(event)

            _apply(snapshot, state)

        session.commit()
    except Exception as exc:
        session.rollback()
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.message = f"{type(exc).__name__}: {exc}"
        session.commit()
        raise

    if result.baseline:
        result.messages.append(
            f"Recorded a baseline for {result.organizations:,} organizations; "
            "changes will be reported from the next run onwards"
        )
        report(result.messages[-1])
    else:
        summary = ", ".join(
            f"{count} {ChangeKind(kind).label.lower()}"
            for kind, count in sorted(result.by_kind.items())
        )
        report(
            f"Detected {result.events:,} change{'' if result.events == 1 else 's'}"
            + (f" ({summary})" if summary else "")
        )

    run.status = result.status
    run.finished_at = utcnow()
    run.records_read = result.organizations
    run.records_written = result.events
    run.message = " | ".join(result.messages) or None
    session.commit()

    return result


def recent_changes(
    session: Session, *, limit: int = 200, kind: str | None = None
) -> list[ChangeEvent]:
    """Most recent change events, newest first."""
    statement = (
        select(ChangeEvent)
        .options(selectinload(ChangeEvent.organization))
        .order_by(ChangeEvent.detected_at.desc(), ChangeEvent.id.desc())
    )
    if kind:
        statement = statement.where(ChangeEvent.kind == kind)
    return list(session.scalars(statement.limit(limit)).all())


def change_count_since_last_run(session: Session) -> int:
    """How many events the most recent changes run produced."""
    latest = session.scalar(
        select(func.max(IngestRun.finished_at)).where(
            IngestRun.stage == "changes", IngestRun.status == RunStatus.SUCCESS
        )
    )
    if latest is None:
        return 0
    run_started = session.scalar(
        select(func.max(IngestRun.started_at)).where(
            IngestRun.stage == "changes", IngestRun.finished_at == latest
        )
    )
    if run_started is None:
        return 0
    return (
        session.scalar(
            select(func.count())
            .select_from(ChangeEvent)
            .where(ChangeEvent.detected_at >= run_started)
        )
        or 0
    )
