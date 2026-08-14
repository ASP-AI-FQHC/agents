"""The pipeline CLI: argument handling and how --limit reaches the stages."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import EinMatch, Filing, MatchStatus, Organization
from pipeline.propublica import ProPublicaClient, enrich_financials
from pipeline.matching import match_organizations
from pipeline.run import STAGES, StageOptions, build_parser, main
from tests.test_propublica_client import FakeClock


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_limit_defaults_to_none() -> None:
    assert build_parser().parse_args([]).limit is None


def test_limit_is_parsed() -> None:
    assert build_parser().parse_args(["--limit", "20"]).limit == 20


def test_limit_below_one_is_rejected(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--limit", "0"])
    assert "--limit must be at least 1" in capsys.readouterr().err


def test_only_the_api_stages_honour_the_limit() -> None:
    honouring = {stage.name for stage in STAGES if stage.honours_limit}
    assert honouring == {"ein", "financials"}


def test_stage_options_default_to_a_full_run() -> None:
    options = StageOptions()
    assert options.force_refresh is False
    assert options.limit is None


# ---------------------------------------------------------------------------
# The limit actually caps the work
# ---------------------------------------------------------------------------


def add_org(session: Session, index: int, *, ein: str | None = None) -> Organization:
    org = Organization(
        dedup_key=f"health center {index}|IL",
        name=f"Health Center {index}",
        normalized_name=f"health center {index}",
        state="IL",
        city="Chicago",
        site_count=3,
    )
    session.add(org)
    session.flush()
    if ein:
        session.add(
            EinMatch(
                organization_id=org.id, ein=ein, score=99.0, status=MatchStatus.AUTO
            )
        )
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


def test_matching_stops_at_the_limit(config: Config, session: Session) -> None:
    for index in range(10):
        add_org(session, index)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"total_results": 0, "organizations": []})

    result = match_organizations(
        session, config, client=make_client(config, session, handler), limit=3
    )

    assert result.searched == 3
    assert result.examined == 3
    # Ten organizations exist, but only three were touched.
    assert session.scalars(select(EinMatch)).all().__len__() == 3


def test_enrichment_stops_at_the_limit(config: Config, session: Session) -> None:
    for index in range(8):
        add_org(session, index, ein=f"36216786{index}")

    payload = {
        "filings_with_data": [
            {"tax_prd_yr": 2023, "tax_prd": 202312, "totrevenue": 1_000_000, "formtype": 0}
        ]
    }

    result = enrich_financials(
        session,
        config,
        client=make_client(config, session, lambda _r: httpx.Response(200, json=payload)),
        limit=2,
    )

    assert result.eligible == 8
    assert result.fetched == 2
    assert len({f.ein for f in session.scalars(select(Filing)).all()}) == 2


def test_a_limited_run_resumes_where_it_stopped(
    config: Config, session: Session
) -> None:
    """Two capped runs cover the same ground as one uncapped run."""
    for index in range(6):
        add_org(session, index)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_results": 0, "organizations": []})

    first = match_organizations(
        session, config, client=make_client(config, session, handler), limit=2
    )
    second = match_organizations(
        session, config, client=make_client(config, session, handler), limit=2
    )

    assert first.searched == 2
    assert second.searched == 2
    # The second run picked up different organizations, not the same two again.
    assert len(session.scalars(select(EinMatch)).all()) == 4


def test_cached_organizations_count_toward_the_limit(
    config: Config, session: Session
) -> None:
    """--limit means "at most N organizations", not "at most N live fetches"."""
    for index in range(6):
        add_org(session, index, ein=f"36216786{index}")

    payload = {
        "filings_with_data": [
            {"tax_prd_yr": 2023, "tax_prd": 202312, "totrevenue": 1_000_000, "formtype": 0}
        ]
    }
    handler = lambda _r: httpx.Response(200, json=payload)  # noqa: E731

    enrich_financials(
        session, config, client=make_client(config, session, handler), limit=3
    )
    second = enrich_financials(
        session, config, client=make_client(config, session, handler), limit=3
    )

    # The three already-cached organizations fill the second run's budget
    # instead of it silently doing three more live fetches on top.
    assert second.from_cache == 3
    assert second.fetched == 0


# ---------------------------------------------------------------------------
# The CLI says a capped run is capped
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_config(tmp_path: Path, config: Config) -> Path:
    """A config file pointing at a throwaway database, so running main() here
    never touches the developer's real data/fqhc.db."""
    import yaml

    from app.db import reset_engine

    raw = yaml.safe_load((config.project_root / "config.yaml").read_text())
    raw["app"]["database_path"] = str(tmp_path / "cli.db")
    raw["cache"]["directory"] = str(tmp_path / "raw")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    reset_engine()
    yield path
    reset_engine()


def test_cli_announces_a_trial_run(cli_config: Path, capsys) -> None:
    """A partial pass must never be mistaken for a complete one."""
    exit_code = main(
        ["--config", str(cli_config), "--stage", "scoring", "--limit", "5", "--quiet"]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "TRIAL RUN: at most 5 organizations" in output
    assert "--limit does not apply to scoring" in output
    assert "This was a trial run capped at 5 organizations" in output


def test_cli_says_nothing_about_limits_on_a_full_run(
    cli_config: Path, capsys
) -> None:
    main(["--config", str(cli_config), "--stage", "scoring", "--quiet"])
    output = capsys.readouterr().out

    assert "TRIAL RUN" not in output
    assert "trial run capped" not in output


# ---------------------------------------------------------------------------
# Footprint restriction
# ---------------------------------------------------------------------------


def add_org_in(session: Session, name: str, state: str, ein: str | None = None):
    org = Organization(
        dedup_key=f"{name.lower()}|{state}",
        name=name,
        normalized_name=name.lower(),
        state=state,
        city="Somewhere",
        site_count=3,
    )
    session.add(org)
    session.flush()
    if ein:
        session.add(
            EinMatch(
                organization_id=org.id, ein=ein, score=99.0, status=MatchStatus.AUTO
            )
        )
    session.commit()
    return org


def test_matching_skips_organizations_outside_the_footprint(
    config: Config, session: Session
) -> None:
    """The API cost of a national sweep buys nothing when only four states score."""
    add_org_in(session, "Illinois Health", "IL")
    add_org_in(session, "Wisconsin Health", "WI")
    add_org_in(session, "Texas Health", "TX")
    add_org_in(session, "Florida Health", "FL")

    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"total_results": 0, "organizations": []})

    result = match_organizations(
        session, config, client=make_client(config, session, handler)
    )

    assert result.searched == 2
    assert result.out_of_footprint == 2
    assert any("outside" in m for m in result.messages)

    searched = {
        m.organization_id for m in session.scalars(select(EinMatch)).all()
    }
    states = {
        session.get(Organization, oid).state for oid in searched
    }
    assert states == {"IL", "WI"}


def test_turning_the_restriction_off_searches_everything(
    config: Config, session: Session
) -> None:
    add_org_in(session, "Illinois Health", "IL")
    add_org_in(session, "Texas Health", "TX")

    config.pipeline.restrict_api_to_target_states = False

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_results": 0, "organizations": []})

    result = match_organizations(
        session, config, client=make_client(config, session, handler)
    )

    assert result.searched == 2
    assert result.out_of_footprint == 0


def test_enrichment_skips_organizations_outside_the_footprint(
    config: Config, session: Session
) -> None:
    add_org_in(session, "Illinois Health", "IL", ein="362167869")
    add_org_in(session, "Texas Health", "TX", ein="741234567")

    payload = {
        "filings_with_data": [
            {"tax_prd_yr": 2023, "tax_prd": 202312, "totrevenue": 1_000_000, "formtype": 0}
        ]
    }
    result = enrich_financials(
        session,
        config,
        client=make_client(config, session, lambda _r: httpx.Response(200, json=payload)),
    )

    assert result.eligible == 1
    assert {f.ein for f in session.scalars(select(Filing)).all()} == {"362167869"}


def test_footprint_follows_the_configured_target_states(
    config: Config, session: Session
) -> None:
    """Widening the scoring footprint widens the API sweep with no code change."""
    add_org_in(session, "Ohio Health", "OH")
    assert config.api_states == ["IL", "WI", "IN", "MI"]

    config.scoring.state.target_states = ["IL", "OH"]
    assert config.api_states == ["IL", "OH"]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_results": 0, "organizations": []})

    result = match_organizations(
        session, config, client=make_client(config, session, handler)
    )
    assert result.searched == 1
