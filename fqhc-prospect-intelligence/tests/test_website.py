"""Key personnel read from an organization's own website.

Everything here runs against fixture HTML through a mocked transport: no test
touches the network, and the shapes in the fixtures are the ones real health
center leadership pages actually use (a card grid, a comma-separated list, a
board table).
"""

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
    IngestRun,
    MatchStatus,
    Organization,
    Person,
    RunStatus,
    WebsiteCrawl,
    WebsitePerson,
    utcnow,
)
from pipeline.propublica import RateLimiter
from pipeline.website import (
    SiteFetcher,
    collect_from_site,
    enrich_websites,
    extract_people,
    looks_like_name,
    looks_like_title,
    normalize_website,
    rank_links,
    same_site,
    split_name_and_title,
)

FIXTURES = Path(__file__).parent / "fixtures"
HOME = (FIXTURES / "website_home.html").read_text()
LEADERSHIP = (FIXTURES / "website_leadership.html").read_text()
BOARD = (FIXTURES / "website_board.html").read_text()

PAGES = {
    "https://lakeviewchc.org": HOME,
    "https://lakeviewchc.org/about/leadership/": LEADERSHIP,
    "https://lakeviewchc.org/about/board-of-directors/": BOARD,
    "https://lakeviewchc.org/about-us/": "<html><body><h1>About Us</h1></body></html>",
}


# ---------------------------------------------------------------------------
# Recognising names and titles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Jane Okafor",
        "Miguel de la Cruz",
        "Aisha Rahman, MD",
        "Mary-Beth O'Connor",
        "Robert L. Nakamura",
    ],
)
def test_names_are_recognized(text: str) -> None:
    assert looks_like_name(text)


@pytest.mark.parametrize(
    "text",
    [
        "Board of Directors",       # a heading, not a person
        "Our Leadership Team",
        "Patient Portal",           # two capitalized words, no person
        "Chief Executive Officer",  # a title on its own
        "Suite 400",                # digits
        "info@lakeviewchc.org",
        "Lakeview",                 # a single word
        "CONTACT US TODAY FOR AN APPOINTMENT",
        "The health center is governed by a board whose members are patients",
    ],
)
def test_non_names_are_rejected(text: str) -> None:
    assert not looks_like_name(text)


@pytest.mark.parametrize(
    "text",
    ["Chief Executive Officer", "Board Chair", "Director of Nursing", "Treasurer"],
)
def test_titles_are_recognized(text: str) -> None:
    assert looks_like_title(text)


@pytest.mark.parametrize(
    "text",
    [
        "Jane Okafor",
        "Serving Chicago since 1974",
        "Board of Directors",  # the heading, not a role held by a person
    ],
)
def test_non_titles_are_rejected(text: str) -> None:
    assert not looks_like_title(text)


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Jane Okafor, Chief Executive Officer", ("Jane Okafor", "Chief Executive Officer")),
        ("Priya Venkatesan – Director of Nursing", ("Priya Venkatesan", "Director of Nursing")),
        ("Tom Whitfield | Director of Facilities", ("Tom Whitfield", "Director of Facilities")),
        ("Grace Lin, CPA, Board Treasurer", ("Grace Lin, CPA", "Board Treasurer")),
    ],
)
def test_one_line_pairs_split_correctly(line: str, expected: tuple[str, str]) -> None:
    assert split_name_and_title(line) == expected


def test_prose_is_not_split_into_a_person() -> None:
    assert split_name_and_title("Primary care, dental and behavioral health") is None
    assert split_name_and_title("Our Board of Directors, which meets monthly") is None


# ---------------------------------------------------------------------------
# Extraction from a page
# ---------------------------------------------------------------------------


def test_card_grid_and_inline_lists_are_both_read() -> None:
    found = {person.name: person.title for person in extract_people(LEADERSHIP)}

    assert found["Jane Okafor"] == "Chief Executive Officer"
    assert found["Miguel de la Cruz"] == "Chief Financial Officer"
    assert found["Robert Nakamura"] == "Chief Information Officer"
    assert found["Priya Venkatesan"] == "Director of Nursing"
    assert found["Tom Whitfield"] == "Director of Facilities"


def test_a_caption_between_name_and_title_is_stepped_over() -> None:
    found = {person.name: person.title for person in extract_people(LEADERSHIP)}
    assert found["Aisha Rahman, MD"] == "Chief Medical Officer"


def test_only_published_emails_are_captured() -> None:
    people = {person.name: person.email for person in extract_people(LEADERSHIP)}
    assert people["Jane Okafor"] == "jokafor@lakeviewchc.org"
    # Nothing is constructed for the people whose address the site withholds.
    assert people["Miguel de la Cruz"] is None


def test_prose_about_the_board_produces_no_people() -> None:
    names = {person.name for person in extract_people(LEADERSHIP)}
    assert not any("leadership" in name.lower() for name in names)
    assert "Patient Portal" not in names


def test_a_board_table_is_read() -> None:
    found = {person.name: person.title for person in extract_people(BOARD)}
    assert found["Denise Whitaker"] == "Board Chair"
    assert found["Grace Lin, CPA"] == "Board Treasurer"
    assert len(found) == 5


def test_script_and_style_content_is_ignored() -> None:
    names = {person.name for person in extract_people(HOME)}
    assert "Patient Portal" not in names


def test_extraction_is_capped() -> None:
    rows = "".join(
        f"<div><h3>Alexis Qq{chr(ord('a') + i // 26)}{chr(ord('a') + i % 26)}</h3>"
        "<p>Board Member</p></div>"
        for i in range(200)
    )
    assert len(extract_people(f"<html><body>{rows}</body></html>", limit=25)) == 25


# ---------------------------------------------------------------------------
# Finding the right page
# ---------------------------------------------------------------------------


def test_leadership_links_outrank_generic_ones() -> None:
    from pipeline.website import parse_page

    ranked = rank_links("https://lakeviewchc.org", parse_page(HOME).links)
    assert ranked[0] == "https://lakeviewchc.org/about/board-of-directors"
    assert ranked[1] == "https://lakeviewchc.org/about/leadership"
    assert ranked[-1] == "https://lakeviewchc.org/about-us"


def test_offsite_documents_and_socials_are_not_followed() -> None:
    from pipeline.website import parse_page

    ranked = rank_links("https://lakeviewchc.org", parse_page(HOME).links)
    assert not any("facebook.com" in url for url in ranked)
    assert not any(url.endswith(".pdf") for url in ranked)


def test_same_site_ignores_www() -> None:
    assert same_site("https://lakeviewchc.org", "https://www.lakeviewchc.org/board")
    assert not same_site("https://lakeviewchc.org", "https://example.org/board")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("lakeviewchc.org", "https://lakeviewchc.org"),
        ("http://lakeviewchc.org/", "http://lakeviewchc.org"),
        ("  https://lakeviewchc.org  ", "https://lakeviewchc.org"),
        ("N/A", None),
        ("", None),
        (None, None),
        ("not a url", None),
    ],
)
def test_website_addresses_are_normalized(raw, expected) -> None:
    assert normalize_website(raw) == expected


# ---------------------------------------------------------------------------
# Fetching, with a mocked transport
# ---------------------------------------------------------------------------


def make_fetcher(
    config: Config,
    *,
    pages: dict[str, str] | None = None,
    robots: str | None = None,
    fail: set[str] | None = None,
) -> tuple[SiteFetcher, list[str]]:
    """A fetcher wired to fixture pages, plus the list of URLs it requested."""
    pages = PAGES if pages is None else pages
    fail = fail or set()
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).rstrip("/")
        requested.append(url)
        if url.endswith("/robots.txt"):
            if robots is None:
                return httpx.Response(404)
            return httpx.Response(200, text=robots)
        if url in fail:
            raise httpx.ConnectTimeout("timed out", request=request)
        body = pages.get(url) or pages.get(url + "/")
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    # No real waiting in tests; the limiter's own spacing is tested separately.
    fetcher = SiteFetcher(
        config, client=client, limiter=RateLimiter(1.0, sleep=lambda _s: None)
    )
    return fetcher, requested


def test_a_site_is_read_end_to_end(config: Config) -> None:
    fetcher, _ = make_fetcher(config)
    with fetcher:
        result = collect_from_site(fetcher, "lakeviewchc.org")

    names = {person.name for person in result.people}
    assert "Jane Okafor" in names          # leadership page
    assert "Denise Whitaker" in names      # board page
    assert result.outcome == "ok"
    assert result.pages_fetched >= 3


def test_each_person_carries_the_page_they_came_from(config: Config) -> None:
    fetcher, _ = make_fetcher(config)
    with fetcher:
        result = collect_from_site(fetcher, "lakeviewchc.org")

    sources = {
        person.name: result.source_urls[
            "".join(c for c in person.name.lower() if c.isalpha())
        ]
        for person in result.people
    }
    assert sources["Denise Whitaker"].endswith("/board-of-directors")
    assert sources["Jane Okafor"].endswith("/leadership")


def test_robots_disallow_is_honoured(config: Config) -> None:
    fetcher, requested = make_fetcher(
        config, robots="User-agent: *\nDisallow: /about/"
    )
    with fetcher:
        result = collect_from_site(fetcher, "lakeviewchc.org")

    assert not any("/about/" in url for url in requested if "robots" not in url)
    assert "Jane Okafor" not in {person.name for person in result.people}


def test_a_site_that_disallows_everything_is_left_alone(config: Config) -> None:
    fetcher, requested = make_fetcher(config, robots="User-agent: *\nDisallow: /")
    with fetcher:
        result = collect_from_site(fetcher, "lakeviewchc.org")

    assert result.outcome == "blocked by robots.txt"
    assert result.people == []
    assert [url for url in requested if not url.endswith("robots.txt")] == []


def test_an_unreachable_site_is_reported_not_raised(config: Config) -> None:
    fetcher, _ = make_fetcher(config, fail={"https://lakeviewchc.org"})
    with fetcher:
        result = collect_from_site(fetcher, "lakeviewchc.org")

    assert result.outcome == "home page unreachable"
    assert result.people == []


def test_no_website_on_file_is_its_own_outcome(config: Config) -> None:
    fetcher, requested = make_fetcher(config)
    with fetcher:
        result = collect_from_site(fetcher, None)

    assert result.outcome == "no website on file"
    assert requested == []


def test_a_site_with_no_leadership_page_says_so(config: Config) -> None:
    fetcher, _ = make_fetcher(
        config,
        pages={"https://example.org": "<html><body><h1>Welcome</h1></body></html>"},
    )
    with fetcher:
        result = collect_from_site(fetcher, "example.org")

    assert result.outcome == "no leadership page found"


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def add_org(
    session: Session,
    name: str,
    *,
    website: str | None = "lakeviewchc.org",
    state: str = "IL",
    ein: str | None = None,
) -> Organization:
    org = Organization(
        dedup_key=f"{name.lower()}|{state}",
        name=name,
        normalized_name=name.lower(),
        state=state,
        city="Chicago",
        site_count=4,
        website=website,
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


def test_stage_stores_people_with_their_source(config: Config, session: Session) -> None:
    org = add_org(session, "Lakeview Community Health")
    fetcher, _ = make_fetcher(config)
    with fetcher:
        result = enrich_websites(session, config, fetcher=fetcher)

    rows = session.scalars(
        select(WebsitePerson).where(WebsitePerson.organization_id == org.id)
    ).all()
    assert result.people_written == len(rows) > 5
    assert all(row.source_url.startswith("https://lakeviewchc.org") for row in rows)


def test_website_people_never_land_in_the_filing_table(
    config: Config, session: Session
) -> None:
    """The 990 table stays exactly as authoritative as a 990."""
    add_org(session, "Lakeview Community Health")
    fetcher, _ = make_fetcher(config)
    with fetcher:
        enrich_websites(session, config, fetcher=fetcher)

    assert session.scalars(select(Person)).all() == []


def test_organizations_with_filing_people_are_skipped(
    config: Config, session: Session
) -> None:
    add_org(session, "Lakeview Community Health", ein="362167869")
    session.add(Person(ein="362167869", tax_year=2023, name="Jane Okafor"))
    session.commit()

    fetcher, requested = make_fetcher(config)
    with fetcher:
        result = enrich_websites(session, config, fetcher=fetcher)

    assert result.eligible == 0
    assert requested == []


def test_only_when_missing_can_be_turned_off(config: Config, session: Session) -> None:
    add_org(session, "Lakeview Community Health", ein="362167869")
    session.add(Person(ein="362167869", tax_year=2023, name="Jane Okafor"))
    session.commit()
    config.website.only_when_missing = False

    fetcher, _ = make_fetcher(config)
    with fetcher:
        result = enrich_websites(session, config, fetcher=fetcher)

    assert result.people_written > 0


def test_organizations_outside_the_footprint_are_left_alone(
    config: Config, session: Session
) -> None:
    add_org(session, "Austin Health Collective", state="TX")
    fetcher, requested = make_fetcher(config)
    with fetcher:
        result = enrich_websites(session, config, fetcher=fetcher)

    assert result.eligible == 0
    assert requested == []


def test_a_recent_crawl_is_not_repeated(config: Config, session: Session) -> None:
    add_org(session, "Lakeview Community Health")
    fetcher, _ = make_fetcher(config)
    with fetcher:
        enrich_websites(session, config, fetcher=fetcher)

    fetcher2, requested = make_fetcher(config)
    with fetcher2:
        result = enrich_websites(session, config, fetcher=fetcher2)

    assert result.skipped_recent == 1
    assert requested == []


def test_force_re_reads_a_recent_crawl(config: Config, session: Session) -> None:
    add_org(session, "Lakeview Community Health")
    fetcher, _ = make_fetcher(config)
    with fetcher:
        enrich_websites(session, config, fetcher=fetcher)

    fetcher2, requested = make_fetcher(config)
    with fetcher2:
        result = enrich_websites(session, config, fetcher=fetcher2, force=True)

    assert result.skipped_recent == 0
    assert requested != []


def test_a_stale_crawl_is_repeated(config: Config, session: Session) -> None:
    org = add_org(session, "Lakeview Community Health")
    session.add(
        WebsiteCrawl(
            organization_id=org.id,
            url="https://lakeviewchc.org",
            fetched_at=utcnow() - timedelta(days=config.website.refresh_after_days + 1),
        )
    )
    session.commit()

    fetcher, _ = make_fetcher(config)
    with fetcher:
        result = enrich_websites(session, config, fetcher=fetcher)

    assert result.crawled == 1


def test_a_re_read_replaces_rather_than_duplicates(
    config: Config, session: Session
) -> None:
    org = add_org(session, "Lakeview Community Health")
    for _ in range(2):
        fetcher, _ = make_fetcher(config)
        with fetcher:
            enrich_websites(session, config, fetcher=fetcher, force=True)

    names = [
        row.name
        for row in session.scalars(
            select(WebsitePerson).where(WebsitePerson.organization_id == org.id)
        ).all()
    ]
    assert len(names) == len(set(names))


def test_an_empty_result_is_still_recorded(config: Config, session: Session) -> None:
    """'Checked, found nothing' must stay distinguishable from 'never checked'."""
    org = add_org(session, "Nowhere Health", website=None)
    fetcher, _ = make_fetcher(config)
    with fetcher:
        result = enrich_websites(session, config, fetcher=fetcher)

    crawl = session.scalar(
        select(WebsiteCrawl).where(WebsiteCrawl.organization_id == org.id)
    )
    assert crawl is not None and crawl.outcome == "no website on file"
    assert result.without_website == 1


def test_the_stage_can_be_disabled(config: Config, session: Session) -> None:
    add_org(session, "Lakeview Community Health")
    config.website.enabled = False
    fetcher, requested = make_fetcher(config)
    with fetcher:
        result = enrich_websites(session, config, fetcher=fetcher)

    assert result.people_written == 0
    assert requested == []


def test_limit_caps_the_number_of_sites_read(config: Config, session: Session) -> None:
    for index in range(3):
        add_org(session, f"Health Center {index}")
    fetcher, _ = make_fetcher(config)
    with fetcher:
        result = enrich_websites(session, config, fetcher=fetcher, limit=1)

    assert result.crawled == 1


def test_run_is_recorded(config: Config, session: Session) -> None:
    add_org(session, "Lakeview Community Health")
    fetcher, _ = make_fetcher(config)
    with fetcher:
        enrich_websites(session, config, fetcher=fetcher)

    run = session.scalars(select(IngestRun).where(IngestRun.stage == "website")).one()
    assert run.status == RunStatus.SUCCESS
    assert run.records_written > 0


def test_board_roles_sort_first(config: Config, session: Session) -> None:
    from app.queries import organization_website_people

    org = add_org(session, "Lakeview Community Health")
    fetcher, _ = make_fetcher(config)
    with fetcher:
        enrich_websites(session, config, fetcher=fetcher)

    people = organization_website_people(session, org.id)
    assert people[0].is_board_member
    assert not people[-1].is_board_member


def test_a_director_of_a_department_is_not_a_board_member(
    config: Config, session: Session
) -> None:
    """"Director of Nursing" is staff; "Board Chair" is governance."""
    person = WebsitePerson(
        organization_id=1, name="Priya Venkatesan", title="Director of Nursing",
        source_url="https://example.org",
    )
    assert not person.is_board_member
