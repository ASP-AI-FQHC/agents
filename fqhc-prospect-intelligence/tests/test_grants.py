"""Grants awarded to a health center, from two sources that mean different things.

The rule under test throughout: a grant is attached on an exact nine-digit EIN
and never on a name, and nothing is called "active" without a reported end date.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Grant, GrantSource
from pipeline.grants import (
    AWARD_FIELDS,
    award_files,
    parse_date,
    read_award_file,
    scan_schedule_i,
)
from pipeline.irs import has_schedule_i, parse_schedule_i

# ---------------------------------------------------------------------------
# Schedule I
# ---------------------------------------------------------------------------

GRANTOR_RETURN = """<?xml version="1.0" encoding="UTF-8"?>
<Return xmlns="http://www.irs.gov/efile">
  <ReturnHeader>
    <TaxYr>2023</TaxYr>
    <Filer>
      <EIN>361234567</EIN>
      <BusinessName><BusinessNameLine1Txt>Illinois Primary Health Care
        Association</BusinessNameLine1Txt></BusinessName>
    </Filer>
  </ReturnHeader>
  <ReturnData>
    <IRS990ScheduleI>
      <RecipientTable>
        <RecipientBusinessName>
          <BusinessNameLine1Txt>Near North Health Service Corporation</BusinessNameLine1Txt>
        </RecipientBusinessName>
        <RecipientEIN>363197647</RecipientEIN>
        <IRCSectionDesc>501(c)(3)</IRCSectionDesc>
        <CashGrantAmt>115422</CashGrantAmt>
        <NonCashAssistanceAmt>4000</NonCashAssistanceAmt>
        <PurposeOfGrantTxt>Outreach and enrollment assistance</PurposeOfGrantTxt>
      </RecipientTable>
      <RecipientTable>
        <RecipientBusinessName>
          <BusinessNameLine1Txt>Some Other Health Center</BusinessNameLine1Txt>
        </RecipientBusinessName>
        <RecipientEIN>987654321</RecipientEIN>
        <CashGrantAmt>50000</CashGrantAmt>
      </RecipientTable>
      <GrantsOtherAsstToIndivInUSGrp>
        <TypeOfGrantOrAssistanceTxt>Emergency assistance to individuals</TypeOfGrantOrAssistanceTxt>
        <CashGrantAmt>12000</CashGrantAmt>
      </GrantsOtherAsstToIndivInUSGrp>
    </IRS990ScheduleI>
  </ReturnData>
</Return>"""


def test_a_grant_is_read_from_the_grantors_return() -> None:
    grantor = parse_schedule_i(GRANTOR_RETURN)

    assert grantor.grantor_ein == "361234567"
    assert grantor.grantor_name == "Illinois Primary Health Care Association"
    assert grantor.tax_year == 2023

    first = grantor.grants[0]
    assert first.recipient_ein == "363197647"
    assert first.cash == 115_422
    assert first.non_cash == 4_000
    assert first.total == 119_422
    assert first.purpose == "Outreach and enrollment assistance"


def test_the_filers_own_name_is_not_taken_from_a_recipient() -> None:
    """Both are BusinessNameLine1Txt; only the header one is the filer."""
    grantor = parse_schedule_i(GRANTOR_RETURN)
    assert "Near North" not in (grantor.grantor_name or "")


def test_grants_to_individuals_are_skipped() -> None:
    """They carry no recipient EIN, so they cannot be attached to anyone."""
    grantor = parse_schedule_i(GRANTOR_RETURN)
    assert len(grantor.grants) == 2
    assert all(grant.recipient_ein for grant in grantor.grants)


def test_a_total_needs_at_least_one_reported_component() -> None:
    grantor = parse_schedule_i(GRANTOR_RETURN)
    second = grantor.grants[1]
    assert second.non_cash is None
    assert second.total == 50_000  # cash alone, not cash + an assumed zero


def test_the_cheap_precheck_rejects_returns_without_a_schedule_i() -> None:
    assert has_schedule_i(GRANTOR_RETURN.encode())
    assert not has_schedule_i(
        b"<Return xmlns='http://www.irs.gov/efile'><IRS990/></Return>"
    )


def test_scanning_finds_only_the_eins_asked_for(tmp_path) -> None:
    (tmp_path / "grantor.xml").write_text(GRANTOR_RETURN, encoding="utf-8")
    (tmp_path / "unrelated.xml").write_text(
        "<Return xmlns='http://www.irs.gov/efile'><ReturnHeader>"
        "<Filer><EIN>111111111</EIN></Filer></ReturnHeader><ReturnData>"
        "<IRS990/></ReturnData></Return>",
        encoding="utf-8",
    )

    from pipeline.irs import reset_document_index

    reset_document_index()
    found, result = scan_schedule_i(tmp_path, {"363197647"})

    assert result.documents_read == 2
    assert result.documents_with_schedule_i == 1
    assert len(found) == 1
    recipient_ein, (grantor, grant) = found[0]
    assert recipient_ein == "363197647"
    assert grantor.grantor_name == "Illinois Primary Health Care Association"
    assert grant.total == 119_422


def test_a_minimum_amount_filters_out_pass_through_grants(tmp_path) -> None:
    (tmp_path / "grantor.xml").write_text(GRANTOR_RETURN, encoding="utf-8")

    from pipeline.irs import reset_document_index

    reset_document_index()
    found, _ = scan_schedule_i(
        tmp_path, {"363197647"}, minimum_amount=200_000
    )
    assert found == []


def test_scanning_nothing_is_not_an_error(tmp_path) -> None:
    from pipeline.irs import reset_document_index

    reset_document_index()
    found, result = scan_schedule_i(tmp_path, set())
    assert found == []
    assert result.documents_read == 0


# ---------------------------------------------------------------------------
# Federal award files
# ---------------------------------------------------------------------------

USASPENDING = """\
recipient_name,recipient_ein,award_id_fain,awarding_agency_name,cfda_number,\
cfda_title,total_obligated_amount,period_of_performance_start_date,\
period_of_performance_current_end_date,award_description
Near North Health Service Corporation,36-3197647,H80CS00123,\
Health Resources and Services Administration,93.224,Health Center Program,\
9842113,2025-02-01,2028-01-31,Community health center operations
Erie Family Health Centers,362961856,H80CS00456,\
Health Resources and Services Administration,93.224,Health Center Program,\
12400000,2021-02-01,2024-01-31,Community health center operations
"""


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_usaspending_export_loads(tmp_path) -> None:
    records, warnings = read_award_file(write(tmp_path, "awards.csv", USASPENDING))

    assert warnings == []
    assert len(records) == 2
    first = records[0]
    assert first.recipient_ein == "363197647"   # punctuation stripped
    assert first.award_number == "H80CS00123"
    assert first.amount == 9_842_113
    assert first.cfda_number == "93.224"
    assert first.program_title == "Health Center Program"
    assert first.start_date == datetime(2025, 2, 1, tzinfo=timezone.utc)
    assert first.end_date == datetime(2028, 1, 31, tzinfo=timezone.utc)


def test_columns_are_resolved_by_name_not_position(tmp_path) -> None:
    """An agency export with its own headers and a different column order."""
    renamed = (
        "Award End Date,Awardee Name,Employer Identification Number,"
        "Grant Number,Agency Name,Award Amount,Award Start Date\n"
        "01/31/2028,Near North,36-3197647,H80CS00123,HRSA,\"$9,842,113\",02/01/2025\n"
    )
    records, warnings = read_award_file(write(tmp_path, "agency.csv", renamed))

    assert warnings == []
    assert records[0].recipient_ein == "363197647"
    assert records[0].amount == 9_842_113
    assert records[0].award_number == "H80CS00123"
    assert records[0].end_date == datetime(2028, 1, 31, tzinfo=timezone.utc)


def test_a_file_without_an_ein_column_is_refused_not_guessed_at(tmp_path) -> None:
    """Matching a grant on a name is exactly what this must never do."""
    nameless = (
        "Recipient Name,Award Amount,Agency Name\n"
        "Near North Health Service Corporation,9842113,HRSA\n"
    )
    records, warnings = read_award_file(write(tmp_path, "nameless.csv", nameless))

    assert records == []
    assert warnings and "no recipient EIN column" in warnings[0]
    assert "never on a name" in warnings[0]


def test_a_missing_amount_column_warns_but_still_loads(tmp_path) -> None:
    partial = "recipient_ein,award_id_fain\n363197647,H80CS00123\n"
    records, warnings = read_award_file(write(tmp_path, "partial.csv", partial))

    assert len(records) == 1
    assert records[0].amount is None
    assert warnings and "no award amount column" in warnings[0]


def test_an_unparseable_date_is_none_rather_than_today() -> None:
    assert parse_date("not a date") is None
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date("2025-02-01T00:00:00Z") == datetime(
        2025, 2, 1, tzinfo=timezone.utc
    )


def test_spreadsheet_lock_files_are_ignored(tmp_path) -> None:
    write(tmp_path, "awards.csv", USASPENDING)
    write(tmp_path, "~$awards.csv", "junk")
    write(tmp_path, ".DS_Store", "junk")
    write(tmp_path, "notes.txt", "junk")

    assert [path.name for path in award_files(tmp_path)] == ["awards.csv"]


def test_no_directory_is_not_an_error(tmp_path) -> None:
    assert award_files(tmp_path / "nothing-here") == []


# ---------------------------------------------------------------------------
# What a stored grant claims
# ---------------------------------------------------------------------------


def test_active_needs_a_reported_end_date() -> None:
    now = datetime.now(timezone.utc)

    running = Grant(
        organization_id=1,
        source=GrantSource.FEDERAL_AWARD,
        end_date=now + timedelta(days=400),
    )
    finished = Grant(
        organization_id=1,
        source=GrantSource.FEDERAL_AWARD,
        end_date=now - timedelta(days=10),
    )
    unstated = Grant(organization_id=1, source=GrantSource.FEDERAL_AWARD)

    assert running.is_active is True
    assert finished.is_active is False
    # Not False. "We do not know when this ends" is not "this has ended".
    assert unstated.is_active is None


def test_a_schedule_i_grant_never_claims_to_be_active() -> None:
    grant = Grant(
        organization_id=1, source=GrantSource.SCHEDULE_I, tax_year=2023, amount=115_422
    )
    assert grant.is_active is None
    assert grant.period_label is None


def test_a_half_reported_period_says_what_it_knows() -> None:
    grant = Grant(
        organization_id=1,
        source=GrantSource.FEDERAL_AWARD,
        start_date=datetime(2025, 2, 1, tzinfo=timezone.utc),
    )
    assert grant.period_label == "Feb 2025 to ?"


@pytest.mark.parametrize("field", ["recipient_ein", "amount", "end_date"])
def test_the_award_reader_knows_the_columns_it_needs(field) -> None:
    assert field in AWARD_FIELDS


# ---------------------------------------------------------------------------
# The stage end to end
# ---------------------------------------------------------------------------


def two_organizations(session):
    """Near North with a confirmed EIN, and one with no EIN at all."""
    from app.models import EinMatch, MatchStatus, Organization

    near_north = Organization(
        dedup_key="near north|il", name="Near North Health Service Corporation",
        normalized_name="near north health service corporation", state="IL",
    )
    unmatched = Organization(
        dedup_key="riverbend|il", name="Riverbend Access",
        normalized_name="riverbend access", state="IL",
    )
    session.add_all([near_north, unmatched])
    session.flush()
    session.add(
        EinMatch(
            organization_id=near_north.id, ein="363197647", score=97.0,
            status=MatchStatus.AUTO,
        )
    )
    session.commit()
    return near_north, unmatched


def stage_config(config, tmp_path, **overrides):
    """The real config, pointed at a temporary directory."""
    updated = config.model_copy(deep=True)
    updated.grants.local_directory = tmp_path / "grants"
    updated.irs.local_directory = tmp_path / "xml"
    for key, value in overrides.items():
        setattr(updated.grants, key, value)
    (tmp_path / "grants").mkdir(exist_ok=True)
    (tmp_path / "xml").mkdir(exist_ok=True)
    return updated


def test_the_stage_loads_an_award_file_and_attaches_it(
    session, config, tmp_path
) -> None:
    from pipeline import grants as grants_module

    near_north, _ = two_organizations(session)
    settings = stage_config(config, tmp_path)
    write(settings.grants.local_directory, "awards.csv", USASPENDING)

    result = grants_module.ingest(session, settings)

    rows = session.scalars(select_grants()).all()
    assert result.awards_written == 1  # only the EIN we hold
    assert len(rows) == 1
    assert rows[0].organization_id == near_north.id
    assert rows[0].award_number == "H80CS00123"
    assert rows[0].source_file == "awards.csv"
    assert rows[0].is_active is True


def test_rerunning_replaces_the_award_file_rather_than_duplicating_it(
    session, config, tmp_path
) -> None:
    from pipeline import grants as grants_module

    two_organizations(session)
    settings = stage_config(config, tmp_path)
    write(settings.grants.local_directory, "awards.csv", USASPENDING)

    grants_module.ingest(session, settings)
    grants_module.ingest(session, settings)

    assert len(session.scalars(select_grants()).all()) == 1


def test_the_stage_says_what_to_download_when_there_is_nothing(
    session, config, tmp_path
) -> None:
    from pipeline import grants as grants_module

    two_organizations(session)
    result = grants_module.ingest(session, stage_config(config, tmp_path))

    assert result.awards_written == 0
    joined = " ".join(result.messages)
    assert "usaspending.gov" in joined
    assert "scan_schedule_i" in joined


def test_the_stage_reads_schedule_i_when_asked_to(session, config, tmp_path) -> None:
    from pipeline import grants as grants_module
    from pipeline.irs import reset_document_index

    near_north, _ = two_organizations(session)
    settings = stage_config(config, tmp_path, scan_schedule_i=True)
    write(settings.irs.local_directory, "grantor.xml", GRANTOR_RETURN)
    reset_document_index()

    result = grants_module.ingest(session, settings)

    assert result.schedule_i_written == 1
    row = session.scalars(select_grants()).one()
    assert row.organization_id == near_north.id
    assert row.grantor_name == "Illinois Primary Health Care Association"
    assert row.grantor_ein == "361234567"
    assert row.amount == 119_422
    assert row.tax_year == 2023
    # History, not a current position.
    assert row.is_active is None


def test_a_grant_is_never_attached_to_an_organization_without_a_confirmed_ein(
    session, config, tmp_path
) -> None:
    from pipeline import grants as grants_module
    from pipeline.irs import reset_document_index

    _, unmatched = two_organizations(session)
    settings = stage_config(config, tmp_path, scan_schedule_i=True)
    write(settings.irs.local_directory, "grantor.xml", GRANTOR_RETURN)
    reset_document_index()

    grants_module.ingest(session, settings)

    rows = session.scalars(select_grants()).all()
    assert all(row.organization_id != unmatched.id for row in rows)


def select_grants():
    from sqlalchemy import select

    return select(Grant)
