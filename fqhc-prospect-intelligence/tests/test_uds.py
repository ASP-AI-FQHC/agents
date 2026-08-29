"""HRSA Uniform Data System: patients, staffing, payer mix and sizing.

The fixtures are two different years' column layouts on purpose -- UDS renames
its headers between releases, and the whole point of the resolver is that a
rename degrades to a missing field rather than breaking the run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    GranteeType,
    IngestRun,
    Organization,
    RunStatus,
    UdsReport,
)
from pipeline.uds import (
    OrganizationIndex,
    UdsRecord,
    estimate_sizing,
    ingest,
    parse_number,
    parse_share,
    parse_uds,
    read_rows,
    source_files,
    year_from_filename,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def uds_config(config: Config, tmp_path: Path) -> Config:
    directory = tmp_path / "uds"
    directory.mkdir()
    config.uds.local_directory = directory
    config.project_root = tmp_path
    return config


def add_org(
    session: Session,
    name: str,
    *,
    state: str = "IL",
    hrsa_id: str | None = None,
    grant_number: str | None = None,
    sites: int = 5,
) -> Organization:
    from pipeline.text import normalize_name

    org = Organization(
        # Mirrors the real ingest: a HRSA id is the identity when there is one,
        # which is why two centres can share a name within a state.
        dedup_key=hrsa_id or f"{normalize_name(name)}|{state}",
        name=name,
        normalized_name=normalize_name(name),
        state=state,
        city="Chicago",
        site_count=sites,
        hrsa_id=hrsa_id,
        grant_number=grant_number,
        grantee_type=GranteeType.AWARDEE,
    )
    session.add(org)
    session.commit()
    return org


# ---------------------------------------------------------------------------
# Cell parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("84,532", 84532.0),
        ("$142,000,000", 142000000.0),
        ("612.4", 612.4),
        ("(1,200)", -1200.0),
        ("", None),
        ("N/A", None),
        ("--", None),
        (None, None),
    ],
)
def test_numbers_are_read_from_spreadsheet_cells(raw, expected) -> None:
    assert parse_number(raw) == expected


def test_a_blank_cell_is_not_zero() -> None:
    """A health center that did not report and one that reported nothing are
    different facts, and the score treats them differently."""
    assert parse_number("") is None
    assert parse_number("0") == 0.0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("58.2", 0.582),   # published as a percentage
        ("0.582", 0.582),  # and, in other years, as a fraction
        ("100", 1.0),
        ("0", 0.0),
        ("", None),
        ("140", None),     # not a share at all
    ],
)
def test_payer_shares_survive_both_conventions(raw, expected) -> None:
    assert parse_share(raw) == pytest.approx(expected) if expected is not None else parse_share(raw) is None


@pytest.mark.parametrize(
    "name,expected",
    [
        ("2023_UDS_Health_Center_Data.xlsx", 2023),
        ("uds-2019.csv", 2019),
        ("HealthCenterData.csv", None),
        ("report_12345.csv", None),
    ],
)
def test_the_year_can_come_from_the_filename(name, expected) -> None:
    """UDS exports routinely have no year column; the filename is all there is."""
    assert year_from_filename(name) == expected


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------


def test_a_standard_export_parses() -> None:
    headers, rows = read_rows(FIXTURES / "uds_2023.csv")
    parsed = parse_uds(headers, rows, default_year=2023)

    assert parsed.rows_read == 4
    first = parsed.records[0]
    assert first.hrsa_id == "010010"
    assert first.patients == 84532
    assert first.total_fte == pytest.approx(612.4)
    assert first.medicaid_share == pytest.approx(0.582)
    assert first.year == 2023


def test_renamed_columns_still_resolve() -> None:
    """Different year, different header names, same facts."""
    headers, rows = read_rows(FIXTURES / "uds_2022_renamed.csv")
    parsed = parse_uds(headers, rows)

    assert not parsed.missing_fields
    record = parsed.records[0]
    assert record.patients == 79001
    assert record.total_fte == pytest.approx(588.0)
    assert record.medicaid_share == pytest.approx(0.571)
    assert record.year == 2022
    assert record.grant_number == "H80CS00123"


def test_an_unrecognizable_layout_is_reported_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "wrong.csv"
    path.write_text("Colour,Shape\nred,round\n")
    headers, rows = read_rows(path)
    parsed = parse_uds(headers, rows)

    assert parsed.missing_fields == ["patients"]
    assert parsed.records == []


def test_missing_optional_columns_become_nulls() -> None:
    """The 2022 layout has no revenue columns at all."""
    headers, rows = read_rows(FIXTURES / "uds_2022_renamed.csv")
    record = parse_uds(headers, rows).records[0]
    assert record.total_revenue is None
    assert record.grant_revenue is None


# ---------------------------------------------------------------------------
# Matching to organizations
# ---------------------------------------------------------------------------


def test_identifiers_beat_names(session: Session) -> None:
    right = add_org(session, "Erie Family Health Centers, Inc.", hrsa_id="010010")
    add_org(session, "Erie Family Health Centers, Inc.", state="WI", hrsa_id="999999")

    index = OrganizationIndex.build(session.scalars(select(Organization)).all())
    matched = index.match(UdsRecord(hrsa_id="010010", name="Something Else", state="IL"))
    assert matched is right


def test_a_grant_number_matches_when_there_is_no_hrsa_id(session: Session) -> None:
    org = add_org(session, "Erie Family Health", grant_number="H80CS00123")
    index = OrganizationIndex.build(session.scalars(select(Organization)).all())
    assert index.match(UdsRecord(grant_number="H80CS00123")) is org


def test_a_name_match_requires_the_state_to_agree(session: Session) -> None:
    add_org(session, "Lakeside Health", state="IL")
    index = OrganizationIndex.build(session.scalars(select(Organization)).all())

    assert index.match(UdsRecord(name="Lakeside Health", state="IL")) is not None
    assert index.match(UdsRecord(name="Lakeside Health", state="WI")) is None
    assert index.match(UdsRecord(name="Lakeside Health")) is None


def test_duplicate_names_in_one_state_match_nothing(session: Session) -> None:
    """Two organizations a name cannot tell apart are not matched by name at
    all -- attaching one center's patient count to another is worse than a gap."""
    add_org(session, "Community Health Center", state="IL", hrsa_id="1")
    add_org(session, "Community Health Center", state="IL", hrsa_id="2")
    session.commit()

    index = OrganizationIndex.build(session.scalars(select(Organization)).all())
    assert index.match(UdsRecord(name="Community Health Center", state="IL")) is None


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def test_stage_loads_a_file_and_attaches_it(uds_config: Config, session: Session) -> None:
    erie = add_org(session, "Erie", hrsa_id="010010")
    add_org(session, "Milwaukee", state="WI", hrsa_id="020020")
    shutil.copy(FIXTURES / "uds_2023.csv", uds_config.uds.local_directory / "uds_2023.csv")

    result = ingest(session, uds_config)

    assert result.files_read == 1
    assert result.matched == 2
    row = session.scalar(
        select(UdsReport).where(UdsReport.organization_id == erie.id)
    )
    assert row.year == 2023 and row.patients == 84532


def test_rows_for_organizations_we_do_not_have_are_counted_not_dropped_silently(
    uds_config: Config, session: Session
) -> None:
    add_org(session, "Erie", hrsa_id="010010")
    shutil.copy(FIXTURES / "uds_2023.csv", uds_config.uds.local_directory / "uds_2023.csv")

    result = ingest(session, uds_config)

    assert result.unmatched == 3
    assert any("did not match" in message for message in result.messages)


def test_multiple_years_are_kept_side_by_side(
    uds_config: Config, session: Session
) -> None:
    org = add_org(session, "Erie", hrsa_id="010010", grant_number="H80CS00123")
    shutil.copy(FIXTURES / "uds_2023.csv", uds_config.uds.local_directory / "uds_2023.csv")
    shutil.copy(
        FIXTURES / "uds_2022_renamed.csv", uds_config.uds.local_directory / "uds_2022.csv"
    )

    ingest(session, uds_config)

    years = sorted(
        row.year
        for row in session.scalars(
            select(UdsReport).where(UdsReport.organization_id == org.id)
        ).all()
    )
    assert years == [2022, 2023]


def test_re_running_updates_rather_than_duplicates(
    uds_config: Config, session: Session
) -> None:
    add_org(session, "Erie", hrsa_id="010010")
    shutil.copy(FIXTURES / "uds_2023.csv", uds_config.uds.local_directory / "uds_2023.csv")

    ingest(session, uds_config)
    ingest(session, uds_config)

    assert len(session.scalars(select(UdsReport)).all()) == 1


def test_an_empty_directory_says_what_to_do(uds_config: Config, session: Session) -> None:
    result = ingest(session, uds_config)

    assert result.status == RunStatus.SUCCESS
    assert any("data.hrsa.gov" in message for message in result.messages)


def test_an_unreadable_file_does_not_stop_the_others(
    uds_config: Config, session: Session
) -> None:
    add_org(session, "Erie", hrsa_id="010010")
    (uds_config.uds.local_directory / "aaa_notes.csv").write_text("Colour,Shape\nred,round\n")
    shutil.copy(FIXTURES / "uds_2023.csv", uds_config.uds.local_directory / "uds_2023.csv")

    result = ingest(session, uds_config)

    assert result.matched == 1
    assert any("unrecognizable" in m or "no recognizable" in m for m in result.messages)


def test_temporary_office_files_are_ignored(uds_config: Config) -> None:
    (uds_config.uds.local_directory / "~$uds_2023.csv").write_text("junk")
    (uds_config.uds.local_directory / "uds_2023.csv").write_text("a,b\n1,2\n")
    assert [p.name for p in source_files(uds_config.uds.local_directory)] == [
        "uds_2023.csv"
    ]


def test_run_is_recorded(uds_config: Config, session: Session) -> None:
    add_org(session, "Erie", hrsa_id="010010")
    shutil.copy(FIXTURES / "uds_2023.csv", uds_config.uds.local_directory / "uds_2023.csv")
    ingest(session, uds_config)

    run = session.scalars(select(IngestRun).where(IngestRun.stage == "uds")).one()
    assert run.status == RunStatus.SUCCESS
    assert run.records_written == 1


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def test_sizing_comes_from_staffing(uds_config: Config, session: Session) -> None:
    org = add_org(session, "Erie", hrsa_id="010010", sites=13)
    shutil.copy(FIXTURES / "uds_2023.csv", uds_config.uds.local_directory / "uds_2023.csv")
    ingest(session, uds_config)

    report = session.scalar(select(UdsReport))
    sizing = estimate_sizing(org, report, uds_config)

    assert sizing.sites == 13
    assert sizing.users == 612               # one account per FTE
    assert sizing.workstations == 521        # 612.4 * 0.85
    assert sizing.devices == 857             # 612.4 * 1.4
    assert "612 staff FTE reported in the 2023 UDS" in sizing.basis


def test_no_staffing_means_no_estimate(uds_config: Config, session: Session) -> None:
    """Revenue and patient counts predict device counts far too weakly. A
    number nobody can defend is worse on a proposal than no number."""
    org = add_org(session, "Erie")
    report = UdsReport(organization_id=org.id, year=2023, patients=5000, total_fte=None)
    assert estimate_sizing(org, report, uds_config) is None
    assert estimate_sizing(org, None, uds_config) is None


def test_sizing_ratios_are_configurable(uds_config: Config, session: Session) -> None:
    org = add_org(session, "Erie", sites=2)
    report = UdsReport(organization_id=org.id, year=2023, total_fte=100.0)
    uds_config.uds.devices_per_fte = 2.0
    uds_config.uds.workstations_per_fte = 1.0

    sizing = estimate_sizing(org, report, uds_config)
    assert (sizing.devices, sizing.workstations, sizing.users) == (200, 100, 100)


def test_support_staff_is_the_remainder(session: Session) -> None:
    org = add_org(session, "Erie")
    report = UdsReport(
        organization_id=org.id, year=2023, total_fte=612.4, provider_fte=148.2
    )
    assert report.support_fte == pytest.approx(464.2)
    assert UdsReport(organization_id=org.id, year=2023, total_fte=10.0).support_fte is None


# ---------------------------------------------------------------------------
# Checking a downloaded file before running anything
# ---------------------------------------------------------------------------


def test_inspect_confirms_a_usable_file() -> None:
    from pipeline.uds import inspect

    text = inspect(FIXTURES / "uds_2023.csv")

    assert "Looks like UDS data: 4 rows" in text
    assert "Reporting year: 2023" in text
    assert "patients" in text
    assert "Erie Family Health Centers, Inc. (IL) -- 84,532 patients" in text


def test_inspect_rejects_the_wrong_file_and_says_why() -> None:
    """HRSA publishes many similarly named files; this is how you tell."""
    from pipeline.uds import inspect

    text = inspect(FIXTURES / "hrsa_sites.csv")

    assert "NOT a UDS health-center file" in text
    assert "total-patients column" in text
    # Every column, numbered -- this is exactly when the full list is wanted,
    # either to recognise the file or to send it on for the aliases to be fixed.
    assert "Site Name" in text
    assert "Site Postal Code" in text
    assert "All 14 columns:" in text


def test_inspect_names_the_columns_it_will_ignore() -> None:
    from pipeline.uds import inspect

    text = inspect(FIXTURES / "uds_2022_renamed.csv")
    assert "Not present (left blank)" in text
    assert "total_revenue" in text


def test_inspect_says_when_the_year_is_missing(tmp_path: Path) -> None:
    from pipeline.uds import inspect

    path = tmp_path / "HealthCenterData.csv"
    path.write_text("Health Center Name,State,Total Patients\nA Center,IL,1000\n")

    text = inspect(path)
    assert "rename it to include the year" in text


def test_inspect_handles_a_missing_file(tmp_path: Path) -> None:
    from pipeline.uds import inspect

    assert "does not exist" in inspect(tmp_path / "nope.csv")


def test_inspect_handles_something_that_is_not_a_spreadsheet(tmp_path: Path) -> None:
    from pipeline.uds import inspect

    path = tmp_path / "notes.xlsx"
    path.write_bytes(b"this is not a workbook")
    assert "could not be read" in inspect(path)


def test_excel_lock_files_are_recognised_not_reported_as_corrupt(tmp_path: Path) -> None:
    """A wildcard over a Downloads folder picks these up; calling them
    unreadable is true and useless."""
    from pipeline.uds import inspect

    lock = tmp_path / "~$Some_Workbook.xlsx"
    lock.write_bytes(b"not a zip")

    text = inspect(lock)
    assert "lock file" in text
    assert "could not be read" not in text
