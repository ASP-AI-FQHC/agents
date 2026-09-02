"""Apify search: used to find a page, never to read one.

Several of these are guarantees rather than behaviours -- LinkedIn is dropped,
no email is ever produced, no snippet text is kept -- and they are tested
because a guarantee nobody checks is a comment.
"""

from __future__ import annotations

import os

import pytest

from pipeline import apify
from pipeline.apify import (
    BLOCKED_HOSTS,
    is_blocked,
    parse_items,
    search_query,
    token,
)

# The shape Apify's Google Search actor actually returns: one item per search
# page, each carrying an organicResults list.
DATASET = [
    {
        "searchQuery": {"term": "site:nearnorthhealth.org leadership"},
        "organicResults": [
            {
                "title": "Leadership | Near North Health",
                "url": "https://www.nearnorthhealth.org/leadership/",
                "description": "Berneice Mills-Thomas, President and CEO...",
            },
            {
                "title": "Berneice Mills-Thomas - LinkedIn",
                "url": "https://www.linkedin.com/in/berneice-mills-thomas",
                "description": "President and CEO at Near North Health",
            },
            {
                "title": "Near North Health Service Corp - ZoomInfo",
                "url": "https://www.zoominfo.com/c/near-north-health/123456",
                "description": "Contact information and email format...",
            },
            {
                "title": "Board of Directors",
                "url": "https://nearnorthhealth.org/about/board-of-directors",
                "description": "Our governing board...",
            },
            {
                "title": "Annual report",
                "url": "https://nearnorthhealth.org/reports/2023.pdf",
                "description": "Download the report",
            },
        ],
    }
]


def test_linkedin_is_dropped_however_highly_it_ranks() -> None:
    """The project's one hard line: nothing behind a login."""
    urls, blocked = parse_items(DATASET)

    assert not any("linkedin" in url for url in urls)
    assert blocked >= 1


def test_contact_brokers_are_dropped_too() -> None:
    """A name from a broker could not honestly be sourced to the organization."""
    urls, _ = parse_items(DATASET)
    assert not any("zoominfo" in url for url in urls)


@pytest.mark.parametrize("host", BLOCKED_HOSTS)
def test_every_blocked_host_is_blocked_on_its_subdomains_too(host) -> None:
    assert is_blocked(f"https://www.{host}/anything")
    assert is_blocked(f"https://sub.{host}/anything")


def test_a_lookalike_domain_is_not_blocked_by_accident() -> None:
    """Suffix matching, not substring: a real health center keeps its result."""
    assert not is_blocked("https://notlinkedin.org/leadership")
    assert not is_blocked("https://mylinkedin-clinic.org/team")
    assert is_blocked("https://linkedin.com/in/someone")


def test_the_pages_we_do_want_survive_in_rank_order() -> None:
    urls, _ = parse_items(DATASET)
    assert urls[0] == "https://www.nearnorthhealth.org/leadership"
    assert "https://nearnorthhealth.org/about/board-of-directors" in urls


def test_documents_are_skipped() -> None:
    urls, _ = parse_items(DATASET)
    assert not any(url.endswith(".pdf") for url in urls)


def test_nothing_but_the_url_is_kept() -> None:
    """A snippet is a search engine's summary, not the organization's words.

    This database quotes only sources it has read directly, so the title and
    description are used for nothing and must not survive parsing.
    """
    urls, _ = parse_items(DATASET)
    assert all(isinstance(url, str) for url in urls)
    for url in urls:
        assert url.lower().startswith("http")
        assert "Berneice" not in url          # no snippet text
        assert "President and CEO" not in url


def test_no_email_can_come_out_of_a_search_result() -> None:
    """Even when a snippet contains one, it is not carried."""
    with_email = [
        {
            "organicResults": [
                {
                    "title": "Contact",
                    "url": "https://nearnorthhealth.org/contact",
                    "description": "Reach our CEO at ceo@nearnorthhealth.org",
                    "email": "ceo@nearnorthhealth.org",
                }
            ]
        }
    ]
    urls, _ = parse_items(with_email)
    assert urls == ["https://nearnorthhealth.org/contact"]
    assert all("@" not in url for url in urls)


def test_a_flat_result_list_is_understood_too() -> None:
    """The actor's output shape has changed before.

    A run that returns an unfamiliar shape and yields nothing is
    indistinguishable from one that found nothing, so both shapes are read.
    """
    flat = [
        {"url": "https://example.org/leadership", "title": "Leadership"},
        {"link": "https://example.org/team", "title": "Team"},
    ]
    urls, _ = parse_items(flat)
    assert urls == ["https://example.org/leadership", "https://example.org/team"]


def test_the_query_is_scoped_to_the_organizations_own_domain() -> None:
    query = search_query("https://www.nearnorthhealth.org/", "Near North Health")
    assert query.startswith("site:nearnorthhealth.org")
    assert "leadership" in query
    assert "board of directors" in query


def test_a_missing_domain_falls_back_to_the_name() -> None:
    query = search_query("", "Near North Health Service Corporation")
    assert "Near North Health Service Corporation" in query
    assert "site:" not in query


def test_duplicate_urls_collapse() -> None:
    dataset = [
        {
            "organicResults": [
                {"url": "https://example.org/leadership"},
                {"url": "https://example.org/leadership/"},
                {"url": "https://example.org/leadership#team"},
            ]
        }
    ]
    urls, _ = parse_items(dataset)
    assert urls == ["https://example.org/leadership"]


def test_the_token_comes_from_the_environment_only(monkeypatch) -> None:
    """config.yaml is committed; a token in it is a token that leaks."""
    monkeypatch.delenv(apify.TOKEN_ENV, raising=False)
    assert token() is None

    monkeypatch.setenv(apify.TOKEN_ENV, "  apify_api_xyz  ")
    assert token() == "apify_api_xyz"

    monkeypatch.setenv(apify.TOKEN_ENV, "   ")
    assert token() is None


def test_a_token_never_appears_in_the_config_schema() -> None:
    from app.config import WebsiteSettings

    fields = " ".join(WebsiteSettings.model_fields)
    assert "token" not in fields.lower()
    assert "key" not in fields.lower()


# ---------------------------------------------------------------------------
# The call itself, against a stub transport
# ---------------------------------------------------------------------------


class StubResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class StubClient:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append({"url": url, "params": params, "json": json})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_a_search_returns_candidate_urls() -> None:
    client = StubClient(StubResponse(DATASET))
    urls, blocked = apify.find_leadership_pages(
        client, "https://nearnorthhealth.org", "Near North", api_token="t"
    )

    assert urls[0] == "https://www.nearnorthhealth.org/leadership"
    assert blocked >= 1
    assert client.calls[0]["params"] == {"token": "t"}
    assert client.calls[0]["json"]["queries"].startswith("site:nearnorthhealth.org")


def test_an_unreachable_search_is_not_fatal() -> None:
    """A search engine being down is not a reason to stop the pipeline."""
    client = StubClient(RuntimeError("connection reset"))
    urls, blocked = apify.find_leadership_pages(
        client, "https://example.org", "Example", api_token="t"
    )
    assert urls == []
    assert blocked == 0


def test_an_error_response_is_not_fatal() -> None:
    client = StubClient(StubResponse({"error": "rate limited"}, status=429))
    urls, _ = apify.find_leadership_pages(
        client, "https://example.org", "Example", api_token="t"
    )
    assert urls == []


def test_an_unexpected_payload_shape_yields_nothing_rather_than_raising() -> None:
    client = StubClient(StubResponse({"not": "a list"}))
    urls, _ = apify.find_leadership_pages(
        client, "https://example.org", "Example", api_token="t"
    )
    assert urls == []
