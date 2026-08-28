"""HRSA ingestion: column resolution, deduplication, and graceful degradation."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config
from app.models import (
    EinMatch,
    GranteeType,
    IngestRun,
    MatchStatus,
    Organization,
    RunStatus,
    Site,
    utcnow,
)
from pipeline.cache import FileCache
from pipeline.hrsa import (
    SITE_FIELDS,
    SourceUnavailable,
    deduplicate,
    ingest,
    load_source,
    merge_awardees,
    parse_awardees,
    parse_sites,
    resolve_columns,
)

FIXTURES = Path(__file__).parent / "fixtures"
SITES_CSV = (FIXTURES / "hrsa_sites.csv").read_text()
AWARDEES_CSV = (FIXTURES / "hrsa_awardees.csv").read_text()
RENAMED_CSV = (FIXTURES / "hrsa_sites_renamed_columns.csv").read_text()


def orgs_by_name(organizations) -> dict[str, object]:
    return {org.name: org for org in organizations}


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------


def test_resolves_standard_hrsa_headers() -> None:
    _, columns, _ = parse_sites(SITES_CSV)

    assert columns["org_name"] == "Health Center Name"
    assert columns["site_name"] == "Site Name"
    assert columns["site_state"] == "Site State Abbreviation"
    assert columns["site_zip"] == "Site Postal Code"
    assert columns["hrsa_id"] == "BHCMIS Organization Identification Number"


def test_resolves_renamed_headers_via_keywords() -> None:
    """HRSA renames columns between releases; the parser must not care."""
    records, columns, _ = parse_sites(RENAMED_CSV)

    assert columns["org_name"] == "Health Center Legal Name"
    assert columns["site_name"] == "Delivery Site Name"
    assert columns["site_id"] == "Site Location Number"
    assert columns["site_state"] == "Site Physical State"
    assert columns["site_zip"] == "Site Zip Code Value"
    assert len(records) == 3
    assert records[0].state == "IL"


def test_unknown_columns_are_simply_absent() -> None:
    resolved = resolve_columns(["Health Center Name", "Site Name"], SITE_FIELDS)
    assert set(resolved) == {"org_name", "site_name"}


def test_missing_name_column_raises() -> None:
    with pytest.raises(ValueError, match="no recognizable health center name"):
        parse_sites("Some Column,Another\n1,2\n")


def test_one_header_is_never_claimed_by_two_fields() -> None:
    _, columns, _ = parse_sites(SITES_CSV)
    assert len(set(columns.values())) == len(columns)


# ---------------------------------------------------------------------------
# Parsing and deduplication
# ---------------------------------------------------------------------------


def test_rows_without_an_organization_name_are_skipped() -> None:
    records, _, skipped = parse_sites(SITES_CSV)
    assert skipped == 1
    assert all(r.org_name for r in records)


def test_sites_collapse_to_one_row_per_organization() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    organizations = deduplicate(records)
    by_name = orgs_by_name(organizations)

    # 10 usable site rows -> 5 distinct organizations.
    assert len(organizations) == 5

    erie = by_name["Erie Family Health Centers, Inc."]
    # 3 active sites: the duplicate S-1002 row collapses and S-1004 is inactive.
    assert erie.site_count == 3
    assert {s.site_id for s in erie.sites} == {"S-1001", "S-1002", "S-1003"}


def test_inactive_sites_are_excluded_by_default_and_included_on_request() -> None:
    records, _, _ = parse_sites(SITES_CSV)

    active_only = orgs_by_name(deduplicate(records))["Erie Family Health Centers, Inc."]
    everything = orgs_by_name(deduplicate(records, active_only=False))[
        "Erie Family Health Centers, Inc."
    ]

    assert active_only.site_count == 3
    assert everything.site_count == 4


def test_organization_name_uses_the_most_common_spelling() -> None:
    """Erie appears 4x as "...Centers, Inc." and 1x as "...Center"."""
    records, _, _ = parse_sites(SITES_CSV)
    names = {org.name for org in deduplicate(records, active_only=False)}
    assert "Erie Family Health Centers, Inc." in names
    assert "Erie Family Health Center" not in names


def test_same_name_in_two_states_stays_two_organizations() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    lakeshore = [o for o in deduplicate(records) if o.name == "Lakeshore Community Health"]

    assert len(lakeshore) == 2
    assert {o.state for o in lakeshore} == {"IN", "MI"}
    assert len({o.dedup_key for o in lakeshore}) == 2


def test_organization_address_prefers_the_administrative_site() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    erie = orgs_by_name(deduplicate(records))["Erie Family Health Centers, Inc."]

    assert erie.street == "1701 W Superior St"
    assert erie.city == "Chicago"
    assert erie.zip_code == "60622"


def test_grantee_type_is_classified_from_hrsa_wording() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    by_name = orgs_by_name(deduplicate(records))

    assert by_name["Erie Family Health Centers, Inc."].grantee_type is GranteeType.AWARDEE
    assert by_name["Milwaukee Health Services Inc"].grantee_type is GranteeType.LOOK_ALIKE


def test_normalized_name_is_stored_for_matching() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    erie = orgs_by_name(deduplicate(records))["Erie Family Health Centers, Inc."]
    assert erie.normalized_name == "erie family health centers"


# ---------------------------------------------------------------------------
# Awardee merge
# ---------------------------------------------------------------------------


def test_multiple_award_rows_sum_into_one_organization() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    organizations = deduplicate(records)
    awardees, _ = parse_awardees(AWARDEES_CSV)

    matched = merge_awardees(organizations, awardees)
    erie = orgs_by_name(organizations)["Erie Family Health Centers, Inc."]

    assert erie.federal_award_amount == 6_200_000.0
    assert matched == 3  # Erie, Prairie, and Lakeshore (Gary)


def test_award_row_without_an_amount_stays_unknown_not_zero() -> None:
    """A blank award column must not be read as $0 of federal funding."""
    records, _, _ = parse_sites(SITES_CSV)
    organizations = deduplicate(records)
    awardees, _ = parse_awardees(AWARDEES_CSV)
    merge_awardees(organizations, awardees)

    prairie = orgs_by_name(organizations)["Prairie Rural Health Clinic"]
    assert prairie.federal_award_amount is None
    assert prairie.funding_program == "Community Health Center Program"


def test_organizations_without_an_award_row_stay_unknown() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    organizations = deduplicate(records)
    merge_awardees(organizations, parse_awardees(AWARDEES_CSV)[0])

    milwaukee = orgs_by_name(organizations)["Milwaukee Health Services Inc"]
    assert milwaukee.federal_award_amount is None


def test_awardee_rows_are_matched_by_name_when_hrsa_id_is_missing() -> None:
    records, _, _ = parse_sites(SITES_CSV)
    organizations = deduplicate(records)
    merge_awardees(organizations, parse_awardees(AWARDEES_CSV)[0])

    lakeshore_in = next(
        o for o in organizations if o.name == "Lakeshore Community Health" and o.state == "IN"
    )
    lakeshore_mi = next(
        o for o in organizations if o.name == "Lakeshore Community Health" and o.state == "MI"
    )
    # The award row is for the Indiana entity; the Michigan one must not inherit it.
    assert lakeshore_in.federal_award_amount == 3_400_000.0
    assert lakeshore_mi.federal_award_amount is None


# ---------------------------------------------------------------------------
# Source loading and degradation
# ---------------------------------------------------------------------------


def _transport(payload: bytes, status: int = 200) -> httpx.Client:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _failing_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fresh_cache_is_used_without_touching_the_network(tmp_path: Path) -> None:
    cache = FileCache(tmp_path, max_age_days=30)
    cache.store("sites.csv", b"cached")

    load = load_source(
        cache, "https://example.org/sites.csv", "sites.csv", client=_failing_client()
    )

    assert load.fetched_live is False
    assert load.error is None
    assert load.entry.read_text() == "cached"


def test_force_refresh_bypasses_a_fresh_cache(tmp_path: Path) -> None:
    cache = FileCache(tmp_path, max_age_days=30)
    cache.store("sites.csv", b"old")

    load = load_source(
        cache,
        "https://example.org/sites.csv",
        "sites.csv",
        force_refresh=True,
        client=_transport(b"new"),
    )

    assert load.fetched_live is True
    assert load.entry.read_text() == "new"


def test_unreachable_source_falls_back_to_stale_cache_with_an_explanation(
    tmp_path: Path,
) -> None:
    cache = FileCache(tmp_path, max_age_days=30)
    cache.store("sites.csv", b"stale", fetched_at=utcnow() - timedelta(days=90))

    load = load_source(
        cache, "https://example.org/sites.csv", "sites.csv", client=_failing_client()
    )

    assert load.fetched_live is False
    assert load.entry.read_text() == "stale"
    assert load.error is not None and "using cached copy" in load.error


def test_unreachable_source_without_any_cache_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailable, match="no cached copy"):
        load_source(
            FileCache(tmp_path),
            "https://example.org/sites.csv",
            "sites.csv",
            client=_failing_client(),
        )


def test_http_error_status_is_treated_as_unreachable(tmp_path: Path) -> None:
    cache = FileCache(tmp_path)
    cache.store("sites.csv", b"stale", fetched_at=utcnow() - timedelta(days=90))

    load = load_source(
        cache,
        "https://example.org/sites.csv",
        "sites.csv",
        client=_transport(b"<html>404</html>", status=404),
    )
    assert load.error is not None
    assert load.entry.read_text() == "stale"


def test_zipped_download_is_unwrapped(tmp_path: Path) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "ignore me")
        archive.writestr("sites.csv", SITES_CSV)

    load = load_source(
        FileCache(tmp_path),
        "https://example.org/sites.zip",
        "sites.csv",
        client=_transport(buffer.getvalue()),
    )

    assert load.entry.read_text().startswith("BHCMIS")


# ---------------------------------------------------------------------------
# End-to-end stage
# ---------------------------------------------------------------------------


@pytest.fixture()
def hrsa_config(config: Config, tmp_path: Path) -> Config:
    """Real config, pointed at a temp cache directory."""
    config.cache.directory = tmp_path / "raw"
    config.project_root = tmp_path
    return config


def _stub_client(sites: str = SITES_CSV, awardees: str = AWARDEES_CSV) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = awardees if "Awardee" in str(request.url) else sites
        return httpx.Response(200, content=body.encode())

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ingest_populates_the_database(session: Session, hrsa_config: Config) -> None:
    result = ingest(session, hrsa_config, client=_stub_client())

    assert result.organizations == 5
    # 10 usable rows -> 8 stored sites: one duplicate row and one inactive site.
    assert result.sites == 8
    assert result.source_reachable is True
    assert result.used_cache is False

    stored = session.scalars(select(Organization).order_by(Organization.name)).all()
    assert [o.name for o in stored][0] == "Erie Family Health Centers, Inc."

    erie = next(o for o in stored if o.name.startswith("Erie"))
    assert erie.site_count == 3
    assert erie.federal_award_amount == 6_200_000.0
    assert erie.grantee_type == GranteeType.AWARDEE
    assert len(erie.sites) == 3


def test_ingest_records_an_audit_run(session: Session, hrsa_config: Config) -> None:
    ingest(session, hrsa_config, client=_stub_client())

    run = session.scalars(select(IngestRun)).one()
    assert run.stage == "hrsa"
    assert run.status == RunStatus.SUCCESS
    assert run.records_written == 5
    assert run.finished_at is not None


def test_ingest_on_cached_data_is_reported_as_partial(
    session: Session, hrsa_config: Config
) -> None:
    """A cache-backed run must be visibly distinguishable from a live one."""
    cache = FileCache(hrsa_config.cache_directory, hrsa_config.cache.max_age_days)
    cached_at = utcnow() - timedelta(days=45)
    cache.store(hrsa_config.hrsa.sites_filename, SITES_CSV.encode(), fetched_at=cached_at)
    cache.store(
        hrsa_config.hrsa.awardees_filename, AWARDEES_CSV.encode(), fetched_at=cached_at
    )

    result = ingest(session, hrsa_config, client=_failing_client())

    assert result.organizations == 5
    assert result.used_cache is True
    assert result.source_reachable is False
    assert result.cache_date is not None
    assert result.status is RunStatus.PARTIAL

    run = session.scalars(select(IngestRun)).one()
    assert run.status == RunStatus.PARTIAL
    assert "unreachable" in (run.message or "")


def test_rerun_preserves_human_ein_decisions(
    session: Session, hrsa_config: Config
) -> None:
    """Re-ingesting HRSA data must not discard a reviewed EIN match."""
    ingest(session, hrsa_config, client=_stub_client())
    erie = session.scalars(
        select(Organization).where(Organization.name.startswith("Erie"))
    ).one()
    session.add(
        EinMatch(
            organization_id=erie.id,
            ein="362167869",
            score=88.0,
            status=MatchStatus.ACCEPTED,
            decided_by="analyst",
        )
    )
    session.commit()

    ingest(session, hrsa_config, force_refresh=True, client=_stub_client())

    session.expire_all()
    erie = session.scalars(
        select(Organization).where(Organization.name.startswith("Erie"))
    ).one()
    assert erie.ein == "362167869"
    assert erie.ein_match.status == MatchStatus.ACCEPTED
    # Sites were rebuilt, not duplicated.
    assert len(session.scalars(select(Site)).all()) == 8


def test_missing_awardee_file_degrades_to_unavailable_grant_data(
    session: Session, hrsa_config: Config
) -> None:
    """Losing the awardee file must not fail the run -- only the grant factor."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "Awardee" in str(request.url):
            raise httpx.ConnectError("blocked", request=request)
        return httpx.Response(200, content=SITES_CSV.encode())

    result = ingest(
        session, hrsa_config, client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    assert result.organizations == 5
    assert result.source_reachable is False
    assert any("grant-dependence" in m for m in result.messages)

    erie = session.scalars(
        select(Organization).where(Organization.name.startswith("Erie"))
    ).one()
    assert erie.federal_award_amount is None


# ---------------------------------------------------------------------------
# Finding a download that HRSA has renamed
# ---------------------------------------------------------------------------

DOWNLOAD_PAGE = """
<html><body>
  <h1>Data Downloads</h1>
  <ul>
    <li><a href="/data/download">Data Downloads</a></li>
    <li><a href="/DataDownload/DD_Files/Health_Center_Service_Delivery_and_LookAlike_Sites.csv">
        Health Center Service Delivery and Look-Alike Sites</a></li>
    <li><a href="/DataDownload/DD_Files/BPHC_HC_Awardee_Data_2026.csv">
        <span>Health Center Program</span> Awardee Data</a></li>
    <li><a href="/DataDownload/DD_Files/awardee_archive_2019.zip">Awardee Data (2019 archive)</a></li>
    <li><a href="https://data.hrsa.gov/topics/health-centers/">Health Centers overview</a></li>
    <li><a href="#top">Back to top</a></li>
  </ul>
</body></html>
"""


def test_a_renamed_download_is_found_on_the_index_page() -> None:
    from pipeline.hrsa import discover_download

    found = discover_download(DOWNLOAD_PAGE, ("awardee",))

    assert found[0] == (
        "https://data.hrsa.gov/DataDownload/DD_Files/BPHC_HC_Awardee_Data_2026.csv"
    )
    # The zip archive is a worse candidate but still offered.
    assert any(url.endswith(".zip") for url in found)


def test_discovery_ignores_pages_that_are_not_downloads() -> None:
    from pipeline.hrsa import discover_download

    found = discover_download(DOWNLOAD_PAGE, ("health", "center"))
    assert all(url.endswith((".csv", ".zip")) for url in found)
    assert not any(url.endswith("/topics/health-centers/") for url in found)


def test_discovery_requires_every_keyword() -> None:
    from pipeline.hrsa import discover_download

    assert discover_download(DOWNLOAD_PAGE, ("awardee", "nonexistent")) == []


def test_relative_links_are_resolved() -> None:
    from pipeline.hrsa import discover_download

    found = discover_download(DOWNLOAD_PAGE, ("service", "delivery"))
    assert found and found[0].startswith("https://data.hrsa.gov/")


def test_a_404_is_followed_by_a_search_of_the_index(tmp_path: Path) -> None:
    """The awardee file has been renamed; the pipeline should find the new one
    rather than reporting the old URL as gone."""
    from pipeline.hrsa import load_source

    moved = "https://data.hrsa.gov/DataDownload/DD_Files/BPHC_HC_Awardee_Data_2026.csv"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.endswith("/data/download"):
            return httpx.Response(200, text=DOWNLOAD_PAGE)
        if url == moved:
            return httpx.Response(200, content=AWARDEES_CSV.encode())
        return httpx.Response(404)

    cache = FileCache(tmp_path, 30)
    load = load_source(
        cache,
        "https://data.hrsa.gov/DataDownload/DD_Files/Old_Name.csv",
        "hrsa_program_awardees.csv",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        discover_keywords=("awardee",),
    )

    assert load.fetched_live
    assert moved in requested
    assert "has moved" in (load.error or "")


def test_discovery_is_only_attempted_after_the_configured_urls_fail(
    tmp_path: Path,
) -> None:
    from pipeline.hrsa import load_source

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=AWARDEES_CSV.encode())

    cache = FileCache(tmp_path, 30)
    load = load_source(
        cache,
        "https://data.hrsa.gov/DataDownload/DD_Files/Works.csv",
        "hrsa_program_awardees.csv",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        discover_keywords=("awardee",),
    )

    assert load.error is None
    assert requested == ["https://data.hrsa.gov/DataDownload/DD_Files/Works.csv"]


def test_when_the_index_has_no_match_the_message_says_so(tmp_path: Path) -> None:
    from pipeline.hrsa import SourceUnavailable, load_source

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/data/download"):
            return httpx.Response(200, text="<html><body>nothing here</body></html>")
        return httpx.Response(404)

    cache = FileCache(tmp_path, 30)
    with pytest.raises(SourceUnavailable, match="no link matching awardee"):
        load_source(
            cache,
            "https://data.hrsa.gov/DataDownload/DD_Files/Gone.csv",
            "hrsa_program_awardees.csv",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            discover_keywords=("awardee",),
        )
