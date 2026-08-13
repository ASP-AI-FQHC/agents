"""Form 990 enrichment: parsing, retention, and never inventing figures."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
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
)
from pipeline.propublica import (
    ProPublicaClient,
    enrich_financials,
    latest_with_financials,
    parse_filings,
)
from tests.test_propublica_client import FakeClock

FIXTURES = Path(__file__).parent / "fixtures"
ORG_PAYLOAD = json.loads((FIXTURES / "propublica_organization.json").read_text())
EZ_PAYLOAD = json.loads((FIXTURES / "propublica_organization_990ez.json").read_text())


def add_org(
    session: Session,
    name: str,
    ein: str | None = "362167869",
    status: MatchStatus = MatchStatus.AUTO,
    state: str = "IL",
) -> Organization:
    org = Organization(
        dedup_key=f"{name.lower()}|{state}",
        name=name,
        normalized_name=name.lower(),
        state=state,
        city="Chicago",
        site_count=5,
    )
    session.add(org)
    session.flush()
    if ein is not None:
        session.add(EinMatch(organization_id=org.id, ein=ein, score=97.0, status=status))
    session.commit()
    return org


def make_client(config: Config, session: Session, handler) -> ProPublicaClient:
    clock = FakeClock()
    return ProPublicaClient(
        config,
        session,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def payload_handler(payload: dict, status: int = 200):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_the_three_most_recent_filings() -> None:
    filings = parse_filings("362167869", ORG_PAYLOAD, limit=3)

    assert [f.tax_year for f in filings] == [2023, 2022, 2021]
    latest = filings[0]
    assert latest.total_revenue == 89_412_355.0
    assert latest.total_expenses == 84_003_112.0
    assert latest.total_assets == 71_559_204.0
    assert latest.form_type == "990"
    assert latest.pdf_url.endswith("362167869_2023.pdf")


def test_period_end_is_derived_from_the_tax_period() -> None:
    latest = parse_filings("362167869", ORG_PAYLOAD, limit=3)[0]
    assert latest.period_end is not None
    assert (latest.period_end.year, latest.period_end.month) == (2023, 12)
    assert latest.period_end.day == 31  # end of the month, not the 1st


def test_filing_limit_is_respected() -> None:
    assert len(parse_filings("362167869", ORG_PAYLOAD, limit=2)) == 2
    assert len(parse_filings("362167869", ORG_PAYLOAD, limit=10)) == 4


def test_990ez_field_names_are_understood() -> None:
    """990-EZ filers report under different keys than 990 filers."""
    filings = parse_filings("111111111", EZ_PAYLOAD, limit=3)

    assert filings[0].total_revenue == 842_119.0
    assert filings[0].total_expenses == 811_540.0
    assert filings[0].form_type == "990-EZ"


def test_filing_without_extracted_data_keeps_null_figures() -> None:
    """A PDF-only filing must surface as "not available", not as zero."""
    filings = parse_filings("362167869", ORG_PAYLOAD, limit=10)
    pdf_only = next(f for f in filings if f.tax_year == 2020)

    assert pdf_only.total_revenue is None
    assert pdf_only.total_expenses is None
    assert pdf_only.total_assets is None
    assert pdf_only.has_financials is False
    assert pdf_only.pdf_url is not None  # the document is still offered


def test_extracted_data_wins_over_a_pdf_only_listing_for_the_same_year() -> None:
    payload = {
        "filings_with_data": [
            {"tax_prd_yr": 2023, "tax_prd": 202312, "totrevenue": 500.0, "formtype": 0}
        ],
        "filings_without_data": [
            {"tax_prd_yr": 2023, "tax_prd": 202312, "pdf_url": "https://x/2023.pdf"}
        ],
    }
    filings = parse_filings("362167869", payload)

    assert len(filings) == 1
    assert filings[0].total_revenue == 500.0
    # ...and the document link from the other listing is still adopted.
    assert filings[0].pdf_url == "https://x/2023.pdf"


def test_unparseable_amounts_become_null_not_zero() -> None:
    payload = {
        "filings_with_data": [
            {
                "tax_prd_yr": 2023,
                "tax_prd": 202312,
                "totrevenue": "",
                "totfuncexpns": None,
                "totassetsend": "n/a",
            }
        ]
    }
    filing = parse_filings("362167869", payload)[0]

    assert filing.total_revenue is None
    assert filing.total_expenses is None
    assert filing.total_assets is None


def test_zero_revenue_is_preserved_as_zero() -> None:
    """A genuine reported zero is data, and must not be confused with unknown."""
    payload = {
        "filings_with_data": [
            {"tax_prd_yr": 2023, "tax_prd": 202312, "totrevenue": 0, "formtype": 0}
        ]
    }
    filing = parse_filings("362167869", payload)[0]

    assert filing.total_revenue == 0.0
    assert filing.has_financials is True


def test_empty_or_missing_payload_yields_no_filings() -> None:
    assert parse_filings("362167869", None) == []
    assert parse_filings("362167869", {}) == []
    assert parse_filings("362167869", {"filings_with_data": []}) == []


def test_latest_with_financials_skips_pdf_only_years() -> None:
    filings = parse_filings("362167869", ORG_PAYLOAD, limit=10)
    # 2020 is PDF-only; the newest year carrying figures is 2023.
    assert latest_with_financials(filings).tax_year == 2023

    assert latest_with_financials([f for f in filings if f.tax_year == 2020]) is None


# ---------------------------------------------------------------------------
# Stage behaviour
# ---------------------------------------------------------------------------


def test_stage_stores_filings_for_confirmed_organizations(
    config: Config, session: Session
) -> None:
    add_org(session, "Erie Family Health Centers")

    result = enrich_financials(
        session, config, client=make_client(config, session, payload_handler(ORG_PAYLOAD))
    )

    assert result.eligible == 1
    assert result.fetched == 1
    assert result.filings_written == 3
    assert result.organizations_with_financials == 1

    filings = session.scalars(select(Filing).order_by(Filing.tax_year.desc())).all()
    assert [f.tax_year for f in filings] == [2023, 2022, 2021]
    assert filings[0].total_revenue == 89_412_355.0
    assert filings[0].period_end is not None


@pytest.mark.parametrize(
    "status",
    [MatchStatus.PENDING, MatchStatus.REJECTED, MatchStatus.UNMATCHED],
)
def test_unconfirmed_matches_are_never_enriched(
    config: Config, session: Session, status: MatchStatus
) -> None:
    """Financials must not be attached on the strength of an unconfirmed guess."""
    add_org(session, "Prairie Rural Health Clinic", status=status)

    result = enrich_financials(
        session, config, client=make_client(config, session, payload_handler(ORG_PAYLOAD))
    )

    assert result.eligible == 0
    assert session.scalars(select(Filing)).all() == []


def test_human_accepted_matches_are_enriched(config: Config, session: Session) -> None:
    add_org(session, "Erie Family Health Centers", status=MatchStatus.ACCEPTED)

    result = enrich_financials(
        session, config, client=make_client(config, session, payload_handler(ORG_PAYLOAD))
    )
    assert result.eligible == 1
    assert result.filings_written == 3


def test_second_run_is_served_from_cache(config: Config, session: Session) -> None:
    add_org(session, "Erie Family Health Centers")
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=ORG_PAYLOAD)

    enrich_financials(session, config, client=make_client(config, session, handler))
    second = enrich_financials(
        session, config, client=make_client(config, session, handler)
    )

    assert calls["n"] == 1
    assert second.fetched == 0
    assert second.from_cache == 1
    assert second.used_cache is True
    # Cached data still lands in the database -- no rows are lost on a re-run.
    assert len(session.scalars(select(Filing)).all()) == 3


def test_shrinking_the_retention_window_prunes_old_filings(
    config: Config, session: Session
) -> None:
    add_org(session, "Erie Family Health Centers")
    enrich_financials(
        session, config, client=make_client(config, session, payload_handler(ORG_PAYLOAD))
    )
    assert len(session.scalars(select(Filing)).all()) == 3

    config.propublica.filings_per_org = 2
    enrich_financials(
        session,
        config,
        client=make_client(config, session, payload_handler(ORG_PAYLOAD)),
        force=True,
    )

    years = [f.tax_year for f in session.scalars(select(Filing)).all()]
    assert sorted(years, reverse=True) == [2023, 2022]


def test_unknown_ein_is_recorded_as_not_found(config: Config, session: Session) -> None:
    add_org(session, "Erie Family Health Centers")

    result = enrich_financials(
        session,
        config,
        client=make_client(config, session, lambda _r: httpx.Response(404)),
    )

    assert result.not_found == 1
    assert result.filings_written == 0
    assert session.scalars(select(Filing)).all() == []


def test_stage_stops_after_repeated_failures(config: Config, session: Session) -> None:
    for index in range(10):
        add_org(session, f"Health Center {index}", ein=f"36216786{index}")

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked", request=request)

    config.propublica.max_retries = 0
    result = enrich_financials(
        session, config, client=make_client(config, session, failing)
    )

    assert result.failed == 3
    assert result.fetched == 0
    assert result.source_reachable is False
    assert any("stopped after" in m for m in result.messages)

    run = session.scalars(select(IngestRun)).one()
    assert run.status == RunStatus.FAILED


def test_run_is_recorded_with_counts(config: Config, session: Session) -> None:
    add_org(session, "Erie Family Health Centers")

    enrich_financials(
        session, config, client=make_client(config, session, payload_handler(ORG_PAYLOAD))
    )

    run = session.scalars(select(IngestRun)).one()
    assert run.stage == "financials"
    assert run.status == RunStatus.SUCCESS
    assert run.records_written == 3
    assert run.finished_at is not None
