"""Form 990 Part VII: people, board members and paid contractors."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    Contractor,
    EinMatch,
    IngestRun,
    MatchStatus,
    Organization,
    Person,
    RunStatus,
)
from pipeline.irs import (
    best_return,
    document_index,
    reset_document_index,
    enrich_people,
    extract_zip_members,
    local_xml_for_ein,
    normalize_ein,
    parse_index,
    parse_return,
    reset_index_cache,
)

FIXTURES = Path(__file__).parent / "fixtures"
MODERN = (FIXTURES / "irs_990_modern.xml").read_bytes()
LEGACY = (FIXTURES / "irs_990_legacy.xml").read_bytes()
INDEX_CSV = (FIXTURES / "irs_index_sample.csv").read_text()


@pytest.fixture(autouse=True)
def _clear_index_cache():
    reset_index_cache()
    reset_document_index()
    yield
    reset_index_cache()
    reset_document_index()


# ---------------------------------------------------------------------------
# Parsing the current schema
# ---------------------------------------------------------------------------


def test_header_identifies_the_filing() -> None:
    parsed = parse_return(MODERN)
    assert parsed.ein == "362167869"
    assert parsed.tax_year == 2023


def test_people_are_parsed_with_titles_and_roles() -> None:
    parsed = parse_return(MODERN)
    assert len(parsed.people) == 5

    chief = parsed.people[0]
    assert chief.name == "MARIA T ALVAREZ"
    assert chief.title == "CHIEF EXECUTIVE OFFICER"
    assert chief.roles == ["Officer"]
    assert chief.average_hours == 40.0
    assert chief.compensation == 412_500
    assert chief.total_compensation == 450_700  # includes other compensation


def test_multiple_role_checkboxes_are_all_captured() -> None:
    parsed = parse_return(MODERN)
    cio = next(p for p in parsed.people if "INFORMATION" in (p.title or ""))
    assert cio.roles == ["Officer", "Key employee"]


def test_board_members_are_identified() -> None:
    """The trustee/director checkbox is what makes someone a board member."""
    parsed = parse_return(MODERN)
    board = parsed.board_members

    assert [person.name for person in board] == [
        "REV. JAMES P DONNELLY",
        "SUSAN B WHITFIELD",
    ]
    assert all(person.is_board_member for person in board)
    # The chief executive is an officer, not a board member.
    assert not parsed.people[0].is_board_member


def test_unpaid_board_member_keeps_a_reported_zero() -> None:
    """$0 reported and no figure reported are different facts."""
    parsed = parse_return(MODERN)
    chair = next(p for p in parsed.people if p.name.startswith("REV."))
    unpaid = next(p for p in parsed.people if p.name == "SUSAN B WHITFIELD")

    assert chair.compensation == 0.0
    assert chair.total_compensation == 0.0
    assert unpaid.compensation is None
    assert unpaid.total_compensation is None


def test_contractors_are_parsed_with_the_service_described() -> None:
    """The commercially interesting rows: who the incumbent vendors are."""
    parsed = parse_return(MODERN)
    assert len(parsed.contractors) == 3

    vendors = {c.name: c for c in parsed.contractors}
    it_vendor = vendors["NORTHSIDE MANAGED IT LLC"]
    assert it_vendor.services == "INFORMATION TECHNOLOGY SUPPORT SERVICES"
    assert it_vendor.compensation == 412_750
    assert vendors["EPIC SYSTEMS CORPORATION"].compensation == 1_284_000


# ---------------------------------------------------------------------------
# Parsing older schema generations
# ---------------------------------------------------------------------------


def test_legacy_schema_parses_identically() -> None:
    """A 2011 return uses different element names for the same facts."""
    parsed = parse_return(LEGACY)

    assert parsed.ein == "391385403"     # hyphens stripped
    assert parsed.tax_year == 2011
    assert len(parsed.people) == 2

    chief = parsed.people[0]
    assert chief.name == "GLORIA HENDERSON"
    assert chief.title == "PRESIDENT AND CEO"
    assert chief.roles == ["Officer"]
    assert chief.compensation == 187_400

    assert parsed.board_members[0].name == "WALTER J BRZESKI"
    assert parsed.contractors[0].name == "LAKEFRONT TECHNOLOGY GROUP INC"
    assert parsed.contractors[0].services == "NETWORK AND HELPDESK SERVICES"


@pytest.mark.parametrize("flag", ["1", "true", "X", "yes"])
def test_checkbox_flags_are_read_in_every_form(flag: str) -> None:
    xml = f"""<Return xmlns="http://www.irs.gov/efile">
      <ReturnData><IRS990><Form990PartVIISectionAGrp>
        <PersonNm>A DIRECTOR</PersonNm>
        <IndividualTrusteeOrDirectorInd>{flag}</IndividualTrusteeOrDirectorInd>
      </Form990PartVIISectionAGrp></IRS990></ReturnData></Return>"""
    assert parse_return(xml).people[0].is_board_member


def test_unset_checkbox_is_not_a_role() -> None:
    xml = """<Return xmlns="http://www.irs.gov/efile">
      <ReturnData><IRS990><Form990PartVIISectionAGrp>
        <PersonNm>NOT A DIRECTOR</PersonNm>
        <IndividualTrusteeOrDirectorInd>0</IndividualTrusteeOrDirectorInd>
      </Form990PartVIISectionAGrp></IRS990></ReturnData></Return>"""
    assert parse_return(xml).people[0].roles == []


# ---------------------------------------------------------------------------
# Degenerate input
# ---------------------------------------------------------------------------


def test_a_return_without_part_vii_is_empty_not_an_error() -> None:
    """Small filers legitimately have neither section."""
    xml = """<Return xmlns="http://www.irs.gov/efile">
      <ReturnHeader><TaxYr>2023</TaxYr></ReturnHeader>
      <ReturnData><IRS990EZ/></ReturnData></Return>"""
    parsed = parse_return(xml)

    assert parsed.tax_year == 2023
    assert parsed.people == []
    assert parsed.contractors == []


def test_rows_without_a_name_are_skipped() -> None:
    xml = """<Return xmlns="http://www.irs.gov/efile"><ReturnData><IRS990>
      <Form990PartVIISectionAGrp><TitleTxt>DIRECTOR</TitleTxt></Form990PartVIISectionAGrp>
      <Form990PartVIISectionAGrp><PersonNm>REAL PERSON</PersonNm></Form990PartVIISectionAGrp>
    </IRS990></ReturnData></Return>"""
    parsed = parse_return(xml)

    assert [p.name for p in parsed.people] == ["REAL PERSON"]


def test_malformed_xml_raises_a_clear_error() -> None:
    with pytest.raises(ValueError, match="Not valid XML"):
        parse_return(b"<Return><unclosed>")


def test_namespace_variations_do_not_matter() -> None:
    """The efile namespace URI has changed between schema versions."""
    xml = """<Return xmlns="http://www.irs.gov/some-other-namespace">
      <ReturnData><IRS990><Form990PartVIISectionAGrp>
        <PersonNm>NAMESPACED PERSON</PersonNm>
      </Form990PartVIISectionAGrp></IRS990></ReturnData></Return>"""
    assert parse_return(xml).people[0].name == "NAMESPACED PERSON"


def test_best_return_prefers_the_newest_with_data() -> None:
    empty_2024 = parse_return(
        """<Return xmlns="http://www.irs.gov/efile">
        <ReturnHeader><TaxYr>2024</TaxYr></ReturnHeader>
        <ReturnData><IRS990EZ/></ReturnData></Return>"""
    )
    modern = parse_return(MODERN)
    legacy = parse_return(LEGACY)

    chosen = best_return([empty_2024, legacy, modern])
    assert chosen is modern
    assert best_return([empty_2024]) is None
    assert best_return([]) is None


# ---------------------------------------------------------------------------
# Locating documents
# ---------------------------------------------------------------------------


def test_index_maps_eins_to_object_ids() -> None:
    index = parse_index(INDEX_CSV)
    assert index["362167869"] == ["202441123456789012"]
    assert "391385403" in index


def test_over_padded_index_eins_are_trimmed_to_nine_digits() -> None:
    """An EIN is exactly nine digits; extra leading zeros are padding."""
    index = parse_index(INDEX_CSV)
    assert "042103594" in index
    assert "0042103594" not in index


def test_index_without_recognizable_columns_raises() -> None:
    with pytest.raises(ValueError, match="no recognizable EIN"):
        parse_index("SOMETHING,ELSE\n1,2\n")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("39-1385403", "391385403"), ("42103594", "042103594"), ("", None), (None, None)],
)
def test_ein_normalization(value: str | None, expected: str | None) -> None:
    assert normalize_ein(value) == expected


def test_local_files_are_found_by_ein_in_the_filename(tmp_path: Path) -> None:
    (tmp_path / "362167869_2023.xml").write_bytes(MODERN)
    (tmp_path / "362167869_2022.xml").write_bytes(MODERN)
    (tmp_path / "999999999_2023.xml").write_bytes(LEGACY)
    (tmp_path / "notes.txt").write_text("ignore me")

    found = local_xml_for_ein(tmp_path, "36-2167869")

    assert [path.name for path in found] == [
        "362167869_2023.xml",
        "362167869_2022.xml",
    ]


def test_missing_directory_yields_nothing(tmp_path: Path) -> None:
    assert local_xml_for_ein(tmp_path / "absent", "362167869") == []


def test_zip_archives_are_unpacked(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "ignore")
        archive.writestr("362167869_public.xml", MODERN)

    members = dict(extract_zip_members(buffer.getvalue()))
    assert list(members) == ["362167869_public.xml"]
    assert parse_return(members["362167869_public.xml"]).tax_year == 2023


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


@pytest.fixture()
def irs_config(config: Config, tmp_path: Path) -> Config:
    config.irs.local_directory = tmp_path / "irs_xml"
    (tmp_path / "irs_xml").mkdir()
    config.project_root = tmp_path
    return config


def add_org(
    session: Session,
    name: str,
    ein: str | None,
    *,
    state: str = "IL",
    status: MatchStatus = MatchStatus.AUTO,
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
    if ein:
        session.add(
            EinMatch(organization_id=org.id, ein=ein, score=99.0, status=status)
        )
    session.commit()
    return org


def test_stage_stores_people_and_contractors(
    irs_config: Config, session: Session, tmp_path: Path
) -> None:
    add_org(session, "Erie Family Health", "362167869")
    (irs_config.irs.local_directory / "362167869.xml").write_bytes(MODERN)

    result = enrich_people(session, irs_config)

    assert result.eligible == 1
    assert result.resolved == 1
    assert result.people_written == 5
    assert result.contractors_written == 3

    people = session.scalars(select(Person)).all()
    assert {p.tax_year for p in people} == {2023}
    board = [p for p in people if p.is_board_member]
    assert len(board) == 2
    assert board[0].role_label == "Board member"

    vendors = session.scalars(select(Contractor)).all()
    assert any("IT" in v.name for v in vendors)


def test_unconfirmed_matches_are_skipped(
    irs_config: Config, session: Session
) -> None:
    """Same rule as everywhere: an unconfirmed EIN carries nothing."""
    add_org(session, "Prairie Rural", "351122334", status=MatchStatus.PENDING)
    (irs_config.irs.local_directory / "351122334.xml").write_bytes(MODERN)

    result = enrich_people(session, irs_config)

    assert result.eligible == 0
    assert session.scalars(select(Person)).all() == []


def test_organizations_outside_the_footprint_are_skipped(
    irs_config: Config, session: Session
) -> None:
    add_org(session, "Lone Star Health", "741234567", state="TX")
    (irs_config.irs.local_directory / "741234567.xml").write_bytes(MODERN)

    assert enrich_people(session, irs_config).eligible == 0


def test_missing_documents_are_reported_not_fatal(
    irs_config: Config, session: Session
) -> None:
    add_org(session, "Erie Family Health", "362167869")

    result = enrich_people(session, irs_config)

    assert result.without_documents == 1
    assert result.people_written == 0
    assert result.status is RunStatus.SUCCESS
    assert any("No Form 990 XML found" in m for m in result.messages)


def test_rerunning_replaces_rather_than_duplicates(
    irs_config: Config, session: Session
) -> None:
    add_org(session, "Erie Family Health", "362167869")
    (irs_config.irs.local_directory / "362167869.xml").write_bytes(MODERN)

    enrich_people(session, irs_config)
    enrich_people(session, irs_config)

    assert len(session.scalars(select(Person)).all()) == 5
    assert len(session.scalars(select(Contractor)).all()) == 3


def test_newest_filing_wins_when_several_are_present(
    irs_config: Config, session: Session
) -> None:
    add_org(session, "Erie Family Health", "362167869")
    (irs_config.irs.local_directory / "362167869_2011.xml").write_bytes(
        LEGACY.replace(b"39-1385403", b"362167869")
    )
    (irs_config.irs.local_directory / "362167869_2023.xml").write_bytes(MODERN)

    enrich_people(session, irs_config)

    people = session.scalars(select(Person)).all()
    assert {p.tax_year for p in people} == {2023}
    assert len(people) == 5


def document_for(ein: str) -> bytes:
    """The modern fixture, re-stamped with a different filer EIN."""
    return MODERN.replace(b"<EIN>362167869</EIN>", f"<EIN>{ein}</EIN>".encode())


def test_limit_caps_the_stage(irs_config: Config, session: Session) -> None:
    for index in range(4):
        ein = f"36216786{index}"
        add_org(session, f"Health Center {index}", ein)
        (irs_config.irs.local_directory / f"{ein}.xml").write_bytes(document_for(ein))

    result = enrich_people(session, irs_config, limit=2)
    assert result.resolved == 2


# ---------------------------------------------------------------------------
# Finding documents in a real IRS download
# ---------------------------------------------------------------------------


def test_documents_are_found_by_the_ein_inside_them(
    irs_config: Config, session: Session
) -> None:
    """IRS bulk files are named by object id, which contains no EIN at all, so
    matching on the filename would find nothing in a real download."""
    add_org(session, "Erie Family Health", "362167869")
    (irs_config.irs.local_directory / "202441123456789012_public.xml").write_bytes(
        MODERN
    )

    result = enrich_people(session, irs_config)

    assert result.resolved == 1
    assert result.people_written == 5


def test_zip_archives_are_read_without_unpacking(
    irs_config: Config, session: Session
) -> None:
    """The IRS ships gigabyte ZIPs; requiring users to unpack them is friction
    for no benefit."""
    add_org(session, "Erie Family Health", "362167869")
    add_org(session, "Milwaukee Health Services", "391385403", state="WI")

    archive_path = irs_config.irs.local_directory / "2023_TEOS_XML_01A.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("202441123456789012_public.xml", MODERN)
        archive.writestr("201211987654321098_public.xml", LEGACY.replace(b"39-1385403", b"391385403"))
        archive.writestr("manifest.txt", "ignored")

    result = enrich_people(session, irs_config)

    assert result.resolved == 2
    assert result.people_written == 7
    assert result.contractors_written == 4


def test_index_prefers_the_newest_tax_year(irs_config: Config) -> None:
    from pipeline.irs import document_index

    older = MODERN.replace(b"<TaxYr>2023</TaxYr>", b"<TaxYr>2019</TaxYr>")
    (irs_config.irs.local_directory / "a.xml").write_bytes(older)
    (irs_config.irs.local_directory / "b.xml").write_bytes(MODERN)

    refs = document_index(irs_config.irs.local_directory)["362167869"]
    assert [ref.tax_year for ref in refs] == [2023, 2019]


def test_a_corrupt_archive_does_not_break_the_directory(
    irs_config: Config, session: Session
) -> None:
    add_org(session, "Erie Family Health", "362167869")
    (irs_config.irs.local_directory / "broken.zip").write_bytes(b"not a zip at all")
    (irs_config.irs.local_directory / "good.xml").write_bytes(MODERN)

    assert enrich_people(session, irs_config).resolved == 1


def test_peek_reads_identity_without_parsing() -> None:
    from pipeline.irs import peek

    assert peek(MODERN) == ("362167869", 2023)
    assert peek(LEGACY) == ("391385403", 2011)
    assert peek(b"<Return><nothing/></Return>") == (None, None)


def test_run_is_recorded(irs_config: Config, session: Session) -> None:
    add_org(session, "Erie Family Health", "362167869")
    (irs_config.irs.local_directory / "362167869.xml").write_bytes(MODERN)
    enrich_people(session, irs_config)

    run = session.scalars(select(IngestRun).where(IngestRun.stage == "people")).one()
    assert run.status == RunStatus.SUCCESS
    assert run.records_written == 8  # 5 people + 3 contractors


def test_no_contact_columns_exist() -> None:
    """Contact details are not in a 990, so there is nowhere to put invented
    ones. This is a structural guarantee, not a convention."""
    columns = set(Person.__table__.columns.keys())
    assert not {"email", "phone", "telephone", "address"} & columns
