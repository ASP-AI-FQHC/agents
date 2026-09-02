"""Apify, used to find a page -- never to read one.

Roughly a fifth of health center websites link their leadership page from
somewhere the home page crawler cannot see it: behind a hamburger menu built in
JavaScript, inside a mega-menu rendered client side, or at a path nothing links
to at all. For those, a search engine already knows the URL.

So this module asks Apify's Google Search actor one question -- *what is the
leadership page on this domain?* -- and returns URLs. Nothing else. The pages
themselves are then fetched by :mod:`pipeline.website`, through the same
``SiteFetcher`` as always, which honours ``robots.txt``, keeps to the
configured rate, and extracts names with the same rules. Every name still comes
from the organization's own page, still links back to it, and is still labelled
as read from a web page rather than from a filing.

What this module will not do, by construction:

* **LinkedIn is dropped from every result set.** Profile data there sits behind
  a login, which is the line this project drew at the start, and reading it is
  against LinkedIn's terms. The filter is not a setting.
* **No email is ever produced here.** Search snippets are not a source of
  contact details, and an address inferred from a name and a domain is a guess
  wearing the costume of a fact. Emails continue to come only from a page the
  organization published or a return it filed.
* **No content is taken from the search result.** Titles and snippets are used
  to rank candidate URLs and are then discarded. A snippet is a search
  engine's summary, not the organization's own words, and this database only
  quotes sources it has read directly.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

ProgressFn = Callable[[str], None]

# Apify's Google Search Results Scraper. Run synchronously and return the
# dataset in one call, which is what "run-sync-get-dataset-items" does.
ACTOR = "apify~google-search-scraper"
RUN_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

TOKEN_ENV = "APIFY_TOKEN"

# Never followed, whatever a search returns. The first is a matter of terms of
# service and of the project's own rule about logins; the rest are aggregators
# that republish stale copies of the page we actually want, and a name read
# from one of them could not honestly be sourced to the organization.
BLOCKED_HOSTS: tuple[str, ...] = (
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "crunchbase.com",
    "zoominfo.com",
    "rocketreach.co",
    "apollo.io",
    "signalhire.com",
    "lusha.com",
    "bloomberg.com",
    "dnb.com",
    "buzzfile.com",
    "manta.com",
    "indeed.com",
    "glassdoor.com",
)

_SKIP_EXTENSION = re.compile(
    r"\.(pdf|jpe?g|png|gif|svg|zip|docx?|xlsx?|pptx?|mp4|mp3)($|\?)", re.IGNORECASE
)


def token() -> str | None:
    """The Apify token, from the environment only.

    Deliberately not a config.yaml field: config.yaml is committed, and a token
    in a committed file is a token that leaks. Set it in the shell, or in the
    LaunchAgent's environment for a scheduled run.
    """
    value = (os.environ.get(TOKEN_ENV) or "").strip()
    return value or None


def host_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def is_blocked(url: str) -> bool:
    """Whether this result must be discarded whatever its rank."""
    host = host_of(url)
    return any(host == bad or host.endswith("." + bad) for bad in BLOCKED_HOSTS)


def search_query(website: str, organization_name: str) -> str:
    """The one question worth asking a search engine about a health center.

    Scoped to the organization's own domain. Without ``site:`` the top results
    are directories and job boards, none of which may be used as a source here,
    so the query that returns fewer results returns better ones.
    """
    host = host_of(website)
    terms = '("leadership" OR "board of directors" OR "our team" OR "executive team" OR "administration")'
    if host:
        return f"site:{host} {terms}"
    # No usable domain: fall back to the name, and expect to keep very little
    # once the blocked hosts are removed.
    return f'"{organization_name}" leadership team'


@dataclass
class SearchResult:
    """One organic result, reduced to the only field this module keeps."""

    url: str
    rank: int = 0


@dataclass
class DiscoveryResult:
    organizations_searched: int = 0
    urls_found: int = 0
    blocked_dropped: int = 0
    failed: int = 0
    messages: list[str] = field(default_factory=list)


def parse_items(items: list[dict], *, limit: int = 6) -> tuple[list[str], int]:
    """Candidate URLs from an Apify dataset, best first.

    Returns ``(urls, blocked_dropped)``. The actor returns one item per search
    page, each carrying an ``organicResults`` list; both that shape and a flat
    list of results are accepted, because the actor's output has changed shape
    before and a run that returns nothing is indistinguishable from a run that
    returned something unparsed.
    """
    urls: list[str] = []
    blocked = 0

    def consider(entry: dict) -> None:
        nonlocal blocked
        url = (entry.get("url") or entry.get("link") or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            return
        if is_blocked(url):
            blocked += 1
            return
        if _SKIP_EXTENSION.search(url):
            return
        clean = url.split("#", 1)[0].rstrip("/")
        if clean not in urls:
            urls.append(clean)

    for item in items:
        organic = item.get("organicResults")
        if isinstance(organic, list):
            for entry in organic:
                if isinstance(entry, dict):
                    consider(entry)
        elif isinstance(item, dict) and ("url" in item or "link" in item):
            consider(item)

    return urls[:limit], blocked


def find_leadership_pages(
    client,
    website: str,
    organization_name: str,
    *,
    api_token: str,
    results_per_query: int = 10,
    timeout_seconds: float = 90.0,
) -> tuple[list[str], int]:
    """Ask Apify for candidate leadership-page URLs on one organization's site.

    Raises nothing: a failure returns no URLs, and the caller records that the
    organization was searched without result. A search engine being unavailable
    is not a reason for the pipeline to stop.
    """
    payload = {
        "queries": search_query(website, organization_name),
        "maxPagesPerQuery": 1,
        "resultsPerPage": results_per_query,
        # US results for US health centers, and no personalization.
        "countryCode": "us",
        "languageCode": "en",
    }
    try:
        response = client.post(
            RUN_URL,
            params={"token": api_token},
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        items = response.json()
    except Exception:
        return [], 0

    if not isinstance(items, list):
        return [], 0
    return parse_items(items)
